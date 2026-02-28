from __future__ import annotations

from datetime import timedelta

import pandas as pd

from lumibot.backtesting.interactive_brokers_rest_backtesting import InteractiveBrokersRESTBacktesting
from lumibot.entities import Asset


def test_ibkr_crypto_get_quote_prefers_daily_series_at_midnight(monkeypatch):
    import lumibot.tools.ibkr_helper as ibkr_helper

    idx = pd.date_range("2025-01-01 00:00", periods=3, freq="D", tz="America/New_York")
    df_day = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "bid": [100.5, 101.5, 102.5],
            "ask": [100.5, 101.5, 102.5],
            "volume": [1, 1, 1],
        },
        index=idx,
    )

    calls: list[dict] = []

    def fake_get_price_data(*, timestep: str, **kwargs):
        calls.append({"timestep": timestep})
        return df_day

    monkeypatch.setattr(ibkr_helper, "get_price_data", fake_get_price_data)

    base = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

    ds = InteractiveBrokersRESTBacktesting(
        datetime_start=idx[0].to_pydatetime(),
        datetime_end=(idx[-1] + timedelta(days=1)).to_pydatetime(),
        market="24/7",
        show_progress_bar=False,
        log_backtest_progress_to_file=False,
    )
    ds.load_data()

    monkeypatch.setattr(ds, "get_datetime", lambda: idx[1].to_pydatetime())
    q = ds.get_quote(base, quote=quote)

    assert q is not None
    assert float(getattr(q, "price", 0.0)) == 101.5
    assert calls, "Expected IBKR price data fetch to seed the daily series"
    assert all(call["timestep"] == "day" for call in calls)
