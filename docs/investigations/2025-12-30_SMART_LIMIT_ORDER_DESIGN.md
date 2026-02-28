# SMART_LIMIT Order Type — Design, Research, and Implementation Checklist

Status: implementation in progress (tracking decisions + progress)
Last updated: 2025-12-30

This document is the working design spec for a new **SMART_LIMIT** order type in LumiBot.
It captures:
- Why we’re adding it
- The research behind the design (industry comparisons)
- How it should behave in **live** vs **backtesting**
- How it must serialize for **Botspot/Bot Manager progress** and **LumiWealth cloud updates**
- The engineering plan, test plan, and documentation plan
- A temporary checklist to execute against (remove checklist later; keep the rest of the doc)

---

## Problem Statement (Why this exists)

We’ve reached a point where “market order fills” being modeled as crossing the spread (buy at ask, sell at bid)
is both:
- **More realistic** (especially for options), and
- **Materially worse** for backtest performance for strategies that relied on optimistic fills.

That’s not a bug — it’s the spread cost being surfaced.

But real traders rarely use market orders for options (and often not for spreads). Instead they:
- place a limit order around the midpoint, and
- “walk” the limit price toward the bid/ask until they fill or time out.

We need to provide an ergonomic, broker-agnostic way to model and execute this behavior:
- so strategies can be written with realistic execution in mind,
- and so backtests can remain broker-like and not “mid-price fantasy” unless explicitly chosen.

In short: **SMART_LIMIT is a first-class execution primitive**.

---

## Goals

### Functional goals

1) Add `OrderType.SMART_LIMIT` that is **capability-driven**, not asset-type-driven:
   - Works for **any asset** where the active broker/data source can provide **bid/ask** and can submit/modify/cancel **limit orders**.
   - When bid/ask is unavailable for a given asset/time (data gaps, provider limitations), SMART_LIMIT must **degrade gracefully** (do not crash) with an explicit warning that execution realism is reduced.

**Note:** SMART_LIMIT is asset-agnostic by design. If bid/ask exists, it works — no asset-type whitelist.

2) Align behavior with industry expectations (Option Alpha “SmartPricing” / Schwab “Walk Limit” concept):
   - start near mid
   - cancel/replace (or modify) through a sequence of prices
   - bound aggressiveness via a “Final Price” rule
   - time out and cancel by default

3) Work in both environments:
   - **Live trading**: “real” timed repricing using broker quotes
   - **Backtesting**: best-effort simulation consistent with available historical data

4) Preserve accuracy:
   - execution should never be “better than possible”
   - multi-leg should be treated as a package (net price), not silently “legging” unless explicitly chosen

5) Preserve reliability:
   - no crashes when quotes are missing (degrade with explicit warnings)
   - stable serialization for cloud updates/progress

### Non-goals (for this project)

- Rebuilding the backtest engine to run at 1-second resolution globally.
- Adding a new “Strategy clock” concept. We should reuse existing scheduling infrastructure.
- Large-scale rewrites of demo strategies in `Strategy Library/Demos` (these are acceptance fixtures; only make minimal, deliberate changes when needed for correctness/testing).

---

## Constraints / Guardrails

- Never run `git checkout`.
- Backtests must use the downloader URL from your runtime environment:
  - `DATADOWNLOADER_BASE_URL=https://<your-downloader-host>:8080` (remote) or `http://localhost:8080` (local)
- Do not break:
  - legacy unit tests
  - acceptance backtests
  - Polygon backtests (even if not the focus)
- Keep backtests fast; any added work should be narrowly scoped to active orders.

---

## Terminology

### SMART_LIMIT (LumiBot)

New order type that:
- starts at/near midpoint of the spread
- repeatedly reprices the same logical order toward an allowed final price
- cancels if not filled within a timeout

### Option Alpha: SmartPricing (reference behavior)

Option Alpha documents SmartPricing as:
- a sequence of timed limit orders that traverse the bid/ask spread to a “Final Price”
- starting near the mid-price
- moving toward the bid when selling / toward the ask when buying
- cancel/replace per step
- keeping the final price working for 2 minutes then canceling

Preset timings:
- **Normal**: up to 4 prices, 10 seconds each
- **Fast**: up to 3 prices, 5 seconds each
- **Patient**: up to 5 prices, 20 seconds each

References:
- https://optionalpha.com/help/smartpricing
- https://optionalpha.com/blog/new-smart-pricing-options-to-improve-trade-fill-prices

### Schwab: Walk Limit (conceptual sibling)

Schwab’s “Walk Limit” is the same concept: cancel/replace limit orders as price changes.

### “SOR” vs “smart execution”

True “Smart Order Routing” is usually broker/venue-side routing.
What we’re building in LumiBot is closer to **smart execution / smart pricing**,
but we may still call the order type `SMART_LIMIT` because it matches the trader mental model.

---

## How LumiBot Works Today (relevant architecture)

This is the minimum mental model needed to implement SMART_LIMIT without reinventing the platform.

### Order creation and submission

- `Strategy.create_order(...)` creates an `Order` object.
  - file: `lumibot/strategies/strategy.py`
- `Strategy.submit_order(...)` sends orders to `broker.submit_order(...)` or `broker.submit_orders(...)`.
  - file: `lumibot/strategies/strategy.py`
- Live brokers submit through a broker-side orders thread (`Broker._orders_thread`).
  - file: `lumibot/brokers/broker.py`

### Live “secondary clock” exists already

In live trading:
- `StrategyExecutor.check_queue()` runs in a background thread and ticks ~every 0.5s today.
  - file: `lumibot/strategies/strategy_executor.py`
- This is already a separate loop from `on_trading_iteration`, and is the correct place to run an
  “execution manager” that reprices working SMART_LIMIT orders.

Important: SMART_LIMIT must not depend on the check_queue sleep duration staying constant.
It should use `time.monotonic()` scheduling and only act when `now >= next_action_at`.

### Backtesting order evaluation loop exists

In backtesting:
- `BacktestingBroker.process_pending_orders()` is called every bar.
  - file: `lumibot/backtesting/backtesting_broker.py`
- This function already handles:
  - order fills based on OHLC
  - quote-based fallback fills (used for ThetaData option realism)
  - trailing-stop updates per bar

This is the correct place to add SMART_LIMIT simulation for backtesting.

### Multi-leg in LumiBot (current state)

Live:
- Some brokers accept multi-leg “combo” orders (e.g., Tradier `_submit_multileg_order`).
  - file: `lumibot/brokers/tradier.py`
- Brokers may support modify/replace for existing orders:
  - `Broker.modify_order()` exists (broker-specific behavior).
  - file: `lumibot/brokers/broker.py`

Backtesting:
- Multi-leg orders are currently represented as:
  - individual child orders (legs), and
  - a synthetic parent order used for tracking.
  - file: `lumibot/backtesting/backtesting_broker.py` (`_submit_orders(is_multileg=True)` creates parent order)

If SMART_LIMIT is supposed to be accurate for multi-leg, we likely need to simulate it at the **package (net)**
level in backtesting, not as independent leg fills.

---

## Serialization Requirements (Botspot progress + LumiWealth cloud)

SMART_LIMIT parameters must serialize through:

1) Progress logging / DynamoDB payloads:
   - StrategyExecutor calls `Order.to_minimal_dict()` for progress updates.
   - file: `lumibot/entities/order.py` (`to_minimal_dict`)
   - We may need to add minimal SMART_LIMIT fields there (preset + current step maybe).

2) LumiWealth cloud portfolio updates:
   - `Strategy.send_update_to_cloud()` uses `[order.to_dict() for order in orders]`.
   - file: `lumibot/strategies/_strategy.py` (`send_update_to_cloud`)
   - `Order.to_dict()` includes any field that is:
     - not underscore-prefixed
     - JSON-serializable, or
     - has a `to_dict()` method.
   - file: `lumibot/entities/order.py` (`to_dict`)

Implication:
- SMART_LIMIT config should be a **real object** with:
  - `to_dict()` returning primitives only
  - `from_dict()` to reconstruct (optional but recommended)
- And we must keep payload size reasonable (avoid embedding large internal state).

---

## Proposed Public API (typed; no “mystery dicts”)

### Order type

- Add `Order.OrderType.SMART_LIMIT = "smart_limit"`.
  - file: `lumibot/entities/order.py`

### Presets (mirror Option Alpha naming)

Option Alpha uses:
- `FAST`, `NORMAL`, `PATIENT`

We should match this exact naming to:
- align trader expectations
- make it easy to translate OA → LumiBot usage

### SmartLimitConfig object

We need more than just a preset in practice:
- final-price behavior matters
- timeout behavior matters
- rounding behavior matters
- multi-leg net pricing depends on “credit vs debit”

But we should keep v1 small and default-heavy.

Proposed shape:

- `SmartLimitPreset` (Enum): FAST, NORMAL, PATIENT
- `SmartLimitTimeoutBehavior` (Enum):
  - CANCEL (default; OA-style)
  - optional future: FALLBACK_TO_MARKET (not default)
- `SmartLimitFinalPriceRule` (Enum + typed params):
  - PERCENT_OF_SPREAD (default OA = 100%)
  - DOLLARS_FROM_MID (optional)
  - TYPICAL_SLIPPAGE_CAP (optional, can be implemented later)
- `SmartLimitConfig` (dataclass-like):
  - `preset: SmartLimitPreset`
  - `final_price_rules: list[SmartLimitFinalPriceRuleSpec]` (optional; default 100% spread)
  - `timeout_behavior: SmartLimitTimeoutBehavior = CANCEL`
  - `final_hold_seconds: int = 120` (OA)
  - `max_total_seconds: int | None` (optional; defaults from preset + final_hold)
  - `rounding: SmartLimitRoundingPolicy` (see rounding section)

`Strategy.create_order(..., smart_limit=...)` should accept:
- `smart_limit=None` (default)
- `smart_limit=SmartLimitPreset.NORMAL` (simple)
- `smart_limit=SmartLimitConfig(...)` (advanced)

The `Order` stores:
- `order_type = SMART_LIMIT`
- a `smart_limit` attribute holding a **SmartLimitConfig** instance.

Serialization:
- `SmartLimitConfig.to_dict()` and `SmartLimitConfig.from_dict()`.

---

## Slippage (Backtesting) — Option Alpha parity and LumiBot plan

### What Option Alpha does (important benchmark)

Option Alpha’s backtester (0DTE / next-day) uses 1-minute historical data and generally models fills as:
- **mid-price fills with user-specified “slippage from mid”**
- not a true order-book / per-second microstructure simulation

Their own content emphasizes that slippage is a configurable assumption used to stress test realism.
They also pre-fill “typical” slippage values for some underlyings in their UI (e.g., small cents values),
but it is still an explicit user setting (not dynamically derived from the spread each trade).

### LumiBot plan (piggyback on the TradingFee pattern, but do NOT misuse TradingFee)

We already have a clean pattern for “execution cost configuration” via:
- `buy_trading_fees` / `sell_trading_fees` passed into `Strategy.backtest(...)`
- applied in the backtesting broker via `calculate_trade_cost(...)`

We should add an analogous, typed, explicitly-named mechanism for **slippage** rather than overloading fees:

- New entity: `TradingSlippage` (or similar name)
  - must be strongly typed (object), with `to_dict()` for serialization
  - intended for backtesting fill modeling (not live)
  - supports at least “dollars from mid” to match OA

- New strategy/backtest parameters (mirroring TradingFee UX):
  - `buy_trading_slippages` / `sell_trading_slippages`
  - default is zero slippage when unset

How it integrates with SMART_LIMIT:
- SMART_LIMIT backtesting default fill model is **mid ± slippage_from_mid** (single-leg or net mid for multi-leg),
  constrained by SMART_LIMIT “Final Price” rules.
- If the order provides explicit slippage settings, use them.
- Otherwise, use the strategy-level defaults (`buy_trading_slippages` / `sell_trading_slippages`).

Why this is better than forcing it into TradingFee:
- slippage changes the effective execution price (and therefore cost basis and PnL shape)
- fees are a separate cash debit and should remain separate
- mixing the two makes results harder to reason about and breaks expectations

### Slippage math (explicit; to be documented in public docs)

For a single-leg order with bid/ask:
- `mid = (bid + ask) / 2`
- Buy fill target: `mid + slippage_amount`
- Sell fill target: `mid - slippage_amount`

For a multi-leg package:
- compute **net bid**, **net ask**, **net mid** from legs
- apply slippage to the **net mid** (not per-leg)

The slippage amount is **absolute price units** (e.g., $0.05), not a percent,
to match Option Alpha’s “dollars from mid” model.

Trade logging:
- Backtest trade logs include a `trade_slippage` column (CSV + HTML tooltips),
  alongside existing `trade_cost`/fees.

---

## Execution Model (Live)

SMART_LIMIT live behavior should mimic OA:
- create a limit order at a first price (near mid)
- if not filled after step duration, cancel/replace at next price
- stop at final price and keep it working for 2 minutes
- cancel if still unfilled (default)

### Where it runs

Do not change strategy cadence. Use the existing background loop:
- `StrategyExecutor.check_queue()` is a continuous loop used in live.
  - It currently sleeps `0.5s` between iterations.
  - SMART_LIMIT should not depend on this sleep duration.

We should implement an internal **execution manager** that:
- is called from the check_queue loop
- scans for active SMART_LIMIT orders
- advances their state machine when time thresholds are reached

### Timekeeping

Use `time.monotonic()` for scheduling:
- Each SMART_LIMIT order tracks:
  - `next_action_at` (monotonic seconds)
  - `step_index`
  - `ladder_prices` (or a generator seed)

### Broker interactions

Prefer modify/replace when broker supports it:
- `Broker.modify_order(order, limit_price=...)`

Fallback:
- cancel + submit a new order (must maintain a “logical order id” in LumiBot)

Important: multi-leg support depends on broker capabilities:
- Multi-leg vs legging is a **broker capability** question (and exists today independent of SMART_LIMIT).
- SMART_LIMIT should follow the existing multi-leg pathway:
  - If the broker supports true combo orders, SMART_LIMIT reprices the **package/net** limit.
  - If the broker does not support combos for that instrument, LumiBot may be forced to “leg” — that must emit a **loud warning** because the execution semantics differ from a package fill.

---

## Execution Model (Backtesting)

Backtesting must be broker-like but is constrained by data resolution.

### The core reality

If we only have 1-minute bars/quotes, we cannot know the true intra-minute fill timing.
No engine can “perfectly” simulate 5s/10s/20s repricing without higher-resolution historical data.

Industry norm:
- Approximate execution using:
  - bar-high/low crossings (for limit/stop fills), and/or
  - slippage-from-mid caps (Option Alpha style).

### What we can do in LumiBot today

We already support:
- limit orders remaining open across bars
- quote-based fills (for ThetaData options)
- cancel/replace logic in backtesting (conceptually; modify emits events)

SMART_LIMIT backtest plan:
- Represent SMART_LIMIT as an order that periodically updates its `limit_price` based on:
  - current quote bid/ask (preferred), else
  - last trade / bar OHLC (fallback)
- On each backtest bar:
  1) compute target limit price for current step
  2) update order’s working limit
  3) check fill conditions using the same rules we use for limit orders

Critical principle: **Backtesting must mimic live broker behavior as closely as possible.**
We should keep the **Option Alpha step schedule** (5s/10s/20s + 120s final hold) as the model target.
Given minute data constraints, we will simulate that schedule in a way that:
- stays deterministic,
- allows **fills inside the spread** (because that happens in real markets),
- and does not explode runtime (only active SMART_LIMIT orders incur extra work).

### Inside-the-spread fills (required)

Real-life limit orders often fill inside the spread (especially in liquid names), so SMART_LIMIT backtesting must allow it.

Practical modeling options (to finalize during implementation):
1) **Mid + slippage model (Option Alpha-style)**:
   - Compute mid from bid/ask (or net mid for multi-leg).
   - Apply a configurable slippage allowance (or derive one from spread/typical slippage).
   - Mark the SMART_LIMIT attempt filled at the first ladder step that reaches that “expected fill price”.
   - If the “expected fill price” exceeds the configured Final Price bound, the order does not fill and times out/cancels.
   - This is fast, deterministic, and matches how many platforms handle 1-minute backtests.
2) **Trade-print crossing model (when trade-last exists)**:
   - If the data source provides a trade-last for the bar timestamp (ThetaData often does), consider a limit filled
     when trade-last crosses the limit (buy: trade_last <= limit; sell: trade_last >= limit).
   - This allows inside-spread fills without assuming “always fills at mid”.

We should pick one primary model (and document it) rather than mixing ad-hoc rules.

### Non-quote data sources (fallback)

SMART_LIMIT requires bid/ask to be meaningful. When bid/ask is unavailable:
- We **degrade gracefully** (no crash).
- SMART_LIMIT **downgrades to Market** and emits a warning explaining why.
- This can affect Polygon backtests before ~2020 (limited quote history).

### Multi-leg backtesting (critical)

We should simulate SMART_LIMIT multi-leg as a **net (package) limit order**:
- compute net bid/ask/mid from leg quotes:
  - buy leg uses ask for worst-case pay
  - sell leg uses bid for worst-case receive
- walk the net price ladder
- fill all legs “atomically” at the same simulated timestamp when net fill occurs

This likely requires refactoring existing “multileg parent + independent child fills” behavior
inside the backtesting broker so SMART_LIMIT combos do not silently leg.

---

## Final Price Rules (Option Alpha parity)

Option Alpha’s default final price is:
- 100% of the bid/ask spread (i.e., allow walking to the bid for sells / ask for buys)

They also support multiple final-price caps and choose the “best” (least aggressive) cap.
We can implement parity incrementally:

Phase 1 (must):
- `PERCENT_OF_SPREAD = 100%` default

Phase 2 (nice to have):
- allow selecting multiple caps (percent spread, $ from mid) and choose best

---

## Tick Size / Rounding (don’t hardcode; infer + fallback)

This is mandatory for correctness; otherwise brokers reject orders.

### Options tick sizes (overview)

- Many options are “penny eligible”, but not all.
- Tick sizes can depend on whether the class is in the penny program and the premium level.
- For SPX specifically, Cboe specs indicate:
  - under 3.00 → 0.05
  - otherwise → 0.10
  - and complex net pricing often has additional constraints

References:
- Cboe SPX specs: https://www.cboe.com/tradable_products/sp_500/spx_options/specifications
- Options Education (penny increments): https://www.optionseducation.org/news/penny-increments

### Strategy for LumiBot

1) **Infer tick increment from observed quotes when available**:
   - If bid/ask end in .05 increments, use 0.05
   - If bid/ask end in .10 increments, use 0.10
   - If bid/ask appear penny, use 0.01
   This works for both:
   - SPX-family
   - non-penny stock options
   - many other cases (including “stocks that trade in nickels” situations)

2) **Fallback rules when we cannot infer**:
   - Options: allow broker conformance to round/reject; we can also add a small static rule set for SPX/RUT/VIX/NDX family to be safe.
   - Futures: use contract tick size where available (some brokers already fetch tick sizes for futures).
   - Stocks: generally 0.01, but allow quote inference if quoting increments differ.

3) **Package rounding (multi-leg)**:
   - net limit price must respect package tick size constraints
   - in backtesting, we must replicate the constraint to avoid optimistic fills

---

## Missing Quotes / Data Gaps (degrade, don’t crash)

When bid/ask quotes are missing for an asset:
- SMART_LIMIT should degrade gracefully and emit an explicit warning, not crash.
- Default downgrade is **Market** (do not synthesize mid from OHLC).

The key is:
- do not silently pretend we have quotes
- do not crash the strategy/backtest
- surface the limitation in logs and (optionally) in progress payloads

---

## Observability / Progress Reporting

We need SMART_LIMIT to be debuggable:
- log transitions (created → step advanced → final hold → canceled/filled)
- but do not spam per-bar logs by default

Note:
- We explicitly do **not** want any “log max per second” dropping logic.
- Logging should remain opt-in / level-driven.

For Botspot/Bot Manager:
- consider adding a small “execution status” field to the progress payload:
  - current step
  - final price rule used
  - current working limit price
This should be minimal to keep payload size under control.

---

## Documentation Plan (two layers)

### Layer 1: Human docs (`docs/`) — this document + future updates

- This document lives in `docs/investigations/` and should remain after implementation.
- Update `docs/BACKTESTING_ARCHITECTURE.md` to reference SMART_LIMIT once implemented.
- Create a focused “how execution is simulated” section for SMART_LIMIT and how it differs from MARKET/ LIMIT.

### Layer 2: Public docs (`docsrc/`) + docstrings

We must update:
- `Strategy.create_order()` docstring:
  - new `smart_limit` argument
  - examples for simple preset and advanced config
- `OrderType` docs:
  - describe SMART_LIMIT
  - describe default cancel behavior
  - describe multi-leg semantics

Sphinx updates:
- add a dedicated page in `docsrc/` explaining SMART_LIMIT
- add a small mention on `docsrc/index.rst` (not “loud”, but present)

---

## Testing Plan

### Unit tests (fast)

1) Serialization:
   - `SmartLimitConfig.to_dict()` produces primitives only
   - `Order.to_dict()` includes smart_limit config without errors
   - `send_update_to_cloud()` payload building does not choke on SMART_LIMIT orders

2) Ladder generation:
   - preset produces correct number of steps
   - correct direction for buy vs sell
   - final price obeys cap rules
   - rounding is applied correctly

3) Multi-leg net price:
   - compute net bid/ask/mid deterministically from leg quotes
   - enforce net tick rounding

### Backtesting integration tests (deterministic)

Using synthetic quotes/bars:
- MARKET (bid/ask cross) vs SMART_LIMIT vs plain LIMIT
- SMART_LIMIT:
  - does not fill outside possible prices
  - cancels on timeout
  - respects final price bounds

Multi-leg:
- fills legs atomically at net price
- does not “leg” unless explicitly configured (if we add that mode)

### Acceptance backtests

Run the known ThetaData acceptance suite (existing process in `docs/handoffs/...`).
Pay special attention to strategies that trade:
- options frequently
- index options
- multi-leg positions

Legacy backtests run everything. If a backtest has been in the suite for >1 year, treat it as high‑priority and do not skip it.

---

## Open Questions / Decisions (to resolve before implementation)

1) Broker capability inventory:
   - Confirm per-broker modify/replace support for limit orders.
   - Document any brokers that require cancel+new for repricing.

2) Tick size inference:
   - Are we comfortable with quote-inference as the primary method?
   - How do we handle assets where quotes are stale / synthetic?

---

## Checklist (temporary; remove later)

### Planning / research
- [x] Confirm Option Alpha SmartPricing behavior details (presets, final hold, final price defaults).
- [x] Confirm Option Alpha backtester slippage behavior (mid fills + user-defined slippage; no book simulation).
- [ ] Inventory LumiBot brokers we support and confirm multi-leg combo + modify/replace capabilities where applicable.
- [x] Document SMART_LIMIT backtest fill model (mid + slippage).
- [x] Decide tick-size rounding rules (quote inference + fallback rules).

### Core implementation
- [x] Add `OrderType.SMART_LIMIT` (`lumibot/entities/order.py`).
- [x] Add `SmartLimitPreset` enum and `SmartLimitConfig` object with `to_dict()`/`from_dict()`.
- [x] Extend `Strategy.create_order()` to accept `smart_limit=` and populate the Order.
- [x] Ensure `Order.to_dict()` includes smart_limit cleanly and remains small.
- [x] Ensure `Order.to_minimal_dict()` includes minimal SMART_LIMIT status for progress reporting (optional).
- [x] Add `TradingSlippage` and strategy-level defaults (`buy_trading_slippages` / `sell_trading_slippages`).
- [x] Log `trade_slippage` in trades CSV/HTML alongside fees.

### Live execution
- [x] Add a live execution manager that runs in `StrategyExecutor.check_queue()` loop.
- [x] Implement cancel/replace / modify path with monotonic scheduling (`next_action_at`).
- [x] Implement multi-leg package repricing for brokers that support multileg combos.
- [x] Degrade gracefully with warnings when quotes are missing.

### Backtesting execution
- [x] Implement SMART_LIMIT state machine in `BacktestingBroker.process_pending_orders()`.
- [x] Implement multi-leg net price simulation (atomic fills) for SMART_LIMIT combos.
- [x] Ensure fills remain broker-like and do not create lookahead leaks to user strategy APIs.
- [x] Gracefully stop when backtest end date exceeds available trading days (no infinite loop).

### Tests
- [x] Add unit tests for serialization, rounding, ladder generation.
- [x] Add integration tests for SMART_LIMIT behavior in backtesting (single-leg + multi-leg).
- [x] Add tests for downgrade-to-market when quotes are missing.
- [x] Add tests for slippage application (buy/sell + multi-leg net).
- [x] Reinforce non-SMART_LIMIT multi-leg behavior remains unchanged.
- [x] Add tests for future end-date termination (no freeze).
- [x] Run targeted existing tests and acceptance backtests; confirm no regressions.

### Docs
- [x] Update docstrings for `create_order()` and `OrderType` with SMART_LIMIT usage examples.
- [x] Add a public docs page under `docsrc/` describing SMART_LIMIT.
- [x] Add a small mention on `docsrc/index.rst` (not “loud”).
- [x] Add a docs page describing slippage (or fold into SMART_LIMIT docs if brief).
- [x] Update `docs/BACKTESTING_ARCHITECTURE.md` to reference SMART_LIMIT simulation once implemented.
