# Production Backtest Benchmarks: Endpoint Breakdown (Sanitized)

Date: 2026-01-08

This investigation records a **sanitized** snapshot of which downloader endpoint families dominate two “must-fix” production backtests. Raw IDs/URLs and log queries are intentionally kept out of this committed document (those belong in `docs/handoffs/`).

## Benchmarks (must-fix)

1) **SPX Short Straddle Intraday (Full year)**
- Dominant family: `option/history/quote`
- Secondary: `index/history/price`
- Takeaway: runtime is primarily **hydration/fanout-bound**, not compute-bound.

2) **Alpha Picks Options (≈1 month)**
- Dominant family: `option/list/strikes`
- Secondary: `option/history/quote`, `option/history/eod`
- Takeaway: runtime is primarily **chain/strike discovery + quote/eod hydration**, not compute-bound.

## Observed endpoint family counts (order-of-magnitude)

These are representative counts observed from production log aggregation for the above benchmarks.

### SPX Short Straddle Intraday (slow run)
- `option/history/quote`: ~2.3k
- `index/history/price`: ~0.2k

### Alpha Picks Options (slow run)
- `option/list/strikes`: ~0.6k
- `option/history/quote`: ~0.5k
- `option/history/eod`: ~0.2k

### Alpha Picks Options (fast/warm run)
- `option/history/quote`: O(10–100)

## Implications

- Fixing “hours-long” option backtests requires reducing request **fanout** and improving cache **coverage** for the dominant families, especially `option/history/quote` and (for some strategies) `option/list/strikes`.
- CPU sizing helps only after hydration request volume is brought under control.

## Next steps (implementation work is tracked elsewhere)

- Ensure `download_status` propagates end-to-end (BotManager must not overwrite it).
- Ensure `download_status` progress reflects **a single asset download operation** (one option contract or one stock time series broken into N request “pieces”), not whole-backtest progress.
