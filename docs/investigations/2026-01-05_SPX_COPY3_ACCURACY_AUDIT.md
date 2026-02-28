# 2026-01-05 — SPX Copy3 Accuracy Audit (manager_bot_id=6be31002-44ec-4ae7-857a-db5e01323e7c)

**Last Updated:** 2026-01-06

## Scope

- Strategy code (read-only repro): `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/6be31002-44ec-4ae7-857a-db5e01323e7c/main.py`
- Engine: LumiBot (local + production-faithful flags)
- Data provider: ThetaData via remote downloader
- Cache backend: S3 (`LUMIBOT_CACHE_BACKEND=s3`)

## Goals

- Performance:
  - Cold S3 namespace run finishes (no hours/days).
  - Warm S3 proof run shows near-zero downloader submits.
- Accuracy:
  - Full “MELI-style” audit table for every trade with maximum telemetry.

## Root Cause (shared with Copy2)

Same underlying issue as Copy2: intraday cache end-validation previously allowed stale reuse of prior-day minute data across multiple days, which broke determinism for SPXW underlying prices and thus strike selection.

See: `docs/investigations/2026-01-05_SPX_COPY2_ACCURACY_AUDIT.md`

## Run Log (fill in as executed)

### Cold namespace inspection + proof (short window)

- LumiBot git ref: `4.4.28` (includes intraday end-validation fix; see history for 2026-01-05 commits)
- Date window: `2025-01-21 -> 2025-01-24`
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/spx_copy3_coldfix_20260105_054417`
- Local cache folder: `/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_copy3_coldfix_20260105_054417`
- S3 cache version: `spx_copy3_coldfix_20260105_054417`
- Wall time: `288.7s`
- Queue submits (`Submitted to queue`): `205`
- Notes:
  - Cold run hydrates S3; warm run should submit ~0 work and match trades.

### Warm S3 proof (same window)

- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/spx_copy3_warmfix_20260105_054917`
- Local cache folder: `/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_copy3_warmfix_20260105_054917`
- S3 cache version (same as cold): `spx_copy3_coldfix_20260105_054417`
- Wall time: `36.8s`
- Queue submits: `0`
- Notes:
  - **Trades are identical** to the cold run (40 rows in `*_trades.csv`).
  - Warm-cache invariant holds: **0** queue submits.

## Production cold→warm proof (short window)

### Cold (prod)

- Window: `2025-01-07 -> 2025-01-17`
- Bot ID: `spx_copy3_prod_cold-20250107-20250117-m4fiqq28`
- S3 cache version: `spx_copy3_cold_20260105_235109`
- Wall time: `295.2s`
- Queue submits (`Submitted to queue`): `337`
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy3_prod_cold/spx_copy3_prod_cold-20250107-20250117-m4fiqq28/`

### Warm (prod; same S3 version)

- Window: `2025-01-07 -> 2025-01-17`
- Bot ID: `spx_copy3_prod_warm-20250107-20250117-zefyg4xe`
- S3 cache version: `spx_copy3_cold_20260105_235109` (same as cold)
- Wall time: `73.3s`
- Queue submits: `0`
- Trades determinism: ✅ `trades.csv` is byte-identical vs cold
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy3_prod_warm/spx_copy3_prod_warm-20250107-20250117-zefyg4xe/`

## Production cold→warm proof (extended window)

### Cold (prod; extended)

- Window: `2025-01-07 -> 2025-04-07`
- Bot ID: `spx_copy3_prod_long_cold-20250107-20250407-kf986ptx`
- S3 cache version: `spx_copy3_long_20260106`
- Backtest time: `2004.8s` (~33.4m)
- Queue submits (`Submitted to queue`): `2038`
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy3_prod_long_cold/spx_copy3_prod_long_cold-20250107-20250407-kf986ptx/`

### Warm (prod; same S3 version; extended)

- Window: `2025-01-07 -> 2025-04-07`
- Bot ID: `spx_copy3_prod_long_warm-20250107-20250407-pus61m2z`
- S3 cache version: `spx_copy3_long_20260106` (same as cold)
- Backtest time: `388.2s` (~6.5m)
- Queue submits: `0`
- Artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy3_prod_long_warm/spx_copy3_prod_long_warm-20250107-20250407-pus61m2z/`

## Accuracy Audit Deliverables

- Audit artifact(s) (CSV recommended):
  - `docs/investigations/data/2026-01-05_spx_copy3_trade_events_audit.csv` (full `audit.*` telemetry)
- Summary in this markdown:
  - Total trade-events: 40 rows (order submit + fills, including multileg entries)
  - Cold vs warm determinism: ✅ identical trades + fills for the window
  - Data quality flags observed:
    - No forward-fill warnings observed in this window
    - Option quote submissions were bounded (cold) and zero (warm)
