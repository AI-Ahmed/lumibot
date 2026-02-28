# Prod-like local benchmark runs (1-week windows)

These runs use `scripts/run_backtest_prodlike.py` with:

- Cold local disk: unique `--cache-folder` each run (to simulate ECS “fresh task” behavior)
- Cold→warm S3: same `LUMIBOT_CACHE_S3_VERSION` across runs
- Remote downloader enabled (prod-like)

## SPX Short Straddle (1 week)

- Strategy file: `Strategy Library/Demos/SPX Short Straddle Intraday (Copy 2).py`
- Window: `2025-04-21 → 2025-04-28`
- Cache version: `bench_spx_2025-04-21_2025-04-28_20260109_182049`

### Run #1 (cold S3)

- Wall time: `1875s` (~31m)
- Queue submits: `188`
- Endpoint breakdown:
  - `v3/option/history/quote`: `145`
  - `v3/option/list/strikes`: `33`
  - `v3/index/history/price`: `7`
  - `v3/index/history/ohlc`: `2`
  - `v3/option/list/expirations`: `1`

### Run #2 (warm S3, cold local)

- Wall time: `97s`
- Queue submits: `0`

## Alpha Picks Options (1 week)

- Strategy file: `Strategy Library/Demos/Alpha Picks Options.py`
- Window: `2025-05-05 → 2025-05-12`
- Cache version: `bench_alpha_2025-05-05_2025-05-12_20260109_185514`

### Run #1 (cold S3)

- Wall time: `3950s` (~66m)
- Queue submits: `211`
- Endpoint breakdown:
  - `v3/option/list/strikes`: `117`
  - `v3/option/history/eod`: `42`
  - `v3/option/history/quote`: `29`
  - `v3/stock/history/eod`: `5`
  - `v2/hist/stock/dividend`: `5`
  - `v2/hist/stock/split`: `5`
  - `v3/option/list/expirations`: `5`
  - `v3/stock/history/quote`: `3`

### Run #2 (warm S3, cold local)

- Wall time: `229s`
- Queue submits: `30`
- Endpoint breakdown:
  - `v3/option/list/strikes`: `12`
  - `v3/option/history/quote`: `9`
  - `v3/option/history/eod`: `8`
  - `v3/option/list/expirations`: `1`

### Run #3 (warm S3, cold local)

- Wall time: `34s`
- Queue submits: `0`

## Takeaways

- These strategies are dramatically faster when S3 cache is warm (even with cold local disk).
- The remaining cold-run time is overwhelmingly downloader hydration, not CPU.
- The biggest cold-run drivers match production:
  - SPX: `option/history/quote`
  - Alpha Picks: `option/list/strikes` (+ quote/eod)

## Fanout reduction smoke test (post-change)

After updating `OptionsHelper.find_strike_for_delta()` to use a Black–Scholes delta-inversion estimate + bounded probing (fallback to the legacy binary walk):

- Strategy: SPX Short Straddle
- Window: `2025-04-21 → 2025-04-22` (2 days; validation smoke test)
- Cache version: `bench_spx_2d_2025-04-21_2025-04-22_20260109_201620`
- Wall time: `348s`
- Queue submits: `68`
- Endpoint breakdown:
  - `v3/option/list/strikes`: `33`
  - `v3/option/history/quote`: `29`
  - `v3/index/history/price`: `3`
  - `v3/index/history/ohlc`: `2`
  - `v3/option/list/expirations`: `1`
