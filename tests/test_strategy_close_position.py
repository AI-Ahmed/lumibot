import logging
from types import SimpleNamespace
import unittest

from lumibot.entities import Asset, Position
from lumibot.strategies import Strategy
from lumibot.strategies._strategy import Vars


class DummyBroker:
    IS_BACKTESTING_BROKER = False

    def __init__(self):
        self.name = "dummy"
        self.data_source = SimpleNamespace(SOURCE="TEST")
        self.quote_assets = set()
        self._filled_positions = []
        self.close_calls = []

    def get_tracked_position(self, strategy_name, asset):
        for position in self._filled_positions:
            if position.strategy == strategy_name and position.asset == asset:
                return position
        return None

    def get_tracked_positions(self, strategy_name=None):
        return [
            position
            for position in self._filled_positions
            if strategy_name is None or position.strategy == strategy_name
        ]

    def close_position(self, strategy_name, asset, fraction=1.0):
        position = self.get_tracked_position(strategy_name, asset)
        if position is None or position.quantity == 0:
            self.close_calls.append({"asset": asset, "order": None})
            return None

        qty = position.quantity * fraction
        order = SimpleNamespace(
            identifier="CLOSE-ORDER",
            asset=asset,
            quantity=qty,
            side="sell",
            order_type="market",
        )
        self.close_calls.append({"asset": asset, "order": order})
        return order


class DummyStrategy(Strategy):
    parameters = {}

    def __init__(self, broker):
        self.broker = broker
        self.logger = logging.getLogger("DummyStrategy")
        self._name = "DummyStrategy"
        self.vars = Vars()
        self._quote_asset = Asset("USD", Asset.AssetType.FOREX)
        self.broker.quote_assets.add(self._quote_asset)


class TestStrategyClosePosition(unittest.TestCase):
    pass


if __name__ == "__main__":
    unittest.main()
