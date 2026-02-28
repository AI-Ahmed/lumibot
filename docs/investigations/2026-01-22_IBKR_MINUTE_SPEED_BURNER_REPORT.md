# 2026-01-22_IBKR_MINUTE_SPEED_BURNER_REPORT.md

> Rolling speed report for the IBKR minute-level “speed burner” benchmarks (futures + crypto). This is the paper trail for “10×–100× faster”.

**Last Updated:** 2026-01-23
**Status:** Active
**Audience:** Developers, AI Agents

---

## Overview

This report tracks backtest speed improvements over time using deterministic “speed burner” workloads:

- **Futures speed burner:** 2 symbols (e.g., `MES`, `MNQ`) on 1-minute cadence.
- **Crypto speed burner:** 3 symbols (e.g., `BTC`, `ETH`, `SOL`) on 1-minute cadence.

Each iteration intentionally stresses the hot path:
- `get_last_price()` per asset
- `get_historical_prices(..., 100, "minute")` per asset
- `get_historical_prices(..., 20, "day")` per asset
- frequent order submissions (alternating BUY/SELL market orders)

**Goal:** warm-cache runs must be queue-free and complete in bounded wall time.

---

## Benchmark runner(s)

- Unit-style stubbed runner (no network): `tests/test_ibkr_speed_burner_stubbed.py`
- Local benchmark script (no network): `scripts/bench_ibkr_speed_burner_stubbed.py`
- Local warm-cache runner (cache-only, asserts queue-free): `scripts/bench_ibkr_speed_burner_warm_cache.py`
- Cache warmer (cold run, populates parquet): `scripts/warm_ibkr_speed_burner_data.py`

Future (acceptance / cache-backed):
- Add a prodlike runner in `scripts/` that hits the cache and asserts queue-free behavior.

### Warm-cache prerequisites

The warm-cache runner is intentionally strict:
- it refuses to download data (queue-free invariant)
- it fails fast if the required parquet cache objects are missing

If it fails due to missing cache, warm the cache once (via apitest/downloader or a manual backtest run),
then re-run the warm-cache benchmark. The simplest warm step for this benchmark is:

- `python3 scripts/warm_ibkr_speed_burner_data.py`

Notes:
- Both scripts default `LUMIBOT_CACHE_FOLDER` to `tests/backtest/_ibkr_speed_burner_cache` (gitignored).
- The futures window in this benchmark uses an expired contract month. The warm step requires a
  populated IBKR conid registry (`<cache>/ibkr/conids.json`), which can be sourced from the
  one-time TWS conid backfill.

---

## Results table (fill in as we iterate)

Record wall time and iterations/sec for each milestone. Keep results append-only.

### Benchmark protocol (canonical; use for all future rows)

Warm-cache (queue-free) runner:

```bash
env LUMIBOT_DISABLE_DOTENV=1 python3 scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 2000
env LUMIBOT_DISABLE_DOTENV=1 python3 scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 20000
```

Protocol:
- Record the **median of 3 runs** (no profiler) for both `--iterations 2000` and `--iterations 20000`.
- Do **not** compare wall time between YAPPI/non-YAPPI runs (profiling overhead is large).

Environment (protocol baseline):
- commit: `34599dfb`
- python: `3.11.8`
- pandas: `2.2.1`
- platform: `macOS-26.1-arm64-arm-64bit`

| Date | Change | Futures time (s) | Crypto time (s) | Notes |
|------|--------|------------------|-----------------|-------|
| 2026-01-22 | Protocol baseline (median of 3; `--iterations 2000`) | 3.687 | 5.540 | warm-cache; queue-free; no profiler |
| 2026-01-22 | Cache `Strategy.get_historical_prices()` signature check | 3.520 | 5.309 | median of 3; eliminates per-call `inspect.signature()` overhead |
| 2026-01-22 | Faster `Data.get_bars()` warm-run fast-path | 2.271 | 3.373 | median of 3; cache OHLCV view + avoid redundant tail/column selection |
| 2026-01-22 | Source-tree stubbed benchmark (200 iters) | 1.072 | 1.491 | `scripts/bench_ibkr_speed_burner_stubbed.py` |
| 2026-01-22 | Native multi-minute cache keys + slice fast-path | 0.936 | 1.383 | Fix `15min` → `15minute` keying; benchmark runs with `IS_BACKTESTING=true` quiet logs; 11 series loads |
| 2026-01-22 | Warm-cache (cache-only) benchmark | 0.579 | 0.849 | `scripts/bench_ibkr_speed_burner_warm_cache.py` (queue-free; 2 futures + 3 crypto) |
| 2026-01-22 | Re-measure stubbed (commit `804d13eb`) | 0.571 | 0.770 | `scripts/bench_ibkr_speed_burner_stubbed.py` (200 iters; `LUMIBOT_DISABLE_DOTENV=1`) |
| 2026-01-22 | Re-measure warm-cache (commit `804d13eb`) | 0.334 | 0.485 | `scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 200` (queue-free; `LUMIBOT_DISABLE_DOTENV=1`) |
| 2026-01-22 | Remove synthetic bars across gaps | TBD | TBD | Correctness + avoids fake work |
| 2026-01-22 | Prefetch once → slice forever | TBD | TBD | Eliminates refetch/window thrash |
| 2026-01-22 | DataFrame slice fast-path | TBD | TBD | Avoid per-call DataFrame rebuild |
| 2026-01-22 | Skip per-slice dropna/fillna + faster Bars derived cols | 3.797 | 5.305 | `Data.get_bars()` avoids redundant `dropna()`/`fillna()` when the dataset is already complete; `Bars` uses NumPy for derived columns |
| 2026-01-23 | Precompute derived `return` once + skip per-slice Bars inserts (commit `2c61dfbb`) | 1.541 | 2.338 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Cache `_data_store` key lookups (commit `12dc32e5`) | 1.471 | 2.289 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Skip submit-time audit work when disabled (commit `eee1c670`) | 1.135 | 1.807 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Lazy order events (commit `1e7ab862`) | 1.063 | 1.540 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Skip OrderClass enum conversion for non-parent (commit `f257fce7`) | 0.956 | 1.361 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Cache backtesting quiet-logs flag (commit `f40c3101`) | 0.932 | 1.338 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Speed up `get_iter_count()` cursor path (commit `41ffb849`) | 0.857 | 1.206 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Avoid `.iloc` overhead in `Data.get_bars()` (commit `51f8b575`) | 0.873 | 1.215 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Faster backtest trade events (commit `37454be6`) | 0.841 | 1.177 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | IBKR warm-cache hot paths (commit `386bc700`) | 0.704 | 0.964 | median of 3; warm-cache; `--iterations 2000`; no profiler |
| 2026-01-23 | Cache repeated `Data.get_bars()` slices (commit `08f34b98`) | 0.504 | 0.768 | median of 3; warm-cache; `--iterations 2000`; no profiler; big win for daily + multi-minute repeated history calls |
| 2026-01-23 | Reduce backtesting order/event overhead (commit `b30f9cc2`) | 0.491 | 0.735 | median of 3; warm-cache; `--iterations 2000`; no profiler; micro-cuts: Order/Enum hot-paths + no-op events + skip backtest-only fee/discord work |
| 2026-01-23 | Reduce backtest hot-loop overhead (commit `fdb172f6`) | 0.482 | 0.731 | median of 3; warm-cache; `--iterations 2000`; no profiler; prefers enum identity checks in backtesting hot paths |
| 2026-01-23 | Cache timestep parsing (commit `cf4e9ea8`) | 0.472 | 0.713 | median of 3; warm-cache; `--iterations 2000`; no profiler; avoids repeated regex parsing for `"minute"`/`"day"` timesteps |
| 2026-01-23 | Cache repeated `get_iter_count()` dt lookups (commit `9cdb91eb`) | 0.474 | 0.711 | median of 3; warm-cache; `--iterations 2000`; no profiler; reduces duplicate dt→index work inside a single iteration |
| 2026-01-23 | Cut Bars column checks for non-dividend data (commit `b1c9e232`) | 0.460 | 0.690 | median of 3; warm-cache; `--iterations 2000`; no profiler; reduces `Index.__contains__` probes in `Bars.__init__` |
| 2026-01-23 | Speed up Order side/type checks (commit `aa341282`) | 0.458 | 0.690 | median of 3; warm-cache; `--iterations 2000`; no profiler; uses identity comparisons in hot `Order` helpers |
| 2026-01-23 | Avoid `DataFrame.empty` in `Data.get_bars()` (commit `3a264c0e`) | 0.450 | 0.671 | median of 3; warm-cache; `--iterations 2000`; no profiler; removes expensive `DataFrame.empty` checks in tight loops |
| 2026-01-23 | Cache `Bars` `df.columns` flags (commit `00c96d43`) | 0.439 | 0.653 | median of 3; warm-cache; `--iterations 2000`; no profiler; removes repeated `Index.__contains__` probes in `Bars.__init__` |
| 2026-01-23 | Use identity checks for remaining `OrderClass` comparisons (commit `e5102b68`) | 0.418 | 0.622 | median of 3; warm-cache; `--iterations 2000`; no profiler; speed change within noise, but reduces enum comparison overhead |
| 2026-01-23 | Skip order price validation when inputs are `None` (commit `8b36ddec`) | 0.409 | 0.596 | median of 3; warm-cache; `--iterations 2000`; no profiler; removes unnecessary validator calls for common market orders |
| 2026-01-23 | Make SafeLists lock-free in backtests (commit `9011aec2`) | 0.396 | 0.579 | median of 3; warm-cache; `--iterations 2000`; no profiler; reduces SafeList lock overhead in hot backtesting paths |
| 2026-01-23 | Avoid StrEnum `asset_type` `__eq__` in hot paths (commit `df1d1524`) | 0.389 | 0.576 | median of 3; warm-cache; `--iterations 2000`; no profiler; flip to `"crypto" == asset.asset_type` style comparisons |
| 2026-01-23 | Fast-path `_is_invalid_price()` for numeric types (commit `73a51aa4`) | 0.383 | 0.573 | median of 3; warm-cache; `--iterations 2000`; no profiler; avoids `pd.isna()`/`float()` in hot fill checks |
| 2026-01-23 | Skip quote fills when bid/ask missing (commit `598ba96a`) | 0.325 | 0.478 | median of 3; warm-cache; `--iterations 2000`; no profiler; IBKR market orders skip expensive quote path when bid/ask are unavailable and fall back to OHLC |

### Long-run sanity (iterations scaling)

Backtests that feel “hours long” typically degrade with iteration count due to hidden O(n) / O(n log n)
work (e.g., event list cleanup, growing order/position lists).

This table uses a longer loop length to catch that early:

| Date | Change | Iterations | Futures time (s) | Crypto time (s) | Notes |
|------|--------|------------|------------------|-----------------|-------|
| 2026-01-22 | Protocol baseline (median of 3; warm-cache) | 20000 | 27.109 | 35.802 | queue-free; no profiler |
| 2026-01-22 | Cache `Strategy.get_historical_prices()` signature check | 20000 | 25.426 | 34.235 | median of 3; eliminates per-call `inspect.signature()` overhead |
| 2026-01-22 | Faster `Data.get_bars()` warm-run fast-path | 20000 | 14.467 | 17.061 | median of 3; cache OHLCV view + avoid redundant tail/column selection |
| 2026-01-22 | Warm-cache (cache-only) benchmark | 2000 | 6.737 | 9.460 | `python3 scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 2000` |
| 2026-01-22 | Faster asof + avoid unused dataline dicts | 2000 | 6.069 | 9.137 | `Data.get_iter_count()` uses index searchsorted; `Data.get_bars()` slices native df before `_get_bars_dict()` |
| 2026-01-22 | Skip per-slice dropna/fillna + faster Bars derived cols | 2000 | 3.797 | 5.305 | Same as above; also includes a benchmark guard to avoid accumulating unfillable orders when the clock exceeds the cached window |
| 2026-01-22 | Same (scaling check) | 20000 | 26.296 | 34.779 | `python3 scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 20000` |
| 2026-01-22 | Re-measure (commit `804d13eb`) | 2000 | 3.895 | 5.861 | `scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 2000` (`LUMIBOT_DISABLE_DOTENV=1`) |
| 2026-01-22 | Re-measure (commit `804d13eb`) | 20000 | 28.940 | 37.817 | `scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 20000` (`LUMIBOT_DISABLE_DOTENV=1`) |
| 2026-01-23 | Precompute derived `return` once + skip per-slice Bars inserts (commit `2c61dfbb`) | 20000 | 7.811 | 7.197 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Cache `_data_store` key lookups (commit `12dc32e5`) | 20000 | 7.071 | 6.483 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Skip submit-time audit work when disabled (commit `eee1c670`) | 20000 | 6.302 | 6.838 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Lazy order events (commit `1e7ab862`) | 20000 | 5.639 | 6.289 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Skip OrderClass enum conversion for non-parent (commit `f257fce7`) | 20000 | 5.318 | 5.875 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Cache backtesting quiet-logs flag (commit `f40c3101`) | 20000 | 5.283 | 5.901 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Speed up `get_iter_count()` cursor path (commit `41ffb849`) | 20000 | 4.810 | 5.329 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Avoid `.iloc` overhead in `Data.get_bars()` (commit `51f8b575`) | 20000 | 4.596 | 4.900 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Faster backtest trade events (commit `37454be6`) | 20000 | 4.512 | 4.945 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | IBKR warm-cache hot paths (commit `386bc700`) | 20000 | 3.889 | 4.330 | median of 3; warm-cache; `--iterations 20000`; no profiler |
| 2026-01-23 | Cache repeated `Data.get_bars()` slices (commit `08f34b98`) | 20000 | 2.469 | 2.163 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.632s (~13.6× vs 62.911s baseline) |
| 2026-01-23 | Reduce backtesting order/event overhead (commit `b30f9cc2`) | 20000 | 2.439 | 2.234 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.675s (~13.5× vs 62.911s baseline) |
| 2026-01-23 | Reduce backtest hot-loop overhead (commit `fdb172f6`) | 20000 | 2.483 | 2.237 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.719s (~13.3× vs 62.911s baseline) |
| 2026-01-23 | Cache timestep parsing (commit `cf4e9ea8`) | 20000 | 2.341 | 2.065 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.410s (~14.3× vs 62.911s baseline) |
| 2026-01-23 | Cache repeated `get_iter_count()` dt lookups (commit `9cdb91eb`) | 20000 | 2.345 | 2.088 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.442s (~14.2× vs 62.911s baseline) |
| 2026-01-23 | Cut Bars column checks for non-dividend data (commit `b1c9e232`) | 20000 | 2.251 | 1.932 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.181s (~15.0× vs 62.911s baseline) |
| 2026-01-23 | Speed up Order side/type checks (commit `aa341282`) | 20000 | 2.253 | 1.947 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=4.200s (~15.0× vs 62.911s baseline) |
| 2026-01-23 | Avoid `DataFrame.empty` in `Data.get_bars()` (commit `3a264c0e`) | 20000 | 2.152 | 1.842 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.991s (~15.8× vs 62.911s baseline) |
| 2026-01-23 | Cache `Bars` `df.columns` flags (commit `00c96d43`) | 20000 | 2.077 | 1.718 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.795s (~16.6× vs 62.911s baseline) |
| 2026-01-23 | Use identity checks for remaining `OrderClass` comparisons (commit `e5102b68`) | 20000 | 2.052 | 1.748 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.800s (~16.6× vs 62.911s baseline) |
| 2026-01-23 | Skip order price validation when inputs are `None` (commit `8b36ddec`) | 20000 | 2.021 | 1.766 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.787s (~16.6× vs 62.911s baseline) |
| 2026-01-23 | Make SafeLists lock-free in backtests (commit `9011aec2`) | 20000 | 1.974 | 1.731 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.705s (~17.0× vs 62.911s baseline) |
| 2026-01-23 | Avoid StrEnum `asset_type` `__eq__` in hot paths (commit `df1d1524`) | 20000 | 1.954 | 1.730 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.684s (~17.1× vs 62.911s baseline) |
| 2026-01-23 | Fast-path `_is_invalid_price()` for numeric types (commit `73a51aa4`) | 20000 | 1.919 | 1.709 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.628s (~17.3× vs 62.911s baseline) |
| 2026-01-23 | Use `_identifier` directly in `Order.__hash__` / `Order.__eq__` (commit `1a62585d`) | 20000 | 1.940 | 1.715 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.655s (~17.2× vs 62.911s baseline); 2000-iter median: futures=0.385s crypto=0.576s |
| 2026-01-23 | Speed up `SafeList.remove(key="identifier")` (commit `14fb483e`) | 20000 | 1.923 | 1.705 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.628s (~17.3× vs 62.911s baseline); 2000-iter median: futures=0.382s crypto=0.568s |
| 2026-01-23 | Add identity fast-path to `StrEnum.__eq__` (commit `4ad3d242`) | 20000 | 1.912 | 1.700 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.612s (~17.4× vs 62.911s baseline); 2000-iter median: futures=0.380s crypto=0.561s |
| 2026-01-23 | Speed up `Strategy.get_historical_prices` hot path (commit `03c6cc74`) | 20000 | 1.855 | 1.592 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.472s (~18.1× vs 62.911s baseline); 2000-iter median: futures=0.380s crypto=0.579s |
| 2026-01-23 | Skip cancel-open-orders scan on normal liquidation (commit `9f1d0e14`) | 20000 | 1.838 | 1.583 | median of 3 (noisy); warm-cache; `--iterations 20000`; no profiler; total=3.421s (~18.4× vs 62.911s baseline) |
| 2026-01-23 | Use monotonic order identifiers in backtests (commit `56e60140`) | 20000 | 1.749 | 1.582 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.360s (~18.7× vs 62.911s baseline); 2000-iter median: futures=0.346s crypto=0.539s |
| 2026-01-23 | Skip quote fills when bid/ask missing (commit `598ba96a`) | 20000 | 1.655 | 1.477 | median of 3; warm-cache; `--iterations 20000`; no profiler; total=3.132s (~20.1× vs 62.911s baseline); 2000-iter median: futures=0.325s crypto=0.478s |

---

## Notes / invariants

- Do not create synthetic bars across gaps. See `docs/BACKTESTING_SESSION_GAPS_AND_DATA_GAPS.md`.
- If the strategy clock lands in a futures session gap, orders may be accepted but must not fill until data resumes.
- Prefer “warm-cache speed” as the primary metric; cold downloads are allowed once but must not repeat.

## Speed gate (release check)

Use the benchmark script’s built-in assertions as a conservative speed gate:

```bash
python3 scripts/bench_ibkr_speed_burner_warm_cache.py \
  --iterations 2000 \
  --assert-futures-max-s 10 \
  --assert-crypto-max-s 15
```
