# 2026-01-05 — NVDA Accuracy Audit + Crash Root Cause (manager_bot_id=334e2c98-7134-4f38-860c-b6b11879a51b)

## Scope

- Strategy code (read-only repro): `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/334e2c98-7134-4f38-860c-b6b11879a51b/main.py`
- Engine: LumiBot (local prod-faithful backtests)
- Data provider: ThetaData via remote downloader
- Cache backend: S3 (`LUMIBOT_CACHE_BACKEND=s3`)

## Goals

- **P0 (prod crash):** explain and eliminate the “ERROR_CODE_CRASH with no traceback” failure mode.
- **Performance:** warm-cache runs should be fast (no runaway refetch loops).
- **Accuracy:** produce a full “MELI-style” audit table for every trade-event with maximum telemetry.

## Root Cause: missing-day detection used UTC dates (after-hours spillover)

### Symptom

Backtests would repeatedly report:

- `Using forward-filled price ... (no current price available)`
- `prefetch_complete but coverage insufficient ...` + `refreshing cache ... reasons=end`
- **But no downloader request was enqueued for the missing trading day**, so the cached dataset stayed stale.

In practice this manifested as **every other trading day missing** for intraday stock OHLC:

- ET trading days present: **Feb 3 + Feb 5**
- But UTC calendar days present: **Feb 3 + Feb 4 + Feb 5 + Feb 6**
  - because **Feb 3 after-hours** (19:xx ET) is **Feb 4 UTC**, and similarly for Feb 5 → Feb 6 UTC.

### Why it happened

ThetaData intraday caches are stored with **UTC timestamps** and (by default) include extended-hours bars.

`thetadata_helper.get_missing_dates()` historically computed cached coverage using:

- `df.index.date` (UTC calendar days)

This caused the after-hours bars to “leak” into the next UTC day, so the cache looked like it already covered the next trading day and **skipped downloading it**.

That led to:

- missing intraday OHLC for alternating market days,
- lots of forward-filled prices,
- and long-running backtests (and in production, a likely ECS OOM/exit without a Python traceback, reported as `ERROR_CODE_CRASH`).

### Fix (LumiBot)

Commit: `5dda5002` — `Fix ThetaData missing-day detection across UTC midnights`

- In `lumibot/tools/thetadata_helper.py`, compute missing-day coverage using the **market-local date** (convert index to `LUMIBOT_DEFAULT_PYTZ` before taking `.date`).
- Regression test: `tests/test_thetadata_missing_dates_market_timezone.py`

## Local Proof Run (audit-enabled)

This run is short but includes a real order and fill so the audit telemetry is concrete.

- Window: `2025-01-20 -> 2025-02-10`
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/nvda_trade_audit_20260105_063026`
- Cache folder: `/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_nvda_trade_audit_20260105_063026`
- S3 cache version: `v1`
- Result: ✅ completed, tearsheet produced, and trade-event audit emitted.

### Audit artifact

- `docs/investigations/data/2026-01-05_nvda_trade_events_audit.csv`

### Trade-event highlights (human sanity check)

| dt (ET) | event | contract | qty | limit | fill | underlying px | notes |
|---|---|---|---:|---:|---:|---:|---|
| 2025-01-27 09:30 | submit | NVDA 2025-04-17 135C | 43 | 9.25 | — | 124.5658 | drawdown ≥ 5% triggered entry |
| 2025-01-27 09:30 | fill | NVDA 2025-04-17 135C | 43 | 9.25 | 9.25 | 124.5658 | fill model: OHLC limit logic |

## Notes / Next Steps

- A longer continuous run through **option expiry (2025-04-17)** is required to fully audit the lifecycle (expiry/exit behavior) in a single backtest run.
- Cold-cache hydrating runs can exceed a 20m leash; warm-cache proof runs should be much faster once S3 is fully hydrated for the full window.

## Warm-cache proof (short window; tearsheet present; queue-free)

This validates the intended caching semantics for NVDA backtests:

- Cold run (fresh S3 namespace) may enqueue downloader work.
- Warm run (same S3 namespace, fresh local cache folder) should be **queue-free** and materially faster.

### Run details

- Window: `2015-02-02 -> 2015-03-02`
- Strategy: NVDA customer repro main.py (read-only)

Cold run (hydrates S3):

- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/20260105_103005_nvda_cache_smoke_cold`
- S3 cache version: `nvda_cache_smoke_20260105_103005`
- Result: ✅ completed, tearsheet produced
- `queue_submits`: **24**

Warm run (fresh local disk, warm S3):

- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/20260105_103200_nvda_cache_smoke_warm`
- S3 cache version: `nvda_cache_smoke_20260105_103005` (same as cold)
- Result: ✅ completed, tearsheet produced
- `queue_submits`: **0**

## Production verification (post-4.4.29 deploy)

This reproduces the customer-reported “ERROR_CODE_CRASH with no traceback” class and confirms the run now completes in production.

Runs launched via `scripts/run_backtest_prod.py` and artifacts downloaded via BotManager `/backtest_results`.

### Short end-window smoke (fast verification)

- Bot ID: `nvda_prod_endwindow-20251215-20251231-xtwsqacv`
- Window: `2025-12-15 -> 2025-12-31`
- Result: ✅ `status=completed` (no crash)
- `queue_submits`: **2** (from `logs.csv`)
- Downloaded artifacts saved under:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/nvda_prod_endwindow/nvda_prod_endwindow-20251215-20251231-xtwsqacv/`

### Full 2025 window (previously crashed)

- Bot ID: `nvda_prod_2025-20250111-20251231-pg4hd3f2`
- Window: `2025-01-11 -> 2025-12-31`
- Result: ✅ `status=completed` (no crash)
- `elapsed`: **0:22:52** (from final `backtest_progress.elapsed`)
- `queue_submits`: **204** (from `logs.csv`)
- Downloaded artifacts saved under:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/nvda_prod_2025/nvda_prod_2025-20250111-20251231-pg4hd3f2/`

Artifacts confirmed present via BotManager API:

- `tearsheet.html`
- `trades.csv`
- `stats.csv`
- `completion.json`
- `logs.csv`
- `settings.json`
- `profile_yappi.csv` (if profiling enabled)

## Follow-up: OOM-like crashes when refreshing multi-year intraday caches

Even with the missing-day UTC-midnight bug fixed, we observed a separate production failure mode:

- BotSpot shows: `ERROR_CODE_CRASH`
- CloudWatch logs show no Python traceback; `logs.csv` stops immediately after a downloader result
- `download_status` shows a single intraday OHLC request (e.g., NVDA minute bars) stuck at `queue_status=processing`

### Why it happens

When S3 cache namespaces are shared, a symbol’s cache file can contain multi-year intraday history
(e.g., NVDA minute bars from a prior 2013→2025 run). On a refresh boundary near the end of a
shorter backtest window (e.g., a 2025-only run), LumiBot can:

1) load that multi-year parquet into memory
2) deep-copy it during cache update/write (`DataFrame.copy()` defaults to `deep=True`)

That can **double peak RSS** and exceed the ECS task memory limit, producing an OOM-like hard exit
with no Python traceback (surfacing as `ERROR_CODE_CRASH`).

### Fix (LumiBot)

- In `lumibot/tools/thetadata_helper.py`, avoid deep-copying large cached frames during:
  - cache load (`df_cached.copy(deep=False)`), and
  - cache writes (`update_cache()` uses shallow copies + avoids duplicative normalization copies).
- In `lumibot/tools/thetadata_helper.py`, when `preserve_full_history=False` and a `start/end` bound
  is provided, load cached parquet slices via PyArrow dataset filtering instead of reading the full
  multi-year intraday frame into memory.
- In `lumibot/backtesting/thetadata_backtesting_pandas.py`, set `preserve_full_history=False` for
  **non-option assets** so a 2025-only backtest doesn’t try to hold a multi-year intraday cache in RAM.

Regression test added:
- `tests/test_thetadata_helper_update_parquet_memory.py`
- `tests/test_thetadata_helper_load_parquet_filter.py`
