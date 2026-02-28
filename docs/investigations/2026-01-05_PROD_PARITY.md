# Production vs Local Parity (Warm S3) — 2026-01-05
> Evidence-backed comparison of warm-cache wall times (prod vs local) and where the remaining gap comes from.

**Last Updated:** 2026-01-06  
**Status:** Active  
**Audience:** Developers + AI Agents  

---

## Scope

- Provider: ThetaData via remote downloader
- Cache backend: S3 (`LUMIBOT_CACHE_BACKEND=s3`)
- “Warm” means:
  - Same `LUMIBOT_CACHE_S3_VERSION` as a prior hydrating run
  - Fresh local cache folder (simulates a fresh ECS task / cold container disk)

Production runs were executed via `scripts/run_backtest_prod.py` and artifacts were downloaded to:

- `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/<label>/<bot_id>/...`

Local runs were executed via `scripts/run_backtest_prodlike.py` and artifacts were written to:

- `/Users/robertgrzesik/Documents/Development/backtest_runs/.../logs/*`

---

## Results (Warm S3, short windows)

### NVDA (customer strategy)

- Strategy code: `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/334e2c98-7134-4f38-860c-b6b11879a51b/main.py`
- Window: `2025-01-20 -> 2025-02-10`
- Cache version: `nvda_prod_cold_20260105_071442`

Production (warm):
- `bot_id=nvda_prod_parity_warm-20250120-20250210-fku3p0ge`
- `wall_s=48.82`

Local (warm):
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_nvda_localwarm_20260105_074828`
- `elapsed_s=28.5`, `queue_submits=0`

Additional (production, larger window):
- Window: `2025-01-11 -> 2025-12-31`
- `bot_id=nvda_prod_2025-20250111-20251231-pg4hd3f2`
- `elapsed=0:22:52`, `queue_submits=204`

### SPX Copy2

- Strategy code: `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/c7c6bbd9-41f7-48c9-8754-3231e354f83b/main.py`
- Window: `2025-01-21 -> 2025-01-24`
- Cache version: `spx_copy2_prod_cold_20260105_072151`

Production (warm):
- `bot_id=spx_copy2_prod_parity_warm-20250121-20250124-5y3rzoih`
- `wall_s=48.72`

Local (warm):
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_spx_copy2_localwarm2_20260105_080952`
- `elapsed_s=37.5`, `queue_submits=0`

### SPX Copy3

- Strategy code: `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/6be31002-44ec-4ae7-857a-db5e01323e7c/main.py`
- Window: `2025-01-21 -> 2025-01-24`
- Cache version: `spx_copy3_prod_cold_20260105_072714`

Production (warm):
- `bot_id=spx_copy3_prod_parity_warm-20250121-20250124-msb86jww`
- `wall_s=51.55`

Local (warm):
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_spx_copy3_localwarm2_20260105_081235`
- `elapsed_s=33.2`, `queue_submits=0`

---

## Where the remaining gap comes from (YAPPI)

These profiles were run on warm S3 versions (no downloader submits) to attribute **compute vs artifacts vs S3 I/O**.

### NVDA (prod vs local)

Production (profile enabled):
- `bot_id=nvda_prod_parity_yappi-20250120-20250210-2yb4y7ii`
- `wall_s=58.02`
- Profile CSV:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/nvda_prod_parity_yappi/nvda_prod_parity_yappi-20250120-20250210-2yb4y7ii/nvda_prod_parity_yappi-20250120-20250210-2yb4y7ii_profile_yappi.csv`
- `scripts/analyze_yappi_csv.py` summary (overall `tsub_s`):
  - `pandas_numpy ≈ 44.5%`
  - `artifacts ≈ 18.4%`
  - `s3_io ≈ 2.0%`

Local (profile enabled):
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_nvda_local_yappi_20260105_082058`
- Profile CSV:
  - `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_nvda_local_yappi_20260105_082058/logs/NVDADrawdownCallStrategy_2026-01-05_08-21_9qnrZ3_profile_yappi.csv`
- `scripts/analyze_yappi_csv.py` summary (overall `tsub_s`):
  - `pandas_numpy ≈ 40.5%`
  - `artifacts ≈ 21.7%`
  - `s3_io ≈ 2.3%`

Interpretation:
- The warm-run gap is **not** dominated by S3/network; `s3_io` is small in both.
- The gap is mostly **CPU/compute throughput** + some artifact overhead; production backtests run on 2 vCPU ECS tasks, while local runs use a much faster workstation CPU.

### SPX Copy2 (prod vs local)

Production (profile enabled):
- `bot_id=spx_copy2_prod_parity_yappi-20250121-20250124-cvet7tx3`
- `wall_s=95.40`
- Profile CSV:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs/prod_runs/spx_copy2_prod_parity_yappi/spx_copy2_prod_parity_yappi-20250121-20250124-cvet7tx3/spx_copy2_prod_parity_yappi-20250121-20250124-cvet7tx3_profile_yappi.csv`
- Summary (overall `tsub_s`):
  - `pandas_numpy ≈ 42.9%`
  - `artifacts ≈ 16.0%`
  - `s3_io ≈ 4.7%`

Local (profile enabled):
- Workdir: `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_spx_copy2_local_yappi_20260105_082811`
- Profile CSV:
  - `/Users/robertgrzesik/Documents/Development/backtest_runs/parity_spx_copy2_local_yappi_20260105_082811/logs/SPXShortStraddle_2026-01-05_08-28_TdMSfr_profile_yappi.csv`
- Summary (overall `tsub_s`):
  - `pandas_numpy ≈ 40.9%`
  - `artifacts ≈ 20.5%`
  - `s3_io ≈ 4.5%`

Interpretation:
- Same conclusion: production warm runs are mostly **compute-bound**, not downloader-bound.

---

## Next steps (if we want closer parity)

1. Reduce artifact overhead on short runs (optional):
   - Consider gating indicator-heavy plotting behind a “fast backtest” mode (must not change prod defaults without buy-in).
2. Reduce compute bottlenecks in ThetaDataBacktestingPandas hot paths:
   - Focus on pandas indexing/merges; yappi points to `pandas_numpy`.
3. If parity is “good enough”:
   - Treat remaining gap as expected given ECS vCPU limits; document it and move on.
