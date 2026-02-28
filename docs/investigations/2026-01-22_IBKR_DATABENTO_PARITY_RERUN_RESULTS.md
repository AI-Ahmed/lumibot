# 2026-01-22 IBKR Futures Parity Rerun Results (Stored “DataBento-era” Artifacts)
> Rerun results for the IBKR-vs-artifact parity harness, including current mismatches and the most likely root causes.

**Last Updated:** 2026-01-22  
**Status:** Active  
**Audience:** Developers, AI Agents  

---

## Overview

This investigation records the outcome of rerunning:
- `scripts/run_ibkr_futures_parity_artifact_baselines.py`

against the stored “DataBento-era” baseline artifacts referenced in:
- `docs/IBKR_DATABENTO_FUTURES_PARITY.md`

These stored artifacts are treated as **Tier 1 regression signals**. They are not the “gold standard” definition of accuracy; that is Tier 3 (“live replay baseline”).

---

## Runs executed (local)

The parity harness produces local artifacts under:
- `tests/backtest/_parity_runs/ibkr_vs_artifact_baselines_<timestamp>/`

Key fix enabling these reruns:
- The harness now seeds `<run cache>/ibkr/conids.json` (expired futures conid registry) from the repo backfill cache and writes a matching `.s3key` marker so S3 hydration doesn’t overwrite it.

---

## Findings

### A) MESFlipStrategy (CME, ~5 days)

Observed:
- **Strict parity FAIL**: baseline and IBKR have the same timestamps, but the first fills differ by a large amount (~60 points).

Representative evidence:
- Baseline price-line at `2025-10-30 00:00:00-04:00` is ~`6948`.
- IBKR cached explicit contract series for `MES` exp `2025-12-19` at the same timestamp is ~`7008`.

Most likely root cause:
- The stored artifact appears to have been generated with a **different continuous futures pricing convention** (e.g., back-adjusted continuous pricing) than the current IBKR backtesting implementation, which stitches **explicit contracts** and therefore reflects the **raw** contract price.

Why this matters:
- If Tier 3 “broker realism” is the goal, raw explicit-contract pricing is typically the correct model for execution.
- If Tier 1 artifact parity is required for a given strategy, we must align the continuous pricing convention between the stored artifact generator and the IBKR rerun path (or regenerate the artifacts under the new convention).

### B) MESMomentumSMA9 (CME, ~29 days)

Observed:
- **Strict parity FAIL**: the first divergence occurs very early (first few fills), and the trade stream then cascades.
- The earlier “worst bug” (filling with a future session open while keeping the old timestamp) remains fixed, but small per-fill differences still cause divergence in this ATR/bracket-heavy strategy.

Most likely contributors:
- Small per-bar differences between the stored artifact’s bar aggregation and IBKR’s bar aggregation (even when both are “Trades”/OHLC-like) can shift ATR-derived stop prices by 1–2 ticks.
- Once the first bracket stop differs, the trade stream diverges and later parity becomes meaningless without a “first divergence” audit.

---

## Next steps (parity ladder aligned)

1) **Decide the Tier 1 tolerance posture for continuous futures**
   - If the stored artifacts are known to be generated with a back-adjusted continuous series, we should document that explicitly and either:
     - accept MESFlip Tier 1 parity as “expected FAIL” (with a reason), or
     - regenerate Tier 1 artifacts with the same continuous pricing convention we use for broker realism.

2) **Add Tier 2 manual audit notes for the first divergence**
   - For MESMomentum: pick the first divergent fill and record:
     - bar(s) used, bid/ask or OHLC model, tick rounding, and the exact stop/limit trigger.

3) **Move toward Tier 3 live replay baselines**
   - Once live trading logs + the exact live data stream are capturable for futures/crypto, Tier 3 becomes the only “real” accuracy gate.

