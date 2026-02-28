# Production Speed Parity — YAPPI Findings (2026-01-03)

Goal: explain the **production vs local speed gap** with profiler evidence (not guesses).

This doc records one profiled production run and a comparable profiled local run.

## Production run (profile enabled)

- Bot Manager `bot_id`: `codexspx3280b3dee4b7410fba`
- Strategy code: `Demos/SPX Short Straddle Intraday (Copy).py` (used as portable `main.py` in Bot Manager; local `sys.path` preamble removed)
- Window: `2025-01-06 → 2025-12-24`
- Flags (prod-like + profiling):
  - `BACKTESTING_DATA_SOURCE=thetadata`
  - `DATADOWNLOADER_BASE_URL=https://<your-downloader-host>:8080`
  - `SHOW_PLOT=True`, `SHOW_INDICATORS=True`, `SHOW_TEARSHEET=True`
  - `BACKTESTING_QUIET_LOGS=false`, `BACKTESTING_SHOW_PROGRESS_BAR=true`
  - `BACKTESTING_PROFILE=yappi`
- Observed runtime (Bot Manager status `elapsed`): **~32 minutes**
- Artifacts (Bot Manager status): `profile_yappi_csv=true` (confirmed downloadable)

Downloaded artifacts (local paths; not committed):
- `Strategy Library/logs/prod_profiles/codexspx3280b3dee4b7410fba_profile_yappi_raw.csv`
- `Strategy Library/logs/prod_profiles/codexspx3280b3dee4b7410fba_stats_csv`
- `Strategy Library/logs/prod_profiles/codexspx3280b3dee4b7410fba_trades_csv`

### Top YAPPI hotspots (production)

From `codexspx3280b3dee4b7410fba_profile_yappi_raw.csv`:
- `/usr/local/lib/python3.12/threading.py Condition.wait` (`ttot_s ≈ 6003`)
- `/usr/local/lib/python3.12/threading.py Event.wait` (`ttot_s ≈ 4005`)
- `/usr/local/lib/python3.12/queue.py Queue.get` (`ttot_s ≈ 1999`)
- `lumibot.tools.data_downloader_queue_client.QueueClient.execute_request / wait_for_result` (`ttot_s ≈ 1048 / 1029`)
- `lumibot.tools.thetadata_helper.get_request / get_historical_data` (`ttot_s ≈ 1050 / 1289`)
- `lumibot.backtesting.thetadata_backtesting_pandas.ThetaDataBacktestingPandas.get_quote` (`ttot_s ≈ 1289`)

Interpretation:
- The run is **dominated by waiting** (threading/queue waits + downloader queue polling), not CPU compute.
- This is consistent with “too many downloader requests” (high `ncall` counts for quote/historical fetch paths).
- It is **not** consistent with a single “silent hang” socket wedge (requests return; the backtest progresses).

## Local run (profile enabled)

- Run id: `SPXShortStraddle_2026-01-02_20-36_dst4Kh`
- Window: `2025-01-06 → 2025-12-24`
- Flags: same as production (prod-like + `BACKTESTING_PROFILE=yappi`)
- Observed runtime (local log): **~2m18s**
- Profile artifact: `Strategy Library/logs/SPXShortStraddle_2026-01-02_20-36_dst4Kh_profile_yappi.csv`

Key difference observed in logs:
- Local run uses the **index fast-path** (“closest strike without quote probe”), which reduces quote-validation probing and cuts request volume.

## Action items (next)

1. **Deploy v4.4.22** (includes the index fast-path perf fix) to production.
2. Re-run the same profiled production backtest and compare:
   - wall time
   - `ncall` for `data_downloader_queue_client.queue_request`, `thetadata_backtesting_pandas.get_quote`
   - share of time in `threading/queue` waits vs non-waiting work
3. If production is still materially slower after request-count reduction:
   - investigate downloader throughput / queue contention
   - investigate ECS CPU/memory sizing
   - consider additional caching/negative-caching tweaks (only with accuracy preserved)
