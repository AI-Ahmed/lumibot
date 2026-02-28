# 2026-01-13 — Render OOM (Tradier) follow-up + SMART_LIMIT loop

This handoff captures the operational incident discussion and where the durable docs/tests/fixes live.

> NOTE: `docs/handoffs/` is ignored by default; add with `git add -f` when committing.

## Incident summary

- Client running `stock_alpha_picks` on Render experienced repeated OOM restarts.
- Primary suspected contributors:
  - Live Tradier polling behavior on accounts with large historical order/trade state.
  - SMART_LIMIT background loop doing unnecessary work even when the strategy doesn’t use SMART_LIMIT.

## Key takeaways

- “No trading activity” does not imply “no work”: background loops can still be hot.
- Any loop that runs sub-second must not scan collections that can grow with account history.
- Live systems need bounded histories (trade events, orders) and cheap “no-op” behavior when features are unused.

## What to read

- Investigation write-up (tracked):
  - `docs/investigations/2026-01-13_RENDER_OOM_TRADIER_AND_SMART_LIMIT.md`
- SMART_LIMIT test runbook (tracked):
  - `docs/SMART_LIMIT_LIVE_TESTING.md`
- Release/deploy pitfalls + PyPI propagation checks (tracked):
  - `docs/DEPLOYMENT.md`

## Validation notes

- Unit/regression tests exist for SMART_LIMIT behavior and for the “active order fast path” to prevent scanning full
  order history in the background loop.
- Real-broker smoke tests exist for Alpaca and Tradier (`pytest -m apitest`), but are intentionally opt-in and may
  skip depending on credentials and market hours.

