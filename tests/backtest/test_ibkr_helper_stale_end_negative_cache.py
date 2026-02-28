from __future__ import annotations

import shutil
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from lumibot.constants import LUMIBOT_DEFAULT_PYTZ
from lumibot.entities import Asset


def test_ibkr_stale_end_marks_missing_window_to_avoid_repeated_history_fetches(monkeypatch):
    import lumibot.tools.backtest_cache as backtest_cache
    import lumibot.tools.ibkr_helper as ibkr_helper

    # Keep all artifacts under the repo tree (no /tmp writes).
    cache_root = Path(__file__).resolve().parent / "_tmp_ibkr_cache" / uuid.uuid4().hex
    cache_root.mkdir(parents=True, exist_ok=True)

    try:
        # Disable remote cache; this is a unit test for local parquet behavior.
        monkeypatch.setenv("LUMIBOT_CACHE_BACKEND", "local")
        monkeypatch.setenv("LUMIBOT_CACHE_MODE", "disabled")
        backtest_cache.reset_backtest_cache_manager(for_testing=True)

        # Patch module-level cache root constants (ibkr_helper imports by value).
        monkeypatch.setattr(ibkr_helper, "LUMIBOT_CACHE_FOLDER", str(cache_root))
        monkeypatch.setattr(backtest_cache, "LUMIBOT_CACHE_FOLDER", str(cache_root))

        # Avoid any contract resolution/network calls.
        monkeypatch.setattr(ibkr_helper, "_resolve_conid", lambda *args, **kwargs: 123)

        asset = Asset("GC", asset_type=Asset.AssetType.FUTURE, expiration=date(2026, 2, 25), multiplier=100)
        quote = Asset("USD", asset_type=Asset.AssetType.FOREX)

        start = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 19, 18, 0))
        last_bar = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 19, 19, 0))
        end = LUMIBOT_DEFAULT_PYTZ.localize(datetime(2026, 1, 19, 23, 0))

        timestep = "60minute"
        exchange = "COMEX"
        source = "Trades"

        cache_file = ibkr_helper._cache_file_for(  # type: ignore[attr-defined]
            asset=asset,
            quote=quote,
            timestep=timestep,
            exchange=exchange,
            source=source,
        )

        df_seed = pd.DataFrame(
            {
                "open": [100.0, 100.0],
                "high": [101.0, 101.0],
                "low": [99.0, 99.0],
                "close": [100.0, 100.0],
                "volume": [1000, 1000],
                "missing": [False, False],
            },
            index=pd.DatetimeIndex([start, last_bar]),
        )
        ibkr_helper._write_cache_frame(cache_file, df_seed)  # type: ignore[attr-defined]

        calls: list[dict] = []

        def fake_queue_request(*, url, querystring, headers=None, timeout=None):
            calls.append({"url": url, "querystring": dict(querystring or {})})
            ts = pd.Timestamp(last_bar).tz_convert("UTC")
            ms = int(ts.value // 1_000_000)
            return {
                "data": [
                    {"t": ms, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1000},
                ]
            }

        monkeypatch.setattr(ibkr_helper, "queue_request", fake_queue_request)

        df1 = ibkr_helper.get_price_data(
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start,
            end_dt=end,
            exchange=exchange,
            include_after_hours=True,
            source=source,
        )
        history_calls = [c for c in calls if "/ibkr/iserver/marketdata/history" in c["url"]]
        assert len(history_calls) == 1
        assert not df1.empty
        assert df1.index.max() == last_bar
        cached_mid = pd.read_parquet(cache_file)
        assert end in cached_mid.index

        # Second call should not re-fetch history; the stale-end negative cache should satisfy the
        # requested bound without hitting the downloader again.
        df2 = ibkr_helper.get_price_data(
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start,
            end_dt=end,
            exchange=exchange,
            include_after_hours=True,
            source=source,
        )
        history_calls = [c for c in calls if "/ibkr/iserver/marketdata/history" in c["url"]]
        assert len(history_calls) == 1
        assert not df2.empty
        assert df2.index.max() == last_bar

        cached = pd.read_parquet(cache_file)
        # Placeholder rows are used only to extend coverage; they must not replace the real bar.
        assert last_bar in cached.index
        assert bool(cached.loc[last_bar, "missing"]) is False
        assert end in cached.index
        assert bool(cached.loc[end, "missing"]) is True
    finally:
        shutil.rmtree(cache_root, ignore_errors=True)
