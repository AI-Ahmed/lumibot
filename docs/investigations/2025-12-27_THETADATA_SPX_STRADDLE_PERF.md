# ThetaData Backtesting Perf — SPX Short Straddle (2025-12-27)

## Goal

Make the 1-year SPX Short Straddle Intraday backtest run reliably in production (no “stalls”) and materially faster on a warm cache, without changing demo strategy code.

## Repro

Strategy Library command (warm cache focus):

```bash
cd "/Users/robertgrzesik/Documents/Development/Strategy Library"
env PYTHONPATH="/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot" \
  IS_BACKTESTING=True BACKTESTING_DATA_SOURCE=thetadata \
  DATADOWNLOADER_BASE_URL=https://<your-downloader-host>:8080 \
  BACKTESTING_START=2024-12-31 BACKTESTING_END=2025-12-20 \
  python3 "Demos/SPX Short Straddle Intraday (Copy).py"
```

## Findings

- ThetaData option **quote history does not include last-trade price** (it includes NBBO + metadata). Any trade-only value must come from OHLC/history endpoints.
- A “quote-only first, OHLC later” pattern caused a heavy `pd.concat` code path in `ThetaDataBacktestingPandas._update_pandas_data()` to repeatedly concatenate frames with mismatched columns, triggering a Pandas `FutureWarning` and measurable slowdown.

## Changes applied (high level)

- Prefer quote marks (bid/ask) inside `OptionsHelper` for strike/delta computations and spread checks, only falling back to last-trade when quote-derived marks are unavailable.
- Allow `Data.get_quote()` to return quote fields even when OHLCV columns are absent (returns `None` for missing OHLCV keys rather than `{}`).
- For ThetaData backtests, fetch option OHLC+quote together in `ThetaDataBacktestingPandas.get_quote()` to avoid the “quote-only then OHLC” merge path that caused the concat warning storm.

## Local timing snapshot (warm cache)

- Baseline year run: ~462s wall time
- After fixes above: ~445s wall time

## Follow-ups / next leverage

- If we want to avoid option OHLC downloads entirely (when a strategy truly doesn’t need trade prints), we should store **quote-only vs OHLC** datasets separately (no column-mismatch merges), or implement a safe prefetch/update strategy that doesn’t trigger concat dtype warnings.
- Production wall-time gap vs local likely reflects instance CPU + cache hit rate + downloader latency; adding detailed per-backtest timing breakdowns (download vs sim) to the status payload will make this measurable.
