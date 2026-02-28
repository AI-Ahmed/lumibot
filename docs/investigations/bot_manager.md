# Seconds‑Level Backtesting Design (Futures First) — Notes + Game Plan

Last Updated: 2026-01-28
Repo: `/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot`
Target version branch: `version/4.4.42`

This document is a design + execution plan for adding **true 1‑second backtesting**.

## 0) Problem statement

We want strategies to be able to:
- run on a 1-second cadence (e.g., `sleeptime="1S"`)
- request second bars (`get_historical_prices(..., timestep="second")`)
- get correct fills/mark-to-market based on second bars
- remain fast enough that seconds mode is usable (warm-cache, router-mode)

## 1) Definitions

### 1.1 True seconds-mode

“True seconds-mode” means:
- the strategy loop and broker simulation are driven by second timestamps
- the dataset’s actual timestamps control time advancement (event-driven)
- no “fill magnifier only” shortcuts

### 1.2 Warm-cache invariant

A warm-cache rerun must:
- perform **zero** downloader queue submissions
- perform **zero** per-iteration history fetches
- reuse identical cache keys given identical inputs

## 2) Scope

### 2.1 Phase 1 scope (must ship)

- Futures seconds bars support (data plane + cache).
- Seconds clock + iteration.
- Correct order evaluation and mark-to-market.

### 2.2 Phase 2 scope

- IBKR crypto seconds bars.

### 2.3 Out of scope (until later)

- Options seconds bars.
- Stocks seconds bars.
- Forex seconds bars.

## 3) Data source realities

Important constraint:
- IBKR’s historical seconds bars are often rate-limited and may not be feasible for multi-day backtests.

Recommended approach:
- Use a market data provider for seconds bars (e.g., DataBento) via the downloader + cache.
- Preserve IBKR execution semantics in the broker simulation.

## 4) API surface (what users call)

We need a stable, documented timestep naming:
- Canonical: `"second"`
- Aliases: `"1s"`, `"1S"`

We must document support per data source.

## 5) Router-mode requirements

Router-mode must treat seconds bars the same way it treats minute/day bars:
- first request triggers prefetch of the required window (start-warmup → end)
- subsequent calls slice in-memory

## 6) Cache design for seconds bars

Key principles:
- fewer, larger objects (avoid tens of thousands of tiny files)
- stable cache keys (include symbol, expiration, exchange routing, timeframe, start/end bounds)
- store timestamps as int64 for fast slicing

Suggested formats:
- parquet/arrow on disk
- in-memory arrays in the hot loop

## 7) Engine clock design

### 7.1 Event-driven timestamps

We must iterate over timestamps that exist in the data.

Options for multi-asset strategies:
- Union clock: iterate over union of timestamps across subscribed series.
- Primary clock: pick one “clock series” and align others by last-known price.

We must pick one rule and make it explicit.

### 7.2 Gaps and closed sessions

- Do not synthesize bars.
- Orders remain pending during gaps.
- Mark-to-market can use last known price, but it must be explicit and stable.

## 8) Fill semantics at 1s OHLC

We must define and test:
- market orders
- limit orders
- stop orders
- bracket orders

## 9) Performance plan (recovering the 60×)

### 9.1 First-order wins

- Keep seconds bars in memory as arrays.
- Avoid pandas object churn per bar.
- Avoid repeated datetime conversions.

### 9.2 Second-order wins

- Cache repeated history slices for common lookbacks.
- Optimize order/position bookkeeping hotspots.

### 9.3 Required profiling loop

- Baseline (cold + warm)
- YAPPI attribution
- One change
- Tests
- Repeat

## 10) Testing plan

Must add tests for:
- timestep normalization (`second` aliases)
- router prefetch-once invariants (call-count tests)
- warm-cache queue-free invariants
- deterministic replay on a fixed small dataset
- calendar spreads at seconds resolution (regression for “ghost PnL” class)

## 11) BotManager / deployment implications

Seconds-mode may increase runtime and cost.

We need BotManager support for:
- selecting timeframe (minute vs second)
- enforcing guardrails (max window in seconds, warnings)
- surfacing data provider limitations clearly

## 12) Deliverables checklist

Phase 1 (futures seconds):
- [ ] seconds bars obtainable via downloader + cache
- [ ] router uses prefetch once + slice
- [ ] strategy runs with `sleeptime="1S"`
- [ ] fills correct and deterministic
- [ ] warm-cache rerun queue-free
- [ ] benchmarks + speed ledger updated

---

## 13) Architecture sketch (end-to-end)

The conceptual flow we want (router-mode, prod-like):

```
Strategy (sleeptime=1S)
  |
  |  get_historical_prices(asset, lookback, timestep="second")
  v
Router (BACKTESTING_DATA_SOURCE)
  |
  |  chooses datasource for (asset_type=future/cont_future/crypto/...)
  v
Datasource (seconds-aware)
  |
  |  prefetch(start - warmup, end) ONCE per series key
  |  store in local + S3 cache (if enabled)
  v
In-memory series store
  |
  |  slice-by-index (cheap)
  v
Bars object returned to Strategy
```

Key invariant:
- After the first prefetch, every subsequent history call is a **slice** (no queue, no disk reload, no pandas rebuild).

---

## 14) Data model requirements (seconds bars)

We need a single “bars model” that is efficient and consistent:

- Timestamp representation:
  - Canonical internal representation should be integer epoch (ns or s).
  - Conversions to `pd.DatetimeIndex` should happen once per dataset, not per slice.

- Column set (minimum):
  - `open`, `high`, `low`, `close`, `volume`
  - optionally: `trade_count`, `vwap` (if provider supplies), but keep the core path stable.

- Timezone:
  - Canonicalize to UTC internally.
  - Emit tz-aware timestamps consistently to avoid strategy-side `pd.to_datetime` thrash.

---

## 15) Cache layout + keying (seconds)

Seconds caches must be designed to avoid:
- millions of tiny objects
- key churn (warm never becomes warm)

Requirements:
- Keys MUST include:
  - asset identity (symbol + expiration for futures)
  - asset_type (FUTURE vs CONT_FUTURE)
  - exchange/source routing inputs (anything that can change what data means)
  - timeframe (canonical timestep string)
  - start/end bounds

Strong preference:
- fewer, larger parquet objects with partitioning by:
  - symbol
  - timeframe
  - date (daily partitions)

Reason:
- daily partitions allow:
  - efficient prefetch for longer windows
  - S3 read parallelism without tiny-object explosion

---

## 16) Clock semantics (the biggest correctness/perf lever)

### 16.1 Do not brute-force step missing time

For futures, there are maintenance windows and gaps. If you brute-force iterate all seconds between start and end,
you will:
- waste time on missing seconds
- tempt yourself to synthesize bars (forbidden)

### 16.2 Recommended default for v1: “primary clock series”

For Phase 1 (futures-only), the simplest robust rule is:
- Choose one “clock series” (the primary traded future).
- Advance time on that series’ timestamps.
- For any other series, use the last-known bar <= current timestamp (for mark-to-market only; no synthetic fills).

If/when we support multi-asset seconds strategies (futures + crypto), we may move to:
- union clock, or a configurable clock

### 16.3 Strategy scheduling

We must define:
- What does `sleeptime="1S"` mean in event-driven mode?
  - Answer: “evaluate at every timestamp in the clock series” (not every integer second).

---

## 17) Fills at seconds resolution (rules we must codify)

We need deterministic “bar-based” rules. Examples (must align with existing semantics where possible):

- Market order:
  - fills on the next actionable bar (open or close—choose and document)

- Limit order:
  - fills if the bar trades through the limit price:
    - BUY limit fills if `low <= limit`
    - SELL limit fills if `high >= limit`
  - price selection rule (limit price vs bar open) must be deterministic

- Stop order:
  - triggers if bar trades through stop price:
    - BUY stop triggers if `high >= stop`
    - SELL stop triggers if `low <= stop`

- Bracket order:
  - if both stop and take-profit could trigger in the same bar, define ordering and price selection rules

These rules must be tested because seconds mode makes “same-bar ambiguity” much more frequent.

---

## 18) Performance plan (explicit: how we get back ~60×)

We treat this like a “speed debt payoff” project:

### 18.1 First-order (must do)

- Store bars once, slice forever:
  - one in-memory store per series
  - integer index offsets for lookbacks

- Avoid per-iteration pandas:
  - strategies may use pandas internally, but the engine must not force it per call

- Avoid per-iteration datetime conversions:
  - timestamp conversion should be precomputed at prefetch time

### 18.2 Second-order (after it works)

- Slice cache:
  - if a strategy repeatedly asks for the same `lookback_bars`, cache the last N slices (LRU) keyed by `(lookback, current_index)`

- Order pipeline optimization:
  - profile hotspots in:
    - pending order scanning
    - bracket management
    - position lookups

### 18.3 Evidence requirements

For each perf change:
- one benchmark row (median-of-3) on:
  - a 1-day seconds run (fast iteration)
  - a 1-week seconds run (realistic)
- one YAPPI capture on the slowest benchmark
- one unit test guarding the invariant

---

## 19) Benchmark suite for seconds work (minimum)

We need three categories:

1) “No-op seconds” strategy
   - Does nothing each tick besides request a small lookback slice.
   - Purpose: measure engine overhead.

2) “Order-heavy seconds” strategy
   - Places a predictable pattern of limit/stop/bracket orders.
   - Purpose: validate fills + order pipeline performance.

3) Real customer strategies (representative)
   - Futures (NQ/GC/CL) strategies that caused issues historically.

Windows:
- 1 day (iteration)
- 1 week (gate)

---

## 20) Tests to add (minimum set)

### 20.1 Timestep normalization tests

- `second`, `1s`, `1S` normalize to canonical.

### 20.2 Prefetch-once / slice-forever tests

- repeated `get_historical_prices(..., timestep=second)` in a loop triggers only one underlying fetch.

### 20.3 Warm-cache queue-free tests

- rerun with warm cache must submit 0 queue jobs.

### 20.4 Calendar spread seconds regression

- two legs, same root, different expiration:
  - no ledger collisions
  - portfolio_value behaves smoothly (no “ghost spikes”)

---

## 21) BotManager implications (why this doc is named this way)

Seconds-mode changes operational characteristics:
- runtime increases
- cache size increases
- downloader load increases

BotManager should eventually support:
- explicit selection of timestep (“minute” vs “second”)
- guardrails:
  - max window length for seconds unless user explicitly opts in
  - warnings in UI and logs when a run is “seconds mode”
- surfaced cost estimates:
  - approximate bars count
  - expected cache size
  - expected runtime class (small/medium/large)

This is not about removing artifacts; it’s about making the system predictable at seconds scale.

---

## 22) Future extensions (beyond futures)

After futures seconds is stable:

- Crypto seconds (IBKR):
  - likely closer to 24/7 timestamps; union/primary clock decisions matter more

- Stocks/ETFs:
  - market hours + corporate actions make correctness tricky

- Options:
  - data volume is enormous; seconds mode requires tight constraints and likely special handling

- Forex:
  - decide provider and semantics later; don’t block futures/crypto on this

