# PARQUET ARTIFACT CONTRACT (BACKTEST RESULTS)

> Root cause + fix plan for missing backtest Parquet artifacts and slow downstream DuckDB queries.

**Last Updated:** 2026-02-10  
**Status:** Active  
**Audience:** Developers + AI Agents  

---

## Overview

BotSpot tooling relies on DuckDB queries against backtest artifacts. CSV parsing over presigned S3 URLs is slow and causes repeated "Querying indicators.csv" steps to take 10s-40s. Parquet artifacts are the intended fast path, but production backtests were not reliably emitting/uploading Parquet, which prevented the Node service from switching to the Parquet sibling.

This investigation documents why Parquet was missing and how we made Parquet export **observable** and **contract-enforceable** for BotManager-driven backtests.

## Symptoms

- S3 backtest results had `*_indicators.csv`, `*_trades.csv`, `*_stats.csv`, etc. but **no `*.parquet` siblings**.
- BotSpot agent/server logs showed repeated expensive CSV scanning (DuckDB over HTTP) instead of Parquet.

## Root Cause

`StrategyExecutor._trace_stats()` was storing a nested Python object in stats rows:

- `positions` was written as a list of dicts containing **raw `Asset` objects**
- `pandas.DataFrame.to_parquet()` (pyarrow) cannot reliably serialize arbitrary Python objects nested inside object-typed columns
- Stats Parquet export failed with an error like:
  - `Conversion failed for column positions with type object`
- The Parquet export was previously **best-effort** (caught + warning), so the backtest completed and uploaded CSVs, but Parquet was silently absent.

## Fixes Implemented

### 1) Stop Embedding Raw Objects in Stats Rows

- `StrategyExecutor._trace_stats()` now serializes `position.asset` via `asset.to_minimal_dict()` (fallback to string) so stats rows are JSON/parquet-friendly.

### 2) Defense-In-Depth Sanitizer Before Parquet Export

- Added `lumibot/tools/parquet_utils.py`:
  - `coerce_object_columns_to_json_strings(df)` converts object-ish columns (lists/dicts/custom objects) into JSON strings.
  - Decimal values are coerced to float for Arrow compatibility.
  - `write_parquet_with_logging(...)` provides:
    - success logs including rows/cols/bytes/duration + coerced columns
    - failure logs with an explicit error code (`PARQUET_EXPORT_FAILED`) and samples of object columns
    - optional fail-fast behavior when Parquet is required.

Stats parquet now uses the sanitizer to ensure nested payloads cannot break Parquet writes.

### 3) Always Emit Empty Indicators + Trade Events Artifacts

Downstream systems treat missing artifacts as "Artifact not found" errors.

- `plot_indicators()` now always writes `*_indicators.csv` + `*_indicators.parquet` even when no chart data exists (empty indicators = valid artifact).
- `Broker.export_trade_events_to_csv()` now always writes `*_trade_events.csv` + `*_trade_events.parquet` even when there are no events (empty events = valid artifact).

### 4) Parquet Contract Mode (Fail-Fast)

New env var:

- `LUMIBOT_BACKTEST_PARQUET_MODE`
  - `best_effort` (default): warn and continue (CSV compatibility layer)
  - `required`: raise on Parquet failure (backtest should fail)

BotManager production backtests should set `LUMIBOT_BACKTEST_PARQUET_MODE=required` so missing Parquet is never silently ignored.

## Tests Added / Updated

- Stats parquet regression for object-ish `positions` (previously failed in prod).
- Trace stats regression ensuring raw `Asset` objects are not embedded in stats rows.
- Indicators regression ensuring empty indicator artifacts are still emitted.

## Deployment Notes

For Parquet to show up in BotSpot:

1. LumiBot must emit Parquet successfully (contract mode recommended).
2. BotManager runner image must upload `*.parquet` artifacts and enforce required artifacts when configured.
3. BotSpot Node should prefer/query Parquet siblings and cache artifact downloads across calls.
