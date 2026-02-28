from __future__ import annotations

import pytest

from lumibot.tools.ibkr_helper import _max_period_for_bar, _timestep_to_ibkr_bar


@pytest.mark.parametrize(
    "timestep,expected_bar,expected_seconds,expected_component",
    [
        ("second", "1sec", 1, "second"),
        ("1S", "1sec", 1, "second"),
        ("1sec", "1sec", 1, "second"),
        ("20second", "20sec", 20, "20second"),
        ("15s", "15sec", 15, "15second"),
    ],
)
def test_timestep_to_ibkr_bar_supports_seconds_aliases(
    timestep: str,
    expected_bar: str,
    expected_seconds: int,
    expected_component: str,
):
    bar, seconds, component = _timestep_to_ibkr_bar(timestep)
    assert bar == expected_bar
    assert seconds == expected_seconds
    assert component == expected_component


def test_max_period_for_seconds_bars():
    assert _max_period_for_bar("1sec") == "1000sec"
    assert _max_period_for_bar("20sec") == "20000sec"
