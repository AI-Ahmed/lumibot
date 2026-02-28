from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from lumibot.backtesting.routed_backtesting import RoutedBacktestingPandas
from lumibot.constants import LUMIBOT_DEFAULT_PYTZ
from lumibot.entities import Asset


def _minute_ohlc(start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_dt)
    end_ts = pd.Timestamp(end_dt)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize(LUMIBOT_DEFAULT_PYTZ)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize(LUMIBOT_DEFAULT_PYTZ)
    start_ts = start_ts.tz_convert(LUMIBOT_DEFAULT_PYTZ)
    end_ts = end_ts.tz_convert(LUMIBOT_DEFAULT_PYTZ)

    idx = pd.date_range(start_ts, end_ts, freq="1min")
    px = (pd.Series(range(len(idx)), index=idx, dtype="float64") * 0.01) + 100.0
    return pd.DataFrame(
        {
            "open": px,
            "high": px + 0.01,
            "low": px - 0.01,
            "close": px,
            "volume": 1000,
        },
        index=idx,
    )


def _multi_minute_ohlc(start_dt: datetime, end_dt: datetime, *, minutes: int) -> pd.DataFrame:
    start_ts = pd.Timestamp(start_dt)
    end_ts = pd.Timestamp(end_dt)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize(LUMIBOT_DEFAULT_PYTZ)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize(LUMIBOT_DEFAULT_PYTZ)
    start_ts = start_ts.tz_convert(LUMIBOT_DEFAULT_PYTZ)
    end_ts = end_ts.tz_convert(LUMIBOT_DEFAULT_PYTZ)

    idx = pd.date_range(start_ts, end_ts, freq=f"{int(minutes)}min")
    px = (pd.Series(range(len(idx)), index=idx, dtype="float64") * 0.1) + 1000.0
    return pd.DataFrame(
        {
            "open": px,
            "high": px + 0.1,
            "low": px - 0.1,
            "close": px,
            "volume": 1000,
        },
        index=idx,
    )


def test_router_ibkr_prefetches_full_window_once_for_cont_future_minute(monkeypatch):
    import lumibot.tools.ibkr_helper as ibkr_helper

    # Avoid any local ThetaTerminal side effects during datasource init.
    monkeypatch.setenv("DATADOWNLOADER_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("DATADOWNLOADER_API_KEY", "<redacted>")

    start = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 0, 0))
    end = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 2, 0))

    router = RoutedBacktestingPandas(
        datetime_start=start,
        datetime_end=end,
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
        config={
            "backtesting_data_routing": {
                "default": "thetadata",
                "future": "ibkr",
                "cont_future": "ibkr",
                "crypto": "ibkr",
            }
        },
    )
    router.load_data()

    asset = Asset("NQ", asset_type=Asset.AssetType.CONT_FUTURE)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    calls: list[dict] = []

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True, source=None):
        calls.append(
            {
                "asset": asset,
                "quote": quote,
                "timestep": timestep,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "exchange": exchange,
                "include_after_hours": include_after_hours,
                "source": source,
            }
        )
        return _minute_ohlc(start_dt, end_dt)

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    # First access should prefetch the full backtest window once.
    router._datetime = start
    _ = router.get_historical_prices(asset, length=80, timestep="minute", quote=quote)

    # Subsequent accesses must slice in-memory without calling the underlying fetch again.
    router._datetime = start + timedelta(minutes=30)
    _ = router.get_historical_prices(asset, length=80, timestep="minute", quote=quote)

    router._datetime = start + timedelta(minutes=90)
    _ = router.get_historical_prices(asset, length=80, timestep="minute", quote=quote)

    assert len(calls) == 1, f"Expected a single IBKR prefetch call, got {len(calls)}"

    first = calls[0]
    assert first["timestep"] == "minute"
    assert first["end_dt"] == router.datetime_end
    assert first["start_dt"] <= router.datetime_start


def test_router_ibkr_prefetches_full_window_once_for_cont_future_multi_minute(monkeypatch):
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setenv("DATADOWNLOADER_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("DATADOWNLOADER_API_KEY", "<redacted>")

    start = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 0, 0))
    end = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 6, 0, 0))

    router = RoutedBacktestingPandas(
        datetime_start=start,
        datetime_end=end,
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
        config={"backtesting_data_routing": {"default": "thetadata", "cont_future": "ibkr"}},
    )
    router.load_data()

    asset = Asset("GC", asset_type=Asset.AssetType.CONT_FUTURE)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    calls: list[dict] = []

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True, source=None):
        calls.append({"timestep": timestep, "start_dt": start_dt, "end_dt": end_dt})
        assert timestep == "60minute"
        return _multi_minute_ohlc(start_dt, end_dt, minutes=60)

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    router._datetime = start + timedelta(hours=10)
    bars = router.get_historical_prices(asset, length=5, timestep="60m", quote=quote)
    assert bars is not None
    assert getattr(bars, "df", None) is not None
    assert len(bars.df) == 5

    router._datetime = start + timedelta(hours=14)
    _ = router.get_historical_prices(asset, length=5, timestep="60m", quote=quote)

    assert len(calls) == 1, f"Expected a single IBKR prefetch call, got {len(calls)}"

    canonical_key = (asset, quote, "60minute")
    data_obj = router._data_store.get(canonical_key)
    assert data_obj is not None
    assert getattr(data_obj, "_native_timestep_quantity", None) == 60
    assert getattr(data_obj, "_native_timestep_unit", None) == "minute"


def test_router_ibkr_prefetch_slices_expected_window(monkeypatch):
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setenv("DATADOWNLOADER_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("DATADOWNLOADER_API_KEY", "<redacted>")

    start = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 0, 0))
    end = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 2, 0))

    router = RoutedBacktestingPandas(
        datetime_start=start,
        datetime_end=end,
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
        config={"backtesting_data_routing": {"default": "thetadata", "cont_future": "ibkr"}},
    )
    router.load_data()

    asset = Asset("NQ", asset_type=Asset.AssetType.CONT_FUTURE)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True, source=None):
        return _minute_ohlc(start_dt, end_dt)

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    router._datetime = start + timedelta(minutes=75)
    bars = router.get_historical_prices(asset, length=5, timestep="minute", quote=quote)
    assert bars is not None
    df = getattr(bars, "df", None)
    assert df is not None and not df.empty
    assert len(df) == 5

    last_ts = df.index.max()
    assert last_ts <= pd.Timestamp(router.get_datetime())


def test_router_ibkr_prefetches_full_window_once_for_crypto_minute(monkeypatch):
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setenv("DATADOWNLOADER_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("DATADOWNLOADER_API_KEY", "<redacted>")

    start = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 0, 0))
    end = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 5, 2, 0))

    router = RoutedBacktestingPandas(
        datetime_start=start,
        datetime_end=end,
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
        config={"backtesting_data_routing": {"default": "thetadata", "crypto": "ibkr"}},
    )
    router.load_data()

    asset = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    calls: list[dict] = []

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True, source=None):
        calls.append({"timestep": timestep, "start_dt": start_dt, "end_dt": end_dt})
        return _minute_ohlc(start_dt, end_dt)

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    router._datetime = start + timedelta(minutes=30)
    _ = router.get_historical_prices(asset, length=10, timestep="minute", quote=quote)
    router._datetime = start + timedelta(minutes=90)
    _ = router.get_historical_prices(asset, length=10, timestep="minute", quote=quote)

    assert len(calls) == 1
    assert calls[0]["end_dt"] == router.datetime_end
    assert calls[0]["start_dt"] <= router.datetime_start
