import pandas as pd

from lumibot.tools import thetadata_helper


def test_update_cache_avoids_deep_copy_for_large_frames(monkeypatch, tmp_path):
    """Guard against production OOMs from deep-copying large cached intraday frames.

    Production backtests can reuse a cache namespace that already contains multi-year minute data
    (millions of rows). `DataFrame.copy()` defaults to `deep=True` which can double peak RSS during
    cache updates and lead to hard kills (no traceback). This test ensures `update_cache()` does
    not perform deep copies of the input frames.
    """

    class _DummyCache:
        mode = thetadata_helper.CacheMode.DISABLED

    monkeypatch.setattr(thetadata_helper, "get_backtest_cache", lambda: _DummyCache())

    original_copy = pd.DataFrame.copy

    def _copy_no_deep(self, deep=True):  # noqa: ANN001
        if deep:
            raise AssertionError("update_cache() must not deep-copy large frames")
        return original_copy(self, deep=deep)

    monkeypatch.setattr(pd.DataFrame, "copy", _copy_no_deep, raising=True)

    idx = pd.date_range("2025-01-01", periods=3, freq="min", tz="UTC", name="datetime")
    df_all = pd.DataFrame(
        {"open": [1.0, 2.0, 3.0], "high": [1.0, 2.0, 3.0], "low": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0]},
        index=idx,
    )
    df_cached = df_all.iloc[:2].copy(deep=False)

    cache_file = tmp_path / "nvda_minute_ohlc.parquet"
    thetadata_helper.update_cache(cache_file, df_all, df_cached, missing_dates=None, remote_payload=None)
