from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from lumibot.entities import Asset


def test_ibkr_helper_caches_history_and_reuses_cache(monkeypatch, tmp_path):
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setattr(ibkr_helper, "LUMIBOT_CACHE_FOLDER", tmp_path.as_posix())

    calls = {"secdef": 0, "history": 0}

    def fake_queue_request(url: str, querystring, headers=None, timeout=None):
        if url.endswith("/ibkr/iserver/secdef/search"):
            calls["secdef"] += 1
            return [
                {
                    "conid": 123,
                    "sections": [{"secType": "CRYPTO", "exchange": "PAXOS"}],
                }
            ]
        if url.endswith("/ibkr/iserver/marketdata/history"):
            calls["history"] += 1
            # two 1-min bars ending at startTime (ms timestamps)
            return {
                "data": [
                    {"t": 1700000000000, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10},
                    {"t": 1700000060000, "o": 2, "h": 3, "l": 2, "c": 3, "v": 11},
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(ibkr_helper, "queue_request", fake_queue_request)

    asset = Asset(symbol="BTC", asset_type="crypto")
    quote = Asset(symbol="USD", asset_type="forex")
    start = datetime.fromtimestamp(1700000000, tz=timezone.utc)
    end = datetime.fromtimestamp(1700000060, tz=timezone.utc)

    df1 = ibkr_helper.get_price_data(
        asset=asset,
        quote=quote,
        timestep="minute",
        start_dt=start,
        end_dt=end,
        exchange=None,
        include_after_hours=True,
    )
    assert not df1.empty
    assert "open" in df1.columns
    assert "bid" in df1.columns
    assert "ask" in df1.columns
    assert "missing" not in df1.columns
    assert isinstance(df1.index, pd.DatetimeIndex)

    # NOTE: IBKR crypto backtesting requires actionable bid/ask for quote-aware fills.
    # `ibkr_helper` may fetch additional history sources (e.g. Bid_Ask + Midpoint) to
    # derive bid/ask when Trades bars don't contain separate quote fields.
    history_calls_after_first = calls["history"]

    # Second call should reuse cached data without hitting the history endpoints again.
    df2 = ibkr_helper.get_price_data(
        asset=asset,
        quote=quote,
        timestep="minute",
        start_dt=start,
        end_dt=end,
        exchange=None,
        include_after_hours=True,
    )
    assert not df2.empty
    assert calls["history"] == history_calls_after_first
    assert calls["secdef"] == 1


def test_ibkr_helper_persists_fetched_bars_even_when_requested_window_has_no_overlap(monkeypatch, tmp_path):
    import lumibot.tools.ibkr_helper as ibkr_helper

    monkeypatch.setattr(ibkr_helper, "LUMIBOT_CACHE_FOLDER", tmp_path.as_posix())

    calls = {"secdef": 0, "history": 0}

    def fake_queue_request(url: str, querystring, headers=None, timeout=None):
        if url.endswith("/ibkr/iserver/secdef/search"):
            calls["secdef"] += 1
            return [
                {
                    "conid": 123,
                    "sections": [{"secType": "CRYPTO", "exchange": "PAXOS"}],
                }
            ]
        if url.endswith("/ibkr/iserver/marketdata/history"):
            calls["history"] += 1
            # two 1-min bars at an earlier time range (ms timestamps)
            return {
                "data": [
                    {"t": 1700000000000, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10},
                    {"t": 1700000060000, "o": 2, "h": 3, "l": 2, "c": 3, "v": 11},
                ]
            }
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(ibkr_helper, "queue_request", fake_queue_request)

    asset = Asset(symbol="BTC", asset_type="crypto")
    quote = Asset(symbol="USD", asset_type="forex")

    # Request a time window that is strictly AFTER the returned bars.
    start = datetime.fromtimestamp(1700000000 + 3600, tz=timezone.utc)
    end = datetime.fromtimestamp(1700000060 + 7200, tz=timezone.utc)

    df = ibkr_helper.get_price_data(
        asset=asset,
        quote=quote,
        timestep="minute",
        start_dt=start,
        end_dt=end,
        exchange=None,
        include_after_hours=True,
    )
    assert df.empty

    # Multiple parquet files may be produced when `ibkr_helper` fetches/derives bid/ask.
    # We require that the Trades series was persisted even if it doesn't overlap the request.
    trades_files = list(tmp_path.rglob("*_TRADES.parquet"))
    assert len(trades_files) == 1
    cached = pd.read_parquet(trades_files[0])
    assert len(cached) == 2
    assert calls["history"] >= 1
    assert calls["secdef"] == 1
