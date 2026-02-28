import datetime
from types import SimpleNamespace
from unittest.mock import patch

from lumibot.components.options_helper import OptionsHelper
from lumibot.entities import Asset


class _StubStrategy:
    def __init__(self, *, underlying_price=60.0):
        self.parameters = {}
        self.underlying_price = underlying_price
        self.last_greeks_call = None

    def log_message(self, *args, **kwargs):
        return None

    def get_last_price(self, asset):
        if asset.asset_type == Asset.AssetType.STOCK:
            return self.underlying_price
        return None

    def get_quote(self, option_asset):
        return SimpleNamespace(bid=1.0, ask=3.0)

    def get_greeks(
        self,
        asset,
        asset_price=None,
        underlying_price=None,
        risk_free_rate=None,
        query_greeks=False,
    ):
        self.last_greeks_call = {
            "asset": asset,
            "asset_price": asset_price,
            "underlying_price": underlying_price,
        }
        return {"delta": 0.5}


def test_get_delta_for_strike_uses_quote_mid_when_last_trade_missing():
    strategy = _StubStrategy()
    helper = OptionsHelper(strategy)

    underlying = Asset("UBER", asset_type=Asset.AssetType.STOCK)
    expiry = datetime.date(2027, 6, 17)

    delta = helper.get_delta_for_strike(
        underlying_asset=underlying,
        underlying_price=60.0,
        strike=60.0,
        expiry=expiry,
        right="call",
    )

    assert delta == 0.5
    assert strategy.last_greeks_call is not None
    assert strategy.last_greeks_call["asset_price"] == 2.0
    assert strategy.last_greeks_call["underlying_price"] == 60.0


def test_get_delta_for_strike_uses_snapshot_only_quotes_when_backtesting_source_supports_it():
    class _StubOptionSource:
        def __init__(self):
            self.calls = []

        def get_quote(self, _asset, quote=None, exchange=None, **kwargs):
            self.calls.append({"quote": quote, "exchange": exchange, **kwargs})
            return SimpleNamespace(bid=1.0, ask=3.0)

    class _StubBroker:
        IS_BACKTESTING_BROKER = True

        def __init__(self, option_source):
            self.option_source = option_source
            self.data_source = None

    option_source = _StubOptionSource()
    strategy = _StubStrategy()
    strategy.broker = _StubBroker(option_source)
    helper = OptionsHelper(strategy)

    underlying = Asset("UBER", asset_type=Asset.AssetType.STOCK)
    expiry = datetime.date(2027, 6, 17)

    delta = helper.get_delta_for_strike(
        underlying_asset=underlying,
        underlying_price=60.0,
        strike=60.0,
        expiry=expiry,
        right="call",
    )

    assert delta == 0.5
    assert option_source.calls, "Expected OptionsHelper to call broker.option_source.get_quote()"
    assert option_source.calls[0].get("snapshot_only") is True


def test_get_expiration_on_or_after_date_rejects_expiry_without_strikes_near_underlying():
    strategy = _StubStrategy(underlying_price=60.0)
    helper = OptionsHelper(strategy)

    chains = {
        "UnderlyingSymbol": "UBER",
        "Chains": {
            "CALL": {"2027-06-17": [120.0, 125.0, 130.0]},
            "PUT": {"2027-06-17": [120.0, 125.0, 130.0]},
        },
    }

    expiry = helper.get_expiration_on_or_after_date(
        dt=datetime.date(2027, 6, 17),
        chains=chains,
        call_or_put="call",
        underlying_asset=Asset("UBER", asset_type=Asset.AssetType.STOCK),
        allow_prior=False,
    )

    assert expiry is None


def test_get_expiration_on_or_after_date_accepts_expiry_with_nearby_strikes():
    strategy = _StubStrategy(underlying_price=60.0)
    helper = OptionsHelper(strategy)

    chains = {
        "UnderlyingSymbol": "UBER",
        "Chains": {
            "CALL": {"2027-06-17": [50.0, 60.0, 70.0]},
            "PUT": {"2027-06-17": [50.0, 60.0, 70.0]},
        },
    }

    expiry = helper.get_expiration_on_or_after_date(
        dt=datetime.date(2027, 6, 17),
        chains=chains,
        call_or_put="call",
        underlying_asset=Asset("UBER", asset_type=Asset.AssetType.STOCK),
        allow_prior=False,
    )

    assert expiry == datetime.date(2027, 6, 17)


def test_get_expiration_on_or_after_date_skips_quote_validation_temporarily_after_failures():
    class _RecordingStrategy(_StubStrategy):
        def __init__(self, *, now, underlying_price=60.0):
            super().__init__(underlying_price=underlying_price)
            self._now = now
            self.messages = []

        def get_datetime(self):
            return self._now

        def log_message(self, msg, color=None, **_kwargs):
            self.messages.append((msg, color))

    class _StubBroker:
        IS_BACKTESTING_BROKER = True

        def __init__(self):
            self.data_source = None

    strategy = _RecordingStrategy(now=datetime.datetime(2027, 6, 1, 10, 0), underlying_price=60.0)
    strategy.broker = _StubBroker()
    helper = OptionsHelper(strategy)

    chains = {
        "UnderlyingSymbol": "UBER",
        "Chains": {
            "CALL": {"2027-06-17": [50.0, 60.0, 70.0]},
            "PUT": {"2027-06-17": [50.0, 60.0, 70.0]},
        },
    }

    # First call: quote validation fails -> no valid expiry -> validation disabled for a short window.
    with patch.object(helper, "_get_option_mark_from_quote", return_value=(None, None, None)):
        expiry1 = helper.get_expiration_on_or_after_date(
            dt=datetime.date(2027, 6, 1),
            chains=chains,
            call_or_put="call",
            underlying_asset=Asset("UBER", asset_type=Asset.AssetType.STOCK),
            allow_prior=False,
        )

    assert expiry1 is None
    assert helper._expiration_validation_disabled_until["UBER"] == datetime.date(2027, 6, 8)

    # Second call (different dt, same as-of date): should skip quote validation and return by-date expiry.
    with patch.object(helper, "_get_option_mark_from_quote", side_effect=AssertionError("should not validate quotes")):
        expiry2 = helper.get_expiration_on_or_after_date(
            dt=datetime.date(2027, 6, 2),
            chains=chains,
            call_or_put="call",
            underlying_asset=Asset("UBER", asset_type=Asset.AssetType.STOCK),
            allow_prior=False,
        )

    assert expiry2 == datetime.date(2027, 6, 17)


def test_get_expiration_on_or_after_date_disabled_window_still_requires_nearby_strikes():
    class _RecordingStrategy(_StubStrategy):
        def __init__(self, *, now, underlying_price=60.0):
            super().__init__(underlying_price=underlying_price)
            self._now = now

        def get_datetime(self):
            return self._now

    class _StubBroker:
        IS_BACKTESTING_BROKER = True

        def __init__(self):
            self.data_source = None

    strategy = _RecordingStrategy(now=datetime.datetime(2027, 6, 1, 10, 0), underlying_price=60.0)
    strategy.broker = _StubBroker()
    helper = OptionsHelper(strategy)

    chains_far_strikes = {
        "UnderlyingSymbol": "UBER",
        "Chains": {
            "CALL": {"2027-06-17": [120.0, 125.0, 130.0]},
            "PUT": {"2027-06-17": [120.0, 125.0, 130.0]},
        },
    }

    # Force validation-disabled state.
    helper._expiration_validation_disabled_until = {"UBER": datetime.date(2027, 6, 8)}

    with patch.object(helper, "_get_option_mark_from_quote", side_effect=AssertionError("should not validate quotes")):
        expiry = helper.get_expiration_on_or_after_date(
            dt=datetime.date(2027, 6, 2),
            chains=chains_far_strikes,
            call_or_put="call",
            underlying_asset=Asset("UBER", asset_type=Asset.AssetType.STOCK),
            allow_prior=False,
        )

    assert expiry is None


def test_get_expiration_on_or_after_date_reuses_horizon_cache_for_fallback_expiries():
    """Backtests should reuse a cached fallback expiry (prior to target horizon) for a short TTL.

    Without this, strategies that ask for a fixed horizon (e.g., 30–60D) during periods where the
    chain does not yet list far-dated expirations will re-run quote validation on every attempt,
    generating many downloader submits and slowing long-window backtests.
    """

    class _RecordingStrategy(_StubStrategy):
        def __init__(self, *, now, chains, underlying_price=60.0):
            super().__init__(underlying_price=underlying_price)
            self._now = now
            self._chains = chains

        def get_datetime(self):
            return self._now

        def get_chains(self, _underlying_asset):
            return self._chains

    class _StubBroker:
        IS_BACKTESTING_BROKER = True

        def __init__(self):
            self.data_source = None

    chains = {
        "UnderlyingSymbol": "UBER",
        "Chains": {
            # A far-dated expiry exists but has no strikes near the underlying price (validation should
            # reject it), forcing a fallback to the near-dated expiry.
            "CALL": {
                "2027-06-10": [50.0, 60.0, 70.0],
                "2027-07-15": [120.0, 125.0, 130.0],
            },
            "PUT": {
                "2027-06-10": [50.0, 60.0, 70.0],
                "2027-07-15": [120.0, 125.0, 130.0],
            },
        },
    }

    strategy = _RecordingStrategy(now=datetime.datetime(2027, 6, 1, 10, 0), chains=chains, underlying_price=60.0)
    strategy.broker = _StubBroker()
    helper = OptionsHelper(strategy)

    underlying = Asset("UBER", asset_type=Asset.AssetType.STOCK)

    # First call: resolve via fallback prior expiry and populate the horizon cache.
    with patch.object(helper, "_get_option_mark_from_quote", return_value=(1.0, None, None)):
        expiry1 = helper.get_expiration_on_or_after_date(
            dt=datetime.date(2027, 7, 1),  # 30d horizon from 2027-06-01
            chains=chains,
            call_or_put="call",
            underlying_asset=underlying,
            allow_prior=False,
        )

    assert expiry1 == datetime.date(2027, 6, 10)

    # Advance the as-of date but keep the same horizon_days; should reuse cached fallback expiry
    # without re-probing quotes.
    strategy._now = datetime.datetime(2027, 6, 5, 10, 0)
    with patch.object(helper, "_get_option_mark_from_quote", side_effect=AssertionError("should not validate quotes")):
        expiry2 = helper.get_expiration_on_or_after_date(
            dt=datetime.date(2027, 7, 5),  # same 30d horizon from 2027-06-05
            chains=chains,
            call_or_put="call",
            underlying_asset=underlying,
            allow_prior=False,
        )

    assert expiry2 == datetime.date(2027, 6, 10)


def test_get_expiration_on_or_after_date_disabled_window_does_not_return_expired_prior_expiry():
    """When quote validation is temporarily disabled, never return expirations before the current date.

    This guards against a backtesting-only failure mode:
    - Expiration validation fails (no quote history) and disables quote validation for a short window.
    - Subsequent calls would return a "prior-to-target-horizon" expiry even if it is already expired
      relative to the current simulation date, causing strategies to thrash.
    """

    class _RecordingStrategy(_StubStrategy):
        def __init__(self, *, now, underlying_price=60.0):
            super().__init__(underlying_price=underlying_price)
            self._now = now

        def get_datetime(self):
            return self._now

    class _StubBroker:
        IS_BACKTESTING_BROKER = True

        def __init__(self):
            self.data_source = None

    now = datetime.datetime(2027, 6, 10, 10, 0)
    strategy = _RecordingStrategy(now=now, underlying_price=60.0)
    strategy.broker = _StubBroker()
    helper = OptionsHelper(strategy)

    # Force quote-validation-disabled state for the underlying.
    helper._expiration_validation_disabled_until = {"UBER": now.date() + datetime.timedelta(days=7)}

    chains_only_expired = {
        "UnderlyingSymbol": "UBER",
        "Chains": {
            "CALL": {"2027-05-01": [50.0, 60.0, 70.0]},
            "PUT": {"2027-05-01": [50.0, 60.0, 70.0]},
        },
    }

    expiry = helper.get_expiration_on_or_after_date(
        dt=datetime.date(2027, 7, 25),
        chains=chains_only_expired,
        call_or_put="call",
        underlying_asset=None,
        allow_prior=False,
    )

    assert expiry is None
