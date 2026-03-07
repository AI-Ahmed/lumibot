#!/usr/bin/env bash
# Run the same steps as GitHub Actions CI locally (install, lint, unit tests, backtest tests).
# Use before push to catch failures without waiting for Actions.
#
# Usage:
#   ./scripts/run_ci_local.sh          # full CI (lint + unit + backtest)
#   RUN_BACKTEST=0 ./scripts/run_ci_local.sh   # skip backtest (faster)
#
# Optional: export GIT_TOKEN so the private FPAP dependency is installed (same as Actions).
# Optional: USE_UV=0 to force pip even when uv is installed (e.g. in CI).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Prefer repo .venv so pre-push hook (no activated venv) uses project deps (ruff, pytest, etc.)
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
else
  echo "run_ci_local.sh: no python found; activate the repo venv or install Python."
  exit 127
fi
export PYTHON

# CI-like env (match .github/workflows/cicd.yaml)
export AIOHTTP_NO_EXTENSIONS="${AIOHTTP_NO_EXTENSIONS:-1}"
export BACKTESTING_DATA_SOURCE="${BACKTESTING_DATA_SOURCE:-none}"
export BACKTESTING_SHOW_PROGRESS_BAR="${BACKTESTING_SHOW_PROGRESS_BAR:-false}"
export PYTEST_MARKERS="${PYTEST_MARKERS:-not apitest and not downloader}"

# Prefer uv when available (venvs created with "uv venv" often have no pip)
USE_UV="${USE_UV:-}"
if [ "$USE_UV" = "" ]; then
  command -v uv >/dev/null 2>&1 && USE_UV=1 || USE_UV=0
fi

echo "=== Resolve and install dependencies (CI-like) ==="
if [ -n "${GIT_TOKEN:-}" ]; then
  sed 's/${GIT_TOKEN}/'"$GIT_TOKEN"'/g' requirements.txt > requirements.resolved.txt
else
  grep -v 'FPAP.git' requirements.txt > requirements.resolved.txt
fi

if [ "$USE_UV" = "1" ]; then
  uv pip install requests ruff pytest-mock
  uv pip install -r requirements.resolved.txt
else
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install requests ruff pytest-mock
  "$PYTHON" -m pip install -r requirements.resolved.txt
fi

echo "=== Lint (Ruff, same scope as CI) ==="
"$PYTHON" -m ruff check --select F,I \
  lumibot/tools/thetadata_helper.py \
  lumibot/tools/data_downloader_queue_client.py \
  lumibot/backtesting/thetadata_backtesting_pandas.py \
  lumibot/components/options_helper.py \
  lumibot/strategies/_strategy.py \
  tests/backtest/test_acceptance_backtests_ci.py \
  tests/test_thetadata_day_timestamp_alignment.py \
  tests/test_thetadata_get_last_price_trade_only.py \
  tests/test_options_helper_thetadata_actionable_strikes.py \
  tests/test_thetadata_queue_client.py

echo "=== Unit tests (shard 0/6, markers=$PYTEST_MARKERS) ==="
SHARD_INDEX=0 SHARD_TOTAL=6 "$PYTHON" - <<'PY'
import os
import subprocess
import sys
from collections import Counter

shard_index = int(os.environ["SHARD_INDEX"])
shard_total = int(os.environ["SHARD_TOTAL"])
markers = os.environ.get("PYTEST_MARKERS", "not apitest and not downloader")

collect_cmd = [
    "pytest", "tests/", "--ignore=tests/backtest/",
    "-m", markers, "--collect-only", "-q",
]
proc = subprocess.run(collect_cmd, capture_output=True, text=True)
if proc.returncode != 0:
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)

nodeids = [line.strip() for line in proc.stdout.splitlines() if "::" in line]
counts = Counter(nodeid.split("::", 1)[0] for nodeid in nodeids)
files = sorted(counts.items(), key=lambda item: (-item[1], item[0]))

bins = [{"count": 0, "files": []} for _ in range(shard_total)]
for path, count in files:
    target_bin = min(bins, key=lambda b: (b["count"], len(b["files"])))
    target_bin["files"].append(path)
    target_bin["count"] += count

selected_files = bins[shard_index]["files"]
with open("shard_files.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(selected_files) + "\n")

print(f"Shard {shard_index}/{shard_total} files={len(selected_files)} tests={bins[shard_index]['count']}")
PY

UNIT_FILES=$(cat shard_files.txt | tr '\n' ' ')
"$PYTHON" -m pytest -m "$PYTEST_MARKERS" --tb=short -q --durations=30 -x $UNIT_FILES

if [ "${RUN_BACKTEST:-1}" = "0" ]; then
  echo "=== Skipping backtest tests (RUN_BACKTEST=0) ==="
  echo "=== Local CI finished (lint + unit) ==="
  exit 0
fi

echo "=== Backtest tests (shard 0/4) ==="
SHARD_INDEX=0 SHARD_TOTAL=4 "$PYTHON" - <<'PY'
import os
import subprocess
import sys

shard_index = int(os.environ["SHARD_INDEX"])
shard_total = int(os.environ["SHARD_TOTAL"])
markers = os.environ.get("PYTEST_MARKERS", "not apitest and not downloader")

collect_cmd = [
    "pytest", "tests/backtest/", "-m", markers, "--collect-only", "-q",
]
proc = subprocess.run(collect_cmd, capture_output=True, text=True)
if proc.returncode != 0:
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    sys.exit(proc.returncode)

nodeids = [line.strip() for line in proc.stdout.splitlines() if "::" in line]
bins = [[] for _ in range(shard_total)]
for idx, nodeid in enumerate(nodeids):
    bins[idx % shard_total].append(nodeid)

with open("shard_nodeids.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(bins[shard_index]) + "\n")

print(f"Shard {shard_index}/{shard_total} nodeids={len(bins[shard_index])}")
PY

BACKTEST_NODEIDS=$(cat shard_nodeids.txt | tr '\n' ' ')
"$PYTHON" -m pytest -m "$PYTEST_MARKERS" --tb=short -q --durations=30 -x $BACKTEST_NODEIDS

echo "=== Local CI finished (lint + unit + backtest) ==="
