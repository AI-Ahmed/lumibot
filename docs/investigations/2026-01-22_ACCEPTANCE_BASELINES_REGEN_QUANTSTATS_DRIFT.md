# ACCEPTANCE BASELINES REGEN (QUANTSTATS DRIFT)

Acceptance baseline refresh after QuantStats percent/metric formatting drift caused deterministic acceptance backtests to fail.

**Last Updated:** 2026-01-22  
**Status:** Completed  
**Audience:** Developers, AI Agents  

## Overview

Several acceptance backtests started failing due to changes in how QuantStats formats and/or computes headline percent metrics (notably `Total Return`, `CAGR% (Annual Return)`, and `Max Drawdown`) in the tearsheet summary table.

This investigation documents:
- what changed,
- how we stabilized the tearsheet output for CI, and
- which acceptance baselines were regenerated.

## What Changed

### Symptoms

- Tearsheet CSV cells sometimes rendered with low precision (e.g., `-11%` instead of `-11.89%`), causing strict centipercent comparisons to fail.
- In some cases, the numeric value for a metric shifted slightly (typically a few centipercent) even when the underlying return series was unchanged.

### Root Cause

Acceptance baselines are strict and compare tearsheet metrics at **0.01%** resolution. QuantStats output is not guaranteed to be stable across library changes, and some percent cells can be emitted at reduced precision.

### Fix Strategy

- Normalize headline metric cells to `xx.xx%` using a stable recomputation over the exact `df_final` series passed into QuantStats during tearsheet generation.
- Re-run acceptance backtests and regenerate baseline metric expectations to reflect the stabilized output in the current environment.

## Files Changed

- `lumibot/tools/indicators.py`
  - Ensures anchor-day `initial_equity` is scalar even if the first timestamp is duplicated.
  - Normalizes tearsheet headline metric cells to 0.01% resolution.
- `tests/backtest/acceptance_backtests_baselines.json`
  - Updated metric expectations for affected acceptance cases.

## Notes / Follow-ups

- If QuantStats is upgraded again, re-verify acceptance tearsheet stability.
- If we want to avoid baseline churn, consider pinning QuantStats (or vendor the specific metric formatting) as part of the test harness.

