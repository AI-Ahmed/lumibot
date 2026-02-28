# SPX INTRADAY STALE LOOP FIX (THETADATA)

> Root cause, fix, and validation for a production-severity ThetaData cache-coverage bug that could make SPX-family strategies refetch index minute OHLC forever (ETA “days”) even with populated S3 caches.

**Last Updated:** 2026-01-13
**Status:** Active
**Audience:** Developers, AI Agents

---

## Overview

Some SPX-family strategies in production were effectively stuck in a loop:

- they logged `[THETA][CACHE][STALE] prefetch_complete but coverage insufficient` continuously
- they repeatedly submitted `v3/index/history/ohlc` requests to the downloader queue
- warm-cache determinism was broken (a “warm run” still behaved like a cold run)

This was not “S3 is slow”. It was a correctness/semantics bug: the code required coverage through an impossible end timestamp for an RTH-bounded feed. Fixing the coverage requirement is the 10×–100× lever.

---

## Symptoms (what you see when this is happening)

### CloudWatch (production)

Backtests repeatedly emit:

- `[THETA][CACHE][STALE] ... coverage insufficient; clearing flag`
- `[THETA][CACHE][REFRESH] ... reasons=end`
- `Submitted to queue: ... path=v3/index/history/ohlc ... symbol=SPX ... interval=1m`

When this happens, a full-year backtest can show absurd ETAs (days) because the loop triggers
minute-by-minute (or iteration-by-iteration) refetch attempts.

### UI / status API

You may see:

- `stage=backtesting` but progress appears “stuck” on an early date
- `download_status` empty/unknown while the backtest is actually performing downloader work

---

## Concrete production incident (client benchmark)

Example backtest (prod):

- `BOT_ID` / `manager_bot_id`: `111124bd-2ccc-4078-a650-88cd637d1eb6`
- Strategy class tag in logs: `SPX0DTEHybridStrangle`
- Log group: `/aws/ecs/prod-trading-bots-backtest`

Representative one-hour slice during incident:

- `Submitted to queue`: ~1200/hour
- Dominant path: `v3/index/history/ohlc`
- `[THETA][CACHE][STALE]`: ~1:1 with submits

Interpretation:

- this is a pathological “cache never becomes complete” loop
- it is not solved by bigger boxes; it is primarily an input-window/coverage semantics issue

---

## Local reproduction (prod-like) + baseline numbers

Strategy used for local baseline:

- `/Users/robertgrzesik/Documents/Development/Strategy Library/Demos/SPX Short Straddle Intraday (Copy 4).py`
  - class: `SPX0DTEHybridStrangle`

Runner:

- `scripts/run_backtest_prodlike.py` (see `docs/PRODLIKE_LOCAL_BACKTEST_RUNS.md`)

Warm invariant:

- a warm run means `queue_submits == 0` (same S3 namespace, fresh local disk cache folder)

Example window:

- `2025-02-03 → 2025-02-07` (5 trading days)

Observed baseline (local, prod-like):

- Cold run: ~80.6s, `queue_submits=75`
  - top paths: `option/history/quote` 40, `option/list/strikes` 31, `index/history/ohlc` 3
- Warm run (yappi): ~34.5s, `queue_submits=0`
  - yappi warm attribution: `pandas_numpy` dominated (~50%+), `s3_io` ~1%

Takeaway:

- after coverage semantics are correct, warm speed is largely compute/artifact-bound for this strategy
- “S3 is slow” is not supported by yappi for this warm baseline

---

## Root cause (why the loop existed)

### Provider reality: SPX index minute OHLC is RTH-bounded

For Theta index minute OHLC, we consistently see:

- ~391 rows/day
- last bar at (or very near) the session close
- early closes on half-days

Therefore “cache coverage complete” for index intraday must be defined by the **trading session**,
not by an arbitrary end datetime like “23:59”.

### The failure mode: an impossible end requirement

When the backtest window end was represented as midnight (or UTC-midnight, e.g. `...T00:00:00.000Z`),
the internal “coverage required” timestamp could drift later than the last available bar:

- the feed ends at market close
- the code required coverage through a later timestamp (often equivalent to `23:59` or `18:59` ET)

Then the coverage check did:

- `prefetch_complete=true` AND `existing_end < end_requirement`
- => mark cache STALE + clear flag + refetch

Which repeats every minute iteration because the requirement is never satisfiable.

---

## Fix (what changed)

### Code location

- `lumibot/backtesting/thetadata_backtesting_pandas.py`
  - `ThetaDataBacktestingPandas._update_pandas_data()`

### Behavioral change

For index intraday (`ts_unit` like `minute`/`hour`), clamp the “coverage required” end timestamp to:

> **the last trading session close at or before the end requirement**

This handles:

- weekends (clamp to prior trading day close)
- holidays (clamp to prior trading day close)
- early closes (clamp to that early close)

Important nuance:

- The clamp uses a dedicated cache (`_session_close_cache_last`) separate from any “forward-close”
  cache used by small-window alignment logic, to avoid cache-key collisions and stale reuse.

### Why this is safe

This does not “drop real bars” for the provider:

- the provider does not publish index minute OHLC after the session close
- requiring coverage beyond the last available bar forces pointless work and breaks warm determinism

---

## Regression tests (what prevents reintroducing it)

Primary test file:

- `tests/test_thetadata_intraday_end_validation.py`

Coverage added/extended:

- Index minute OHLC end clamp
- Index intraday “quote probe” end clamp (prevents bypass via quote/last-price paths)
- Holiday/weekend clamping (e.g. 12/25 clamps to 12/24 close, respecting early close when applicable)

Run locally:

```bash
/Users/robertgrzesik/bin/safe-timeout 300s python -m pytest -q tests/test_thetadata_intraday_end_validation.py
```

---

## Production validation checklist (post-deploy)

Given a `BOT_ID` log stream:

1) `v3/index/history/ohlc` `Submitted to queue` volume should collapse on warm runs.
2) `[THETA][CACHE][STALE]` should not spam minute-by-minute.
3) Re-running the same backtest window should satisfy “warm means warm” (`queue_submits≈0`).

---

## Links / references

- PR: https://github.com/Lumiwealth/lumibot/pull/946
- Related docs:
  - `docs/BACKTESTING_PERFORMANCE.md` (warm proof + profiling protocol)
  - `docs/PRODLIKE_LOCAL_BACKTEST_RUNS.md` (prod-like runner + scoreboard)

---

## What to do next (once the loop is gone)

For the client benchmark `SPX0DTEHybridStrangle`, the next warm-speed levers are:

- reduce/disable expensive artifacts in production (tearsheet/plots) or make them async
- optimize pandas-heavy hot paths (yappi already shows the bucket)
- reduce options request volume in cold runs via prewarm, but product goal is warm speed first
