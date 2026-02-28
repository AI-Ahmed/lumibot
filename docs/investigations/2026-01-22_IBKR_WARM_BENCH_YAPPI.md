# 2026-01-22_IBKR_WARM_BENCH_YAPPI.md

YAPPI profiling notes for the IBKR warm-run minute-level speed burner (futures + crypto).

**Last Updated:** 2026-01-23  
**Status:** Active  
**Audience:** Developers, AI Agents  

---

## Overview

This investigation captures the **YAPPI** profile for the IBKR warm-run “speed burner” benchmark so future speed work is driven by measurements, not guesses.

**Important**
- YAPPI adds substantial overhead. Use it to rank bottlenecks, but **do not compare** profiled wall times to non-profile benchmark runs.
- The benchmark itself is documented in `docs/investigations/2026-01-22_IBKR_MINUTE_SPEED_BURNER_REPORT.md`.
- This investigation discusses caching detection of an optional `return_polars` kwarg in `Strategy.get_historical_prices`.
  This does **not** mean LumiBot is switching to Polars; it’s strictly about avoiding per-call introspection overhead.

## Environment

- commit: `41ffb849`
- python: `3.11.8`
- pandas: `2.2.1`
- platform: `macOS-26.1-arm64-arm-64bit`

## Commands

Warm-run benchmark (median-of-3 is recorded separately; no profiler):

```bash
env LUMIBOT_DISABLE_DOTENV=1 python3 scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 2000
env LUMIBOT_DISABLE_DOTENV=1 python3 scripts/bench_ibkr_speed_burner_warm_cache.py --iterations 20000
```

YAPPI capture (do not compare wall times to the above):

```bash
env LUMIBOT_DISABLE_DOTENV=1 python3 scripts/bench_ibkr_speed_burner_warm_cache.py \
  --iterations 2000 \
  --profile-yappi-csv tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_34599dfb_2000_profile_yappi.csv
```

YAPPI analysis helper:

```bash
python3 scripts/analyze_yappi_csv.py \
  tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_34599dfb_2000_profile_yappi.csv
```

## Bucket breakdown (self time / `tsub_s`)

From `scripts/analyze_yappi_csv.py`:

- `pandas_numpy`: ~57%
- `lumibot_other`: ~29%
- `other`: ~12%
- `stdlib_wait`: ~2%
- `progress_logging`: ~1%

Interpretation:
- Warm-run iteration throughput is dominated by **pandas slicing/copies** and per-call object construction overhead in LumiBot’s bars/history pipeline.

## Top hotspots (by self time / `tsub_s`)

Top items from the 2000-iteration profile (self time focuses on “where CPU is really spent”):

1. `lumibot/entities/data.py:519 Data.checker` (data validation wrapper)
2. `pandas/core/generic.py DataFrame._slice`
3. `pandas/core/construction.py sanitize_array`
4. `pandas/core/arrays/datetimelike.py DatetimeArray._validate_scalar`
5. `pandas/core/indexes/base.py Index.__new__`
6. `pandas/core/array_algos/take.py _take_nd_ndarray`
7. `pandas/core/internals/managers.py BlockManager._slice_take_blocks_ax0`
8. `lumibot/entities/data.py:865 Data.get_bars` (inclusive hotspot; slicing + column selection)
9. `inspect.py:_signature_from_function` + `inspect.Signature` + `inspect.Parameter` (introspection overhead inside `Strategy.get_historical_prices`)
10. `lumibot/entities/bars.py:167 Bars.__init__` (derived-column insertion + pandas mutation)

## Actionable next steps (driven by this profile)

1) Remove per-call `inspect.signature()` work in `Strategy.get_historical_prices` by caching per data source function/class.  
2) Make `Bars.__init__` cheaper for pandas-backed backtests by avoiding repeated derived-column insertion work.  
3) Reduce pandas work in `Data.get_bars` by minimizing per-call DataFrame column selection and redundant tail/slice operations.

These changes should be measured after each commit using the canonical benchmark protocol in the speed report.

## Milestones (profile deltas)

### 2026-01-22 — Cache `Strategy.get_historical_prices()` signature check

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_inspectcache_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `pandas_numpy`: ~59%
- `lumibot_other`: ~31%
- `other`: ~8%

Key delta vs baseline:
- `inspect.*` functions (signature/parameter construction) no longer show up as a dominant hotspot, confirming the per-call introspection overhead was removed.

### 2026-01-22 — Faster `Data.get_bars()` warm-run fast-path

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_datagbars_fastpath_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `pandas_numpy`: ~46%
- `lumibot_other`: ~42%
- `other`: ~10%

Key delta:
- pandas share drops substantially, and the dominant hotspots shift toward LumiBot-side overhead:
  - `Bars.__init__`
  - `BacktestingBroker._process_trade_event`
  - `PandasData.find_asset_in_data_store`

### 2026-01-23 — Precompute derived `return` once + skip per-slice Bars inserts

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_returns_precompute_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~58%
- `pandas_numpy`: ~28%
- `other`: ~9%
- `stdlib_wait`: ~4%
- `progress_logging`: ~1%

Key delta:
- `Bars.__init__` and pandas `__setitem__/BlockManager.insert` no longer show up as dominant hotspots because
  `return` is precomputed once per dataset (in `Data.repair_times_and_fill`) and `Bars` skips derived-column work
  when those columns are already present.
- Remaining inclusive hotspots are now mostly LumiBot-side broker/order pipeline and data-source lookup:
  - `BacktestingBroker._submit_order` / `process_pending_orders`
  - `InteractiveBrokersRESTBacktesting.get_quote`
  - `PandasData.find_asset_in_data_store`

### 2026-01-23 — Cache `_data_store` key lookups (`PandasData.find_asset_in_data_store`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_findasset_cache_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~56%
- `pandas_numpy`: ~30%

Key delta:
- `PandasData.find_asset_in_data_store` drops from a top inclusive hotspot to ~0.06s total for ~50k calls in the
  benchmark (cache hits), confirming the cache removed repeated candidate-list construction and dict probes.

### 2026-01-23 — Skip submit-time audit work when disabled

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_audit_lazy_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~58%
- `pandas_numpy`: ~29%

Key delta:
- `_audit_submit_fields()` no longer runs when audits are disabled (default), removing a large amount of quote work
  from the order submission hot path.

### 2026-01-23 — Lazy order events (allocation reduction)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_1e7ab862_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~59%
- `pandas_numpy`: ~32%

Key delta:
- `Order.__init__` no longer allocates multiple `threading.Event()` objects per order (lazy allocation). This removes
  large numbers of internal `threading.Condition` allocations and reduces per-order overhead in high-churn backtests.

### 2026-01-23 — Skip OrderClass enum conversion for non-parent (small hot-path cut)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_f257fce7_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~57%
- `pandas_numpy`: ~31%
- `other`: ~7%
- `stdlib_wait`: ~4%
- `progress_logging`: ~1%

Key delta:
- Profile confirms we are now dominated by **LumiBot order + event pipeline** and **pandas datetime scalar validation**
  rather than the earlier bars-construction hotspots.
- Next likely wins are:
  - reduce order-status equivalence checks (`Order.is_equivalent_status`) and enum comparisons (`OrderClass.__eq__`)
  - eliminate repeated `Index.__contains__` probes in tight loops
  - reduce per-iteration logger overhead (`StrategyLoggerAdapter.isEnabledFor`) and repeated `os.environ` lookups

### 2026-01-23 — Cache quiet-logs mode (remove per-call env lookups)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_quietlogs_cache_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~59%
- `pandas_numpy`: ~32%
- `stdlib_wait`: ~4%
- `other`: ~3%
- `progress_logging`: ~1%

Key delta:
- `StrategyLoggerAdapter.isEnabledFor` and `os.environ` lookups no longer appear as dominant hotspots; quiet-logs mode
  is now cached during logger setup.

### 2026-01-23 — Speed up `get_iter_count()` (cursor + NumPy search)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_itercount_cursor_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~68%
- `pandas_numpy`: ~22%
- `stdlib_wait`: ~5%
- `other`: ~4%
- `progress_logging`: ~1%

Key delta:
- `DatetimeArray._validate_scalar` / `_unbox_scalar` and `DatetimeIndex.searchsorted` are no longer dominant.
  `check_data()` now uses the optimized `get_iter_count()` (dict hit → monotonic cursor → NumPy searchsorted),
  which avoids pandas datetime scalar validation on the common “dt not an exact bar timestamp” path.

### 2026-01-23 — Avoid `.iloc` indexer overhead in `Data.get_bars()` (commit `51f8b575`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_df_slice_fastpath_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~70%
- `pandas_numpy`: ~20%

Key delta:
- `_iLocIndexer.*` no longer appears as a dominant inclusive hotspot; `Data.get_bars()` now slices via
  `DataFrame._slice()` which avoids the `.iloc` indexer stack for integer row bounds.
- Warm-cache benchmark median improves (see `docs/investigations/2026-01-22_IBKR_MINUTE_SPEED_BURNER_REPORT.md`).

### 2026-01-23 — Faster backtest trade events (commit `37454be6`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_tradeevent_tuple_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~69%
- `pandas_numpy`: ~23%

Key delta:
- Backtesting no longer runs `Order.is_equivalent_status()` chains for canonical broker events.
- Trade-event rows are stored as compact tuples when audits are disabled (default), reducing per-event allocation.

### 2026-01-23 — IBKR warm-cache hot paths (commit `386bc700`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_386bc700_2000_profile_yappi.csv`

### 2026-01-23 — Cache repeated `Data.get_bars()` slices (commit `08f34b98`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_slice_cache_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~82%
- `pandas_numpy`: ~7%
- `other`: ~7%
- `stdlib_wait`: ~2%
- `progress_logging`: ~2%

Key delta:
- pandas slicing/index work drops sharply because identical native slices are reused (most impactful for:
  - `timestep="day"` requests in intraday strategies (daily window changes rarely)
  - native multi-minute bars requested every minute (e.g., `15minute` history in 1-minute strategies)
- Remaining bottleneck shifts toward the LumiBot order/event pipeline:
  - `Order.__init__`
  - `BacktestingBroker._process_trade_event`
  - `BacktestingBroker.process_pending_orders` / `_execute_filled_order`

Safety note:
- This optimization reuses the same DataFrame object for identical slices. Strategies should treat
  `bars.df` as **read-only** (or call `.copy()` before mutating) to avoid unexpected cross-call effects.

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~69%
- `pandas_numpy`: ~24%
- `other`: ~4%
- `stdlib_wait`: ~2%
- `progress_logging`: ~2%

Key delta:
- MARKET fills avoid `Broker.get_quote()` (no `Quote` objects) via `BacktestingBroker._fast_get_bid_ask_for_fill()`.
- IBKR `_pull_source_symbol_bars()` skips start/end datetime work when the series is already fully loaded for the backtest window.
- IBKR `_pull_source_symbol_bars()` slices directly from `self._data_store` (avoids `find_asset_in_data_store()` candidate generation).

### 2026-01-23 — Reduce backtesting order/event overhead (commit `b30f9cc2`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_b30f9cc2_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~84%
- `pandas_numpy`: ~8%
- `other`: ~4%
- `stdlib_wait`: ~3%
- `progress_logging`: ~2%

Top hotspots (self time / `tsub_s`):
1. `lumibot/entities/order.py Order.__init__`
2. `lumibot/entities/order.py OrderClass.__eq__`
3. `lumibot/brokers/broker.py BacktestingBroker._process_trade_event`
4. `lumibot/entities/data.py Data.get_iter_count`
5. `lumibot/entities/data.py Data.get_bars`
6. `lumibot/backtesting/backtesting_broker.py BacktestingBroker.process_pending_orders`
7. `lumibot/entities/bars.py Bars.__init__`
8. `pandas Index.__contains__`
9. `lumibot/entities/asset.py AssetType.__eq__`
10. `lumibot/tools/helpers.py parse_timestep_qty_and_unit`

Key delta:
- Micro-cuts reduce constant overhead in the order/event pipeline (fees, no-op subscriber events, discord formatting, enum comparisons).
- The dominant bottleneck remains the **order + trade-event pipeline**, not the bars/history layer.

### 2026-01-23 — Reduce backtest hot-loop overhead (commit `fdb172f6`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_fdb172f6_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~84%
- `pandas_numpy`: ~8%
- `other`: ~4%
- `stdlib_wait`: ~3%
- `progress_logging`: ~2%

Key delta:
- `OrderClass.__eq__` call count drops to ~290k in the 2000-iteration profile (from ~310k), reflecting fewer enum equality checks in `BacktestingBroker` hot paths.
- Next likely wins are still in the high-call micro-costs:
  - `Bars.__init__` column checks (`Index.__contains__`)
  - timestep parsing (`parse_timestep_qty_and_unit`)
  - `Data.get_iter_count` / `Data.get_bars` call frequency and per-call overhead

### 2026-01-23 — Cache timestep parsing (commit `cf4e9ea8`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_cf4e9ea8_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~86%
- `pandas_numpy`: ~8%

Key delta:
- `parse_timestep_qty_and_unit` drops from ~0.05s self time to ~0.01s self time for ~40k calls in the 2000-iteration profile.
- This translates to a measurable warm-cache speed improvement in the long-run benchmark (see the speed report).

### 2026-01-23 — Cache repeated `get_iter_count()` dt lookups (commit `9cdb91eb`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_9cdb91eb_2000_profile_yappi.csv`

Key delta:
- `Data.get_iter_count` self time drops to ~0.086s for ~50k calls in the 2000-iteration profile (down from ~0.10s).

### 2026-01-23 — Cut Bars column checks for non-dividend data (commit `b1c9e232`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_b1c9e232_2000_profile_yappi.csv`

Key delta:
- `Index.__contains__` call count drops to ~40k in the 2000-iteration profile (from ~80k), since most futures/crypto datasets do not have dividends and we avoid checking dividend-derived columns.

### 2026-01-23 — Speed up Order side/type checks (commit `aa341282`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_aa341282_2000_profile_yappi.csv`

Key delta:
- `OrderClass.__eq__` call count drops to ~140k in the 2000-iteration profile (from ~290k), since hot `Order` helper methods now use identity comparisons (`is`) instead of `==`/list-membership checks.

### 2026-01-23 — Avoid `DataFrame.empty` in `Data.get_bars()` (commit `3a264c0e`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_3a264c0e_2000_profile_yappi.csv`

Key delta:
- `DataFrame.empty` disappears from the profile (0 calls), replaced with cheap `df.shape[0]` checks for empty slices/cache hits.

### 2026-01-23 — Cache `Bars` `df.columns` flags (commit `00c96d43`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_00c96d43_2000_profile_yappi.csv`

Key delta:
- `Index.__contains__` disappears from the profile (0 calls), since `Bars.__init__` now caches column presence flags per `df.columns` object.

### 2026-01-23 — Use identity checks for remaining `OrderClass` comparisons (commit `e5102b68`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_e5102b68_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~5%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Key delta:
- No major shift in bucket breakdown (this is a micro-cut).
- Top self-time hotspots remain dominated by the order/event pipeline and per-call history/bar access:
  - `Order.__init__`
  - `BacktestingBroker._process_trade_event`
  - `Data.get_bars` / `Data.get_iter_count`
  - `BacktestingBroker.process_pending_orders` / `_execute_filled_order`
  - `Bars.__init__`

### 2026-01-23 — Skip order price validation when inputs are `None` (commit `8b36ddec`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_8b36ddec_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~5%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Key delta:
- `check_price` no longer appears in the profile (0 calls) because `Order._set_prices()` now skips
  validator calls when price inputs are `None` (common for market orders).
- Remaining dominant hotspots are unchanged: order/event pipeline + `Data.get_bars` / `Data.get_iter_count`.

### 2026-01-23 — Make SafeLists lock-free in backtests (commit `9011aec2`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_9011aec2_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~5%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Key delta:
- SafeList lock overhead drops substantially in the profile (same call counts, lower self time):
  - `SafeList.remove`: ~0.04s → ~0.028s (45k calls)
  - `SafeList.__iter__`: ~0.029s → ~0.015s (50k calls)
  - `SafeList.__contains__`: ~0.014s → ~0.004s (10k calls)
- Remaining dominant hotspots are still in:
  - `Order.__init__`
  - `BacktestingBroker._process_trade_event`
  - `Data.get_bars` / `Data.get_iter_count`

### 2026-01-23 — Avoid StrEnum `asset_type` `__eq__` in hot paths (commit `df1d1524`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_df1d1524_2000_profile_yappi.csv`

Key delta:
- `asset.asset_type == "crypto"` style checks in hot broker/order/data code paths are flipped to
  `"crypto" == asset.asset_type` so comparisons use C-level string equality instead of `StrEnum.__eq__`.
- `AssetType.__eq__` remains a high-call hotspot overall (~176k calls per 2000-iter run), but self time
  drops slightly in this profile (see speed report + follow-on StrEnum work).

### 2026-01-23 — Fast-path `_is_invalid_price()` for numeric types (commit `73a51aa4`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_73a51aa4_2000_profile_yappi.csv`

Key delta:
- `BacktestingBroker._is_invalid_price` self time drops roughly in half for the same call volume (~30k calls in the
  2000-iteration profile) by handling common numeric types (float/int/NumPy scalars) without `pd.isna()` and without
  repeated `float()` conversions.

### 2026-01-23 — Use `_identifier` directly in `Order.__hash__` / `Order.__eq__` (commit `1a62585d`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_1a62585d_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~4%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Top hotspots (self time / `tsub_s`, 2000-iter profile):
1) `lumibot/entities/order.py:181 Order.__init__` (~0.11s, 10k calls)  
2) `lumibot/brokers/broker.py:2077 BacktestingBroker._process_trade_event` (~0.11s, 20k calls)  
3) `lumibot/trading_builtins/safe_list.py:80 SafeList.remove` (~0.11s, 45k calls)  
4) `lumibot/entities/data.py:1019 Data.get_bars` (~0.11s, 20k calls)  
5) `lumibot/entities/data.py:600 Data.get_iter_count` (~0.09s, 50k calls)  

Key delta:
- `Order.__eq__` does not appear as a dominant hotspot in this profile, but the canonical warm-cache benchmark does
  not improve vs `73a51aa4` (slight regression within noise). Remaining work is still dominated by order/event
  pipeline costs and SafeList list operations.

### 2026-01-23 — Speed up `SafeList.remove(key="identifier")` (commit `14fb483e`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_14fb483e_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~4%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Top hotspots (self time / `tsub_s`, 2000-iter profile):
1) `lumibot/entities/order.py:181 Order.__init__` (~0.11s, 10k calls)  
2) `lumibot/trading_builtins/safe_list.py:83 SafeList.remove` (~0.11s, 45k calls)  
3) `lumibot/brokers/broker.py:2077 BacktestingBroker._process_trade_event` (~0.10s, 20k calls)  
4) `lumibot/entities/data.py:1019 Data.get_bars` (~0.10s, 20k calls)  
5) `lumibot/entities/data.py:600 Data.get_iter_count` (~0.09s, 50k calls)  

Key delta:
- The warm-cache benchmark does not materially change vs `73a51aa4`; this is a small micro-optimization that targets
  backtesting order tracking list removals and reduces identifier property overhead in `SafeList.remove`.

### 2026-01-23 — Add identity fast-path to `StrEnum.__eq__` (commit `4ad3d242`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_4ad3d242_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~4%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Key delta:
- `AssetType.__eq__` self time drops substantially (same call volume ~176k per 2000-iter run), consistent with
  `StrEnum.__eq__` now returning early for common identity comparisons (`self is other`).
- The canonical warm-cache benchmark improves slightly, but the remaining bottlenecks are still dominated by:
  - `SafeList.remove`
  - `Order.__init__`
  - broker order/event pipeline (`BacktestingBroker._process_trade_event`, `process_pending_orders`)

### 2026-01-23 — Speed up `Strategy.get_historical_prices` hot path (commit `03c6cc74`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_03c6cc74_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~4%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Key delta:
- Removes per-call nested function creation and per-call dict allocations inside `Strategy.get_historical_prices()`,
  keeping the `return_polars`-support detection cached but making the hot path cheaper.
- Remaining dominant hotspots are still in the broker/order pipeline and `Data.get_bars` / `Data.get_iter_count`.

### 2026-01-23 — Skip cancel-open-orders scan on normal liquidation (commit `9f1d0e14`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_9f1d0e14_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~89%
- `pandas_numpy`: ~5%
- `stdlib_wait`: ~3%
- `other`: ~2%
- `progress_logging`: ~1%

Key delta:
- Removes `BacktestingBroker.get_active_tracked_orders()` from the speed burner hot loop by no longer scanning/canceling
  unrelated open orders when a position reaches zero in normal fills.

### 2026-01-23 — Use monotonic order identifiers in backtests (commit `56e60140`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_56e60140_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~90%
- `pandas_numpy`: ~5%
- `stdlib_wait`: ~3%
- `other`: ~1%
- `progress_logging`: ~1%

Key delta:
- Removes `uuid.UUID.__init__` from the profile entirely by using a cheap monotonic counter for backtest order identifiers.
- `Order.__init__` self time drops, but the benchmark is still dominated by:
  - `BacktestingBroker._process_trade_event`
  - `SafeList.remove`
  - `Data.get_bars` / `Data.get_iter_count`

### 2026-01-23 — Skip quote fills when bid/ask missing (commit `598ba96a`)

Capture:
- `tests/backtest/_ibkr_speed_burner_cache/_profiles/ibkr_warmcache_598ba96a_2000_profile_yappi.csv`

Bucket summary (self time / `tsub_s`):
- `lumibot_other`: ~90.00%
- `pandas_numpy`: ~4.62%
- `stdlib_wait`: ~3.45%
- `other`: ~1.19%
- `progress_logging`: ~0.74%

Top hotspots (self time / `tsub_s`, 2000-iter profile):
1) `lumibot/entities/order.py:181 Order.__init__` (~0.094s, 10k calls)  
2) `lumibot/entities/data.py:1029 Data.get_bars` (~0.094s, 20k calls)  
3) `lumibot/brokers/broker.py:2089 BacktestingBroker._process_trade_event` (~0.082s, 20k calls)  
4) `lumibot/entities/data.py:610 Data.get_iter_count` (~0.078s, 50k calls)  
5) `lumibot/backtesting/backtesting_broker.py:1546 BacktestingBroker.process_pending_orders` (~0.069s, 4k calls)  
6) `lumibot/entities/data.py:686 Data.checker` (~0.064s, 10k calls)  
7) `lumibot/backtesting/backtesting_broker.py:1325 BacktestingBroker._execute_filled_order` (~0.063s, 10k calls)  
8) `lumibot/strategies/strategy.py:3878 _FuturesSpeedBurnerStrategy.get_historical_prices` (~0.061s, 20k calls)  
9) `lumibot/entities/bars.py:173 Bars.__init__` (~0.059s, 20k calls)  
10) `lumibot/strategies/strategy.py:408 _FuturesSpeedBurnerStrategy.create_order` (~0.039s, 10k calls)  

Key delta:
- For IBKR backtests, MARKET orders skip the quote fill attempt path when bid/ask are unavailable (the quote path would
  immediately fall back to OHLC anyway). This avoids expensive quote plumbing (`get_quote` / Quote object work) in the
  warm-cache benchmark and is a major driver of the ~20× improvement recorded in the speed report.
- When bid/ask exist, quote-based market fills remain enabled (BUY at ask / SELL at bid), preserving the semantics
  exercised by the IBKR futures smoke stubbed tests.
