from __future__ import annotations

from datetime import datetime

from lumibot.components.options_helper import OptionsHelper
from lumibot.entities import Asset, Quote


class _DummyBroker:
    def __init__(self, *, timestep: str):
        class ThetaDataBacktestingPandas:  # noqa: N801 - intentional for __name__ match
            def __init__(self, timestep: str):
                self._timestep = timestep

        self.IS_BACKTESTING_BROKER = True
        self.data_source = ThetaDataBacktestingPandas(timestep)
        self.option_source = None


class _Strategy:
    def __init__(self, *, now: datetime, sleeptime: str, timestep: str, bid: float, ask: float):
        self.is_backtesting = True
        self.broker = _DummyBroker(timestep=timestep)
        self._now = now
        self.sleeptime = sleeptime
        self._bid = bid
        self._ask = ask

    def get_datetime(self):
        return self._now

    def get_quote(self, asset):
        return Quote(asset=asset, bid=self._bid, ask=self._ask, price=None, timestamp=self._now)

    def log_message(self, *args, **kwargs):
        return None


def test_evaluate_option_market_overrides_day_quotes_with_snapshot_nbbo_in_daily_cadence():
    now = datetime(2025, 1, 6, 9, 30, 0)
    strategy = _Strategy(now=now, sleeptime="1D", timestep="day", bid=1.0, ask=2.0)
    helper = OptionsHelper(strategy)

    option = Asset("SPY", asset_type="option", expiration=now.date(), strike=500.0, right="call")

    def _mock_mark(asset, *, snapshot: bool):
        if snapshot:
            return 11.0, 10.0, 12.0
        return None, None, None

    helper._get_option_mark_from_quote = _mock_mark  # type: ignore[method-assign]

    evaluation = helper.evaluate_option_market(option, max_spread_pct=0.5)
    assert evaluation.bid == 10.0
    assert evaluation.ask == 12.0
    assert evaluation.buy_price == 12.0
    assert evaluation.sell_price == 10.0
    assert "snapshot_nbbo_override" in evaluation.data_quality_flags


def test_evaluate_option_market_keeps_existing_bid_ask_in_intraday_cadence():
    now = datetime(2025, 1, 6, 9, 30, 0)
    strategy = _Strategy(now=now, sleeptime="1M", timestep="minute", bid=1.0, ask=2.0)
    helper = OptionsHelper(strategy)

    option = Asset("SPY", asset_type="option", expiration=now.date(), strike=500.0, right="call")

    def _mock_mark(asset, *, snapshot: bool):  # pragma: no cover - should not affect intraday override
        if snapshot:
            return 11.0, 10.0, 12.0
        return None, None, None

    helper._get_option_mark_from_quote = _mock_mark  # type: ignore[method-assign]

    evaluation = helper.evaluate_option_market(option, max_spread_pct=0.5)
    assert evaluation.bid == 1.0
    assert evaluation.ask == 2.0
    assert "snapshot_nbbo_override" not in evaluation.data_quality_flags
