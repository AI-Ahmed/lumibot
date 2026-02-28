# Futures Calendar Spreads: “Ghost PnL” / Equity Curve Spikes (Margin Double-Count) — 2026-01-28

## Symptom

In some futures calendar-spread backtests (e.g., CL front-month vs next-month), the equity curve appears to jump **up/down by ~2× initial margin** whenever the spread is open, even when trade PnL is small.

Typical pattern:
- `cash` stays roughly near the initial budget while the spread is open.
- `portfolio_value` jumps higher by approximately the total margin for both legs (e.g., ~`$16k` for CL if margin is `$8k` per leg).
- When the spread is closed, `portfolio_value` snaps back down.

This looks like profit, but it is not real PnL (it’s accounting).

## Root Cause

The backtesting futures margin/PnL ledger in `BacktestingBroker` keyed lots by:

- `(strategy_name, asset.symbol, asset.asset_type)`

This **ignored `asset.expiration`**.

For strategies that construct futures as:

- `Asset(symbol="CL", asset_type="future", expiration=<date-or-YYYYMM>)`

…both legs of a calendar spread share the same root symbol (`"CL"`) and asset type, and only differ by `expiration`.

As a result:

1) The second leg fill was mistakenly treated as **closing** the first leg (because the ledger thought it was the same contract).
2) Margin was released and PnL realized across different expiries (incorrect).
3) Separately, `Strategy.portfolio_value` (backtesting futures path) adds **margin per open futures position** to compute equity.
4) Since cash/margin were now out-of-sync with the actual open positions, the portfolio value appeared to “spike” by the total margin amount (“ghost PnL”).

## Fix

Include `expiration` in the futures ledger key so different expiries never collide:

- `(strategy_name, asset.symbol, asset.asset_type, asset.expiration)`

This ensures:
- Each contract’s margin is reserved independently.
- Realized PnL is computed only when that specific contract is closed.
- Portfolio value does not jump purely from opening a spread.

## Regression Test

Added a unit test to prevent regressions:
- `tests/test_backtesting_futures_calendar_spread.py`

It opens a simple calendar spread (same symbol, different expiries) and asserts:
- cash reserves margin for **both** legs (no unintended netting)
- the futures ledgers are distinct (different keys)

