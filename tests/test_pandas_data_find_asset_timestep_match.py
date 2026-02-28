from __future__ import annotations

from datetime import datetime

import pandas as pd

from lumibot.data_sources.pandas_data import PandasData
from lumibot.entities import Asset, Data


def _minute_df(tz: str = "America/New_York") -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=3, freq="1min", tz=tz)
    return pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3], "volume": [0, 0, 0]}, index=idx)


def _day_df(tz: str = "America/New_York") -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=3, freq="D", tz=tz)
    return pd.DataFrame({"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3], "close": [1, 2, 3], "volume": [0, 0, 0]}, index=idx)


def test_find_asset_in_data_store_does_not_return_daily_for_minute_requests():
    base = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    daily = Data(base, _day_df(), timestep="day", quote=quote)

    ds = PandasData.__new__(PandasData)
    ds._data_store = {(base, quote): daily}  # type: ignore[attr-defined]

    # Simulate how crypto/forex often passes assets as a tuple while quote=None.
    asset_tuple = (base, quote)
    assert ds.find_asset_in_data_store(asset_tuple, quote=None, timestep="minute") is None
    assert ds.find_asset_in_data_store(asset_tuple, quote=None, timestep="day") == (base, quote)


def test_find_asset_in_data_store_allows_minute_data_to_satisfy_day_requests():
    base = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    minute = Data(base, _minute_df(), timestep="minute", quote=quote)

    ds = PandasData.__new__(PandasData)
    ds._data_store = {(base, quote): minute}  # type: ignore[attr-defined]

    asset_tuple = (base, quote)
    assert ds.find_asset_in_data_store(asset_tuple, quote=None, timestep="day") == (base, quote)
    assert ds.find_asset_in_data_store(asset_tuple, quote=None, timestep="minute") == (base, quote)

