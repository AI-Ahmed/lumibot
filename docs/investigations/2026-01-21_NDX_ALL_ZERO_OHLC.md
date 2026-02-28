# NDX Minute OHLC Returns All-Zero (Prod) — Investigation Notes
#
# Date: 2026-01-21
# Scope: LumiBot + ThetaData integration behavior when vendor returns placeholder OHLC
#
# IMPORTANT (per LumiBot AGENTS.md):
# - Do NOT hardcode or commit production downloader hostnames/URLs.
# - Use placeholders and env vars (e.g., DATADOWNLOADER_BASE_URL).
# - Do NOT paste production credentials.
#

## Summary

In production backtests, requesting minute OHLC for the index symbol `NDX` returns OHLC arrays where
`open/high/low/close` are all `0.0` across an entire window (hundreds of rows). LumiBot treats all-zero
OHLC rows as vendor placeholder/bad data and filters them out, resulting in an empty dataset.

This can cascade into:
- repeated cache misses and repeated downloads,
- “No OHLC data returned … refusing to proceed” errors, and/or
- long-running backtests that accomplish little (depending on the higher-level strategy loop).

This is not a “strategy code” bug; it is a data-quality + resilience handling issue.

## Evidence (what we saw)

Using the downloader queue contract (method/path/query_params), index minute OHLC:
- `SPX`, `VIX`, `VXN` → non-zero OHLC
- `NDX` → all-zero OHLC for the same time window

LumiBot data-quality filter is implemented in:
- `/Users/robertgrzesik/Documents/Development/lumivest_bot_server/strategies/lumibot/lumibot/tools/thetadata_helper.py`
  - `update_df(...)` filters rows where open/high/low/close are all zero.

That filter is correct in principle, but the system must not “spin” forever when the vendor returns
placeholder OHLC for the entire requested window.

## Reproduction (safe / local)

1) Set the downloader endpoint via env vars:
- `DATADOWNLOADER_BASE_URL=http://<your-downloader-host>:8080`
- `DATADOWNLOADER_API_KEY=<your-downloader-api-key>`

2) Submit a request via the queue client (or direct HTTP):
- method: `GET`
- path: `/v3/index/history/ohlc`
- query_params:
  - `symbol=NDX`
  - `interval=1m`
  - `start_date=YYYY-MM-DD`
  - `end_date=YYYY-MM-DD`
  - `format=json`

3) Verify the response:
- open/high/low/close arrays are all `0.0` for all returned rows.

4) Feed into `thetadata_helper.update_df`:
- The function logs a `[THETA][DATA_QUALITY] Filtering ... all-zero OHLC ...` warning.
- The resulting dataframe becomes empty.

## Why this matters

NDX is a common underlying for index/options strategies. If minute OHLC is not available for NDX via
ThetaData (or requires a different vendor symbol), the platform needs one of:
- a correct canonical-to-vendor symbol mapping for NDX that still preserves “NDX means NDX” semantics, or
- a fast, explicit error that NDX minute OHLC is not available (no silent proxy substitution).

User constraint (from ops side):
- Do NOT silently substitute QQQ for NDX.

## Proposed next steps (game plan)

1) Confirm vendor support:
- Does ThetaData support minute OHLC for NDX at all?
- If yes, determine the correct vendor symbol spelling for the NDX index.

2) If symbol mapping is required:
- Implement canonical `NDX` → vendor-correct symbol mapping inside ThetaData integration.
- Document it as “vendor symbol mapping”, not “proxy substitution”.

3) If ThetaData truly does not provide NDX minute OHLC:
- Fail fast with a clear explanation:
  - “NDX minute OHLC not available from this provider. Choose a supported provider/symbol.”

4) Add regression tests:
- Ensure “all-zero OHLC for entire window” produces a single clear failure (no infinite retries).

## Cross-reference

System-wide handoff doc (BotSpot stack):
- `/Users/robertgrzesik/Documents/Development/botspot_node/docs/handoffs/2026-01-21_backtests_stale-status_sync_and_data_issues.md`

