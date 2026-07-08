"""Tests for BACKTESTING_DATA_SOURCE environment variable handling.

These tests validate the datasource selection logic inside `Strategy.run_backtest()`
without running real backtests (which would be slow and flaky in CI).

Equity-only fork: valid sources are yahoo, alpaca, ibkr (default: yahoo).
"""

from datetime import datetime

import pytest

from lumibot.strategies import Strategy


class SimpleTestStrategy(Strategy):
    """Minimal strategy for testing datasource auto-selection."""

    def initialize(self):
        self.sleeptime = "1D"

    def on_trading_iteration(self):
        return


class TestBacktestingDataSourceEnv:
    """Test BACKTESTING_DATA_SOURCE environment variable."""

    class _SelectedDataSource(Exception):
        """Raised by stub backtesting classes to prove datasource selection."""

    class _YahooSelected(_SelectedDataSource):
        pass

    class _AlpacaSelected(_SelectedDataSource):
        pass

    class _IbkrSelected(_SelectedDataSource):
        pass

    def test_auto_select_yahoo_case_insensitive(self, monkeypatch, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="lumibot.strategies._strategy")

        class YahooDataBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._YahooSelected()

        import lumibot.strategies._strategy as strategy_module

        monkeypatch.setattr(strategy_module, "YahooDataBacktesting", YahooDataBacktesting)
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "Yahoo")

        with pytest.raises(self._YahooSelected):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 10),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

        assert any(
            "Using BACKTESTING_DATA_SOURCE setting for backtest data: Yahoo" in record.message
            for record in caplog.records
        )

    def test_auto_select_alpaca_case_insensitive(self, monkeypatch, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="lumibot.strategies._strategy")

        class AlpacaBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._AlpacaSelected()

        import lumibot.strategies._strategy as strategy_module

        monkeypatch.setattr(strategy_module, "AlpacaBacktesting", AlpacaBacktesting)
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "ALPACA")

        with pytest.raises(self._AlpacaSelected):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 10),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

        assert any(
            "Using BACKTESTING_DATA_SOURCE setting for backtest data: ALPACA" in record.message
            for record in caplog.records
        )

    def test_auto_select_ibkr_case_insensitive(self, monkeypatch, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="lumibot.strategies._strategy")

        class InteractiveBrokersRESTBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._IbkrSelected()

        import lumibot.strategies._strategy as strategy_module

        monkeypatch.setattr(
            strategy_module, "InteractiveBrokersRESTBacktesting", InteractiveBrokersRESTBacktesting
        )
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "ibkr")

        with pytest.raises(self._IbkrSelected):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 10),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

        assert any(
            "Using BACKTESTING_DATA_SOURCE setting for backtest data: ibkr" in record.message
            for record in caplog.records
        )

    def test_invalid_data_source_raises_error(self, monkeypatch):
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "InvalidSource")

        with pytest.raises(ValueError, match="Unknown BACKTESTING_DATA_SOURCE"):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 31),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

    def test_forbidden_polygon_raises_error(self, monkeypatch):
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "polygon")

        with pytest.raises(ValueError, match="Unknown BACKTESTING_DATA_SOURCE"):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 31),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

    def test_forbidden_thetadata_raises_error(self, monkeypatch):
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "thetadata")

        with pytest.raises(ValueError, match="Unknown BACKTESTING_DATA_SOURCE"):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 31),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

    def test_env_override_wins_over_explicit_datasource(self, monkeypatch, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="lumibot.strategies._strategy")

        class AlpacaBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._AlpacaSelected()

        class YahooDataBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._YahooSelected()

        import lumibot.strategies._strategy as strategy_module

        monkeypatch.setattr(strategy_module, "AlpacaBacktesting", AlpacaBacktesting)
        monkeypatch.setattr(strategy_module, "YahooDataBacktesting", YahooDataBacktesting)
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "alpaca")

        with pytest.raises(self._AlpacaSelected):
            SimpleTestStrategy.run_backtest(
                YahooDataBacktesting,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 10),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

        assert any(
            "Using BACKTESTING_DATA_SOURCE setting for backtest data: alpaca" in record.message
            for record in caplog.records
        )

    def test_explicit_datasource_used_when_env_none(self, monkeypatch, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="lumibot.strategies._strategy")

        class YahooDataBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._YahooSelected()

        import lumibot.strategies._strategy as strategy_module

        monkeypatch.setattr(strategy_module, "YahooDataBacktesting", YahooDataBacktesting)
        monkeypatch.setenv("BACKTESTING_DATA_SOURCE", "none")

        with pytest.raises(self._YahooSelected):
            SimpleTestStrategy.run_backtest(
                YahooDataBacktesting,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 10),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

        assert not any(
            "Using BACKTESTING_DATA_SOURCE setting for backtest data:" in record.message
            for record in caplog.records
        )

    def test_default_yahoo_when_no_env_set(self, monkeypatch, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="lumibot.strategies._strategy")

        class YahooDataBacktesting:
            def __init__(self, *args, **kwargs):
                raise TestBacktestingDataSourceEnv._YahooSelected()

        import lumibot.credentials
        import lumibot.strategies._strategy as strategy_module

        monkeypatch.setattr(strategy_module, "YahooDataBacktesting", YahooDataBacktesting)
        monkeypatch.setattr(lumibot.credentials, "BACKTESTING_DATA_SOURCE", "yahoo")
        monkeypatch.delenv("BACKTESTING_DATA_SOURCE", raising=False)

        with pytest.raises(self._YahooSelected):
            SimpleTestStrategy.run_backtest(
                None,
                backtesting_start=datetime(2023, 1, 1),
                backtesting_end=datetime(2023, 1, 10),
                show_plot=False,
                show_tearsheet=False,
                show_indicators=False,
                show_progress_bar=False,
                save_tearsheet=False,
                save_stats_file=False,
                save_logfile=False,
            )

        assert any(
            "Using BACKTESTING_DATA_SOURCE setting for backtest data: yahoo" in record.message
            for record in caplog.records
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
