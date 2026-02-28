# Render OOM: Tradier + SMART_LIMIT background loop (2026-01-13)

This doc captures the investigation and remediation plan for repeated OOM crashes reported by a client running a
long-lived LumiBot live strategy on Render.

**Incident class:** long-running live worker, memory growth / allocation churn, Render restarts with “Ran out of memory”.

---

## Symptoms

- Render service repeatedly restarts with an OOM message (example plan: 2GB RAM, also reproduced at 512MB).
- Strategy workload is light (runs once per day), yet memory climbs rapidly and/or in spikes until the container is
  killed and restarted.
- Logs show restarts and repeated lifecycle calls (initialize/before_market_opens/before_starting_trading).

---

## Key findings (root causes / contributors)

### 1) Tradier polling + large historical order/trade state

Accounts with long Tradier histories can carry:
- large closed order lists (broker “all orders” endpoints)
- many historical fills / broker trade events

In a long-running worker, repeatedly materializing and processing large historical collections can create:
- high temporary allocations per poll cycle (pandas copies / list-to-dict expansion)
- unbounded in-memory histories (if not bounded)

Mitigation work that landed previously (see release notes for `4.4.32`):
- Treat active order status values as equivalent across brokers (`submitted/open/new`) to avoid repeated “new” events.
- Bound in-memory trade-event history used for live monitoring so it cannot grow without limit.
- Tradier: avoid expensive DataFrame copy chains; avoid ingesting large historical closed orders on first poll.

### 2) SMART_LIMIT processing loop causing memory growth even with “no trading”

LumiBot runs a lightweight background loop in live mode (`StrategyExecutor.check_queue`) that:
- drains the event queue
- runs SMART_LIMIT processing periodically

Even if a strategy never submits SMART_LIMIT orders, the executor previously scanned **all tracked orders** every tick
to locate SMART_LIMIT orders.

On accounts with large tracked-order histories, this “scan everything” behavior can create significant allocation churn
and increasing RSS (especially on constrained platforms like Render), despite having zero SMART_LIMIT orders.

**Fix (landed on version branch for the next release):**
- SMART_LIMIT processing now uses the broker’s active-order fast path (`get_active_tracked_orders`) when available,
  instead of scanning full tracked-order history.
- Fallback behavior remains for brokers without the fast path.

Why this matters:
- Active orders are typically a small set.
- Historical orders can be arbitrarily large.
- The SMART_LIMIT loop runs frequently (sub-second), so O(N) scans over large N are costly even if “no trading happens”.

---

## How to reproduce locally (safe)

Goal: reproduce memory churn without placing real trades.

### A) Minimal reproduction: SMART_LIMIT loop + large tracked order history

1) Use a broker account with a long historical order list (or synthesize orders in a unit/integration harness).
2) Start a live strategy that runs but does not trade.
3) Observe RSS while SMART_LIMIT processing is enabled.
4) Compare with a run where SMART_LIMIT processing is effectively a no-op.

Expected pre-fix behavior:
- RSS rises significantly over time even with no SMART_LIMIT orders, due to repeated full-history scans.

Expected post-fix behavior:
- SMART_LIMIT loop quickly returns (active-order scan is small); RSS should stabilize.

### B) Real-broker smoke checks (integration)

LumiBot includes `pytest.mark.apitest` broker smoke tests:
- Alpaca: submit+cancel a tiny limit order (should not fill).
- Tradier: paper balances/positions/orders; optional live submit+cancel (requires explicit live config).

These validate “does this still work with real brokers” after changes, but they are not a long-duration RSS soak test.

---

## Operational guidance

### Render

- Render kills the process on memory limit; your service will restart and may appear “healthy” between OOMs.
- Memory growth that is “allocation churn” can look like spikes that never return to baseline (fragmentation / RSS).
- Focus on: eliminating background loops that scan large histories, bounding in-memory histories, and avoiding repeated
  pandas deep copies in live code paths.

### What to log / observe

Always-on (cheap) telemetry should include:
- process RSS
- number of active tracked orders
- number of total tracked orders (if available cheaply)
- trade-event log length (if maintained)

When chasing OOMs, a “periodic heartbeat” line is often enough to correlate memory spikes with broker polling windows
and/or background loops.

---

## What changed / where (pointers)

- SMART_LIMIT background loop is invoked from:
  - `lumibot/strategies/strategy_executor.py` → `StrategyExecutor.check_queue()`
- SMART_LIMIT processing function:
  - `lumibot/strategies/strategy_executor.py` → `_process_smart_limit_orders()`
- The memory fix is to use:
  - `broker.get_active_tracked_orders(...)` when available.

---

## Lessons learned / pitfalls

- “No trading activity” does not imply “no work”: background maintenance loops can still be hot.
- OOMs on small instances are often caused by **repeated allocation churn** rather than a single huge object.
- Anything that runs sub-second must avoid scanning collections that can grow with account history.
- Prefer “active set” APIs for loops; keep “full history” APIs for human-visible reporting endpoints only.
- For long-running live systems, always bound in-memory histories (trade events, order records, quote caches).

