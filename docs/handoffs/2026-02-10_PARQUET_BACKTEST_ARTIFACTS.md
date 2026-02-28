# PARQUET BACKTEST ARTIFACTS (BOTSPOT)

> Emit Parquet siblings for backtest result artifacts (indicators/trades/stats/trade_events) to speed BotSpot analysis and UI queries.

**Last Updated:** 2026-02-10  
**Status:** Draft  
**Audience:** Developers + AI Agents  

---

## Overview

BotSpot frequently queries `*_indicators.csv` (and related artifacts) via DuckDB inside `botspot_node`. CSV parsing + repeated scans can be slow (10s–40s per query in worst cases). This change makes LumiBot emit Parquet versions of the same artifacts so downstream services can prefer Parquet (faster scans, typed columns, compression) while keeping CSV as the compatibility layer.

This is intentionally additive:

- **CSV stays the source-of-compatibility** (existing consumers keep working).
- **Parquet becomes the source-of-speed** for analytics (DuckDB, UI endpoints, agent tools).

## What Changed

LumiBot now attempts to write these Parquet artifacts alongside existing outputs:

- `*_indicators.parquet` (sibling of `*_indicators.csv`)
- `*_trades.parquet` (sibling of `*_trades.csv`)
- `*_stats.parquet` (sibling of `*_stats.csv`)
- `*_trade_events.parquet` (sibling of `*_trade_events.csv`)

Implementation notes:

- Uses `pandas.DataFrame.to_parquet(engine="pyarrow", compression="zstd")`.
- **Best-effort:** Parquet export failures are logged as warnings and do **not** fail a backtest (CSV remains the fallback).

## Why This Helps Performance

Downstream (BotSpot) improvements unlocked by emitting Parquet:

- DuckDB scans Parquet without CSV parsing/inference overhead.
- Parquet is columnar, so projecting a few columns is much cheaper than reading entire CSV rows.
- Enables Parquet-first behavior in `botspot_node` (fallback to CSV when Parquet is missing).

## Verification (Local)

Parquet generation is covered by unit tests:

- Indicators Parquet: `python3 -m pytest tests/test_indicators_detail_text_edge_cases.py -q`
- Trades Parquet: `python3 -m pytest tests/test_indicator_subplots.py::test_plot_returns_preserves_cash_settled_status -q`
- Stats Parquet: `python3 -m pytest tests/test_strategy_dump_stats_regression.py -q`
- Trade events Parquet: `python3 -m pytest tests/test_backtesting_broker.py -q`

Manual sanity check (after running any backtest with `show_indicators=True`):

- Confirm `logs/*_indicators.csv` and `logs/*_indicators.parquet` both exist and have the same row count.

## Rollout Order (BotSpot)

1. Deploy LumiBot (so new backtests produce Parquet artifacts).
2. Deploy Bot Manager upload pipeline (so Parquet artifacts get uploaded with results).
3. Deploy `botspot_node` (Parquet-first querying + timing logs).
4. Deploy `botspot_react` (ensure indicators Parquet remains non-downloadable in UI).

## Risks / Notes

- Parquet typing can differ from DuckDB CSV inference (usually a net improvement). We rely on CSV fallback if anything is missing.
- This feature assumes `pyarrow` is present in the runtime image. (It is already pinned in LumiBot deps; if it is absent, Parquet writes will warn and CSV continues to work.)

