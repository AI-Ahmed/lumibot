#!/usr/bin/env bash
# Upstream cherry-pick TODO tool for equity-only fork.
# Usage:
#   scripts/upstream_cherry_pick_todo.sh status
#   scripts/upstream_cherry_pick_todo.sh list [tier1|tier2]
#   scripts/upstream_cherry_pick_todo.sh cherry-pick tier1
#   scripts/upstream_cherry_pick_todo.sh cherry-pick <sha>
#   scripts/upstream_cherry_pick_todo.sh show <sha>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UPSTREAM_REF="${UPSTREAM_REF:-upstream/dev}"
TODO_DOC="docs/handoffs/2026-07-08_UPSTREAM_CHERRY_PICK_TODO.md"
BASELINE_FILE="docs/UPSTREAM_REVIEWED_BASELINE.txt"

# Tier 1 — ACCEPT (chronological: oldest first)
TIER1_SHAS=(
  82aa9bea
  85f5b1db
  f61c19a5
  f4ce122f
  0cf578fd
  e3cb48fe
  847fcbd9
  6375ad5f
  37469128
  2917361e
  eeb7eb54
  8d67a579
  2e37023c
  8c6d6c08
  588bb03c
)

# Tier 2 — REVIEW (P0 then P1)
TIER2_SHAS=(
  3c5cbb5c
  721fab17
  5a13897a
  7dc7697f
  009443c0
  fee632b1
  bc12c76b
  fb9fc658
  9497a875
  609d7e88
  76a58cb5
  c5a4ffc8
  8d7c3a11
  c6459221
  a9985330
  73b874bf
  f52882a3
)

die() { echo "error: $*" >&2; exit 1; }

read_baseline_sha() {
  [[ -f "$BASELINE_FILE" ]] || return 1
  grep -E '^REVIEWED_THROUGH=' "$BASELINE_FILE" | cut -d= -f2
}

read_baseline_field() {
  local key="$1"
  grep -E "^${key}=" "$BASELINE_FILE" 2>/dev/null | cut -d= -f2- || true
}

require_upstream() {
  git fetch upstream dev >/dev/null 2>&1 || true
  git rev-parse --verify "$UPSTREAM_REF" >/dev/null 2>&1 || die "missing ref $UPSTREAM_REF (git fetch upstream dev)"
}

cherry_status() {
  local sha="$1"
  local line
  line="$(git cherry HEAD "$UPSTREAM_REF" | grep -E "^[+-] ${sha}" || true)"
  if [[ -z "$line" ]]; then
    # Commit may be an ancestor or not in range
    if git merge-base --is-ancestor "$sha" HEAD 2>/dev/null; then
      echo "merged"
    else
      echo "unknown"
    fi
  elif [[ "$line" == +* ]]; then
    echo "pending"
  else
    echo "merged"
  fi
}

print_commit() {
  local sha="$1" tier="$2"
  local status subj
  status="$(cherry_status "$sha")"
  subj="$(git log -1 --format='%s' "$sha" 2>/dev/null || echo '???')"
  printf "  [%s] %s  %s  (%s)\n" "$status" "$sha" "$subj" "$tier"
}

cmd_status() {
  require_upstream
  local behind ahead baseline new_count
  behind="$(git rev-list --count HEAD.."$UPSTREAM_REF")"
  ahead="$(git rev-list --count "$UPSTREAM_REF"..HEAD)"
  echo "Upstream sync status"
  echo "  fork HEAD:    $(git log -1 --oneline HEAD)"
  echo "  upstream:     $(git log -1 --oneline "$UPSTREAM_REF")"
  echo "  behind:       $behind (total fork divergence — includes archived backlog)"
  echo "  ahead:        $ahead"
  echo "  todo doc:     $TODO_DOC"
  if baseline="$(read_baseline_sha 2>/dev/null)"; then
    new_count="$(git rev-list --count "${baseline}..${UPSTREAM_REF}" 2>/dev/null || echo 0)"
    echo "  baseline:     $baseline ($(read_baseline_field REVIEWED_DATE), v$(read_baseline_field REVIEWED_UPSTREAM_VERSION))"
    echo "  new upstream: $new_count commit(s) since baseline (run: $0 new)"
    if [[ "$new_count" == "0" ]]; then
      echo "  → No new upstream commits to review. Backlog archived."
    fi
  else
    echo "  baseline:     (missing — run: $0 set-baseline)"
  fi
  echo ""
  echo "Tier 1 (ACCEPT — cherry-pick as-is):"
  local t1_done=0 t1_pending=0
  for sha in "${TIER1_SHAS[@]}"; do
    print_commit "$sha" "T1"
    case "$(cherry_status "$sha")" in
      merged) ((t1_done++)) || true ;;
      pending) ((t1_pending++)) || true ;;
    esac
  done
  echo "  → $t1_done merged, $t1_pending pending (of ${#TIER1_SHAS[@]})"
  echo ""
  echo "Tier 2 (REVIEW — manual port safe hunks):"
  local t2_done=0 t2_pending=0
  for sha in "${TIER2_SHAS[@]}"; do
    print_commit "$sha" "T2"
    case "$(cherry_status "$sha")" in
      merged) ((t2_done++)) || true ;;
      pending) ((t2_pending++)) || true ;;
    esac
  done
  echo "  → $t2_done merged, $t2_pending pending (of ${#TIER2_SHAS[@]})"
  echo ""
  echo "Blockers:"
  if [[ ! -f lumibot/_lazy_imports.py ]]; then
    echo "  - lumibot/_lazy_imports.py missing (needed for 588bb03c)"
  fi
  if [[ -n "$(git status --porcelain=v1)" ]]; then
    echo "  - working tree dirty — commit or stash before cherry-pick"
  fi
}

cmd_list() {
  local tier="${1:-all}"
  case "$tier" in
    tier1|t1)
      printf '%s\n' "${TIER1_SHAS[@]}"
      ;;
    tier2|t2)
      printf '%s\n' "${TIER2_SHAS[@]}"
      ;;
    all)
      echo "# Tier 1"
      printf '%s\n' "${TIER1_SHAS[@]}"
      echo "# Tier 2"
      printf '%s\n' "${TIER2_SHAS[@]}"
      ;;
    *)
      die "unknown tier: $tier (use tier1, tier2, or all)"
      ;;
  esac
}

cmd_show() {
  local sha="${1:-}"
  [[ -n "$sha" ]] || die "usage: $0 show <sha>"
  git show --stat --format='%h %s%n%b' "$sha"
}

cmd_cherry_pick() {
  local target="${1:-}"
  [[ -n "$target" ]] || die "usage: $0 cherry-pick tier1|<sha>"

  require_upstream

  if [[ -n "$(git status --porcelain=v1)" ]]; then
    die "working tree not clean — resolve before cherry-pick"
  fi

  if [[ "$target" == "tier1" || "$target" == "t1" ]]; then
    echo "Cherry-picking Tier 1 (${#TIER1_SHAS[@]} commits, oldest first)..."
    for sha in "${TIER1_SHAS[@]}"; do
      if [[ "$(cherry_status "$sha")" == "merged" ]]; then
        echo "skip $sha (already merged)"
        continue
      fi
      echo "--- cherry-pick $sha ---"
      git cherry-pick "$sha" || {
        echo "FAILED at $sha — resolve conflicts, then:"
        echo "  git cherry-pick --continue   # or --abort"
        exit 1
      }
    done
    echo "Tier 1 complete. Run pytest subset and update $TODO_DOC checkboxes."
    return 0
  fi

  local sha="$target"
  if [[ "$(cherry_status "$sha")" == "merged" ]]; then
    echo "skip $sha (already merged)"
    return 0
  fi
  echo "Cherry-picking $sha (single commit)..."
  git cherry-pick "$sha"
  echo "Done. For Tier 2 commits, verify no forbidden files were re-introduced."
}

cmd_new() {
  require_upstream
  local baseline limit="${1:-50}"
  baseline="$(read_baseline_sha)" || die "missing $BASELINE_FILE — run: $0 set-baseline"
  local count
  count="$(git rev-list --count "${baseline}..${UPSTREAM_REF}")"
  echo "New upstream commits since reviewed baseline"
  echo "  baseline:  $baseline ($(read_baseline_field REVIEWED_DATE))"
  echo "  upstream:  $(git log -1 --oneline "$UPSTREAM_REF")"
  echo "  count:     $count"
  echo ""
  if [[ "$count" == "0" ]]; then
    echo "Nothing new to review."
    return 0
  fi
  git log --oneline "${baseline}..${UPSTREAM_REF}" | head -n "$limit"
  if [[ "$count" -gt "$limit" ]]; then
    echo "... ($((count - limit)) more — increase limit: $0 new <limit>)"
  fi
}

cmd_set_baseline() {
  require_upstream
  local new_sha date version
  new_sha="$(git rev-parse "$UPSTREAM_REF")"
  date="$(date +%Y-%m-%d)"
  version="$(git show "$new_sha:setup.py" 2>/dev/null | grep -E 'version=' | head -1 | sed -E 's/.*version="([^"]+)".*/\1/' || echo unknown)"
  cat > "$BASELINE_FILE" <<EOF
# Upstream review baseline — equity-only fork
#
# All upstream/dev commits at or before REVIEWED_THROUGH were classified.
# Do NOT re-audit this backlog. Only classify NEW commits after this SHA.
#
# Check for new upstream work:
#   scripts/upstream_cherry_pick_todo.sh new
#
# After a future review session, advance the baseline:
#   scripts/upstream_cherry_pick_todo.sh set-baseline
#
REVIEWED_THROUGH=${new_sha}
REVIEWED_DATE=${date}
REVIEWED_UPSTREAM_VERSION=${version}
EOF
  echo "Baseline updated to $new_sha (v${version}) on ${date}"
  echo "Future 'new' checks will only show commits after this point."
}

usage() {
  cat <<EOF
Upstream cherry-pick TODO tool (equity-only fork)

Usage:
  $0 status                     Show merge gap and per-commit status
  $0 new [limit]                List upstream commits since reviewed baseline
  $0 set-baseline               Advance reviewed baseline to current upstream/dev
  $0 list [tier1|tier2|all]     Print commit SHAs
  $0 show <sha>                 Show commit stat summary
  $0 cherry-pick tier1          Cherry-pick all Tier 1 ACCEPT commits
  $0 cherry-pick <sha>          Cherry-pick one commit

Docs: $TODO_DOC
      $BASELINE_FILE
Env:  UPSTREAM_REF (default: upstream/dev)
EOF
}

main() {
  local cmd="${1:-status}"
  shift || true
  case "$cmd" in
    status) cmd_status ;;
    new) cmd_new "${1:-50}" ;;
    set-baseline|set_baseline) cmd_set_baseline ;;
    list) cmd_list "${1:-all}" ;;
    show) cmd_show "${1:-}" ;;
    cherry-pick|pick) cmd_cherry_pick "${1:-}" ;;
    -h|--help|help) usage ;;
    *) die "unknown command: $cmd (try --help)" ;;
  esac
}

main "$@"
