# ThetaData Option EOD Gaps → Daily MTM Failures (Root Cause + Fix)

One-line description: Investigation write-up for a correctness bug where ThetaData option EOD/day history can be missing even when minute quote history exists, causing daily-cadence option backtests to become unpriceable and produce flat/incorrect tearsheets.

Last Updated: 2026-01-06

Status: FIX IMPLEMENTED — daily-cadence option MTM now falls back to intraday NBBO snapshot quotes; acceptance suite must re-warm S3 for quote snapshot objects.

Audience: LumiBot contributors, especially anyone working on ThetaData backtesting, option pricing, and acceptance backtests.

## Overview

Several option-heavy strategies (notably Strategy Library demos like **Meli Drawdown Options** and **CVNA Drawdown Call**) produced wildly different tearsheets between runs:

- one run: “beautiful” equity curve, many trades, plausible exits
- another run: flat equity curve, almost no activity, “cash_settled at 0”

This document explains:

1) what data condition causes this,
2) which LumiBot code path was relying on the missing data,
3) how we fixed it (without editing strategy code),
4) what to validate next, and
5) why this change interacts with the acceptance backtest “no downloader” invariant.

---

## 1) Symptom (client-visible)

When the bug triggers, you may see:

- “Skipping valuation for asset … because no price was available …”
- option positions held through expiry without realistic exits
- equity curve stays flat (or stalls) for long periods
- tearsheet metrics become nonsensical (e.g., -50% total return from one contract expiring worthless)
- the strategy “looks broken” even if its logic is fine

This is a critical customer-impact issue:

- users judge the product based on strategy realism and backtest stability
- wrong/flat tearsheets are a churn event

---

## 2) The underlying data condition

### 2.1 ThetaData can have EOD option history gaps

ThetaData can return “no data” for:

- option EOD/day history endpoints (or day-level OHLC caches)

even when:

- the same option contract has intraday **quote history** containing actionable NBBO bid/ask

This can happen for:

- specific contracts (certain strikes/expirations)
- specific time spans (older date ranges, or around unusual session metadata)
- providers that represent expirations differently (Friday vs OCC Saturday mapping — separately fixed)

### 2.2 Why this matters in LumiBot daily cadence

Daily-cadence strategies often do:

- one iteration per trading day
- portfolio valuation at the end of each day
- exit decisions based on “price today” (which requires an option mark)

If the option mark cannot be obtained for that day, the strategy can:

- fail to exit (no mark, no valuation)
- or value at 0 (catastrophic and wrong)
- or forward-fill stale prices (better than 0, but still incorrect if exit should happen)

---

## 3) The LumiBot code path that exposed the issue

### 3.1 Portfolio mark-to-market (MTM)

In backtesting, portfolio value is computed in strategy code:

- `lumibot/strategies/_strategy.py`
  - `_update_portfolio_value()` calls `_get_price_from_source()` per held asset
  - `_get_price_from_source()` decides how to price options for MTM

### 3.2 The pre-fix behavior (problematic)

Before the fix, daily cadence could cause option pricing to rely on:

- day/EOD option history (or day-level quote alignment)

When day/EOD history is missing:

- no price is available → valuation skips → equity curve “flat”

Meanwhile, intraday quote history might still exist and would be a valid mark source.

### 3.3 Desired behavior (broker-like)

Broker semantics:

- last-trade can be stale for illiquid options
- NBBO quotes are often available and should be used for:
  - option MTM mark
  - quote-based fills
  - “is this contract tradeable” checks

Therefore: when day/EOD is missing, we should still be able to price options using NBBO quotes.

---

## 4) The fix (what changed)

### 4.1 High-level behavior

For ThetaData option backtests:

1) determine cadence (daily vs intraday)
2) attempt the normal quote path
3) if daily cadence and no actionable bid/ask mark is available, fall back to:
   - intraday snapshot quote

Concretely:

- use `get_quote(timestep="minute", snapshot_only=True)` as the fallback source of truth
- compute mark = mid(bid, ask) when available

This ensures:

- daily-cadence option strategies can value and exit positions
- even when ThetaData EOD option history is missing

### 4.2 Code references

- `lumibot/strategies/_strategy.py`
  - `_get_price_from_source()` ThetaData option branch
- `lumibot/backtesting/thetadata_backtesting_pandas.py`
  - `get_quote(..., snapshot_only=True)` snapshot path (performance + correctness)

### 4.3 Regression test

We added a unit test:

- `tests/test_thetadata_option_daily_mtm_snapshot.py`

It asserts:

- daily cadence tries day quote first
- if that yields no mark, it uses minute snapshot-only quote
- mark is computed correctly from bid/ask

This prevents future “refactors” from reintroducing the bug.

---

## 5) Why this fix interacts with acceptance backtests

Acceptance backtests enforce:

- canonical windows are already cached in S3 v44
- **no downloader calls** may occur during CI acceptance runs

The fix introduces additional quote snapshot requests in daily cadence, which means:

- S3 v44 must include those snapshot quote objects
- otherwise acceptance runs will legitimately call the downloader (to fetch missing quote data)
- and the acceptance tripwire will abort (as designed)

So:

1) this fix is correct
2) acceptance failing afterwards means “cache incomplete for the new required objects”
3) the operational fix is: run a one-time warm/fill outside CI (tripwire off), then rerun acceptance (tripwire on)

See:

- `docs/handoffs/2026-01-06_ACCEPTANCE_BACKTESTS_HANDOFF.md`
- `scripts/warm_acceptance_backtests_cache.py`

---

## 6) How to validate the fix on real strategies (no strategy edits)

### 6.1 Meli Drawdown Options (demo)

When the bug is present:

- the strategy often buys a contract and then cannot exit realistically
- valuations may be skipped due to missing day/EOD history

After the fix:

- the strategy should be able to compute option marks using quote snapshots
- exits should occur when rule conditions are met (and quotes exist)
- equity curve should not be flat “forever”

Validation workflow:

1) run from Strategy Library with prod-like env
2) inspect `*_logs.csv` for:
   - “Skipping valuation … no price …” warnings
3) inspect tearsheet:
   - curve should show trade action / exits
4) repeat run:
   - same trades and same metrics (deterministic)

### 6.2 CVNA Drawdown Call (demo)

Same validation as MELI.

Note:

- CVNA options also expose a separate expiration-mapping issue (Friday vs Saturday provider expiries).
- That mapping issue was fixed earlier (chain-derived expiry mapping + stale placeholder repair).

---

## 7) Common pitfalls

### 7.1 “It works on my machine” due to warm local disk cache

If you run a strategy multiple times on the same machine without isolating `LUMIBOT_CACHE_FOLDER`,
you may be relying on:

- local parquet cache

This can mask:

- missing S3 v44 objects (CI will fail)

Acceptance harness avoids this by forcing a fresh disk cache folder per run.

### 7.2 Forward-fill is not a substitute for real marks

Forward-fill is a pragmatic fallback to avoid valuing an illiquid option at 0 when the current mark is missing.
But if the mark is missing because we hit an EOD data gap that could be solved via quote snapshots, forward-fill
is still wrong (it can hide exit signals and distort tearsheets).

The correct fix is:

- use quote mark when possible
- and only use forward-fill when the market truly has no actionable quote

---

## 8) Open questions (future work)

These are not blockers for the fix, but are worth tracking:

1) Can we detect and repair “EOD missing but quote exists” at the cache layer (precompute day-level marks)?
2) Do we want to persist a “day mark” series derived from intraday quotes to reduce repeated snapshot fetches?
3) How should we treat historical options that have quotes but no trades for long periods (LEAPS)?
4) Are there ThetaData subscription-level differences that affect EOD option history availability?

---

End of investigation.

