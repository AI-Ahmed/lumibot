# NVDA + SPX Copy2/Copy3 + Prod Parity + Startup Latency — Execution Handoff (4.4.25)

Date: **2026-01-04**

Audience: a new human/agent session that must take over execution without re-learning context.

Scope (what this handoff covers):
- **P0: NVDA customer bug** (fails near the end) — fix must be in **LumiBot**, not the strategy
- **P0/P1: SPX Copy2/Copy3 extreme slowness** (hours) — fix must be in **LumiBot**, not the strategy
- **P1: Production vs Local speed parity** (warm runs) — explain and close the gap with evidence
- **P1: Startup latency** (time-to-first-progress) — measure root cause and fix (real, not only UX)

This handoff assumes the acceptance/CI gate work is being handled by a separate agent (see the CI acceptance handoff referenced below). This doc focuses on **NVDA + SPX + parity + startup**.

## What “Done” Means (success criteria)

This work is not “done” until we have **fresh proof** (not old artifacts) that:

### NVDA (P0)
- The customer NVDA backtest completes **end-to-end** over **2013-01-10 → 2025-12-30**.
- It produces new artifacts:
  - `*_tearsheet.html` (browser-openable)
  - `*_trades.csv`
  - `*_logs.csv`
  - `*_settings.json`
- It does **not** crash at the end.
- It is **fast enough**:
  - Target ≤ 20 minutes on a warm cache / typical prod-like run; if slower, we need to identify why (request volume vs compute vs artifacts).

### SPX Copy2/Copy3 (P0/P1)
- Cold-S3 simulation run (fresh namespace) completes full-year runs without “hours/days”.
- Cold run is allowed to hit the downloader (it *must* if S3 is cold), but request volume must be bounded.
- Warm proof run (same S3 namespace, fresh local cache folder) shows:
  - **near-zero** queue submits (“Submitted to queue” lines)
  - material speedup

### Parity + Startup (P1)
- We can quantify the remaining prod vs local warm-run gap with evidence:
  - wall time
  - downloader queue submits (should be near-zero for warm runs)
  - yappi attribution (S3 hydration vs compute vs artifacts vs progress/logging)
- We can quantify startup latency as a timeline:
  - submit → ECS startedAt → first log → first progress row → stage transitions
- We have an evidence-backed plan to close the gap (or confirm it’s mostly ECS CPU/network limits).

---

## 0) Hard Constraints / Rules (do not violate)

### Repo + branch rules (critical)
- Repo root: `/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot`
- **Work ONLY on branch `4.4.25`.**
- **No new branches. No new PRs.** All changes for this release must land on the **single 4.4.25 PR**.
- **Never run `git checkout`**. Use `git switch`, `git restore`.
- Before any commit: `git status` must be clean/understood; do not “lose” other agent work.

### Strategy rules
- **Never modify customer strategies or Strategy Library demo strategies.**
- Fix **LumiBot** (and only touch data-downloader if proven root cause and approved).

### Runtime + safety rules
- **No long-lived commands without a timeout**. Always wrap with `/Users/robertgrzesik/bin/safe-timeout ...`.
- For these P0/P1 investigations:
  - If a run cannot complete within a reasonable leash (**≤20 minutes per run**), treat that as a performance regression and **stop + inspect logs** instead of “waiting hours”.
  - For “cold cache” scenarios, prefer **short windows** (days/weeks) and **chunking** (month/quarter segments) rather than a single long run.
- **Never start ThetaTerminal locally with production credentials.** Backtests must use the remote downloader.

### Write-location policy (security + hygiene)
- Do not create code files outside `~/Documents/Development/`.
- Put helper scripts in `lumibot/scripts/` (this repo).
- Put extracted strategy code under `Strategy Library/tmp/` (not `/tmp`).

---

## 1) “Map” Files (read these first)

### Canonical architecture + cache semantics
- `docs/BACKTESTING_ARCHITECTURE.md`
- `docs/remote_cache.md`
- `docs/THETADATA_CACHE_VALIDATION.md`

### Acceptance suite definition (human release gate)
- `docs/ACCEPTANCE_BACKTESTS.md`

### “Where code lives” on this machine (absolute paths)
- LumiBot repo (this project):
  - `/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot`
- Strategy Library (manual backtests + artifacts):
  - `/Users/robertgrzesik/Documents/Development/Strategy Library`
  - Demos:
    - `/Users/robertgrzesik/Documents/Development/Strategy Library/Demos`
  - Artifacts (tearsheets/trades/logs):
    - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs`
  - Extracted prod strategy code zips (for repro only, never edit):
    - `/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/<manager_bot_id>/main.py`
- BotManager repo (runs production backtests/bots):
  - `/Users/robertgrzesik/Documents/Development/bot_manager`
- BotSpot node backend (starts backtests, injects env, fetches artifacts):
  - `/Users/robertgrzesik/Documents/Development/botspot_node`
- BotSpot React frontend (backtest UI):
  - `/Users/robertgrzesik/Documents/Development/botspot_react`
- ThetaData downloader repo (remote service; local checkout exists):
  - `/Users/robertgrzesik/Documents/Development/botspot_data_downloader`

### Prior handoffs (chronological)
- `docs/handoffs/2026-01-01_THETADATA_SESSION_HANDOFF.md`
- `docs/handoffs/2025-12-26_THETADATA_SESSION_HANDOFF.md`
- `docs/handoffs/2025-12-18_THETADATA_MERGE_HANDOFF.md`
- `docs/handoffs/2025-12-17_THETADATA_BACKTESTING_HANDOFF.md`

### CI acceptance gate (owned by Agent A)
- `docs/handoffs/2026-01-04_THETADATA_CI_ACCEPTANCE_GATE_HANDOFF.md`

### AWS CLI profiles (what to use when)
- `--profile BotManager`
  - Used for: bot backtest ECS logs (CloudWatch group `/aws/ecs/prod-trading-bots-backtest`).
- `--profile default`
  - Used for: data-downloader logs and other infrastructure (exact log group names vary; discover via `describe-log-groups`).

---

## 2) Current Branch State (what already exists on `4.4.25`)

### Where we are
- Branch: `4.4.25`
- All work must remain on this branch.

### Existing fixes already committed (relevant to NVDA/SPX)

These are already on `4.4.25` and are intended to address the customer-facing failures:

1) **SPX Copy2/Copy3 slowness**
- Area: `lumibot/components/options_helper.py`
- Fix: bounded “fast-path” for delta-to-strike selection to avoid per-strike quote probes exploding into thousands of downloader requests.
- Test: `tests/test_options_helper_strike_deltas_fastpath.py`

2) **NVDA end-of-run crash**
- Area: `lumibot/tools/indicators.py`
- Fix: avoid QuantStats/Seaborn KDE crash when returns are degenerate/flat; produce placeholder tearsheet instead of crashing the backtest.
- Test: `tests/test_tearsheet_flat_returns.py`

3) **Corporate actions thrash mitigation**
- Area: `lumibot/tools/thetadata_helper.py`
- Fix: memoize corporate actions (splits/dividends) requests in-memory + failure TTL to avoid repeated fetch storms.
- Test: `tests/test_thetadata_helper_event_memoization.py`

### Helper added for local prod-faithful backtest runs
- Script: `scripts/run_backtest_prodlike.py`
  - Purpose: run an extracted `main.py` strategy locally with **prod-like flags**, **ThetaData downloader**, and **S3 cache**, while keeping artifacts in a known place.
  - Important: run from a directory with **no nested `.env`** to avoid LumiBot’s recursive dotenv scan (details below).

---

## 3) Key Production Run Identifiers (manager_bot_id)

### NVDA customer bug
- BotSpot URL:
  - `https://botspot.trade/backtest/b424be56-b823-4c65-ae2c-9d30c8691390/9c957839-2b10-4399-82c1-871fd077401e?startDate=2013-01-10&endDate=2025-12-30&provider=theta_data`
- **manager_bot_id**:
  - `334e2c98-7134-4f38-860c-b6b11879a51b`

### SPX Copy2/Copy3 slow runs (hours)
- **manager_bot_id**:
  - `c7c6bbd9-41f7-48c9-8754-3231e354f83b`
  - `6be31002-44ec-4ae7-857a-db5e01323e7c`

---

## 4) Production-Faithful Backtest Wiring (do this exactly)

### Required environment variables (prod-like)
These must be set for local “prod-faithful” runs:

- Backtesting:
  - `IS_BACKTESTING=True`
  - `BACKTESTING_DATA_SOURCE=thetadata`
  - `BACKTESTING_START=YYYY-MM-DD`
  - `BACKTESTING_END=YYYY-MM-DD`

- Artifacts / logging (prod-like):
  - `SHOW_PLOT=True`
  - `SHOW_INDICATORS=True`
  - `SHOW_TEARSHEET=True`
  - `BACKTESTING_QUIET_LOGS=false`
  - `BACKTESTING_SHOW_PROGRESS_BAR=true`

- Remote downloader:
  - `DATADOWNLOADER_BASE_URL=http://data-downloader.lumiwealth.com:8080`
  - `DATADOWNLOADER_API_KEY=<secret>`
  - `DATADOWNLOADER_API_KEY_HEADER=<header-name>` (if required by the client)

- S3 cache (LumiBot reads/writes S3 directly):
  - `LUMIBOT_CACHE_BACKEND=s3`
  - `LUMIBOT_CACHE_MODE=readwrite`
  - `LUMIBOT_CACHE_S3_BUCKET=<dev-cache-bucket>`
  - `LUMIBOT_CACHE_S3_PREFIX=<dev-prefix>`
  - `LUMIBOT_CACHE_S3_VERSION=<version>`
  - `LUMIBOT_CACHE_S3_REGION=<region>`
  - `LUMIBOT_CACHE_S3_ACCESS_KEY_ID=<secret>`
  - `LUMIBOT_CACHE_S3_SECRET_ACCESS_KEY=<secret>`

Source of truth for these values on this machine:
- `botspot_node/.env-local` contains the downloader config + S3 cache creds.
  - Do not print the secret values into logs.

### IMPORTANT: LumiBot loads `.env` files recursively (pitfall)
`lumibot/credentials.py` walks the script directory and current working directory recursively to find `.env`.

This can accidentally load an unrelated `.env` located inside the directory tree you run from, overriding env vars and causing confusing behavior (wrong cache settings, wrong downloader settings, etc.).

Mitigations:
- Run local repros from a *clean* working directory under `~/Documents/Development/` that does not contain `.env` anywhere under it.
- Avoid running from `Strategy Library/` directly if it contains nested repos with `.env` (it often does).

This is why the local runner script uses a clean `strategy-library` directory (see below).

---

## 5) How to Retrieve the Exact Strategy Code (without editing strategies)

Customer/backtest strategies are stored as a zip in S3:

- Bucket: `prod-trading-bot-backtests`
- Key pattern: `backtest_code/<manager_bot_id>/code.zip`

### Local storage convention (required)
Store extracted `main.py` under:

`/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/<manager_bot_id>/main.py`

This keeps all “strategy code artifacts” inside the Development folder and near the Strategy Library logs.

### Retrieval approach
Use the AWS creds in `botspot_node/.env-local`:
- `BACKTEST_S3_AWS_ACCESS_KEY_ID`
- `BACKTEST_S3_AWS_SECRET_ACCESS_KEY`
- `BACKTEST_S3_AWS_REGION`

Then download and unzip.

---

## 6) Local Execution Runbook (NVDA/SPX) — strict leashes + artifact capture

### Tooling
Use `scripts/run_backtest_prodlike.py` from this repo.
See `docs/PRODLIKE_LOCAL_BACKTEST_RUNS.md` for the canonical, up-to-date run templates (timeouts, cache isolation, artifact locations).

Purpose:
- Run a `main.py` under local `PYTHONPATH` pointing to this LumiBot repo
- Apply prod-like env flags + downloader env + S3 cache env
- Keep artifacts discoverable and browser-openable
- Keep output in a controlled log file to avoid terminal spam

### Where artifacts should land
Default behavior (recommended):
- `scripts/run_backtest_prodlike.py` writes artifacts to a clean per-run workdir under `~/Documents/Development/backtest_runs/<runid>_<label>/logs/`.

Optional convenience:
- Use `--copy-artifacts-to "/Users/robertgrzesik/Documents/Development/Strategy Library/logs"` to copy the final artifacts into Strategy Library for quick browsing/search.

Minimum artifacts to capture for each “fresh proof run”:
- `*_tearsheet.html` (openable in browser)
- `*_trades.csv`
- `*_logs.csv`
- `*_settings.json`

### Reasonable leashes (use safe-timeout)
Do not “wait forever”.

- NVDA full-window run leash: start with **20 minutes**.
  - If it does not finish in 20 minutes, treat as performance issue; collect logs and stop.
- SPX cold-cache run leash: start with **15 minutes** to inspect request behavior.
  - If request volume is sane, **expand the window gradually** (week → month → quarter) while keeping each run ≤20 minutes.
  - If request volume explodes again, stop immediately and fix root cause before expanding.

---

## 7) S3 Cache Semantics (the part that keeps causing confusion)

### What “cache hit” means
- A cache hit means LumiBot reads cached files from S3 via `LUMIBOT_CACHE_BACKEND=s3`.
- **A warm run should not need the downloader** (no “Submitted to queue” lines) unless the cache coverage is incomplete for that request type.

### What “downloader usage” means
If you see log lines like:
- `ThetaData cache MISS ... fetching ... from ThetaTerminal.`
- `Submitted to queue: request_id=...`

That means **the cache did not have the required file**, so the backtest is hydrating by hitting the remote downloader (which then hits ThetaData).

### How to simulate “production cold start” without deleting anything
Production “cold” means:
- Local disk cache is empty (fresh ECS task)
- S3 cache namespace may be empty for a given strategy/window (first time ever run)

We must simulate cold S3 **without deleting shared caches**.

Do this by isolating the namespace via:
- `LUMIBOT_CACHE_S3_VERSION=spx_cold_<runid>` (recommended)
  - This creates a logically empty S3 namespace for that run.
  - It does not delete or overwrite existing caches.

And simultaneously:
- `LUMIBOT_CACHE_FOLDER=/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_<runid>`
  - This simulates a fresh container local cache.

### “Warm proof” for production
To simulate “second production run”:
- Keep the same `LUMIBOT_CACHE_S3_VERSION` as the cold run (so S3 is warm)
- Change only `LUMIBOT_CACHE_FOLDER` to a new empty folder (fresh container)

Expected outcome:
- **Near-zero** downloader queue submits.
- Runtime drops materially.

---

## 8) NVDA P0 — What to do, exactly

Goal:
- NVDA full-window (2013-01-10 → 2025-12-30) completes without crashing,
- produces tearsheet/trades/logs/settings artifacts,
- does not take “hours”.

### Step-by-step

1) Retrieve the exact strategy code
- Use `manager_bot_id=334e2c98-7134-4f38-860c-b6b11879a51b`
- Download `code.zip` from `prod-trading-bot-backtests/backtest_code/<id>/code.zip`
- Store at:
  - `Strategy Library/tmp/backtest_code/334e2c98-7134-4f38-860c-b6b11879a51b/main.py`

2) Run locally with prod-like flags
- Use a clean run directory (no nested `.env`)
- Leash: 20 minutes
- Required outputs: tearsheet/trades/logs/settings in Strategy Library logs

3) If it still crashes near the end
- Collect:
  - last ~300 lines of `*_logs.csv`
  - traceback
  - yappi profile if enabled (optional; not required for P0 crash)
- Confirm whether it is:
  - an artifact-generation crash (QuantStats), or
  - an option pricing / missing quotes issue, or
  - a cache/ThetaData placeholder behavior leading to NaNs.

4) Fix in LumiBot ONLY
- Add a regression test that fails without the fix and passes with it.
- Keep the strategy code unchanged.

5) Re-run the full window locally (same rules) and confirm artifacts exist.

6) Prod verification (after user deploy)
- Run the same backtest in prod and confirm it completes + artifacts upload.

Notes:
- The current code on `4.4.25` includes a tearsheet guard for flat returns. That should prevent the specific “KDE crash” class.
- If the backtest is now “slow” rather than crashing, focus on request volume and repeated option-list queries as the likely culprit.

---

## 9) SPX Copy2/Copy3 P0/P1 — What to do, exactly

Goal:
- Full-year runs no longer take “hours” on cold S3.
- Request volume must be bounded (no per-strike scan explosions).
- Warm run should be dramatically faster and near-zero downloader submits.

### Step-by-step

1) Retrieve both strategy codes
- IDs:
  - `c7c6bbd9-41f7-48c9-8754-3231e354f83b`
  - `6be31002-44ec-4ae7-857a-db5e01323e7c`
- Store extracted `main.py` under:
  - `Strategy Library/tmp/backtest_code/<id>/main.py`

2) Define a unique SPX “cold namespace”
- Example:
  - `LUMIBOT_CACHE_S3_VERSION=spx_cold_20260104_153000`
  - `LUMIBOT_CACHE_FOLDER=/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_20260104_153000`

3) Cold run (inspection leash first)
- Run each strategy on a **short diagnostic window** first (days/weeks) with:
  - the cold S3 version
  - a fresh local cache folder
- Leash: 15 minutes initially.
- Inspect the produced log file (subprocess log + LumiBot logs) for:
  - `Submitted to queue:` count
  - repeated `OPTION_PARAMS` lines scanning many strikes
  - repeated `get_strike_deltas` calls

4) If request volume is sane
- Expand the window gradually (week → month → quarter), keeping each run ≤20 minutes.
- Once behavior is sane, complete the full year by running **chunked windows** (e.g., month-by-month) rather than a single long job.

5) Warm proof
- Keep the same `LUMIBOT_CACHE_S3_VERSION` (now warm)
- Change only `LUMIBOT_CACHE_FOLDER` (fresh local)
- Run each strategy again.
- Expect near-zero queue submits and much faster runtime.

### If SPX is still exploding in requests
Likely remaining root causes (in order):
1) Strategy calling delta selection repeatedly per minute (should be memoized per timestamp in OptionsHelper).
2) OptionsHelper still probing many strikes because chain payload lacks deltas/greeks; must use bounded search.
3) Strike filtering not applied (but we cannot modify strategy; must handle in LumiBot helper).

Current `4.4.25` has a delta fast-path in `OptionsHelper` intended to eliminate “scan all strikes”.
If it’s still slow, inspect log patterns to identify the remaining loop.

---

## 10) Production vs Local Parity (P1) — evidence-based

Goal:
- Explain why warm prod runs are slower than warm local runs.
- Close the gap with targeted fixes (not guessing).

### Required measurement approach
Use **the same code + same windows + same flags** and run twice (cold then warm) where warm is:
- warm S3 namespace
- cold local disk (fresh container)

Measure:
- wall clock
- queue submit counts
- cache hit/miss patterns
- yappi output (when enabled) for “time spent” attribution

### Hypothesis buckets to prove/disprove
1) S3 I/O dominates (many small reads, slow network)
2) CPU dominates (ECS instance type vs M3 laptop)
3) artifact generation dominates (QuantStats/indicators)
4) progress/log persistence dominates (CloudWatch/Dynamo writes)

Only after yappi + counters prove the bucket, optimize.

---

## 11) Startup Latency (P1) — measure the real root cause

Goal:
- Reduce “nothing happens for 20–30s”.
- Fix both perceived latency (UI) and real latency (backend).

### Measurement (must do first)
For a single prod backtest run, capture:
- submit time
- ECS `startedAt`
- first log line time
- first progress record time
- stage transitions time (queued/pulling/starting/backtesting/finalizing)

Interpretation:
- If delay is before first log: ECS provisioning/pull
- If delay is after first log but before progress: boot/progress upload path

Then fix based on which bucket is dominant.

---

## 12) Coordination With Other Agent Work (CI acceptance)

Another agent is responsible for the CI acceptance gate work.

Rules:
- Do not fight over files; communicate via:
  - commits on `4.4.25`
  - handoff docs
  - explicit ownership of directories (tests/backtest owned by CI agent; NVDA/SPX owned here)
- Before making any change in `tests/backtest/`, check whether CI agent has ongoing edits.

---

## 13) Concrete TODO Checklist (for the next agent)

### P0: NVDA
- [ ] Download NVDA code zip (334e2c98…)
- [ ] Run full-window locally with 20m leash (prod-like flags)
- [ ] If crash persists: capture logs + patch LumiBot + add regression test
- [ ] Re-run full-window and confirm artifacts exist

### P0/P1: SPX Copy2/Copy3
- [ ] Download both code zips (c7c6bb… and 6be310…)
- [ ] Cold S3 namespace run with 15m inspection leash
- [ ] If request volume is sane, complete full-year (≤60m target)
- [ ] Warm proof runs (same S3 namespace, fresh local cache folder)

### P1: Parity + Startup
- [ ] Run parity benchmarks (prod vs local) with yappi + submit counts
- [ ] Measure startup timeline and identify root cause bucket
- [ ] Implement targeted optimization (only once bucket proven)

---

## 14) Notes / Known Pitfalls

- Don’t run from directories with nested `.env` files; LumiBot will auto-load them recursively.
- Don’t “wait hours” to learn a run is slow; set a leash and inspect request patterns early.
- Don’t delete global caches; use S3 namespace versioning to simulate cold.
- Don’t change strategy code to “fix” slowness; fix helper call-paths and caching in LumiBot.

---

## Appendix A — Glossary (terms used in logs/UI)

- `manager_bot_id` / `backtestId`
  - The unique ID for a backtest run in BotSpot/BotManager. In most flows these are the same UUID.
  - Use this ID to:
    - filter CloudWatch logs
    - download `backtest_code/<id>/code.zip`
    - fetch artifacts via BotManager endpoints

- `download_status`
  - A JSON-ish status blob (often stringified) that describes what the backtest is currently waiting on (asset, date, timespan, data type, etc).
  - When a run “looks stuck”, `download_status` tells you what data request is blocking.

- Downloader queue terms
  - `request_id`: returned when a downloader fetch is enqueued.
  - `correlation_id`: deterministic key used to de-dupe/retry the same intent.
  - `queue_status`: pending / processing / completed / dead (downloader side).
  - `queue_position`: queue slot position.

- Cache-related
  - “Local cache”: files under `LUMIBOT_CACHE_FOLDER/...` (ephemeral in ECS).
  - “S3 cache”: objects written/read by LumiBot when `LUMIBOT_CACHE_BACKEND=s3`.
  - “Cold run” (prod-faithful): cold local + cold S3 namespace for the specific strategy/window.
  - “Warm run” (prod-faithful): cold local + warm S3 namespace.

- “Submitted to queue”
  - Log line emitted when LumiBot sends a request to the remote downloader.
  - On a **warm S3** run, this should be near-zero.

---

## Appendix B — Where caches and artifacts live (paths)

### Local caches (macOS)
- Default (unless overridden):
  - `~/Library/Caches/lumibot/1.0/`
- ThetaData under cache folder:
  - `${LUMIBOT_CACHE_FOLDER}/thetadata/...`

### S3 cache key shape (important)
Per `docs/remote_cache.md`, S3 objects are stored as:

`<LUMIBOT_CACHE_S3_PREFIX>/<LUMIBOT_CACHE_S3_VERSION>/<relative path under LUMIBOT_CACHE_FOLDER>`

Implications:
- Changing `LUMIBOT_CACHE_S3_VERSION` gives you an “empty namespace” without deleting anything.
- Changing `LUMIBOT_CACHE_FOLDER` changes the relative path root (but in practice the relative tree under it is stable).

### Strategy Library artifacts (local)
- Logs/artifacts directory:
  - `/Users/robertgrzesik/Documents/Development/Strategy Library/logs`
- For these P0 runs, artifacts must exist in the run workdir `logs/` (and it’s recommended to also copy into Strategy Library via `--copy-artifacts-to` for easy browsing/search).

### Production artifacts (S3)
BotSpot/BotManager store artifacts under (as seen in botspot_node code):
- `s3://prod-trading-bot-backtests/backtest-results/<manager_bot_id>/...`

Note: code zips are separate:
- `s3://prod-trading-bot-backtests/backtest_code/<manager_bot_id>/code.zip`

---

## Appendix C — CloudWatch runbook (how to debug “slow / stuck” in prod)

### Bot backtest log group
- `/aws/ecs/prod-trading-bots-backtest`

### Filter logs for a specific run
Use the BotManager AWS profile:

```bash
/Users/robertgrzesik/bin/safe-timeout 120s \
  aws logs tail "/aws/ecs/prod-trading-bots-backtest" \
  --profile BotManager \
  --since 2h \
  --filter-pattern "<manager_bot_id>"
```

If `tail` is noisy, use `filter-log-events` for a static dump:

```bash
/Users/robertgrzesik/bin/safe-timeout 120s \
  aws logs filter-log-events \
  --profile BotManager \
  --log-group-name "/aws/ecs/prod-trading-bots-backtest" \
  --filter-pattern "<manager_bot_id>" \
  --start-time $(( ( $(date +%s) - 7200 ) * 1000 ))
```

### What to look for in slow runs
- Are we seeing **thousands** of:
  - `Submitted to queue: ...`
  - `[THETA][OPTION_PARAMS] ...`
  - repeated per-strike / per-expiration probing
- Are we seeing repeated `queue_full` backoffs?
- Are we seeing repeated “placeholder-only” responses (`status_code=472 size=0`), indicating no data coverage?

If it’s request explosion, fix is in LumiBot helper logic (OptionsHelper / ThetaData helper caching).
If it’s queue waits, fix might be in downloader capacity or request batching (but verify first).

---

## Appendix D — BotManager API runbook (run backtests automatically)

This machine has the credentials to run backtests via BotManager (read from `botspot_node/.env-local`):
- `BACKTEST_SERVICE_URL`
- `BACKTEST_API_KEY`

BotSpot node sends:
- `POST {BACKTEST_SERVICE_URL}/backtest`
- header: `x-api-key: {BACKTEST_API_KEY}`

Payload shape (simplified, redacted):
```json
{
  "bot_id": "<uuid-manager_bot_id>",
  "main": "<python source of main.py (plus log patch)>",
  "requirements": "lumibot",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "bot_config": { "env": { "...": "..." }, "...": "..." }
}
```

### Practical workflow (no secrets in logs)
1) Generate or pick a `manager_bot_id` (UUID).
2) Provide the Python code string (`main`) exactly as the strategy requires (BotSpot Node prepends a log patch).
3) Include environment vars via `bot_config.env` to ensure prod-like behavior:
   - `BACKTESTING_DATA_SOURCE=thetadata`
   - `DATADOWNLOADER_BASE_URL=...`
   - `DATADOWNLOADER_API_KEY=...`
   - `LUMIBOT_CACHE_BACKEND=s3`, etc.
4) Submit and poll status (BotManager has status endpoints; BotSpot Node also aggregates status).

### Why this matters for NVDA/SPX
If local repro is inconclusive, you can run the same code through BotManager to:
- reproduce exactly as production
- collect CloudWatch logs
- verify artifact uploads

---

## Appendix E — Local run templates (prod-like flags, strict leashes)

### 1) Create a clean run directory (no nested `.env`)
Example pattern:

```bash
RUNID="$(date +%Y%m%d_%H%M%S)"
WORKDIR="/Users/robertgrzesik/Documents/Development/backtest_runs/run_${RUNID}"
mkdir -p "$WORKDIR"
```

### 2) Local cache folder (simulate fresh container)
```bash
CACHE_DIR="/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_${RUNID}"
mkdir -p "$CACHE_DIR"
```

### 3) Run via `scripts/run_backtest_prodlike.py`
NVDA full-window (20m leash):

```bash
/Users/robertgrzesik/bin/safe-timeout 1200s \
  python3 /Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot/scripts/run_backtest_prodlike.py \
    --label nvda_full_prodlike \
    --workdir "$WORKDIR" \
    --cache-folder "$CACHE_DIR" \
    --main "/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/334e2c98-7134-4f38-860c-b6b11879a51b/main.py" \
    --start 2013-01-10 \
    --end 2025-12-30 \
    --copy-artifacts-to "/Users/robertgrzesik/Documents/Development/Strategy Library/logs"
```

SPX cold run (inspection leash first):

```bash
SPX_RUNID="$(date +%Y%m%d_%H%M%S)"
SPX_CACHE_DIR="/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_${SPX_RUNID}"
mkdir -p "$SPX_CACHE_DIR"

/Users/robertgrzesik/bin/safe-timeout 900s \
  python3 scripts/run_backtest_prodlike.py \
    --label spx_copy2_cold_inspect \
    --workdir "$WORKDIR" \
    --cache-folder "$SPX_CACHE_DIR" \
    --cache-version "spx_cold_${SPX_RUNID}" \
    --main "/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/c7c6bbd9-41f7-48c9-8754-3231e354f83b/main.py" \
    --start 2025-01-07 \
    --end 2025-02-07 \
    --copy-artifacts-to "/Users/robertgrzesik/Documents/Development/Strategy Library/logs"
```

Warm proof (same S3 version, new local cache folder):

```bash
SPX_RUNID="spx_cold_${SPX_RUNID}"  # reuse from cold
SPX_CACHE_DIR2="/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_${SPX_RUNID}_warmlocal"
mkdir -p "$SPX_CACHE_DIR2"

/Users/robertgrzesik/bin/safe-timeout 1200s \
  python3 scripts/run_backtest_prodlike.py \
    --label spx_copy2_warm_s3 \
    --workdir "$WORKDIR" \
    --cache-folder "$SPX_CACHE_DIR2" \
    --cache-version "$SPX_RUNID" \
    --main "/Users/robertgrzesik/Documents/Development/Strategy Library/tmp/backtest_code/c7c6bbd9-41f7-48c9-8754-3231e354f83b/main.py" \
    --start 2025-01-07 \
    --end 2025-02-07 \
    --copy-artifacts-to "/Users/robertgrzesik/Documents/Development/Strategy Library/logs"
```

### 4) Post-run quick checks (must do)
- Verify a new tearsheet exists (if you used `--copy-artifacts-to`):
  - `ls -1 "/Users/robertgrzesik/Documents/Development/Strategy Library/logs" | rg "<label>_.*_tearsheet\\.html" | tail`
- Count downloader queue submits (same note):
  - `rg -n "Submitted to queue" "/Users/robertgrzesik/Documents/Development/Strategy Library/logs/<label>_logs.csv" | wc -l`

If “warm” still submits to queue heavily, caching coverage is incomplete for the request type.

---

## Appendix F — Why startup can be slow (a concrete suspicion to test)

One likely contributor to startup delay on this machine:
- `lumibot/credentials.py` recursively scans directories for a `.env` file.
- If the working directory is large (e.g., Strategy Library with nested repos), this scan can be slow and can accidentally load the wrong `.env`.

Action:
- Measure how long the dotenv scan takes (instrument or timestamp logs).
- Consider a targeted fix:
  - stop recursive scanning (only check immediate directory), or
  - add an env var to disable dotenv scanning in production backtests (e.g., `LUMIBOT_DISABLE_DOTENV=1`), or
  - restrict search to a small set of safe directories.

Do not ship a “big refactor” here without buy-in, but measure it — this may explain “nothing happens for a few seconds”.

---

## Appendix G — Full “Game Plan” (step-by-step, with strict leashes)

This is the execution plan the next agent should follow **in order**, with the required “stop conditions” so we don’t burn 6 hours on a run that is obviously broken.

### Step 1: NVDA P0 (customer-facing) — full-window, prod-like, leash = 20 minutes

1) Confirm the exact customer run code is available locally:
- `Strategy Library/tmp/backtest_code/334e2c98-7134-4f38-860c-b6b11879a51b/main.py`

2) Run full-window with prod-like flags:
- Window: **2013-01-10 → 2025-12-30**
- Data source: ThetaData (via downloader)
- S3 cache: dev cache (readwrite)
- Leash: **20 minutes**

Stop conditions:
- If the run exceeds 20 minutes without meaningful progress:
  - stop the run
  - extract “Submitted to queue” count
  - identify the dominant request type(s) (e.g., `option/list/strikes` storms)

Pass conditions:
- finishes
- produces fresh artifacts in the run workdir `logs/` (and optionally copied into `Strategy Library/logs`)
- no end-of-run crash

If it fails:
- capture traceback
- capture last ~300 lines of `_logs.csv`
- patch LumiBot and add regression test (strategy remains unchanged)

### Step 2: SPX Copy2/Copy3 P0/P1 — cold S3 namespace, initial inspection leash = 15 minutes

For each manager_bot_id:
- Copy2: `c7c6bbd9-41f7-48c9-8754-3231e354f83b`
- Copy3: `6be31002-44ec-4ae7-857a-db5e01323e7c`

1) Ensure code is extracted under Strategy Library:
- `Strategy Library/tmp/backtest_code/<id>/main.py`

2) Generate a unique SPX cold namespace (do NOT delete caches):
- Example:
  - `LUMIBOT_CACHE_S3_VERSION=spx_cold_<runid>`
  - `LUMIBOT_CACHE_FOLDER=/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_<runid>`

3) Run a short window first (prod-faithful, but time-bounded):
- Leash: **15 minutes** initially (inspection run)

Stop conditions during the 15m inspection:
- If you see:
  - thousands of `Submitted to queue` within minutes
  - sequential strike scanning (`OPTION_PARAMS` in a tight loop)
  - repeated delta probes per strike
  → stop and fix the request explosion before attempting full completion.

If inspection looks sane (bounded request volume):
- expand the window gradually (week → month → quarter) while keeping each run ≤20 minutes.
- once behavior is stable, complete the full year by running chunked windows (month-by-month) rather than a single long job.

### Step 3: SPX warm proof — warm S3 + cold local (fresh container)

Immediately after cold run:
- Keep the same `LUMIBOT_CACHE_S3_VERSION` (S3 now warm)
- Change only `LUMIBOT_CACHE_FOLDER` to a new empty folder

Pass conditions:
- queue submits near-zero
- materially faster runtime

If warm run still hits downloader heavily:
- caching coverage is incomplete for some request type (chains, strikes, snapshots, quotes)
- use logs to identify which request type is missing from S3 and patch only that gap

### Step 4: Prod parity (warm) — quantify remaining gap with yappi + counters

Once NVDA/SPX are no longer obviously broken:
- Run the same strategy in prod and locally with:
  - same window
  - same flags
  - yappi enabled

Record:
- wall time
- queue submit counts (should be near-zero for warm)
- yappi totals (S3 vs compute vs artifacts vs progress/logging)

### Step 5: Startup latency — timeline and root cause

For one prod run, capture timestamps for:
- submit time (API accepted)
- ECS startedAt
- first log line
- first progress row
- stage transitions

Then decide:
- ECS pull/provisioning vs python boot vs progress write path

---

## Appendix H — “How to Use AWS” (profiles, where logs live, what to pull)

### AWS CLI profiles (what we know works)

This machine uses at least two profiles:
- `BotManager` (restricted; for bot backtest ECS logs)
- `default` (broader; for downloader + BotSpot infra logs)

Confirm configured profiles:
```bash
aws configure list-profiles
```

### Bot backtest logs (BotManager profile)
- CloudWatch log group:
  - `/aws/ecs/prod-trading-bots-backtest`

Filter by manager_bot_id:
```bash
aws logs tail "/aws/ecs/prod-trading-bots-backtest" \\
  --profile BotManager \\
  --since 2h \\
  --filter-pattern "<manager_bot_id>"
```

### Downloader logs (default profile)
Exact log group names can vary; discover them:
```bash
aws logs describe-log-groups --profile default | rg -i \"downloader|thetadata|theta\"
```

Once you have the log group name, filter by:
- `request_id` (from bot logs)
- `correlation_id` (if present)

---

## Appendix I — BotSpot → BotManager → ECS Flow (how production backtests actually run)

This is the “real” production loop:

1) User starts a backtest in BotSpot (React UI)
2) BotSpot React calls BotSpot Node endpoint (e.g., `/backtest/start`)
3) BotSpot Node:
   - pulls the stored strategy code from DB
   - prepends the **LUMIBOT_LOG_PATCH** (forces logfile artifacts)
   - injects provider/caching env vars into `bot_config.env`
   - sends a POST request to BotManager (`BACKTEST_SERVICE_URL`) with:
     - `bot_id` (manager_bot_id)
     - `main` (python code string)
     - `requirements` (usually `lumibot`)
     - `start_date`, `end_date`
     - optional `bot_config`
4) BotManager launches an ECS task, runs `main.py`, writes artifacts to S3, writes progress/status
5) BotSpot Node polls BotManager status and surfaces it to the UI
6) UI polls Node and displays progress/stage/files

### Where to see the exact payload sent (useful for debugging)
In `botspot_node`:
- File: `src/Backtest/backtest.controller.ts`
- Function: `startBacktest`
- It logs:
  - exact payload summary (redacted)
  - lengths of code (`main`) and bot_config keys

This is the first place to confirm:
- which env vars were actually sent to prod
- whether cache backend/mode/version were set the way you expect

---

## Appendix J — “How to Run Strategies on Production” (API keys + safe CLI workflow)

### Where the API key + service URL live locally (do not paste secrets)
In `botspot_node/.env-local`:
- `BACKTEST_SERVICE_URL`
- `BACKTEST_API_KEY`

This is the “backtest API key” BotSpot Node uses to talk to BotManager.

### Safe way to run a prod backtest from CLI (no secrets printed)

The simplest approach is:
1) read env vars from `.env-local` using a small python snippet (don’t `source` it; it contains `;` lines)
2) POST to `{BACKTEST_SERVICE_URL}/backtest` with `x-api-key`

**Important:** do not echo the API key in stdout/stderr. Keep output to “status code, manager id, etc.”

Pseudo-flow:
- Create a new UUID for `bot_id` (manager_bot_id)
- Provide the python strategy code string for `main` (BotSpot Node uses DB code + log patch)
- Provide the desired `start_date/end_date`
- Provide `bot_config.env` with:
  - downloader config
  - cache backend config
  - profiling config (if desired)

This is intentionally not copy/paste-ready here to avoid accidental secret leakage; implement as a small local script under `bot_manager/scripts/` or `lumibot/scripts/` that:
- loads dotenv
- posts JSON
- prints only safe fields

---

## Appendix K — YAPPI Profiling (how to use it, what artifact to expect, how to analyze)

### Why yappi matters
Yappi is the only reliable way to answer:
- “Is prod slow because of S3 hydration?”
- “Is it CPU (ECS instance type)?”
- “Is it artifacts (tearsheet/indicators)?”
- “Is it progress/log overhead?”

### How to enable it (backtests only)
The intended interface (per prior work) is an env var:
- `BACKTESTING_PROFILE=yappi`

When enabled, a profile artifact is written (CSV) and uploaded alongside other backtest files.

### How to analyze locally
In LumiBot repo:
- `scripts/analyze_yappi_csv.py`

Expected workflow:
1) run a backtest with `BACKTESTING_PROFILE=yappi`
2) locate the `*_profile_yappi.csv` artifact
3) run:
```bash
python3 scripts/analyze_yappi_csv.py /path/to/profile_yappi.csv
```
4) compare “top total time” functions/modules across prod vs local

### What to look for
Group hotspots into buckets:
- S3 cache (boto3, download/upload, serialization)
- downloader client waits (HTTP poll loops, queue waiting)
- pandas compute (merge/copy/timezone conversion)
- artifact generation (QuantStats/indicators/plotting)
- progress/logging (CloudWatch formatting, DB writes)

### Common yappi gotchas
- If you profile the entire process, log spam can dominate the profile.
- Ensure flags match production:
  - `SHOW_TEARSHEET=True`, `SHOW_INDICATORS=True`, `SHOW_PLOT=True`
  - `BACKTESTING_QUIET_LOGS=false`
  - `BACKTESTING_SHOW_PROGRESS_BAR=true`

---

## Appendix L — “How to Tell What’s Slow” (diagnostic patterns + next action)

This section is deliberately redundant because it prevents wasting hours.

### Pattern 1: Downloader queue storm (thousands of submits)
Signals:
- `Submitted to queue:` lines grow extremely fast
- `OPTION_PARAMS` repeated for many strikes/expirations

Likely causes:
- delta-to-strike selection doing per-strike quote probes
- repeated chain/strike-list fetches per minute

Next action:
- stop early (15m leash), inspect request types, patch helper logic to bound probes

### Pattern 2: Placeholder-only data coverage (status_code 472 size=0)
Signals:
- repeated placeholder responses
- repeated “schema upgrade” messages on placeholder-only caches

Likely causes:
- trying to request options before symbol had options coverage
- repeatedly re-fetching known-empty ranges

Next action:
- ensure placeholder suppression / negative caching is effective for that request path

### Pattern 3: Warm S3 run still hits downloader
Signals:
- warm run still shows many `Submitted to queue` lines

Likely causes:
- S3 cache namespace mismatch:
  - wrong `LUMIBOT_CACHE_S3_VERSION`
  - wrong `LUMIBOT_CACHE_S3_PREFIX`
  - wrong bucket/region credentials
- or cache coverage is missing a request type (chains vs strikes vs quotes)

Next action:
- confirm env vars actually set in the run payload (`bot_config.env` in prod)
- confirm which request type is missing and add caching for only that type

### Pattern 4: Low queue submits but still slow
Signals:
- warm run has near-zero submits but wall time still high

Likely causes:
- CPU bottleneck in ECS
- heavy artifact generation
- progress/log writes overhead
- S3 read of many small objects (still cache, but slow)

Next action:
- yappi profile to separate compute vs artifacts vs S3 IO

---

## Appendix M — Cold-cache simulation (S3-first) without deleting anything

The only safe way to test “cold cache” in a shared environment is namespace isolation.

### Recommended method
- Use `LUMIBOT_CACHE_S3_VERSION=spx_cold_<runid>`
- Use `LUMIBOT_CACHE_FOLDER=/Users/robertgrzesik/Documents/Development/tmp/lumibot_cache_spx_<runid>`

### Why this works
S3 key shape is:
- `<prefix>/<version>/<relative-path>`

So changing `version` makes it logically empty without deleting shared objects.

### Cleanup
Do not delete shared caches.
If you need to clean up scratch cold namespaces later, delete only:
- objects under `<prefix>/spx_cold_<runid>/...`

---

## Appendix N — What has already been learned / changed (so you don’t re-learn it)

### Key lessons learned
- “Cold cache” must include **S3 namespace coldness** (not just local disk).
- Strategy Library contains nested `.env` files; LumiBot recursively loads `.env`, which can override env vars and slow startup.
- A warm run should not hit the downloader; if it does, either:
  - env vars are wrong, or
  - cache coverage is missing for that request type.
- Long runs without early inspection are a time sink; use strict leashes and inspect request patterns early.

### Relevant commits already on branch `4.4.25`
(Commit hashes referenced here are already in the git history.)

- `41b9207e` — OptionsHelper: fast-path get_strike_deltas (reduce SPX request explosion)
- `69f57031` — indicators: avoid tearsheet crash on flat returns (NVDA “fails at end” class)
- `ab21fac5` — thetadata_helper: memoize corporate actions (avoid repeated fetch storms)
- `c9c0f17a` — scripts: add prod-like backtest runner + AGENTS write-location policy
- `74459c7b` — docs: date-first handoffs + this NVDA/SPX/parity runbook
