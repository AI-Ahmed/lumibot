# Production speed: endpoint breakdown (existing runs)

This note captures CloudWatch Insights evidence for which Theta downloader endpoints dominate two “real” production backtests.

## How the queries were run

- Log group: `/aws/ecs/prod-trading-bots-backtest`
- Correlation key: `manager_bot_id` appears in `@logStream` (example: `<manager_bot_id>/bot-<manager_bot_id>/<task_id>`).
- Query:
  - Filter: `@logStream like /<manager_bot_id>/` and `@message like /Submitted to queue/`
  - Parse: `path=...`
  - Aggregate: `stats count() as submits by path`

## Benchmarks

### SPX (full-year run)

- `manager_bot_id`: `b5e57e7d-21fe-47e6-b80d-f03ff2f47de2`
- Endpoint submits (last 14 days, within this log stream):
  - `v3/option/history/quote`: `2287`
  - `v3/index/history/price`: `214`

### Alpha Picks Options (month-ish run)

- `manager_bot_id`: `0eb965e9-92fc-4dd6-8bda-9e76bbbbb3ab`
- Endpoint submits (last 14 days, within this log stream):
  - `v3/option/list/strikes`: `642`
  - `v3/option/history/quote`: `462`
  - `v3/option/history/eod`: `219`
  - `v3/option/list/expirations`: `17`

## Interpretation

- SPX is dominated by `option/history/quote` fanout.
- Alpha Picks Options is dominated by chain-building (`option/list/strikes`) plus quote/eod history.

