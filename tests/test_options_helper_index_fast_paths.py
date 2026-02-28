from __future__ import annotations

from datetime import date, datetime

from lumibot.components.options_helper import OptionsHelper
from lumibot.entities import Asset


class _DummyBroker:
    def __init__(self):
        class ThetaDataBacktestingPandas:  # noqa: N801 - intentional for __name__ match
            pass

        self.IS_BACKTESTING_BROKER = True
        self.data_source = ThetaDataBacktestingPandas()


class _ChainsWithStrikes:
    def __init__(self, *, expiry: date, strikes: list[float]):
        self._expiry = expiry
        self._strikes = strikes
        self._chains = {
            "Chains": {
                "CALL": {expiry.strftime("%Y-%m-%d"): strikes},
                "PUT": {expiry.strftime("%Y-%m-%d"): strikes},
            }
        }

    def get(self, key, default=None):
        return self._chains.get(key, default)

    def strikes(self, expiry, right):
        if expiry != self._expiry:
            return []
        right_key = str(right).upper()
        return list(self._chains["Chains"].get(right_key, {}).get(self._expiry.strftime("%Y-%m-%d"), []))


class _Strategy:
    def __init__(self, *, now: datetime, chains):
        self.is_backtesting = True
        self.broker = _DummyBroker()
        self._now = now
        self._chains = chains
        self.sleeptime = "1M"
        self.parameters = {}

    def get_datetime(self):
        return self._now

    def get_last_price(self, asset):
        # Used by the index fast-path distance check.
        if getattr(asset, "symbol", None) == "SPXW":
            return 6010.0
        return None

    def get_chains(self, underlying_asset):
        return self._chains

    def log_message(self, *args, **kwargs):
        return None


def test_get_expiration_on_or_after_date_skips_quote_validation_for_0dte_index():
    now = datetime(2025, 1, 6, 10, 15, 0)
    helper = OptionsHelper(_Strategy(now=now, chains=None))

    chains = {"Chains": {"CALL": {"2025-01-06": [6000.0, 6010.0]}}}
    underlying = Asset("SPXW", asset_type=Asset.AssetType.INDEX)

    def _boom(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("_get_option_mark_from_quote should not be called for 0DTE index expiry fast-path")

    helper._get_option_mark_from_quote = _boom  # type: ignore[method-assign]

    expiry = helper.get_expiration_on_or_after_date(
        dt=now.date(),
        chains=chains,
        call_or_put="call",
        underlying_asset=underlying,
    )

    assert expiry == now.date()


def test_find_next_valid_option_skips_snapshot_quote_scans_for_atm_index():
    now = datetime(2025, 1, 6, 10, 15, 0)
    expiry = now.date()
    chains = _ChainsWithStrikes(expiry=expiry, strikes=[6000.0, 6010.0, 6020.0])
    strategy = _Strategy(now=now, chains=chains)
    helper = OptionsHelper(strategy)

    def _boom(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("_get_option_mark_from_quote should not be called for ATM index fast-path")

    helper._get_option_mark_from_quote = _boom  # type: ignore[method-assign]

    underlying = Asset("SPXW", asset_type=Asset.AssetType.INDEX)
    option = helper.find_next_valid_option(
        underlying_asset=underlying,
        rounded_underlying_price=6010.0,
        expiry=expiry,
        put_or_call="call",
    )

    assert option is not None
    assert option.strike == 6010.0
