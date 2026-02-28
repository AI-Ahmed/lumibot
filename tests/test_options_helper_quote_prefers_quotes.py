from datetime import date

import pytest

from lumibot.components.options_helper import OptionsHelper
from lumibot.entities import Asset, Quote


class _DummyDataSource:
    option_quote_fallback_allowed = True


class _DummyBroker:
    data_source = _DummyDataSource()


class _DummyStrategy:
    def __init__(self, bid=None, ask=None):
        self.broker = _DummyBroker()
        self._bid = bid
        self._ask = ask
        self.last_price_calls = 0
        self.greeks_calls = []

    def log_message(self, *_args, **_kwargs):
        return None

    def get_quote(self, asset, **_kwargs):
        return Quote(asset=asset, bid=self._bid, ask=self._ask, price=None)

    def get_last_price(self, _asset, **_kwargs):
        self.last_price_calls += 1
        raise AssertionError("get_last_price() should not be called when quote data is usable")

    def get_greeks(self, _asset, asset_price=None, underlying_price=None, **_kwargs):
        self.greeks_calls.append({"asset_price": asset_price, "underlying_price": underlying_price})
        return {"delta": 0.5}


def test_evaluate_option_market_one_sided_quote_skips_last_price():
    option = Asset(
        "SPY",
        asset_type="option",
        expiration=date(2025, 1, 17),
        strike=400,
        right="call",
        underlying_asset=Asset("SPY", "stock"),
    )
    strategy = _DummyStrategy(bid=1.23, ask=None)
    helper = OptionsHelper(strategy)

    evaluation = helper.evaluate_option_market(option)

    assert strategy.last_price_calls == 0
    assert evaluation.sell_price == pytest.approx(1.23)
    assert evaluation.buy_price is None


def test_get_delta_for_strike_prefers_quote_price():
    underlying = Asset("SPY", "stock")
    strategy = _DummyStrategy(bid=1.0, ask=1.2)
    helper = OptionsHelper(strategy)

    delta = helper.get_delta_for_strike(
        underlying_asset=underlying,
        underlying_price=500.0,
        strike=500.0,
        expiry=date(2025, 1, 17),
        right="call",
    )

    assert delta == pytest.approx(0.5)
    assert strategy.last_price_calls == 0
    assert strategy.greeks_calls
    assert strategy.greeks_calls[0]["asset_price"] == pytest.approx(1.1)
