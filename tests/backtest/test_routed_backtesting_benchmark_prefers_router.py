from __future__ import annotations

from datetime import datetime

import pandas as pd

from lumibot.backtesting import BacktestingBroker
from lumibot.backtesting.routed_backtesting import RoutedBacktestingPandas
from lumibot.constants import LUMIBOT_DEFAULT_PYTZ
from lumibot.entities import Asset
from lumibot.strategies import Strategy


class _BenchmarkSmokeStrategy(Strategy):
    def initialize(self):
        self.sleeptime = "1M"

    def on_trading_iteration(self):
        return


def test_router_benchmark_uses_router_datasource_instead_of_yahoo(monkeypatch):
    # Avoid any local ThetaTerminal side effects during datasource init.
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

    benchmark_calls = {"router": 0, "yahoo": 0}

    class _Bars:
        def __init__(self, df: pd.DataFrame):
            self.df = df

    bench_index = pd.date_range(start, end, freq="1D", inclusive="left")
    bench_df = pd.DataFrame(
        {
            "open": [100.0] * len(bench_index),
            "high": [101.0] * len(bench_index),
            "low": [99.0] * len(bench_index),
            "close": [100.0] * len(bench_index),
            "volume": [1000] * len(bench_index),
        },
        index=bench_index,
    )

    def fake_between_dates(*args, **kwargs):
        benchmark_calls["router"] += 1
        return _Bars(bench_df)

    monkeypatch.setattr(router, "get_historical_prices_between_dates", fake_between_dates)

    import lumibot.tools.indicators as indicators

    def boom(*args, **kwargs):
        benchmark_calls["yahoo"] += 1
        raise AssertionError("Yahoo benchmark fetch should not be used for router backtests")

    monkeypatch.setattr(indicators, "get_symbol_returns", boom)

    broker = BacktestingBroker(data_source=router)

    strategy = _BenchmarkSmokeStrategy(
        broker=broker,
        budget=100_000,
        backtesting_start=start,
        backtesting_end=end,
        benchmark_asset=Asset("SPY", Asset.AssetType.STOCK),
        quote_asset=Asset("USD", Asset.AssetType.FOREX),
        risk_free_rate=0,
        analyze_backtest=False,
    )
    strategy.is_backtesting = True

    strategy._dump_benchmark_stats()

    assert benchmark_calls["router"] == 1
    assert benchmark_calls["yahoo"] == 0
    assert strategy._benchmark_returns_df is not None
    assert not strategy._benchmark_returns_df.empty
