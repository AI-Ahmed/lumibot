from __future__ import annotations

import pytest

from lumibot.tools.smart_limit_utils import (
    build_price_ladder,
    compute_final_price,
    compute_final_price_from_mid,
    compute_mid,
    infer_tick_size,
    round_to_tick,
)


def test_compute_final_price_clamps_pct():
    bid, ask = 100.0, 101.0
    mid = compute_mid(bid, ask)

    assert compute_final_price(bid, ask, "buy", -1.0) == mid
    assert compute_final_price(bid, ask, "buy", 0.0) == mid
    assert compute_final_price(bid, ask, "buy", 1.0) == ask
    assert compute_final_price(bid, ask, "buy", 2.0) == ask

    assert compute_final_price(bid, ask, "sell", -1.0) == mid
    assert compute_final_price(bid, ask, "sell", 0.0) == mid
    assert compute_final_price(bid, ask, "sell", 1.0) == bid
    assert compute_final_price(bid, ask, "sell", 2.0) == bid


def test_compute_final_price_from_mid_clamps_pct():
    mid = 10.0
    aggressive = 12.0
    assert compute_final_price_from_mid(mid, aggressive, -1.0) == mid
    assert compute_final_price_from_mid(mid, aggressive, 0.0) == mid
    assert compute_final_price_from_mid(mid, aggressive, 1.0) == aggressive
    assert compute_final_price_from_mid(mid, aggressive, 2.0) == aggressive


def test_build_price_ladder_includes_mid_and_final():
    mid = 100.0
    final = 101.0
    ladder = build_price_ladder(mid, final, 3)
    assert ladder == [100.0, 100.5, 101.0]


def test_build_price_ladder_step_count_one_returns_final_only():
    assert build_price_ladder(100.0, 101.0, 1) == [101.0]
    assert build_price_ladder(100.0, 101.0, 0) == [101.0]


def test_infer_tick_size_prefers_larger_common_ticks():
    # By design, `infer_tick_size` returns the first supported tick that both bid/ask are multiples of.
    # For normal 2-decimal prices this is almost always 0.01.
    assert infer_tick_size(1.00, 1.10) == 0.01
    assert infer_tick_size(1.00, 1.05) == 0.01
    assert infer_tick_size(1.01, 1.06) == 0.01


@pytest.mark.parametrize(
    ("side", "price", "tick", "expected"),
    [
        ("buy", 1.01, 0.05, 1.05),
        ("sell", 1.04, 0.05, 1.00),
        (None, 1.03, 0.05, 1.05),
    ],
)
def test_round_to_tick(side, price, tick, expected):
    assert round_to_tick(price, tick, side=side) == expected
