# IBKR Futures (MESMomentumSMA9) Parity Divergence: CME Early-Close Session Gap (4.4.36)

## Summary

The stored DataBento baseline `MESMomentumSMA9_2025-10-15_12-52_88xWTg_*` diverged from the IBKR REST re-run starting on **2025-09-01 13:00 ET** (Labor Day early close for CME Globex equity futures).

The IBKR TRADES minute series had **no 13:00 bar** (last bar at 12:59, next bar at 18:00). Earlier IBKR futures “next-bar” execution logic could treat the 18:00 bar as the next executable bar and use its **open price** for MARKET fills, while the stored baseline artifacts filled using the **last available bar open** (12:59).

This produced large, persistent trade-sequence divergence even though the plotted indicator price-line remained aligned.

After the gap-handling fix in this branch, the “18:00 open used for 13:00 fills” symptom is addressed; the remaining `MESMomentumSMA9` divergence is now driven by a **stop-loss price mismatch** during the early-close gap (one rolling-ATR tick / 14 bars), which then cascades into different order paths (stop vs explicit market exit) and subsequent trade counts.

## Evidence

From the cached IBKR TRADES minute series (U5):
- `2025-09-01 12:59 ET` exists (open ~6482.25)
- `2025-09-01 13:00 ET` is missing
- `2025-09-01 18:00 ET` exists (open **6480.00**)

Remaining divergence (current state, after gap-handling fix):
- baseline stop fill at `2025-09-01 13:05 ET`: **6482.25**
- IBKR stop fill at `2025-09-01 13:05 ET`: **6482.178571...**

This is a one-tick / 14-bar ATR difference (0.25 / 14 ≈ 0.017857, and 4× that ≈ 0.071428), which is enough to change whether/when the bracket stop triggers inside the simulated gap bars. Once the stop fill price differs, the strategy’s subsequent state diverges (at `13:07 ET` IBKR chooses a direct MARKET exit), and the remainder of the trade stream no longer lines up by index.

## Root cause

`BacktestingBroker.process_pending_orders()` has IBKR futures-specific “avoid same-bar lookahead” behavior that selects a “next bar” for fills. When the next bar is separated by a large session gap (holiday early close / maintenance), the selection favored the next session’s open bar.

The stored baseline artifacts were produced under “current-bar fallback” semantics for large gaps, so parity failed.

## Fix (4.4.36)

Adjusted the IBKR futures gap handling so that when the *next* bar is a large session gap, MARKET fills fall back to the **last available bar at-or-before `self.datetime`** instead of jumping to the next session open.

This matches the stored baseline semantics and prevents the “use 18:00 open while timestamp is 13:00” behavior.

## Regression test

Added a stubbed unit test that models the exact early-close gap shape:

- `tests/test_ibkr_futures_backtesting_smoke_stubbed.py::test_ibkr_rest_backtesting_futures_market_fill_uses_last_bar_open_across_large_session_gap`

This test is fully offline (no downloader) and asserts the MARKET fill price uses the 12:59 open rather than the 18:00 open.

## Operational note: downloader flakiness

Attempting to re-run the full artifact-baseline parity harness against the remote downloader (`DATADOWNLOADER_BASE_URL`) was intermittently blocked by repeated queue submit timeouts:

- `HTTPConnectionPool(...): Read timed out. (read timeout=120.0)` during queue submit

When this occurs, parity runs should be executed using already-warmed caches (or after downloader stabilization), because the runner will otherwise stall before it can populate the cache folder.

## Next improvements (not in this change)

- Consider a calendar-aware futures session model (e.g., `pandas_market_calendars` “CME Globex Equity”) so the backtest clock does not iterate through closed windows (13:00–18:00 early close, daily maintenance, etc.).
- If we keep “hold order until reopen” semantics for gaps, we should also move the fill timestamp to the actual bar timestamp used for execution (not the submission timestamp).

## Current parity status (local harness)

- `MESFlipStrategy`: trades parity PASS (indicators overlap 1.0); minor stats drift only.
- `MESMomentumSMA9`: still FAIL due to the early-close gap stop-loss mismatch described above.
