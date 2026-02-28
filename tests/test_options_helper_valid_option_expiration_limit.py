from __future__ import annotations

from datetime import date, timedelta

from lumibot.components.options_helper import OptionsHelper
from lumibot.entities import Asset


class ThetaDataBacktestingPandas:
    _timestep = "minute"


class _FakeBroker:
    IS_BACKTESTING_BROKER = True

    def __init__(self) -> None:
        self.data_source = ThetaDataBacktestingPandas()


class _FakeChains:
    def __init__(self, expirations: list[date], strikes: list[float]) -> None:
        self._expirations = expirations
        self._strikes = strikes

    def get(self, key, default=None):
        if key != "Chains":
            return default
        return {
            "CALL": {exp.strftime("%Y-%m-%d"): {} for exp in self._expirations},
            "PUT": {exp.strftime("%Y-%m-%d"): {} for exp in self._expirations},
        }

    def strikes(self, _expiry: date, _side: str) -> list[float]:
        return list(self._strikes)


class _FakeStrategy:
    is_backtesting = True
    sleeptime = "1H"

    def __init__(self, chains: _FakeChains) -> None:
        self.broker = _FakeBroker()
        self._chains = chains

    def get_datetime(self):
        return None

    def get_chains(self, _asset):
        return self._chains

    def get_last_price(self, _asset):
        return None

    def log_message(self, *_args, **_kwargs):
        return None


def test_find_next_valid_option_limits_expiration_probes_for_equities(monkeypatch):
    """Theta backtests should not scan deep into expirations for equities.

    This guards against NVDA-style hourly strategies that can otherwise submit thousands of
    quote-history requests when nearby expirations have placeholder-only coverage.
    """
    base_expiry = date(2013, 11, 15)
    expirations = [base_expiry + timedelta(days=7 * i) for i in range(20)]
    strikes = [float(100 + i) for i in range(20)]
    chains = _FakeChains(expirations=expirations, strikes=strikes)

    strategy = _FakeStrategy(chains)
    helper = OptionsHelper(strategy)

    calls = {"count": 0}

    def _always_missing(_option_asset: Asset, *, snapshot: bool = False):
        assert snapshot is True
        calls["count"] += 1
        return None, None, None

    monkeypatch.setattr(helper, "_get_option_mark_from_quote", _always_missing)

    underlying = Asset("NVDA", asset_type="stock")
    result = helper.find_next_valid_option(underlying, 110.0, base_expiry, put_or_call="call")
    assert result is None

    # Equity Theta backtest cap: 1 expiration × 3 strikes (tight scan for long-window backtests).
    assert calls["count"] == 3
