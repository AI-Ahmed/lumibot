# Upstream Merge Protocol

One-line description: Engineering runbook for syncing equity-safe changes from Lumiwealth/lumibot without importing forbidden brokers, data sources, or asset types.

Last Updated: 2026-07-08  
Status: Active  
Audience: Contributors  

## Overview

This fork diverged from [Lumiwealth/lumibot](https://github.com/Lumiwealth/lumibot) (`upstream/dev`). Upstream is a **full-feature** trading framework; this fork is **equity-only**.

**Rule:** Analyze → classify → cherry-pick. Never blind-merge.

See also: [`docs/handoffs/2026-07-08_UPSTREAM_MERGE_EQUITY_FILTER.md`](handoffs/2026-07-08_UPSTREAM_MERGE_EQUITY_FILTER.md) for commit classification tables.

---

## Fork scope (reminder)

| KEEP | DELETE |
|------|--------|
| Alpaca, IB (TWS+REST), Yahoo, Alpha Vantage | Tradier, Schwab, Tradovate, Bitunix, ProjectX, CCXT, Polygon, ThetaData, DataBento |
| Equity stocks & ETFs | Options, futures, crypto, forex, prediction markets |

---

## Before every sync

```bash
cd /path/to/lumibot
git fetch upstream dev
scripts/upstream_cherry_pick_todo.sh status   # shows archived backlog + new count
scripts/upstream_cherry_pick_todo.sh new      # only commits AFTER reviewed baseline
git status --porcelain=v1
```

**Reviewed baseline:** `docs/UPSTREAM_REVIEWED_BASELINE.txt` records the upstream SHA through which the 2026-07-08 audit was completed (`b618afa6`, v4.5.74). The 735-commit backlog is **archived** — do not re-classify it. Only review commits that appear in `new`.

Read:
1. `BROKER_DATA_SOURCE_INVENTORY.md`
2. `docs/handoffs/2026-07-08_UPSTREAM_MERGE_EQUITY_FILTER.md`
3. `docs/handoffs/2026-07-08_UPSTREAM_CHERRY_PICK_TODO.md` — Tier 1/2 commit manifest
4. `docs/UPSTREAM_REVIEWED_BASELINE.txt` — last reviewed upstream tip
5. `.cursor/rules/equity-only-fork.mdc`

---

## Classify commits

```bash
# NEW commits only (since reviewed baseline — not the full 735 backlog)
scripts/upstream_cherry_pick_todo.sh new

# Inspect one commit
git show --name-only --format='%h %s' <sha>

# Quick reject scan
git show --name-only <sha> | rg -i 'tradier|schwab|tradovate|thetadata|polygon|databento|ccxt|option|futures|crypto|forex|bitunix|projectx'
```

| Exit | Meaning |
|------|---------|
| Matches forbidden patterns | **REJECT** — skip cherry-pick |
| No matches | **REVIEW** diff — may ACCEPT |
| Only equity-safe paths | **ACCEPT** — cherry-pick |

---

## Apply ACCEPT commits

```bash
git cherry-pick <sha>
# resolve conflicts favoring fork scope (drop forbidden hunks)
uv run pytest tests/test_alpaca.py tests/backtest/test_yahoo.py -q -m "not apitest"
```

If a merge brings back deleted modules, **remove them again** per deletion TODO.

---

## Post-sync checklist

- [ ] No forbidden broker modules re-introduced
- [ ] No options/futures/crypto tests re-added
- [ ] `BACKTESTING_DATA_SOURCE` default still `yahoo` (not `thetadata`)
- [ ] `setup.py` has no forbidden deps re-added
- [ ] CHANGELOG notes fork-specific filtering if version bumped
- [ ] `graphify update .` if code structure changed
- [ ] Advance baseline after review: `scripts/upstream_cherry_pick_todo.sh set-baseline`

---

## What upstream README claims (do NOT adopt wholesale)

Upstream README lists options, crypto, futures, forex, Polymarket, ThetaData, Polygon, DataBento, Tradier, Schwab, Tradovate, ProjectX, Bitunix. **Ignore for this fork** — reference only when tracing commit intent.

---

## AI agent instructions

When user asks to "sync with upstream" or "merge main":

1. Fetch and run `scripts/upstream_cherry_pick_todo.sh new` (not full `HEAD..upstream/dev`)
2. Classify each **new** candidate (see filter handoff)
3. Cherry-pick ACCEPT only
4. Re-filter tests/scripts
5. Report REJECT list with reasons
6. Run `set-baseline` when review session is complete

Do **not** copy upstream `CLAUDE.md` or `AGENTS.md` ThetaData sections without fork override banners.
