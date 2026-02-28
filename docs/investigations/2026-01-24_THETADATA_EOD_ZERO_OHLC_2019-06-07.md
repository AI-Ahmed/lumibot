# ThetaData EOD: All-Zero OHLC Bar (2019-06-07) Causing Portfolio "Cliff"

## Summary

Some ThetaData EOD (`/v3/*/history/eod`) responses contain a **placeholder trading day** where `open=high=low=close=0`.
If LumiBot treats that row as a real price, **portfolio valuation can collapse to ~0 for a single bar** and then rebound
on the next bar, producing a dramatic one-day “cliff” in the equity curve.

This was observed during a long daily backtest where the equity curve dropped sharply around **June 2019** (often reported
as “June 9, 2019” depending on chart/timezone labeling), but the underlying trading-day row is **2019-06-07**.

## Why it happens

- The ThetaData EOD endpoint occasionally emits an EOD row for a valid trading day with **all-zero OHLC** (and small volume).
- That row is not a holiday; **2019-06-07 was a normal trading day**.
- If a strategy holds an asset on that day, mark-to-market can briefly value the position at `0`, collapsing equity.

## Fix implemented

In `lumibot/tools/thetadata_helper.py`, `get_historical_eod_data()` now:

- Detects **all-zero OHLC** rows for **stock/index EOD** only.
- Converts those rows to **missing placeholders**:
  - `open/high/low/close` set to `NaN`
  - `missing=True`
  - `volume=0` (when present)

Downstream, `Data.repair_times_and_fill()` forward-fills `close` (and then fills `open/high/low` from `close`), so
the placeholder day uses the last known close rather than valuing at 0.

## Scope / Safety notes

- Applied only to `asset_type in {"stock", "index"}`.
- Not applied to option EOD, because option EOD can legitimately have `OHLC=0` when only NBBO fields are populated and
we do not want to mask that scenario.
- No new environment variables.
- No reliance on hardcoded downloader endpoints; normal downloader configuration via `DATADOWNLOADER_BASE_URL` applies.

## Regression coverage

- `tests/test_thetadata_eod_zero_ohlc.py` ensures the all-zero bar is marked `missing=True` and OHLC becomes `NaN`.

