# BACKTESTING_ACCURACY_VALIDATION.md

> Defines what “accuracy” means in LumiBot backtesting and how we validate it (Tier 1/2/3).

**Last Updated:** 2026-01-22
**Status:** Active
**Audience:** Developers, AI Agents

---

## Overview

This document defines a practical accuracy validation ladder for LumiBot backtesting.

**Key principle:** accuracy is ultimately measured against **live broker behavior** when possible. Vendor parity (e.g., DataBento-era artifact baselines) is valuable, but it is not “truth”.

---

## Accuracy validation ladder (Tier 3 is the gold standard)

### Tier 1 — Regression parity (fast, automated)

Use deterministic regression suites to detect drift:

- Stored vendor artifact baselines (e.g., DataBento-era runs for futures).
- Deterministic acceptance backtests (warm cache invariant) for key strategies.

Tier 1 tells us: “did something change?” It does not prove real-world broker realism.

### Tier 2 — Manual audits (target the hard edges)

Manually validate fills and timestamps around the places that most often produce backtest errors:

- session gaps (daily maintenance, weekend gaps)
- holiday early closes / irregular sessions
- roll boundaries for continuous futures
- tick rounding + multiplier PnL math
- stop/stop-limit/bracket behavior on gap reopens

Tier 2 tells us: “do the rules make sense?” and catches subtle issues that unit tests may miss.

### Tier 3 — Live replay baseline (the real accuracy test)

**Goal:** replay an interval that was traded live and reproduce **the broker’s realized behavior**:

- same trades (sequence + timestamps)
- same fills (within a defined tolerance)
- same realized PnL curve (within a defined tolerance)

**Why this matters:** this is the only validation that can meaningfully support the statement “our backtest is accurate”.

---

## What a Tier 3 “live replay baseline” needs (minimum)

For a strategy that traded live, capture and persist:

1) **Orders submitted** (timestamp, asset, side, qty, order type, limit/stop prices, tif, flags)
2) **Broker-reported fills** (timestamp, price, qty, venue when available, fees)
3) **Strategy clock / lifecycle context** (what time the strategy believed it was, market flags)
4) **Data stream used live** (the exact data source + any transformations used for decisions)

If we do not preserve the live data stream, we can only say “it looks similar”, not “it reproduces”.

---

## Tolerances (define before asserting)

Typical tolerances must be defined per asset class:

- **Futures:** tick size (e.g., MES 0.25) + multiplier correctness
- **Crypto:** decimal precision + bid/ask vs trades choice (market orders should typically fill at ask/bid if quotes are modeled)
- **Equities/options:** extended hours rules + quote vs trade availability

---

## Related docs

- `docs/BACKTESTING_ARCHITECTURE.md`
- `docs/ACCEPTANCE_BACKTESTS.md`
- `docs/BACKTESTING_SESSION_GAPS_AND_DATA_GAPS.md`
- `docs/IBKR_DATABENTO_FUTURES_PARITY.md`

