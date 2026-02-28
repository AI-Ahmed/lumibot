from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from lumibot.entities import Asset
from lumibot.tools import thetadata_helper


class _DisabledCacheManager:
    enabled = False


def _tz(dt: datetime) -> datetime:
    return thetadata_helper.LUMIBOT_DEFAULT_PYTZ.localize(dt)


def test_snapshot_cache_fetches_full_regular_session_for_options(monkeypatch, tmp_path):
    """Regression: `prefer_full_session=True` must fetch the full regular session for options.

    NVDA/SPX backtests call `get_quote(snapshot_only=True)` many times per day. If snapshot caching
    fetches only a tiny dt-window but keys the cache as a full-session payload, we end up enqueuing
    hundreds/thousands of downloader requests and can also return stale/partial quote frames.
    """
    option = Asset(
        "NVDA",
        asset_type="option",
        expiration=date(2014, 7, 25),
        strike=27.5,
        right="call",
        underlying_asset=Asset("NVDA", asset_type="stock"),
    )

    day = date(2014, 5, 16)
    start_dt = _tz(datetime(2014, 5, 16, 14, 29))
    end_dt = start_dt + timedelta(minutes=6)

    monkeypatch.setattr(thetadata_helper, "get_trading_dates", lambda *_: [day])
    monkeypatch.setattr(thetadata_helper, "get_backtest_cache", lambda: _DisabledCacheManager())
    monkeypatch.setattr(
        thetadata_helper,
        "build_snapshot_cache_filename",
        lambda *_args, **_kwargs: tmp_path / "cache.parquet",
    )
    monkeypatch.setattr(thetadata_helper, "update_cache", lambda *_args, **_kwargs: None)

    captured: dict[str, object] = {}

    def fake_get_historical_data(_asset, fetch_start, fetch_end, *_args, **_kwargs):
        captured["start"] = fetch_start
        captured["end"] = fetch_end
        return pd.DataFrame({"bid": [1.0], "ask": [1.1]}, index=pd.DatetimeIndex([fetch_start]))

    monkeypatch.setattr(thetadata_helper, "get_historical_data", fake_get_historical_data)

    thetadata_helper.get_historical_data_snapshot_cached(
        option,
        start_dt,
        end_dt,
        60_000,
        datastyle="quote",
        include_after_hours=True,
        prefer_full_session=True,
    )

    start = captured.get("start")
    end = captured.get("end")
    assert isinstance(start, datetime)
    assert isinstance(end, datetime)
    assert start.date() == day
    assert end.date() == day
    assert start.strftime("%H:%M:%S") == "09:30:00"
    assert end.strftime("%H:%M:%S") == "16:00:00"


def test_snapshot_cache_refetches_partial_option_session(monkeypatch, tmp_path):
    """Regression: don't treat a tiny cached dt-window as a full-session hit for options."""
    option = Asset(
        "NVDA",
        asset_type="option",
        expiration=date(2014, 7, 25),
        strike=27.5,
        right="call",
        underlying_asset=Asset("NVDA", asset_type="stock"),
    )

    day = date(2014, 5, 16)
    start_dt = _tz(datetime(2014, 5, 16, 14, 29))
    end_dt = start_dt + timedelta(minutes=6)

    cache_file = tmp_path / "cache.parquet"
    cache_file.touch()

    # Simulate an old buggy cache payload that only contained the dt-window around the request.
    existing = pd.DataFrame(
        {"bid": [1.0, 1.0], "ask": [1.1, 1.1]},
        index=pd.DatetimeIndex([start_dt, end_dt]),
    )

    monkeypatch.setattr(thetadata_helper, "get_trading_dates", lambda *_: [day])
    monkeypatch.setattr(thetadata_helper, "get_backtest_cache", lambda: _DisabledCacheManager())
    monkeypatch.setattr(
        thetadata_helper,
        "build_snapshot_cache_filename",
        lambda *_args, **_kwargs: cache_file,
    )
    monkeypatch.setattr(thetadata_helper, "load_cache", lambda *_: existing)
    monkeypatch.setattr(thetadata_helper, "update_cache", lambda *_args, **_kwargs: None)

    called = {"count": 0}

    def fake_get_historical_data(_asset, fetch_start, fetch_end, *_args, **_kwargs):
        called["count"] += 1
        return pd.DataFrame({"bid": [1.0], "ask": [1.1]}, index=pd.DatetimeIndex([fetch_start]))

    monkeypatch.setattr(thetadata_helper, "get_historical_data", fake_get_historical_data)

    thetadata_helper.get_historical_data_snapshot_cached(
        option,
        start_dt,
        end_dt,
        60_000,
        datastyle="quote",
        include_after_hours=True,
        prefer_full_session=True,
    )

    assert called["count"] == 1
