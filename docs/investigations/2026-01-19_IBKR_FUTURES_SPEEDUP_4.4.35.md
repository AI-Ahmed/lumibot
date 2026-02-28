# IBKR Futures Speedup (4.4.35)

## Problem

Production IBKR futures backtests were extremely slow (hours for a 1-week window). Logs showed repeated
`ibkr/iserver/marketdata/history` requests and frequent queue timeouts/retries.

The worst-case trigger was intraday strategies requesting timesteps like `"15minute"` (or `"5minute"`)
using `AssetType.CONT_FUTURE`.

## Root Cause

`InteractiveBrokersRESTBacktesting._pull_source_symbol_bars(...)` had a futures prefetch optimization,
but it only activated when the *requested timestep string* was exactly `"minute"`, `"hour"`, or `"day"`.

For `"15minute"`, the prefetch path did not run, so the system repeatedly fetched history from the
downloader on every strategy iteration.

## Fix (4.4.35)

- Treat the timestep **unit** (minute/hour/day) as the key for the IBKR futures prefetch logic.
  - Example: `"15minute"` and `"1minute"` both resolve to unit `"minute"`, so they share a single
    minute-resolution cache dataset for the backtest window.
- Ensure `PandasData.find_asset_in_data_store(...)` recognizes `cont_future` as a futures-like asset
  so the legacy `(asset, USD)` cache key can be used for resampled timesteps.

## Test Coverage

- `tests/test_ibkr_futures_prefetch_unit.py` asserts that repeated `"15minute"` futures requests
  only call `ibkr_helper.get_price_data(...)` once (prefetch + reuse).

## Expected Impact

- Large reduction in downloader roundtrips for intraday futures strategies with non-`"minute"` strings.
- Eliminates repeated "fetching 3000 minute bars" loops per iteration for strategies running on
  `15minute` bars (they now prefetch once for the run window).

