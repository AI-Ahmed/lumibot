from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from lumibot.backtesting.interactive_brokers_rest_backtesting import InteractiveBrokersRESTBacktesting
from lumibot.entities import Asset


def test_ibkr_rest_backtesting_plumbs_history_source(monkeypatch):
    import lumibot.tools.ibkr_helper as ibkr_helper

    calls = {"count": 0}

    def fake_get_price_data(*, asset, quote, timestep, start_dt, end_dt, exchange=None, include_after_hours=True, source=None):
        calls["count"] += 1
        assert source == "Bid_Ask"
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

    ds = InteractiveBrokersRESTBacktesting(
        datetime_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
        datetime_end=datetime(2025, 1, 2, tzinfo=timezone.utc),
        history_source="Bid_Ask",
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
    )

    asset = Asset(symbol="BTC", asset_type="crypto")
    quote = Asset(symbol="USD", asset_type="forex")
    ds._update_pandas_data(
        asset=asset,
        quote=quote,
        timestep="minute",
        start_dt=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end_dt=datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc),
        exchange=None,
        include_after_hours=True,
    )

    assert calls["count"] == 1

