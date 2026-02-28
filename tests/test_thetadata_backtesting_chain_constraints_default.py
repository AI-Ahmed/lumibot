from datetime import datetime, timedelta

import pytz

from lumibot.backtesting import ThetaDataBacktestingPandas
from lumibot.entities import Asset
from lumibot.tools import thetadata_helper


def _make_theta_backtest_ds(*, now: datetime, timestep: str):
    theta = ThetaDataBacktestingPandas.__new__(ThetaDataBacktestingPandas)
    theta._timestep = timestep
    theta._chains_cache_date = None
    theta._chains_cache = {}

    def _get_datetime():
        return now

    theta.get_datetime = _get_datetime  # type: ignore[attr-defined]
    return theta


def test_thetadata_chains_default_max_expiration_intraday(monkeypatch):
    now = datetime(2025, 4, 21, 9, 30, tzinfo=pytz.UTC)
    current_date = now.date()
    captured = {}

    def fake_get_chains_cached(asset, current_date, chain_constraints=None, **_):
        captured["constraints"] = dict(chain_constraints or {})
        return {"Multiplier": 100, "Exchange": "SMART", "Chains": {"CALL": {}, "PUT": {}}}

    monkeypatch.setattr(thetadata_helper, "get_chains_cached", fake_get_chains_cached)

    theta = _make_theta_backtest_ds(now=now, timestep="minute")

    index_asset = Asset("SPXW", asset_type=Asset.AssetType.INDEX)
    theta.get_chains(index_asset)
    assert captured["constraints"]["max_expiration_date"] == current_date + timedelta(days=45)

    stock_asset = Asset("AAPL", asset_type=Asset.AssetType.STOCK)
    theta.get_chains(stock_asset)
    assert captured["constraints"]["max_expiration_date"] == current_date + timedelta(days=60)


def test_thetadata_chains_default_max_expiration_uses_min_date_if_later(monkeypatch):
    now = datetime(2025, 4, 21, 9, 30, tzinfo=pytz.UTC)
    current_date = now.date()
    captured = {}

    def fake_get_chains_cached(asset, current_date, chain_constraints=None, **_):
        captured["constraints"] = dict(chain_constraints or {})
        return {"Multiplier": 100, "Exchange": "SMART", "Chains": {"CALL": {}, "PUT": {}}}

    monkeypatch.setattr(thetadata_helper, "get_chains_cached", fake_get_chains_cached)

    theta = _make_theta_backtest_ds(now=now, timestep="minute")
    theta._chain_constraints = {"min_expiration_date": current_date + timedelta(days=90)}

    theta.get_chains(Asset("AAPL", asset_type=Asset.AssetType.STOCK))
    assert captured["constraints"]["max_expiration_date"] == current_date + timedelta(days=150)


def test_thetadata_chains_does_not_force_max_expiration_for_day_mode(monkeypatch):
    now = datetime(2025, 4, 21, 9, 30, tzinfo=pytz.UTC)
    captured = {}

    def fake_get_chains_cached(asset, current_date, chain_constraints=None, **_):
        captured["constraints"] = dict(chain_constraints or {})
        return {"Multiplier": 100, "Exchange": "SMART", "Chains": {"CALL": {}, "PUT": {}}}

    monkeypatch.setattr(thetadata_helper, "get_chains_cached", fake_get_chains_cached)

    theta = _make_theta_backtest_ds(now=now, timestep="day")
    theta.get_chains(Asset("AAPL", asset_type=Asset.AssetType.STOCK))
    assert "max_expiration_date" not in captured["constraints"]
