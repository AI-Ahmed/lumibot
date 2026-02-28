# IBKR Expired Futures Contract IDs (conids) — One-Time Backfill + Ongoing Refresh

## Problem

IBKR Client Portal (REST) cannot reliably discover **expired futures** contract IDs (conids). Backtesting that needs
explicit contracts (or a continuous-futures roll built from explicit contracts) becomes incorrect once the contract
falls off the active chain.

This is an IBKR platform limitation: the Web API does not support the TWS API’s `includeExpired` flow for discovering
expired derivatives. We therefore need a one-time “contract ID registry” backfill via TWS/Gateway.

## Goal

Create and maintain a **contract-id registry** that allows LumiBot IBKR REST backtesting to resolve:

- `Asset(asset_type="future", symbol=<root>, expiration=<YYYY-MM-DD>)` → conid
- `Asset(asset_type="cont_future", symbol=<root>)` → deterministic stitched series using LumiBot roll rules

without any “front-month fallback”.

## Storage (use existing cache layout)

Do **not** introduce a new registry format. Use LumiBot’s existing IBKR cache files:

- Conid map: `LUMIBOT_CACHE_FOLDER/ibkr/conids.json`
- Contract info (minTick/multiplier): `LUMIBOT_CACHE_FOLDER/ibkr/future/contracts/CONID_<conid>.json`

These already support optional S3 mirroring via the existing cache backend.

## Safety constraints

- Do not redeploy/restart the data downloader just to run this process (ThetaData must not be interrupted).
- It is OK if IBKR REST becomes unavailable during the backfill window.
- Do not write any real downloader hostnames/keys into docs. Use placeholders.
- Do not persist IBKR usernames/passwords anywhere.

## Seed universe

We backfill **US futures** (US region only). The seed list is sourced from IBKR’s public Symbol Lookup catalog:

- `https://www.interactivebrokers.com/en/trading/symbol.php`

We scrape the “Futures” product type and filter to “United States” region, then dedupe into a seed universe of:

- `(symbol_root, exchange, currency)`

Scripts:

- Seed fetch + dedupe: `scripts/fetch_ibkr_symbol_lookup_us_futures.py`
  - Output: `data/ibkr_symbol_lookup/us_futures_roots.csv` (currently ~323 roots)
- One-time backfill (TWS): `scripts/backfill_ibkr_futures_conids_tws.py`

## Results (2026-01-18)

One successful local backfill run (TWS API) produced:

- Seed universe: ~323 US futures roots
- `ibkr/conids.json`: ~17,612 keys (includes both `quote_symbol=""` and `quote_symbol="USD"` variants for compatibility)
- Unique contracts written: ~8,852 (explicit expirations)
- Failures: 2 roots (timeouts / no contract details returned)

Staging upload (dev):

- Cache version used: `LUMIBOT_CACHE_S3_VERSION=v2`
- Objects uploaded under the existing IBKR cache layout:
  - `ibkr/conids.json`
  - `ibkr/future/contracts/CONID_<conid>.json`

## One-time backfill (TWS API, local machine)

### Prerequisites

- TWS must be running and logged in (paper or live).
- API socket clients enabled in TWS.
- TWS API port reachable (typically `7497` paper / `7496` live).
- TWS must be *connected* to IB servers (lower-right status is green). If TWS is sitting on the login screen or offline,
  the “sec-def farm” stays broken and contract discovery fails.

### What the backfill collects

For each `(symbol_root, exchange, currency)` seed entry, request contract details with:

- `includeExpired=True`

Capture for each returned contract:

- conid
- `lastTradeDateOrContractMonth` (used as the canonical expiration date when it’s `YYYYMMDD`)
- `localSymbol`
- `multiplier`
- `minTick` (from `ContractDetails`)
- `tradingClass`

Persist to:

1) `conids.json` using the existing LumiBot key format (`IbkrConidKey`):

- `future|<root>||<EXCHANGE>|<YYYYMMDD>` → `<conid>`
- Also mirror the historical variant keyed with quote symbol (some caches used `USD` for futures):
  - `future|<root>|USD|<EXCHANGE>|<YYYYMMDD>` → `<conid>`

2) `future/contracts/CONID_<conid>.json` with at least:

- `multiplier`
- `minTick`
- `localSymbol`
- `tradingClass`
- `lastTradeDateOrContractMonth`

### S3 upload strategy

Use a **new S3 cache version** (e.g. `LUMIBOT_CACHE_S3_VERSION=v2`) so we don’t overwrite the current `v1` cache until
parity has passed.

Once validated:

- either copy/promote `v2` → `v1`, or
- explicitly switch the production runtime to `v2` for IBKR caches (preferred for safe rollout).

## Ongoing refresh (Client Portal only)

After the initial backfill, periodically refresh the registry from IBKR REST:

- Use `/trsrv/futures?symbols=...` to capture newly listed contracts **before they expire**.
- Merge into the same `conids.json` map and upload to S3.

This avoids needing TWS except for the initial historical backfill window.

Note: IBKR’s public Symbol Lookup response includes `conid` + `localSymbol` for *currently listed* futures contracts.
That can be used as an additional “no-auth” source for forward refresh, but it does not solve expired-contract discovery.

## Operations: seed the S3 conid registry (prod/dev) without rerunning TWS

If backtests are failing with errors like:

- `IBKR did not return a conid for <ROOT> expiring <YYYYMMDD> on <EXCHANGE>`

…and the target expiration is **expired** (no longer returned by `/ibkr/trsrv/futures`), you must ensure the S3-mirrored
registry contains it. You do **not** need to rerun TWS if a backfill registry already exists (for example the one in
`data/ibkr_tws_backfill_cache_dev_v2/ibkr/conids.json`).

### Safety checklist

- Always **download a backup** of the current S3 object before overwriting.
- Always **union-merge** keys (do not replace blindly).
- Prefer `aws --profile BotManager ...` when running from this machine.

### Targets (current)

- Prod conids: `s3://lumibot-cache-prod/prod/cache/v1/ibkr/conids.json`
- Dev conids: `s3://lumibot-cache-dev/dev/cache/v1/ibkr/conids.json`

Additional cache namespaces may exist (for example `dev/cache/v44/...`). Seed every namespace that is actively used by
backtests.

### Example (merge-before-upload)

```bash
# Backup
aws --profile BotManager s3 cp \
  s3://lumibot-cache-prod/prod/cache/v1/ibkr/conids.json \
  ./prod_conids.before.json

# Merge (seed wins except where remote already has a key)
python3 - <<'PY'
import json
from pathlib import Path

seed = json.loads(Path("data/ibkr_tws_backfill_cache_dev_v2/ibkr/conids.json").read_text())
remote = json.loads(Path("prod_conids.before.json").read_text())

merged = dict(seed)
merged.update(remote)  # remote wins on conflict

Path("prod_conids.merged.json").write_text(json.dumps(merged, sort_keys=True, separators=(",", ":")))
print("merged_keys", len(merged))
PY

# Upload
aws --profile BotManager s3 cp \
  ./prod_conids.merged.json \
  s3://lumibot-cache-prod/prod/cache/v1/ibkr/conids.json
```

### When you still need TWS

You still need a one-time TWS backfill when the desired expiration is **older than any conids you’ve captured** (for
example, if your registry only starts in 2024 and customers want 2015). In that case:

- run `scripts/backfill_ibkr_futures_conids_tws.py` (with `includeExpired=True`)
- upload the resulting `ibkr/conids.json` to a new cache namespace (e.g. `v2`, `v3`, …)
- validate, then promote/seed the production namespace using the merge flow above

## Verification

### Correctness

- `future` with explicit expiration must resolve the exact conid from `conids.json`.
- Missing expiration must hard-fail with a clear “registry missing” error (no silent fallback).
- `cont_future` must be stitched from explicit contracts using LumiBot roll rules.

### Parity (DataBento artifact baselines)

Re-run the approved baseline windows on IBKR and compare:

- `*_indicators.csv` (price-line proxy) within tick size
- `*_trades.csv` (sequence + fills) within tick size
- `*_stats.csv` (equity curve)

Primary harness:

- `scripts/run_ibkr_futures_parity_artifact_baselines.py` (runs cold/warm/yappi and writes a parity summary under `tests/backtest/_parity_runs/`)

## Blocking issue discovered: Client Portal history returning “Chart data unavailable”

During parity runs, the Client Portal history endpoint (`/iserver/marketdata/history`) returned:

- `500 {"error":"Chart data unavailable"}`

…even for liquid instruments (e.g., AAPL and MESZ5), while `auth/status` reported
`authenticated=true` and `connected=true`.

### What we validated

- The **conid registry is correct** (MESZ5 conid `730283085`, expiry `20251219`).
- The **local TWS API can fetch 1-minute TRADES bars** for MESZ5 in the baseline date range.
  This strongly suggests the issue is with the Client Portal gateway path (or how it’s being
  monitored/kept alive), not with the contracts or entitlements.

### Immediate mitigation for parity runs (local TWS → S3 cache)

To unblock “prod-like” parity runs when Client Portal history is unavailable, we can pre-populate
IBKR parquet caches via the local TWS API and upload them to the configured S3 cache version:

- `scripts/backfill_ibkr_futures_bars_tws_for_baselines.py`

This script:
- reads the 3 approved baseline windows (`*_settings.json`)
- computes the implied contract segments using `lumibot.tools.futures_roll`
- fetches TRADES OHLC bars via `reqHistoricalData` (minute/hour/day)
- writes the parquet caches using the same IBKR cache layout and uploads to S3 (via the existing cache backend)

This is intended as a **targeted bootstrap** (just the baseline windows) to keep parity work moving.

### Next steps (system fix)

Parity and production require Client Portal history to be reliable. The current strongest hypothesis
is that the gateway session is being destabilized by “helpful” background auth calls.

Evidence and candidate fix live in the downloader repo:
- The IBKR monitor was repeatedly invoking `ssodh/init`, which can flip `authenticated=false` and produce intermittent 401s/500s.
- We have a local code change ready to gate/remove that call (must be validated in a non-prod deploy first).

## Additional mitigation: avoid Bid_Ask/Midpoint history for futures by default

Even when the main futures history source is `Trades`, LumiBot previously attempted to derive futures bid/ask quotes
by fetching additional history sources (`Bid_Ask` + `Midpoint`). This adds 2× request volume and reintroduces the
Client Portal history flakiness (including stalls) into otherwise cache-satisfied runs.

Change:
- Futures bid/ask derivation is now **disabled by default** and can be re-enabled explicitly with:
  - `LUMIBOT_IBKR_ENABLE_FUTURES_BID_ASK=1`

Rationale:
- Futures backtests are intended to fill off TRADES/OHLC by default (tick-tolerant parity goals).
- The derived bid/ask model is optional and should not be on the critical path for parity, caching, or acceptance runs.

## Acceptance gotcha: closed-session gaps at window boundaries

Futures markets have long closed intervals (weekends/holidays). When a backtest window begins/ends inside a closed
interval, attempting to "fill to the boundary" will always return empty history and can create:

- unnecessary downloader/queue traffic, and
- flakiness / stalls in deterministic acceptance runs.

Mitigation:

- The IBKR futures backtesting path now treats *fully closed* `us_futures` intervals at the start/end of a requested
  window as "already satisfied" by the cache (no fetch attempts for those closed minutes).

## Parity note: “last completed bar” pricing + roll-boundary stitching

During parity work against stored DataBento artifact baselines, two changes materially improved stability:

- Futures `get_last_price(dt)` uses **last completed bar close** semantics (evaluate at `dt - 1µs`) to avoid implicit
  lookahead at minute boundaries and at the daily maintenance/weekend reopen.
- IBKR `cont_future` stitching widens each post-first segment by **1 minute** on the left so the “previous close”
  lookup remains valid at roll timestamps (the bar immediately preceding the roll boundary must exist in the new
  contract segment).

See: `docs/IBKR_DATABENTO_FUTURES_PARITY.md`.
