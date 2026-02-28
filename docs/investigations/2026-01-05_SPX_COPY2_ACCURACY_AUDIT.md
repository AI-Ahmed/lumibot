# 2026-01-05 — SPX Copy2 Accuracy Audit (manager_bot_id=c7c6bbd9-41f7-48c9-8754-3231e354f83b)

**Last Updated:** 2026-01-06

## Scope

- Strategy code (read-only repro): `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/c7c6bbd9-41f7-48c9-8754-3231e354f83b/main.py`
- Engine: LumiBot (local + production-faithful flags)
- Data provider: ThetaData via remote downloader
- Cache backend: S3 (`LUMIBOT_CACHE_BACKEND=s3`)

## Goals

- Performance:
  - Cold S3 namespace run finishes (no hours/days).
  - Warm S3 proof run shows near-zero downloader submits.
- Accuracy:
  - Full “MELI-style” audit table for every trade with maximum telemetry.

## Root Cause (why cold vs warm differed)

**Bug:** `ThetaDataBacktestingPandas._update_pandas_data()` end-coverage validation treated **minute** datasets as “good enough” if the cached **date** was within a **3-day tolerance** of the required end date.

**Impact:** In a true cold-cache run, SPXW index minute OHLC coverage stopped at **2025-01-21 close**, but `get_last_price(SPXW)` reused that **prior-day close** for **2025-01-22 → 2025-01-24**, causing:
- Incorrect underlying prices during entry.
- Different strikes selected (and different fills) vs warm runs.
- Warm-cache proof couldn’t reach “near-zero submits” because cold and warm were not requesting the same option contracts.

**Fix (LumiBot):**
- For intraday (`minute/hour/second`) caches, validate **timestamp coverage** (not date-only) and remove the multi-day tolerance.
- For point-in-time intraday probes (`length<=5`) on **stock/index** last-price paths, align `end_requirement` to the **session close** so we fetch one stable per-day window (prevents refetch churn and prevents stale prior-day closes).
- Regression test: `tests/test_thetadata_intraday_end_validation.py`

## Run Log (fill in as executed)

### Cold namespace inspection + proof (short window)

- LumiBot git ref: `4.4.28` (includes intraday end-validation fix; see history for 2026-01-05 commits)
- Date window: `2025-01-21 -> 2025-01-24`
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/spx_copy2_coldfix_20260105_053636`
- Local cache folder: `/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_copy2_coldfix_20260105_053636`
- S3 cache version: `spx_coldfix_20260105_053636`
- Wall time: `389.3s`
- Queue submits (`Submitted to queue`): `209`
- Notes:
  - Cold run is expected to enqueue downloader work to hydrate S3.
  - The important invariant is that *the warm run requests the same contracts and submits ~0 work*.

### Warm S3 proof (same window)

- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/spx_copy2_warmfix_20260105_054317`
- Local cache folder: `/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_copy2_warmfix_20260105_054317`
- S3 cache version (same as cold): `spx_coldfix_20260105_053636`
- Wall time: `34.8s`
- Queue submits: `0`
- Notes:
  - **Trades are identical** to the cold run (40 rows in `*_trades.csv`).
  - Warm-cache invariant holds: **0** queue submits.

## Production cold→warm proof (short window)

This validates the same invariants in production ECS:

- Cold run is allowed to enqueue downloader work to hydrate S3.
- Warm run (same S3 version, fresh container disk) must be **near-zero queue submits** and **identical trades**.

### Cold (prod)

- Window: `2025-01-07 -> 2025-01-17`
- Bot ID: `spx_copy2_prod_cold-20250107-20250117-d92xwdam`
- S3 cache version: `spx_cold_20260105_234405`
- Queue submits (`Submitted to queue`): `340`
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy2_prod_cold/spx_copy2_prod_cold-20250107-20250117-d92xwdam/`

### Warm (prod; same S3 version)

- Window: `2025-01-07 -> 2025-01-17`
- Bot ID: `spx_copy2_prod_warm-20250107-20250117-j5ongzo7`
- S3 cache version: `spx_cold_20260105_234405` (same as cold)
- Wall time: `66.7s`
- Queue submits: `0`
- Trades determinism: ✅ `trades.csv` is byte-identical vs cold
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy2_prod_warm/spx_copy2_prod_warm-20250107-20250117-j5ongzo7/`

## Production cold→warm proof (extended window)

This is the same invariant as the short-window proof, but over a larger range to ensure:
- no request-explosion regressions,
- cold hydration remains bounded, and
- warm reruns stay queue-free.

### Cold (prod; extended)

- Window: `2025-01-07 -> 2025-04-07`
- Bot ID: `spx_copy2_prod_long_cold-20250107-20250407-s3nfsdts`
- S3 cache version: `spx_copy2_long_20260106`
- Backtest time: `2073.5s` (~34.6m)
- Queue submits (`Submitted to queue`): `2128`
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy2_prod_long_cold/spx_copy2_prod_long_cold-20250107-20250407-s3nfsdts/`

### Warm (prod; same S3 version; extended)

- Window: `2025-01-07 -> 2025-04-07`
- Bot ID: `spx_copy2_prod_long_warm-20250107-20250407-ladlh0om`
- S3 cache version: `spx_copy2_long_20260106` (same as cold)
- Backtest time: `381.5s` (~6.4m)
- Queue submits: `0`
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy2_prod_long_warm/spx_copy2_prod_long_warm-20250107-20250407-ladlh0om/`

## Accuracy Audit Deliverables

- Audit artifact(s) (CSV recommended):
  - `docs/investigations/data/2026-01-05_spx_copy2_trade_events_audit.csv` (full `audit.*` telemetry)
- Summary in this markdown:
  - Total trade-events: 40 rows (order submit + fills, including multileg entries)
  - Cold vs warm determinism: ✅ identical trades + fills for the window
  - Data quality flags observed:
    - No forward-fill warnings observed in this window
    - Option quote submissions were bounded (cold) and zero (warm)
