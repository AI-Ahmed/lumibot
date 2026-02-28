# Schwab + Tradier Runtime Readiness Audit (LumiBot)

Date: 2026-02-06  
Owner: Codex research pass  
Status: Research only (no lumi runtime code changes in this doc)

## Scope

Audit LumiBot runtime support and gaps for:

1. Schwab live deployment path
2. Tradier current token model vs future OAuth ambitions

## Current runtime state

### Schwab support (present)

`lumibot/brokers/schwab.py` supports:

- `SCHWAB_TOKEN` payload ingestion
- token file normalization and persistence
- OAuth session refresh flow (uses `SCHWAB_APP_SECRET` when present)
- live account usage via `SCHWAB_ACCOUNT_NUMBER`

`lumibot/credentials.py` supports:

- `TRADING_BROKER=schwab`
- auto-select Schwab when `SCHWAB_ACCOUNT_NUMBER` exists

### Tradier support (present)

`lumibot/brokers/tradier.py` and related data source code are mature with broad test coverage.
Current auth model expects static access token + account number.

## Gaps and inconsistencies

## P0 correctness/documentation drift

1. `schwab.py` logs that `SCHWAB_APP_SECRET` is no longer used, but later uses it for token refresh support.
2. `data_sources/schwab_data.py` still references legacy env names (`SCHWAB_API_KEY`, `SCHWAB_SECRET`) while broker path uses `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_BACKEND_CALLBACK_URL`.

## P1 coverage and operational risk

1. Schwab test coverage is thin compared with Tradier coverage.
2. No robust automated path tests around:
   - payload decode/write
   - refresh failure behavior
   - account hash bootstrap edge cases.

## Recommended changes

## Phase 1 (before scaling Schwab usage)

1. Align Schwab env naming across broker + data source modules.
2. Remove/replace misleading `SCHWAB_APP_SECRET` log message.
3. Publish single canonical Schwab env contract in Lumi docs.

## Phase 2 (stability)

1. Add unit tests for:
   - token payload processing
   - refresh with and without app secret
   - expected failures when account number or callback env is missing
2. Add non-default smoke test path for Schwab order preview / submit / cancel with strict safety guards.

## Phase 3 (Tradier OAuth only if requested)

1. If Tradier OAuth is added upstream, implement token-refresh lifecycle integration (or keep manual long-lived token model).
2. Do not switch default Tradier runtime contract until refresh lifecycle is proven.

## Open questions

1. Should Schwab runtime require `SCHWAB_APP_SECRET` for production deploys to guarantee refresh support?
2. Do we need runtime-level position/order notional caps for first-release Schwab live trading?
3. Should Tradier remain manual-token only for now, given current runtime assumptions?

## External references

- Schwab User Guide: Authenticate with OAuth  
  https://developer.schwab.com/user-guides/get-started/authenticate-with-oauth
- Schwab User Guide: OAuth Restart vs Refresh Token  
  https://developer.schwab.com/user-guides/apis-and-apps/oauth-restart-vs-refresh-token
- Tradier FAQ  
  https://docs.tradier.com/docs/faq
- Tradier Authentication  
  https://docs.tradier.com/docs/authentication

