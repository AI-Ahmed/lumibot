# IBKR Futures Backtesting (Phase 3) — Implementation Notes (2026-01-15)

## What changed (high level)

This work extends the IBKR Client Portal (REST) backtesting stack to support deterministic **CME equity index futures** backtests (e.g., MES) with the same “fast + cache-first” behavior that IBKR crypto backtests use.

Key pieces:

- **Deterministic acceptance backtests** for IBKR crypto + IBKR futures (`tests/backtest/test_acceptance_backtests_ci.py`) with strict headline metric assertions (0.01% resolution) and a **queue-free invariant** (`thetadata_queue_telemetry.submit_requests == 0`).
- **Quote-based fill support for STOP / STOP_LIMIT / TRAIL** in the core backtesting broker so futures/crypto can fill correctly even when the provider series is quote-like (OHLC highs/lows missing).
- **Futures metadata support (multiplier + minTick)** to make PnL and SMART_LIMIT tick rounding realistic for contracts like MES.
- **Session-aligned futures daily bars** (aligned to `us_futures` session boundaries, not midnight) and a small end-of-window tolerance to avoid repeated downloader retries.
- **Warm-cache backtest apitests** for both crypto and futures to ensure a warmed local cache prevents any downloader calls in a follow-up backtest.

## Why the STOP/TRAIL change was required

IBKR history feeds (and some derived series) can be quote-like: candles may lack actionable `high/low` values even when `close` is present. In those cases, the backtest engine must still:

- advance trailing stop levels, and
- trigger stop orders,

using bid/ask (or midpoint) rather than relying on OHLC highs/lows.

This is implemented in `lumibot/backtesting/backtesting_broker.py` by extending quote-fallback fills to handle STOP, STOP_LIMIT, and TRAIL properly.

## Deterministic acceptance artifacts

- Baselines live in `tests/backtest/acceptance_backtests_baselines.json`.
- Scripts live in `tests/backtest/acceptance_strategies/`.
- The IBKR futures acceptance script uses an explicit contract for determinism (MES expiration in late 2025).

## Related docs

- `docs/IBKR_FUTURES_BACKTESTING.md`
- `docsrc/backtesting.ibkr.rst`

