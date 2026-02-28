<!--
Session handoff for a new Codex/LLM session.
Scope: LumiBot ThetaData backtesting + production stall + prod-vs-local speed parity + acceptance backtests.
Do not delete: this file is referenced by humans when continuing work.
-->

# ThetaData Backtesting (LumiBot) — Session Handoff (2026-01-01)

This handoff captures:
- The **current state** of LumiBot ThetaData backtesting work as of **2026-01-01**
- The **production stall** affecting the SPX Short Straddle Intraday backtest
- The **production vs local speed gap** (e.g., ~1:30 local vs ~7–8m prod)
- The **acceptance backtest suite** (manual, end-to-end) and how to “officialize” it
- The **next game plan**, including adding an **opt-in YAPPI profiler** for production parity debugging

This is written to help a fresh session pick up without re-discovering the same gotchas.

---

## Quick Navigation (Start Here)

If you only read one section, read **#4.1 SPX Short Straddle Intraday stalls in production** and **#6 YAPPI profiling plan**.

**Current state summary**
- **Deployed:** LumiBot `4.4.20` is deployed and merged to `dev`.
- **Open PR for next release:** PR `#924` on branch `stall-recovery-4.4.21` (version `4.4.21`) focuses on preventing “silent hangs” by hardening ThetaData queue + cache I/O.
- **Top two goals (still open):**
  1) Fix **SPX Short Straddle Intraday** production stalls (no more “silent forever”).
  2) Explain and close **production vs local speed** gap (e.g., ~1:30 local vs ~7–8m prod).

**Read order for a new session**
1) #4.1 (prod stall) → #5 (what PR #924 changes) → #6 (YAPPI) → #7 (speed parity runbook).
2) #3 (acceptance suite) for correctness validation.
3) #8 (next session plan) as a checklist.

## Glossary (terms that caused confusion)

- `manager_bot_id`: Unique id for the Bot Manager backtest container run (used in UI and CloudWatch filter patterns).
- `backtestId`: Often equal to `manager_bot_id` in BotSpot; used by API/status endpoints.
- `download_status`: A stringified JSON payload stored in progress records; shows what the backtest is currently “waiting on” (asset/timespan/data_type/etc.).
- `request_id`: The data-downloader “queue request id” returned when a fetch is enqueued.
- `correlation_id`: The client-side deterministic correlation key used to de-dupe / identify retries for the same request intent.
- `queue_status`: pending/processing/completed/dead (server-side).
- `queue_position`: The queue slot position at the downloader.
- “Silent stall”: backtest stops emitting logs for long periods and progress stops, while UI still shows `download_status.active=true`.

**User-authorized change (important)**
- Previous handoffs said “do not edit Strategy Library Demos”. The user has explicitly authorized editing:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/Demos/*`
  for this task and future (as of late Dec 2025 / early Jan 2026). Keep demos stable, but edits are allowed.

## 0) Hard Constraints / Guardrails (do not violate)

### Absolute safety rules

- **Never run `git checkout`** for any reason. Use `git switch`, `git restore`.
- Wrap long-running commands with `/Users/robertgrzesik/bin/safe-timeout …`.
- **Never start ThetaTerminal locally with production credentials.** It instantly kills production access for customers.
- Backtests should use the **remote data-downloader**:
  - `DATADOWNLOADER_BASE_URL=http://data-downloader.lumiwealth.com:8080`
  - Do not hard-code an IP (it can change).
- **Production environment:** do not “hand-edit” production settings.
  - Reading prod logs/config is OK.
  - The only explicitly allowed prod write in this thread: **fix+redeploy data-downloader** *if and only if we prove it is the root cause*.
- **Accuracy > speed**. Never “fix” a stall by skipping trades or silently degrading data semantics unless the user explicitly approves that behavior.

### Testing rules (high importance)

- Tests under `tests/` follow `tests/AGENTS.md`:
  - Any test whose earliest commit date is before **2025-01-01** is **LEGACY**.
  - For LEGACY tests: **fix code, not tests**, unless the expectation was truly wrong and you document the justification in the test.
- Do not add “max logs per second” style rate-limiting. Logging is considered critical for debugging. (Progress bar output is a special case; see below.)

---

## 1) Repo / Services / Where Things Live

### LumiBot (library)
- Repo root:
  - `/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot`
- Docs:
  - Architecture: `docs/BACKTESTING_ARCHITECTURE.md`
  - Handoffs: `docs/handoffs/`

### Strategy Library (manual acceptance runs)
- Demo strategies:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/Demos`
- Backtest artifacts:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs`

### Bot Manager / Production
- Backtests run in ECS tasks; logs are in CloudWatch.
- Backtest UI polls a status payload containing:
  - `manager_bot_id`
  - `download_status` (stringified JSON)
  - elapsed/eta/progress/portfolio value

### Data Downloader (remote service)
- Stable endpoint:
  - `http://data-downloader.lumiwealth.com:8080`
- Local checkout:
  - `/Users/robertgrzesik/Documents/Development/botspot_data_downloader`

### Key docs + important files (by path)

If a new session is lost, these are the “map” files to open first.

**Architecture / rules**
- Backtesting architecture (canonical, long form):
  - `docs/BACKTESTING_ARCHITECTURE.md`
- Backtesting architecture redirect (short stub at repo root):
  - `BACKTESTING_ARCHITECTURE.md`
- Backtesting tests overview:
  - `docs/BACKTESTING_TESTS.md`
- ThetaData cache / remote cache notes:
  - `docs/remote_cache.md`
  - `docs/THETADATA_CACHE_VALIDATION.md`
- Agent safety + conventions:
  - `AGENTS.md`
  - `tests/AGENTS.md`
  - `CLAUDE.md`

**Prior handoffs (the ones humans reference)**
- Latest prior session handoff (Dec 26, 2025):
  - `docs/handoffs/2025-12-26_THETADATA_SESSION_HANDOFF.md`
- Earlier merge/backtesting handoffs (Dec 17–18, 2025):
  - `docs/handoffs/2025-12-17_THETADATA_BACKTESTING_HANDOFF.md`
  - `docs/handoffs/2025-12-18_THETADATA_MERGE_HANDOFF.md`

**Investigations (high signal)**
- SmartLimit / slippage design + semantics:
  - `docs/investigations/SMART_LIMIT_ORDER_DESIGN.md`
- SPX Short Straddle / stall+perf investigation notes:
  - `docs/investigations/THETADATA_INVESTIGATION_2025-12-27_SPX_STRADDLE_PERF.md`
- ThetaData investigation (Dec 11, 2025):
  - `docs/investigations/THETADATA_INVESTIGATION_2025-12-11.md`

**Core code hotspots (backtesting + ThetaData)**
- Backtest engine (fills, settlement, lifecycle):
  - `lumibot/backtesting/backtesting_broker.py`
- Strategy runtime (main loop, lifecycle hooks):
  - `lumibot/strategies/strategy_executor.py`
- ThetaData backtesting data source (pandas):
  - `lumibot/backtesting/thetadata_backtesting_pandas.py`
- ThetaData downloader client / queue recovery (prod stall focus):
  - `lumibot/tools/thetadata_queue_client.py`
- ThetaData helper + cache glue + `download_status` plumbing:
  - `lumibot/tools/thetadata_helper.py`
- Backtest cache (local + S3):
  - `lumibot/tools/backtest_cache.py`
- Options chain / delta strike search / market eval:
  - `lumibot/components/options_helper.py`
- Progress bar printing:
  - `lumibot/tools/helpers.py`

**BotSpot / Bot Manager (prod parity + UI)**
- Bot Manager (runs backtests/bots):
  - `/Users/robertgrzesik/Documents/Development/bot_manager`
- BotSpot backend API (status/progress endpoints):
  - `/Users/robertgrzesik/Documents/Development/botspot_node`
- BotSpot frontend (backtest page):
  - `/Users/robertgrzesik/Documents/Development/botspot_react`

---

## 1.1) Backtesting Architecture (high-level summary)

The canonical architecture doc is `docs/BACKTESTING_ARCHITECTURE.md`. This section is a “mental model” summary for continuing work.

### Core flow

1) A strategy calls `Strategy.backtest()` / `Strategy.run_backtest()` (internals in `lumibot/strategies/_strategy.py`).
2) Backtest selects a backtesting data source:
   - Strategy can specify one explicitly **but**
   - `BACKTESTING_DATA_SOURCE` env var can override that (very common in Strategy Library runs).
3) `BacktestingBroker` drives:
   - order lifecycle
   - fills
   - portfolio accounting
   - mark-to-market valuation (PV is computed, not fetched from a broker)
4) Bars/quotes load through the chosen backtesting data source into `Data` objects (pandas-backed for Theta/Yahoo/Polygon).

### Pricing semantics (correctness rules)

These rules are the “backtesting must mimic live broker behavior” contract:

- **`get_last_price()` is trade-only.**
  - It must never silently fall back to bid/ask/mid.
  - Options often have no trades; `get_last_price(option)` may be stale/missing and that is realistic.

- **Quote-derived marks exist and are used where appropriate:**
  - For **option MTM** and **quote-based fills**, use bid/ask-derived marks (mid) when available.
  - Missing option pricing should not zero portfolio valuation; forward-fill marks instead (avoid “sawtooth” equity curves).

- **No lookahead bias on daily bars.**
  - Day bars must not be visible before they “happen” in the simulated timeline.

### Cache layers (why runs can differ wildly)

There are multiple caching layers; confusing them causes “why is prod slower?” mistakes:
- Local cache (macOS typical): `~/Library/Caches/lumibot/1.0/thetadata`
- Remote cache (S3) via downloader/hydrator (prod uses this heavily)
- Downloader’s own internal cache / queue behavior

When diagnosing performance or “missing quote columns”, always confirm:
- which data source is active (`BACKTESTING_DATA_SOURCE`)
- which downloader URL is used (`DATADOWNLOADER_BASE_URL`)
- whether you’re hitting an old cache schema (NBBO columns missing) vs fresh

---

## 1.2) Environment Variables (high-impact)

These are the env vars that most often change behavior/speed:

- Data source selection:
  - `IS_BACKTESTING=True`
  - `BACKTESTING_DATA_SOURCE=thetadata` (overrides strategy code)
  - `DATADOWNLOADER_BASE_URL=http://data-downloader.lumiwealth.com:8080`

- Backtest window:
  - `BACKTESTING_START=YYYY-MM-DD`
  - `BACKTESTING_END=YYYY-MM-DD`

- Artifacts / logging (large impact on speed):
  - `SHOW_TEARSHEET=True|False`
  - `SHOW_PLOT=True|False`
  - `SHOW_INDICATORS=True|False`
  - `BACKTESTING_QUIET_LOGS=true|false` (CloudWatch/log volume)
  - `BACKTESTING_SHOW_PROGRESS_BAR=true|false`

- Planned (not implemented yet in 4.4.21, but requested next):
  - `BACKTESTING_PROFILE=yappi` (opt-in profiling artifact)

---

## 2) Current Versions / Deployment Timeline

### What is deployed
- **4.4.20 has been deployed and merged into `dev`** (user confirmed).
- **4.4.20 contains the big speed improvements** (Backdoor and SPX Short Straddle backtests now run in ~1–2 minutes locally with warm cache; see below).

### Next proposed release
- **PR targeting 4.4.21 exists**:
  - Branch: `stall-recovery-4.4.21`
  - PR: `https://github.com/Lumiwealth/lumibot/pull/924` (base `dev`)
  - Status: not deployed yet at the time of this handoff.

### QuantStats
- User released **`quantstats-lumi 1.1.0`** to PyPI and merged its PR.
- LumiBot requirements were updated to prefer `quantstats-lumi>=1.1.0` (ensure prod images also install it).

---

## 3) Acceptance Backtests (Manual, End-to-End)

### Why we keep these
These are the “real” system-level validation suite. They catch:
- lookahead issues
- MTM/quote column bugs
- split math bugs
- index coverage/placeholder bugs
- performance regressions (human-visible)

These live in Strategy Library so artifacts land in `Strategy Library/logs`.

### Shared env vars (recommended)

Run from `Strategy Library` and ensure we use local LumiBot source:

```bash
cd "/Users/robertgrzesik/Documents/Development/Strategy Library"
/Users/robertgrzesik/bin/safe-timeout 2400s env \
  PYTHONPATH="/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot" \
  IS_BACKTESTING=True BACKTESTING_DATA_SOURCE=thetadata \
  DATADOWNLOADER_BASE_URL="http://data-downloader.lumiwealth.com:8080" \
  SHOW_PLOT=True SHOW_INDICATORS=True SHOW_TEARSHEET=True \
  BACKTESTING_QUIET_LOGS=false BACKTESTING_SHOW_PROGRESS_BAR=true \
  BACKTESTING_START=YYYY-MM-DD BACKTESTING_END=YYYY-MM-DD \
  python3 "Demos/<strategy>.py"
```

Notes:
- Some `*_logs.csv` are “line logs” not strict CSV.
- For performance tests, set:
  - `BACKTESTING_QUIET_LOGS=true`
  - `BACKTESTING_SHOW_PROGRESS_BAR=false`

### Canonical acceptance suite (from 2025-12-26 handoff)

These were the “official 5”:

1) **Deep Dip Calls (GOOG; file name says AAPL)**
- File: `Demos/AAPL Deep Dip Calls (Copy 4).py`
- Window: `2020-01-01 → 2025-12-01`
- Checks:
  - ≥3 option-entry buys (2020/2022/2025 dip eras)
  - No GOOG split cliff mid-July 2022
  - Artifacts generate

2) **Alpha Picks LEAPS (Call Debit Spread)**
- File: `Demos/Leaps Buy Hold (Alpha Picks).py`
- Short window: `2025-10-01 → 2025-10-15` must trade `UBER, CLS, MFC` (both legs).
- 1-year window: `2025-01-01 → 2025-12-01` (symbols may vary; log skip reasons)

3) **TQQQ SMA200 (ThetaData vs Yahoo sanity)**
- File: `Demos/TQQQ 200-Day MA.py`
- Window: `2013-01-01 → 2025-12-01`
- Check: ThetaData parity close-ish to Yahoo (no obvious inflation)

4) **Backdoor Butterfly 0DTE (index + index options)**
- File: `Demos/Backdoor Butterfly 0 DTE (Copy).py`
- Window: `2025-01-01 → 2025-12-01`
- Check: must not crash on index placeholder tails; artifacts generate

5) **MELI Deep Drawdown Calls**
- File: `Demos/Meli Deep Drawdown Calls.py`
- Window: `2013-01-01 → 2025-12-18`
- Check: no sawtooth equity curve (MTM stability), artifacts generate

### New acceptance candidates (should become “official” now)

These should be promoted into the acceptance suite because they are now core:

6) **Backdoor Butterfly with SmartLimit**
- File: `Demos/Backdoor Butterfly 0 DTE (Copy) - with SMART LIMITS.py`
- Window: `2025-01-01 → 2025-12-01`
- Checks:
  - completes fast locally (warm cache ~1–2 minutes, depending on flags)
  - fills use SmartLimit semantics (mid + slippage; multi-leg net)
  - should produce materially different (usually “better”) results than worst-case market fills

7) **SPX Short Straddle Intraday (the “stall” repro)**
- File(s):
  - `Demos/SPX Short Straddle Intraday (Copy).py`
  - sometimes a “Copy 2” exists in BotSpot/backtests
- Target window (user’s canonical failing prod run):
  - `2025-01-06 → 2025-12-26` (or similar)
- Checks:
  - should complete in production without stalling
  - should not permanently hang with `download_status.active=true`

### “Expected results” (how strict should we be?)

For acceptance backtests we have two distinct kinds of checks:

1) **Hard invariants (should be asserted)**
- “does not crash”
- “produces artifacts” (`*_trades.csv/html`, `*_stats.csv`, `*_tearsheet.html`)
- “trades are non-empty when they should be”
- “no obvious correctness red flags” (e.g., split cliff to ~0, sawtooth PV from 0 marks)
- “no infinite loops / no silent stalls”

2) **Soft metrics (should be tracked, not hard-asserted)**
- Total Return / CAGR / MaxDD are sensitive to:
  - fill model changes (market/limit/smart-limit)
  - data availability (quotes missing)
  - strategy randomness / subtle provider behavior
- Recommendation: store expected ranges and investigate deltas, but avoid exact numeric assertions unless the system is very stable.

### Known good “anchor” artifacts (from 2025-12-26 handoff)

These anchors are useful when visually sanity checking results:

- AAPL Deep Dip Calls:
  - `Strategy Library/logs/AAPLDeepDipCalls_2025-12-25_19-08_WHRsPm_trades.csv`
  - `Strategy Library/logs/AAPLDeepDipCalls_2025-12-25_19-08_WHRsPm_stats.csv`
  - `Strategy Library/logs/AAPLDeepDipCalls_2025-12-25_19-08_WHRsPm_tearsheet.html`
- Leaps Call Debit Spread (short window):
  - `Strategy Library/logs/LeapsCallDebitSpread_2025-12-25_19-14_lLFnSk_trades.csv`
- TQQQ SMA200:
  - ThetaData:
    - `Strategy Library/logs/TqqqSma200Strategy_2025-12-25_19-22_UoZ2yn_tearsheet.html` (printed CAGR ~0.413)
  - Yahoo:
    - `Strategy Library/logs/TqqqSma200Strategy_2025-12-25_19-20_cQkd1T_tearsheet.html` (printed CAGR ~0.409)
- Backdoor Butterfly 0DTE:
  - `Strategy Library/logs/BackdoorButterfly0DTE_2025-12-25_18-29_KAD4Qk_tearsheet.html`
- MELI Deep Drawdown Calls:
  - Known-fixed (no sawtooth):
    - `Strategy Library/logs/MeliDeepDrawdownCalls_2025-12-25_20-38_33bGtY_stats.csv`

### “Officialize acceptance backtests” (future work)

We should make the acceptance suite official in two layers:

1) **Docs (human-facing)**
- Add a markdown doc in LumiBot:
  - `docs/acceptance/ACCEPTANCE_BACKTESTS.md`
  - includes:
    - the list, windows, required env vars
    - what to check (qualitative + quantitative)
    - known failure modes (placeholders, gaps, sawtooth, split cliffs)
    - “expected ranges” for key metrics (Total Return, CAGR, MaxDD)
    - “expected speed” (but only as a relative bound / warning; machine dependent)

2) **CI automation (machine-enforced)**
- CI constraints:
  - Full-window acceptance runs are too expensive for PR gating unless we use the S3 cache (and even then, fork PRs won’t have secrets).
- Proposed structure:
  - Add **short-window smoke backtests** to `tests/backtest/` for PR gating.
  - Add full-window acceptance suite as:
    - nightly scheduled job, and/or
    - label-triggered workflow (only for trusted branches).
- Metrics to check in CI (short window):
  - “does it run / no crash / produces trades / no missing critical artifacts”
  - a few invariants, not exact returns.

**Open design question:** where should “acceptance backtests” live?
- **Docs folder** is the right place to define the acceptance suite: what to run, why, and what to look for.
- **`tests/backtest/`** is the right place for *short*, repeatable, PR-gating backtests.
- **CI** can run a nightly full-window suite (trusted secrets needed) without blocking PR iteration.
- If we ever run full-window acceptance tests in CI, we will likely need:
  - S3 cache secrets available only to trusted branches, and/or
  - a “nightly” workflow rather than PR gating.

---

## 4) Core Problems (Current)

### 4.1 SPX Short Straddle Intraday stalls in production (top priority)

#### Symptoms (prod)
- Backtest runs for ~10–15 minutes and then “goes silent”:
  - no new CloudWatch logs
  - UI shows `download_status.active=true` forever
  - percent stops moving
- Restarting sometimes progresses further (e.g., Jan 10 → Jan 13 → Jan 15), suggesting cache warming changes where it wedges.

#### Key evidence
- Example stalled run:
  - `manager_bot_id=9f2f4693-e541-4038-9994-185b9339208b`
  - UI `download_status` showed it waiting on:
    - `SPXW 2025-01-15 5645.0 CALL minute quote`
  - Bot logs stop shortly after:
    - `ThetaData cache updated for SPXW 2025-01-15 5640.0 CALL minute quote ...`
  - Critically: after that point, there is **no** new:
    - `Submitted to queue: request_id=...`
  - That strongly indicates a **client-side HTTP wedge** before submit logging (i.e., a blocking call that never returns).

#### “Stall triage” runbook (CloudWatch, read-only)

Known bot backtest log group:
- `/aws/ecs/prod-trading-bots-backtest` (AWS CLI profile: `BotManager`)

Fastest way to fetch logs for a specific run:
```bash
aws logs tail "/aws/ecs/prod-trading-bots-backtest" \
  --profile BotManager --since 2h \
  --filter-pattern "9f2f4693-e541-4038-9994-185b9339208b"
```

What to look for:
- The last `ThetaData cache updated for ...` line
- Whether there is a corresponding `Submitted to queue: request_id=...` afterwards
- Whether queue waiting heartbeats appear (`[THETA][QUEUE] Still waiting...`) (only after 4.4.21)

If you need a static dump (better than `get-log-events` for this log group):
```bash
aws logs filter-log-events \
  --profile BotManager \
  --log-group-name "/aws/ecs/prod-trading-bots-backtest" \
  --filter-pattern "9f2f4693-e541-4038-9994-185b9339208b" \
  --start-time $(( ( $(date +%s) - 7200 ) * 1000 ))
```

Downloader logs (AWS default profile):
- Log group name may differ by environment. Find it via:
```bash
aws logs describe-log-groups --profile default | rg -i "downloader|thetadata|theta"
```
Then filter by `request_id` or `correlation_id` once those are visible in bot logs / download_status.

#### What 4.4.21 PR #924 attempts to do
PR #924 (`stall-recovery-4.4.21`) contains the “stall recovery” changes:

- File: `lumibot/tools/thetadata_queue_client.py`
  - Hard timeouts on all HTTP calls (`requests.Session.post/get` with connect/read timeouts).
  - Retry/backoff on transient errors and `queue_full`.
  - Session reset/invalidation after error streaks (new session to clear stuck sockets).
  - Heartbeat logs every ~30s while waiting for:
    - a queue slot (position)
    - a result
  - Bounded total wait (prevents infinite loops; the worst outcome becomes a terminal error, not a silent hang).

Additionally PR #924 reduces downloader pressure from the strategy’s delta/strike probing:
- Uses quote “snapshot” path for delta probing where possible
- Adds per-bar caches in OptionsHelper keyed by strategy datetime (reset each bar)

#### Important: local reproduction status
- The stall was confirmed from production logs.
- It was **not** reproduced as a full local “silent for 1 hour” hang (hard to reproduce without the same network/queue conditions).
- However, unit tests/mocks were used to reproduce “HTTP calls can hang/timeout repeatedly”, and the fix targets that specific failure mode.

#### Next step after this handoff
The next session should:
1) Finish the stall investigation with **CloudWatch correlation**:
   - determine whether the downloader also wedged for that time window
   - if downloader wedged, implement downloader-side request timeouts too
2) Add YAPPI profiling (see below) to help quantify time spent in waiting vs compute.

### 4.2 Production speed parity (local fast, prod much slower)

Observed example:
- Backdoor Butterfly full-year:
  - local: ~1:24–1:30 (warm cache, quiet logs, no progress bar)
  - prod: ~7m42s for a similar full-year window (user measured)

Working hypothesis:
- prod is dominated by:
  - queue wait / downloader latency, OR
  - CPU/IO constraints in ECS, OR
  - progress/log/DB overhead (CloudWatch + Dynamo progress writes), OR
  - higher cache miss rate on prod (S3 misses or different cache key/version)

We need evidence, not guesses.

#### Practical note (why prod can be slower even with warm S3)

Even with S3 warm, prod can still be slower due to:
- ECS instance CPU (ARM t4g.* can be slower than an M3 laptop for Python/pandas workloads)
- higher log + progress overhead (CloudWatch is not a TTY; `\r` progress becomes many lines)
- progress persistence overhead (DynamoDB writes, status uploads)
- queue contention / downloader throughput variance
- network hiccups that force retries (which local runs may not hit)

### 4.3 AAPL Deep Dip Calls is now very slow (472 placeholder churn)

The user observed repeated logs like:
- `status_code=472 size=0` (placeholder-only option day OHLC)
- split-adjustment of strikes (GOOG) leading to “adjusted strike” values like `590.02`
- repeated weekly interval fetches returning placeholders

This may indicate:
- option strike normalization issues for split-adjusted strikes, and/or
- repeated “validation” probing that’s too expensive.

PR #924 includes a mitigation to reduce repeated expiry validation probing (TTL skip) but does not fully solve strike correctness yet.

### 4.4 Progress bar spam (CloudWatch / logs)

Problem:
- In CloudWatch, `\r` progress updates do not overwrite; they appear as hundreds of lines.

Fix in PR #924:
- `lumibot/tools/helpers.py::print_progress_bar()` throttled to ~1 line/sec.

This is considered acceptable because it reduces waste without deleting logs.

---

## 5) What PR #924 (4.4.21) Contains (Checklist)

This is what the next session inherits in the open PR:

### A) ThetaData queue client stall recovery (core)
- `lumibot/tools/thetadata_queue_client.py`
  - request timeouts
  - retry/backoff
  - session reset
  - heartbeat logs
  - bounded total wait

Key implementation details (why this matters):
- Uses **thread-local `requests.Session`** because `requests.Session` is **not thread-safe** and backtests can issue concurrent downloader requests.
- Adds session “generation” invalidation (`_invalidate_sessions(...)`) so repeated timeouts can force new TCP connection pools.
- Adds INFO heartbeats every ~30s while waiting so the bot never “goes silent” with zero logs.
- Best-effort pushes queue metadata into `download_status` so the UI can show *what* it is waiting on (request id, queue status, etc.).

### B) Reduce request pressure from delta/strike probing
- `lumibot/backtesting/thetadata_backtesting_pandas.py`
  - “snapshot-only” quote fast path to avoid loading a full day of minute quotes during delta search
- `lumibot/components/options_helper.py`
  - per-bar caches keyed by strategy datetime
  - delta probing uses quote snapshot marks first

Key implementation details:
- Adds a **`snapshot_only=True`** quote path to ThetaData backtesting quotes so delta probes do not download a full “day chunk” (~956 minute rows) for strikes that will never be traded.
- OptionsHelper’s `_get_option_mark_from_quote(..., snapshot=True)` probes point-in-time NBBO for delta calculations and some “valid expiry” checks.
- Per-bar caches are reset when the strategy datetime changes; they are designed to avoid cross-bar staleness.

### C) Deep Dip 472 churn mitigation (partial)
- `lumibot/components/options_helper.py`
  - backtesting-only TTL skip (7d) to avoid repeated expensive expiry validation loops when no valid expirations are found
  - max validation budget (`max_checks=30`)

### D) Make stalls diagnosable from UI (download_status enrichment)

- `lumibot/tools/thetadata_helper.py`
  - `download_status` now has optional queue fields:
    - `request_id`, `correlation_id`, `queue_status`, `queue_position`, `estimated_wait`, `attempts`, `last_error`,
      `submitted_at`, `last_poll_at`, `timeout_at`
  - `set_download_status(..., timeout_s=...)` records an absolute `timeout_at` for the current fetch (best-effort)
  - `update_download_status_queue_info(...)` is called from the queue client to update queue status at low rate

Why this matters:
- When the UI says “active=true” forever, we need to know **which request** is stuck and whether it is:
  - still queued, or
  - processing, or
  - completed but never delivered to the client.

### E) S3 cache I/O hardening (prevents “hangs” outside the downloader)

- `lumibot/tools/backtest_cache.py`
  - boto3 S3 client uses explicit connect/read timeouts and “standard” retries.
  - This is defensive: a single cache read/write should never be able to wedge the entire backtest.

### F) Avoid risky day-mode minute quote fallback

- `lumibot/backtesting/thetadata_backtesting_pandas.py`
  - Avoids falling back to minute snapshots for “day mode” quote requests.
  - That fallback can massively increase request counts for long daily-option strategies.

### G) Progress bar throttle
- `lumibot/tools/helpers.py`
  - throttles progress output to ~1 line/sec (CloudWatch/CI safe)

### H) Tests added / run
- Added:
  - `tests/test_helpers.py::test_print_progress_bar_throttles_output`
  - `tests/test_thetadata_queue_client.py` coverage for retry/resubmit behavior
- Locally passed (as of PR author’s last report):
  - `pytest -q tests/test_thetadata_queue_client.py tests/test_helpers.py tests/test_options_helper_delta_quote_fallback.py`
  - `pytest -q tests/backtest/test_theta_strategies_integration.py`

---

## 6) Next “Must Do” Feature: Opt-in YAPPI profiling for backtests (recommended before deploying 4.4.21)

User’s intent:
- Add an **env var** to turn on profiling for backtests (not live trading).
- When enabled, LumiBot should:
  - run the backtest normally
  - produce a YAPPI profile artifact at the end
  - upload it like any other backtest artifact (same place tearsheets/trades go)

Why this matters:
- It’s the fastest way to close the “prod vs local speed” question without guessing.
- We can run the exact same window in production and see where time is spent (queue waits vs CPU).

Design constraints:
- Must be **opt-in** (zero overhead when disabled).
- Should not tightly couple QuantStats to LumiBot.
- Should not require users to install extra dependencies in normal runs (but production images can include yappi).

Suggested env vars (example; final naming can vary):
- `BACKTESTING_PROFILE=yappi`
- Optional:
  - `BACKTESTING_PROFILE_FORMAT=pstat|callgrind`
  - `BACKTESTING_PROFILE_UPLOAD=true` (default true in prod backtests)

Artifact format:
- Prefer a **single file** if possible:
  - yappi can output pstat/callgrind as a single file.
- If multiple files are needed, zip them and upload one zip.

Suggested artifact naming (so it’s searchable in logs/DB)
- `*_profile_yappi.pstat`
- or `*_profile_yappi.callgrind`
- If zipped: `*_profile_yappi.zip`

Suggested metadata to write into `*_settings.json` when profiling is enabled
- `profiling_enabled: true`
- `profiling_tool: "yappi"`
- `profiling_format: "pstat"|"callgrind"`
- `profiling_artifact: "<filename>"`

Where to implement:
- Strategy backtest driver or strategy executor around the full backtest run.
  - Start profiler right before simulation begins.
  - Stop profiler after simulation completes and artifacts are written.
  - Dump file(s) to the same artifact directory used by bot backtests.

Tip:
- Keep profiler logic entirely in LumiBot; QuantStats should simply display whatever “Backtest time” and metadata it is passed.

---

## 7) Production Speed Parity Plan (after stall recovery is deployed or at least observable)

We want to explain why prod is slower without changing prod config.

### Step 1: Make runs apples-to-apples
- Ensure local flags match prod:
  - progress bar on/off
  - verbose logs on/off
  - tearsheet/indicators generation on/off
  - same downloader URL

### Step 2: Derive “download wait vs compute” from logs
From CloudWatch logs:
- Count `Submitted to queue` lines
- Sum `Received result ... elapsed=...`
- Count `ThetaData cache MISS`
- Look for long gaps without logs (queue wait vs CPU hang)

### Step 3: Use YAPPI once when needed
- Run one profiled backtest in prod.
- Compare function hotspots with local.
- Decide optimization based on evidence:
  - queue wait → downloader / S3 cache / concurrency / retry tuning
  - compute → python hotspots, pandas, serialization, progress uploads

---

## 8) Recommended Next Session Game Plan (in order)

This is the recommended sequence for the next LLM session:

1) **Finish/validate PR #924 changes** (stall recovery + request pressure + progress throttle)
2) **Add YAPPI env var profiling for backtests** (opt-in, artifact upload)
3) Run a local reproduction attempt:
   - cold cache run of SPX Short Straddle Intraday
   - confirm no indefinite hangs; confirm heartbeat logs fire if waiting
4) User deploys 4.4.21.
5) Verify in prod:
   - SPX Short Straddle Intraday no longer goes silent; either completes or shows heartbeat + recovers.
   - Run Backdoor with profiling enabled to quantify prod speed gap.
6) Start the “officialize acceptance backtests” effort:
   - docs/acceptance doc + short-window CI backtests
   - consider a nightly/label workflow for full windows

---

## 9) Useful CloudWatch / AWS CLI Notes (read-only workflows)

Backtest bot logs are in a CloudWatch log group. In prior debugging, `aws logs get-log-events` returned empty but `aws logs filter-log-events` worked.

Strategy:
1) Use `manager_bot_id` to identify the correct log stream.
2) Use `filter-log-events` with the manager id as the filter string.

Note: exact log group names for downloader may vary; find via:
- list log groups containing “downloader”
- then filter for the request ids/correlation ids.

---

## 10) Meta: Why an `if logger.isEnabledFor(INFO)` guard might exist

You may see patterns like:
```py
if self.logger.isEnabledFor(logging.INFO):
    self.logger.info(colored(f"Order was filled: {order}", color="green"))
```

Even though logging filters by level internally, the guard can still be useful because:
- `colored(f"...{order}...")` and `f"{order}"` are evaluated **before** `logger.info()` runs.
- If `order.__str__` is expensive, the guard prevents work when INFO is disabled.

Better alternative (no guard needed):
```py
self.logger.info("Order was filled: %s", order)
```
because interpolation is deferred until the logger actually emits.

---

## Appendix A — Canonical “SPX stalling” sample payload (UI)

Example `download_status` seen during stalls:
```json
{
  "active": true,
  "asset": {"symbol":"SPXW","type":"option","strike":5645.0,"exp":"2025-01-15","right":"CALL","mult":100},
  "quote":"USD",
  "data_type":"quote",
  "timespan":"minute",
  "progress":0,
  "current":0,
  "total":1
}
```

This does not currently include `request_id`. PR #924 attempted to surface more metadata, but verify what the UI actually shows.

---

## Appendix B — Suggested acceptance metrics to track over time

For each acceptance run, record:
- Total Return
- CAGR
- Max Drawdown
- # trades / fills (order count)
- Backtest time (elapsed)
- “Data source” used
- Any warnings: placeholders/gaps/quotes missing

Important: do not assert exact values unless the system is stable; use broad ranges and invariants.

---

## Appendix C — Known `manager_bot_id` references (prod debugging)

These are the `manager_bot_id` values provided during this session that correspond to “something went wrong in production” investigations. These are used as the **filter key** when pulling CloudWatch logs.

Quick command (BotManager profile):
```bash
aws logs tail "/aws/ecs/prod-trading-bots-backtest" \
  --profile BotManager --since 24h \
  --filter-pattern "<manager_bot_id>"
```

### Stalls / “goes silent” / stuck with `download_status.active=true`

- `72c0dffa-b3e8-488a-8fa7-3192ce3fc007`
  - Early report: extremely slow, looked “stalled” around ~23% progress.
- `4296b619-a417-4ec5-88f2-c3264449cbba`
  - Very slow/stuck report: ~27–28% after ~12–22 hours, “no logs on website”.
- `3d73c360-b4ef-477f-87ce-6247928f85a1`
  - SPX Short Straddle Intraday: stuck with `download_status` showing an SPXW option minute quote request.
- `9f2f4693-e541-4038-9994-185b9339208b`
  - SPX Short Straddle Intraday: primary stall evidence used in PR #924.
  - UI showed it waiting on `SPXW 2025-01-15 5645C minute quote`; logs ended right after caching `5640C`, with no subsequent `Submitted to queue` log line.

### “Cannot predict future” / end date beyond available trading days (infinite loop bug)

- `54bfdaf6-da96-4e0e-9fc0-7d42f76f61d7`
  - Repro from ECS logs: backtest freezes at ~80% when BACKTESTING_END is in the future; log spam “Cannot predict future”.
- `de54d5c1-36f8-40e3-b040-77cb502b76d7`
  - TeslaEmaTrend prod logs (Lumibot 4.4.16) showing repeated:
    - “Backtesting reached end of available trading days data”
    - “Cannot predict future”

### Production speed parity / reference runs

- `1f86e156-2ebe-404c-9aad-0aa6aed0e2b5`
  - Prod rerun you cited for speed parity: ~55 minutes wall time (compare to local).
- `0c00f2e1-2901-4fee-ab99-ecf729b6b075`
  - Backdoor Butterfly prod run started to measure prod time vs local time.
- `5b3ad480-16f7-46e0-a806-d6e5d6631c0c`
  - A “running well / big improvement” prod run (positive reference point).
