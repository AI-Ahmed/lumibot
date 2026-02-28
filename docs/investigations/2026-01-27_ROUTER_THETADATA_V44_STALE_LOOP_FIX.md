# 2026-01-27 — Router / ThetaData — v44 warm-cache STALE loop fix

## TL;DR

In router-mode ThetaData backtests, warm S3 cache runs on `LUMIBOT_CACHE_S3_VERSION=v44` were taking **~205s** locally (and **~20 minutes** in production BotSpot per report) while emitting **~1,882** lines of:

`[THETA][CACHE][STALE] ... prefetch_complete but coverage insufficient`

Root cause: the day-cache metadata could simultaneously say **`prefetch_complete=True`** (via `tail_missing_permanent=True`) and **`existing_end < end_requirement`** (because day-mode `Data.df` intentionally drops placeholder rows, making `existing_end` look like the last *real* bar). That mismatch caused a per-bar refresh loop (STALE → REFRESH) even though the run was already queue-free.

Fix: treat `tail_missing_permanent=True` as satisfying the end-coverage check so we reuse the warm cache without thrashing.

## Repro (local, prod-like)

These commands intentionally avoid printing any secrets. They assume you have local dotenv wiring for the downloader + S3 cache.

```bash
python3 scripts/run_backtest_prodlike.py \
  --main /Users/robertgrzesik/Documents/Development/backtest_strategies/tqqq_smc_slow/main.py \
  --start 2016-01-01 \
  --end 2026-01-01 \
  --data-source '{"default":"thetadata","crypto":"ibkr","future":"ibkr","cont_future":"ibkr"}' \
  --cache-version v44 \
  --cache-folder /Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_v44_repro \
  --use-dotenv-s3-keys \
  --label v44_repro
```

### Baseline symptoms (before fix)

- `elapsed_s ≈ 205.6`
- `queue_submits = 0` (warm S3 invariant held)
- `thetadata_cache_stale = 1882`

### After fix

- `elapsed_s ≈ 28.1`
- `queue_submits = 0`
- `thetadata_cache_stale = 0`

## What was happening

### 1) Theta daily caches can contain placeholder trading days

ThetaData day OHLC caches can include rows with `missing=True` placeholders (for example, when data is not available at warm time).

In the v44 `TQQQ` day cache, the last real day was `2025-12-26`, and the next trading days were present as placeholder rows (e.g., `2025-12-29`, `2025-12-30`, `2025-12-31`), along with additional placeholder rows into January.

### 2) Day-mode `Data.df` intentionally drops placeholder rows

`ThetaDataBacktestingPandas._update_pandas_data()` builds two frames:
- a “metadata frame” that includes placeholders for coverage accounting
- a “cleaned df” that drops placeholder rows (so downstream Strategy history calls don’t ingest NaNs)

The `Data` object stored in-memory uses the cleaned df, so its *observed* end timestamp corresponds to the last **real** day.

### 3) `prefetch_complete` can still be True due to `tail_missing_permanent`

When placeholder coverage reaches (or extends past) the requested end window, the metadata may set:
- `tail_placeholder = True`
- `tail_missing_permanent = True`
- `prefetch_complete = True` (via `_compute_prefetch_complete`)

This is intended to prevent repeated downloader submissions when the “tail” is known to be unavailable.

### 4) The mismatch created a hot-loop (STALE → REFRESH)

On subsequent iterations, the day-mode metadata rebuild used the cleaned df (no placeholders), which makes `existing_end` appear behind `end_requirement`.

That produced:
- `prefetch_complete=True` (because `tail_missing_permanent=True`)
- `end_ok=False` (because `existing_end` uses real rows only)

Result: every bar logged `[THETA][CACHE][STALE]` and entered the refresh path, even though:
- the run was queue-free (`queue_submits=0`)
- no real additional data could be fetched/merged for the required end (the placeholders were the “best available” for that cache namespace)

## Fix

Change: treat `tail_missing_permanent=True` as satisfying the end-coverage check during cache validation.

Code:
- `lumibot/backtesting/thetadata_backtesting_pandas.py` — in the end validation block, if `end_ok` is false but `tail_missing_permanent` is true, force `end_ok=True` (debug-logged).

This prevents day-mode backtests from re-entering the refresh path on every bar when the cache explicitly records that the tail is permanently missing for this run.

## Tests and validation

### Unit tests

- Added a regression assertion that calling `_update_pandas_data()` twice with a tail placeholder does **not** invoke the downloader the second time.
- Ran targeted unit tests around ThetaData cache behavior.

### Acceptance backtests

Ran the full acceptance backtest suite:
- `pytest -q tests/backtest/test_acceptance_backtests_ci.py`

Important: local acceptance runs must use the Strategy Library demo dotenv wiring (so they point at the correct warm S3 namespace), otherwise they will fail due to missing cache objects.

## Production impact expectation

This change targets “warm cache but still slow” behavior.

If production BotSpot backtests were spending most of their wall time in the v44 day-mode STALE/REFRESH loop, this fix should provide a **multi‑X speedup** without changing strategy logic or disabling router mode.

## Follow-ups

- Consider a dedicated “placeholder healing” workflow for stocks/indices (outside CI) when placeholders represent now-available days (to improve accuracy at the end of historical windows), while keeping acceptance warm-cache invariants intact.
