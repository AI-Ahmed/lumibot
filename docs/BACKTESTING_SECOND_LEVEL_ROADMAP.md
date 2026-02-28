# BACKTESTING_SECOND_LEVEL_ROADMAP.md

> Roadmap for “seconds-level” backtesting support without making backtests 60× slower.

**Last Updated:** 2026-02-06
**Status:** Active
**Audience:** Developers, AI Agents

---

## Overview

Second-level data has two distinct uses in backtesting:

1) **Second-level fills** (higher fill realism), without changing the strategy’s iteration cadence.
2) **Second-level strategy loops** (higher decision cadence), which can become infeasible if implemented as “run the full strategy every second” for long windows.

This document defines a staged plan that preserves current minute-level performance goals while creating a clean path to seconds-level realism.

See also:
- `docs/BACKTESTING_ACCURACY_VALIDATION.md` (Tier 3 “live replay baseline” is the gold standard)
- `docs/BACKTESTING_PERFORMANCE.md` (profiling + speed methodology)
- `docs/BACKTESTING_SESSION_GAPS_AND_DATA_GAPS.md` (no synthetic bars; gaps are “no data”)
- `docs/investigations/bot_manager.md` (current implementation plan for true seconds-mode, futures-first)

## Current Status (2026-02-06)

- `ibkr_helper` now normalizes second-level timestep aliases (`second`, `1S`, `1sec`, etc.) into IBKR-style bar values.
  This removes “unknown unit” style parsing failures, but does not by itself guarantee that second-level history is
  supported end-to-end.
- Full second-level backtesting is still pending engine work (clock iteration + `Data` second-unit support + fill semantics tests).

Internal note (do not treat as shipped capability): second/tick history routing via a TWS/Gateway adapter is being
worked on in the downloader layer, but it is not relied on by default.

---

## Definitions (so we don’t talk past each other)

### “Second-level data”

A time series whose timestamps advance in seconds (e.g., 1-second OHLC bars or last-trade snapshots).

### “Second-level backtesting”

Can mean either:
- **Seconds for fills only** (strategy still runs on minutes), or
- **Seconds for the full clock** (strategy runs each second).

These are different engineering and performance problems.

---

## Phase 7a — Seconds for fills only (bar magnifier)

Goal:
- Keep strategy iteration at minute cadence (fast),
- Use seconds data only when needed to evaluate fills for active orders.

Mechanics:
- Strategy clock advances on minute bars (or event-driven; see Phase 7b).
- When there are active limit/stop/bracket orders during a minute bar:
  - load the corresponding seconds bars for that minute window (or the minimal window that covers the order’s lifetime),
  - evaluate whether/when the order would have triggered inside that minute,
  - produce a deterministic fill timestamp/price consistent with the fill model.

Why this is high ROI:
- Many strategies place orders infrequently relative to bar count.
- You avoid 60× more strategy iterations while improving the realism where it matters most (stops/brackets).

Key constraints:
- Seconds history must follow the same **no synthetic bars** rule (gaps remain gaps).
- The magnifier must not introduce lookahead (only use seconds data that occurs before the evaluated timestamp).

---

## Phase 7b — Event-driven clock (the real path to 100×)

Goal:
- Avoid iterating over dead time (closed gaps, empty periods),
- Advance simulation time only on meaningful events.

Clock advances on:
- bar close timestamps (minute/day),
- seconds-bar timestamps only when required by Phase 7a,
- fill/cancel events (order state transitions),
- scheduled user callbacks (e.g., “every day at 09:30”).

Why this matters:
- In markets with maintenance gaps or holidays, “per-second loop” is mostly wasted work.
- Event-driven simulation naturally skips closed gaps, and it makes performance scale with *activity*, not wall-clock duration.

---

## Phase 7c — Full second-level strategy loops (small windows only)

Goal:
- Allow true second-by-second strategy logic for short research windows.

Guardrails (required):
- explicit opt-in mode (e.g., `seconds_mode=true`)
- hard ceilings (hours/days, not months)
- clear warnings/errors when users attempt “months at 1-second”

This mode is intended for targeted studies, not broad optimization sweeps.

---

## Phase 7d — Performance gates for seconds-mode

We treat seconds-mode as a feature that must prove it does not destroy performance.

Required benchmarks:
- **Seconds fill magnifier benchmark:** demonstrate that Phase 7a adds only bounded overhead on minute-level runs.
- **Seconds feasibility benchmark:** demonstrate an upper bound on “full seconds loop” window length with acceptable runtime.

Required reporting:
- write measurements into an investigations report (append-only), similar to:
  - `docs/investigations/2026-01-22_IBKR_MINUTE_SPEED_BURNER_REPORT.md`

---

## Phase 7e — True seconds strategy loops (production-capable)

Goal:
- Support **true** second-by-second strategy logic for meaningful windows (days/weeks), not just “small research windows”.
- Preserve correctness: no synthetic bars, deterministic fills, full artifacts.

Key insight:
- A naïve “run the full strategy every integer second between start/end” is both slow and often incorrect (it iterates
  through closed-session gaps that have no data).

Required design pillars:

1) **Event-driven seconds clock**
   - The simulation advances on actual seconds timestamps present in the dataset (“clock series”), not on every integer second.
   - This naturally skips maintenance windows and missing time without fabricating bars.

2) **Prefetch once → slice forever**
   - For each `(asset, timeframe=second, quote/source key)`, fetch the full window once (start-warmup → end).
   - Subsequent `get_historical_prices` calls must be in-memory slices, not downloads or parquet reloads.

3) **Avoid per-tick pandas churn**
   - Seconds-mode cannot afford repeated `DataFrame.copy()`, `pd.to_datetime(...)`, or merges per tick.
   - Normalize timestamps once at prefetch time; slice by integer offset.

4) **Explicit multi-asset clock semantics**
   - Define whether time advances on:
     - a primary clock series (recommended first), or
     - union of timestamps across series (later, if needed).

This phase is the “real product” when customers ask for second-level strategies.
For implementation details and the current project plan, see:
- `docs/investigations/bot_manager.md`
