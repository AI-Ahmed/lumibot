from __future__ import annotations

import pandas as pd

from lumibot.tools.ibkr_helper import _derive_bid_ask_from_bid_ask_and_midpoint


def test_derives_bid_and_ask_from_bid_ask_and_midpoint_close():
    idx = pd.date_range("2025-01-01 00:00", periods=3, freq="1min", tz="America/New_York")

    bid_ask = pd.DataFrame({"close": [101.0, 201.0, 301.0]}, index=idx)  # treated as ask_close
    midpoint = pd.DataFrame({"close": [100.0, 200.0, 300.0]}, index=idx)

    derived = _derive_bid_ask_from_bid_ask_and_midpoint(bid_ask, midpoint)

    assert list(derived.columns) == ["bid", "ask"]
    assert derived.loc[idx[0], "ask"] == 101.0
    assert derived.loc[idx[0], "bid"] == 99.0
    assert derived.loc[idx[1], "bid"] == 199.0
    assert derived.loc[idx[2], "ask"] == 301.0


def test_derivation_clamps_inverted_or_invalid_spreads_to_midpoint():
    idx = pd.date_range("2025-01-01 00:00", periods=2, freq="1min", tz="America/New_York")

    # Construct an "inverted" scenario where the implied bid would exceed ask.
    bid_ask = pd.DataFrame({"close": [100.0, 100.0]}, index=idx)
    midpoint = pd.DataFrame({"close": [200.0, -1.0]}, index=idx)

    derived = _derive_bid_ask_from_bid_ask_and_midpoint(bid_ask, midpoint)

    # Row 0: bid would be 300, ask 100 -> inverted; clamp to mid=200.
    assert derived.loc[idx[0], "bid"] == 200.0
    assert derived.loc[idx[0], "ask"] == 200.0
    # Row 1: negative mid is invalid; leave NaN so callers can fall back to trade/mark close.
    assert pd.isna(derived.loc[idx[1], "bid"])
    assert pd.isna(derived.loc[idx[1], "ask"])
