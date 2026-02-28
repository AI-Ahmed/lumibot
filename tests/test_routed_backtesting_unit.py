from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from lumibot.backtesting.routed_backtesting import RoutedBacktestingPandas
from lumibot.backtesting.thetadata_backtesting_pandas import ThetaDataBacktestingPandas
from lumibot.entities import Asset


def test_router_routes_crypto_to_ibkr(monkeypatch):
    import lumibot.tools.thetadata_helper as thetadata_helper
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setattr(ThetaDataBacktestingPandas, "kill_processes_by_name", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(thetadata_helper, "reset_theta_terminal_tracking", lambda *_args, **_kwargs: None)

    calls = {"ibkr": 0}

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True):
        calls["ibkr"] += 1
        idx = pd.DatetimeIndex(
            [
                datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc),
            ]
        ).tz_convert("America/New_York")
        return pd.DataFrame(
            {"open": [1.0, 2.0], "high": [1.1, 2.1], "low": [0.9, 1.9], "close": [1.0, 2.0], "volume": [10, 11]},
            index=idx,
        )

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    ds = RoutedBacktestingPandas(
        datetime_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime_end=datetime(2025, 1, 2, tzinfo=timezone.utc),
        config={"backtesting_data_routing": {"crypto": "ibkr", "default": "thetadata"}},
        username="dev",
        password="dev",
        use_quote_data=False,
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
    )

    asset = Asset(symbol="BTC", asset_type="crypto")
    quote = Asset(symbol="USD", asset_type="forex")
    ds._update_pandas_data(asset, quote, length=2, timestep="minute", start_dt=datetime(2025, 1, 2, tzinfo=timezone.utc))

    assert calls["ibkr"] == 1
    assert ds._data_store


def test_router_accepts_futures_key_alias(monkeypatch):
    import lumibot.tools.thetadata_helper as thetadata_helper
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setattr(ThetaDataBacktestingPandas, "kill_processes_by_name", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(thetadata_helper, "reset_theta_terminal_tracking", lambda *_args, **_kwargs: None)

    calls = {"ibkr": 0}

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True, source=None):
        calls["ibkr"] += 1
        idx = pd.DatetimeIndex(
            [
                datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
                datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc),
            ]
        ).tz_convert("America/New_York")
        return pd.DataFrame(
            {"open": [1.0, 2.0], "high": [1.1, 2.1], "low": [0.9, 1.9], "close": [1.0, 2.0], "volume": [10, 11]},
            index=idx,
        )

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    ds = RoutedBacktestingPandas(
        datetime_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime_end=datetime(2025, 1, 2, tzinfo=timezone.utc),
        config={"backtesting_data_routing": {"futures": "ibkr", "default": "thetadata"}},
        username="dev",
        password="dev",
        use_quote_data=False,
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
    )

    fut = Asset(symbol="MES", asset_type="future")
    quote = Asset(symbol="USD", asset_type="forex")
    ds._update_pandas_data(fut, quote, length=2, timestep="minute", start_dt=datetime(2025, 1, 2, tzinfo=timezone.utc))

    assert calls["ibkr"] == 1
