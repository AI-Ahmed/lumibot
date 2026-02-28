# 2026-01-22 MESMomentumSMA9 First-Divergence Mini-Audit (IBKR vs Stored Artifacts)
> Tier 2 “manual audit” notes for the earliest observed divergence in the MESMomentumSMA9 parity rerun.

**Last Updated:** 2026-01-22  
**Status:** Active  
**Audience:** Developers, AI Agents  

---

## Overview

This is a minimal Tier 2 audit (“manual sanity check”) for the **first** divergent fill in:
- `docs/investigations/2026-01-22_IBKR_DATABENTO_PARITY_RERUN_RESULTS.md`

The intent is to answer: “Is this mismatch caused by LumiBot execution semantics, or by the underlying bar data not matching?”

---

## First divergent fill

Baseline (stored artifact) first fill:
- `2025-09-01 00:08:00-04:00` BUY `MES`
- Fill price: `6471.00`

IBKR rerun first fill:
- `2025-09-01 00:08:00-04:00` BUY `MES`
- Fill price: `6470.50`

Difference:
- `0.50` (= 2 ticks for MES, tick size `0.25`)

---

## Underlying IBKR minute bar at the fill timestamp (Trades/OHLC)

For `2025-09-01 00:08:00-04:00` on the explicit contract month `MES` exp `2025-09-19`, the IBKR minute bar is:
- `open=6470.50`
- `high=6470.75`
- `low=6470.25`
- `close=6470.75`

Observation:
- The **baseline fill price `6471.00` is outside the IBKR bar range** (`high=6470.75`).

Conclusion (likely):
- This first divergence is driven by **bar data mismatch** between the stored artifact’s underlying “Trades/OHLC” bars and IBKR’s “Trades/OHLC” bars at the same timestamp.
- It is *not* explained by a simple execution-model difference (e.g., “fill at open vs close”) because even the IBKR high does not reach the baseline fill.

---

## Implication for parity strategy

If the goal is Tier 1 strict parity:
- We need the bar construction/aggregation rules to match between the baseline generator and the IBKR rerun (or regenerate the baseline artifacts using the same bar feed and aggregation rules).

If the goal is Tier 3 broker realism:
- This evidence supports treating the stored artifacts as a regression signal, not truth, because the underlying data differs.

