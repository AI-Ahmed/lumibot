from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from lumibot.entities import Asset
from lumibot.strategies.strategy import Strategy
from lumibot.tools.indicators import _format_indicator_plotly_text, plot_indicators


@pytest.mark.parametrize(
    ("detail_text", "expects_break"),
    [
        (None, False),
        (float("nan"), False),
        (pd.NA, False),
        ("", False),
        ("   ", False),
        ("hello", True),
        (0, True),
        (0.0, True),
        (1.2345, True),
        ({"a": 1}, True),
        (["x", "y"], True),
    ],
    ids=[
        "none",
        "nan",
        "pd_NA",
        "empty",
        "whitespace",
        "string",
        "int",
        "float_zero",
        "float",
        "dict",
        "list",
    ],
)
def test_format_indicator_plotly_text_is_nan_safe(detail_text: object, expects_break: bool) -> None:
    text = _format_indicator_plotly_text(123.45, detail_text)
    assert isinstance(text, str)
    assert text.startswith("Value: ")

    if expects_break:
        assert "<br>" in text
    else:
        assert "<br>" not in text


def test_plot_indicators_handles_lines_with_detail_text_nan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LUMIBOT_WRITE_INDICATORS_HTML", "1")
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    # Mixed rows: one omits the key entirely -> pandas fills NaN (float) for that row.
    chart_lines_df = pd.DataFrame(
        [
            {"plot_name": "default_plot", "name": "Trend", "datetime": pd.Timestamp("2020-01-01"), "value": 1.0},
            {
                "plot_name": "default_plot",
                "name": "Trend",
                "datetime": pd.Timestamp("2020-01-02"),
                "value": 1.1,
                "detail_text": "trend=1",
            },
        ]
    )

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=None,
        chart_lines_df=chart_lines_df,
        strategy_name="Test",
        show_indicators=True,
    )

    mock_write.assert_called_once()
    assert (tmp_path / "plot.csv").exists()
    assert (tmp_path / "plot.parquet").exists()

    parquet_df = pd.read_parquet(tmp_path / "plot.parquet")
    assert not parquet_df.empty
    assert "type" in parquet_df.columns


def test_plot_indicators_handles_lines_missing_detail_text_column(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LUMIBOT_WRITE_INDICATORS_HTML", "1")
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    # All rows omit detail_text -> column absent.
    chart_lines_df = pd.DataFrame(
        [
            {"plot_name": "default_plot", "name": "Trend", "datetime": pd.Timestamp("2020-01-01"), "value": 1.0},
            {"plot_name": "default_plot", "name": "Trend", "datetime": pd.Timestamp("2020-01-02"), "value": 1.1},
        ]
    )

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=None,
        chart_lines_df=chart_lines_df,
        strategy_name="Test",
        show_indicators=True,
    )

    mock_write.assert_called_once()


def test_plot_indicators_emits_empty_csv_and_parquet_when_no_chart_data(tmp_path, monkeypatch) -> None:
    """Regression: indicators artifacts should exist even when a strategy emits no data."""
    monkeypatch.setenv("LUMIBOT_WRITE_INDICATORS_HTML", "1")
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=None,
        chart_lines_df=None,
        chart_ohlc_df=None,
        strategy_name="Test",
        show_indicators=True,
    )

    mock_write.assert_called_once()
    assert (tmp_path / "plot.csv").exists()
    assert (tmp_path / "plot.parquet").exists()


def test_plot_indicators_handles_markers_missing_detail_text_column(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LUMIBOT_WRITE_INDICATORS_HTML", "1")
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    chart_markers_df = pd.DataFrame(
        [
            {
                "plot_name": "default_plot",
                "name": "Entry",
                "datetime": pd.Timestamp("2020-01-01"),
                "value": 100.0,
                "symbol": "circle",
                "color": "green",
            },
            {
                "plot_name": "default_plot",
                "name": "Exit",
                "datetime": pd.Timestamp("2020-01-02"),
                "value": 101.0,
                "symbol": "circle",
                "color": "red",
            },
        ]
    )

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=chart_markers_df,
        chart_lines_df=None,
        strategy_name="Test",
        show_indicators=True,
    )

    mock_write.assert_called_once()


@pytest.mark.parametrize(
    "detail_text",
    [None, float("nan"), pd.NA, "", "hello", 1.23, {"k": "v"}],
    ids=["none", "nan", "pd_NA", "empty", "string", "float", "dict"],
)
def test_plot_indicators_handles_non_string_detail_text_values(tmp_path, monkeypatch, detail_text: object) -> None:
    monkeypatch.setenv("LUMIBOT_WRITE_INDICATORS_HTML", "1")
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    chart_lines_df = pd.DataFrame(
        [
            {
                "plot_name": "default_plot",
                "name": "Test",
                "datetime": pd.Timestamp("2020-01-01"),
                "value": 1.0,
                "detail_text": detail_text,
            }
        ]
    )

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=None,
        chart_lines_df=chart_lines_df,
        strategy_name="Test",
        show_indicators=True,
    )

    mock_write.assert_called_once()


def _make_strategy_stub():
    strat = Strategy.__new__(Strategy)
    strat._chart_markers_list = []
    strat._chart_lines_list = []
    strat.logger = MagicMock()
    strat.portfolio_value = 1_000
    strat.get_datetime = lambda: pd.Timestamp("2024-01-01")
    return strat


def test_add_line_rejects_non_string_detail_text() -> None:
    strat = _make_strategy_stub()
    with pytest.raises(ValueError, match="detail_text.*must be a string"):
        strat.add_line("line", 1.0, detail_text=123)  # type: ignore[arg-type]


def test_add_marker_rejects_non_string_detail_text() -> None:
    strat = _make_strategy_stub()
    with pytest.raises(ValueError, match="detail_text.*must be a string"):
        strat.add_marker("marker", 1.0, detail_text=123)  # type: ignore[arg-type]


def test_add_marker_deduplicates_same_timestamp_name_symbol_plot() -> None:
    strat = _make_strategy_stub()
    dt = pd.Timestamp("2024-01-01T00:00:00Z")
    asset = Asset(symbol="SPY", asset_type="stock")

    first = strat.add_marker("buy", 100.0, symbol="circle", dt=dt, plot_name="default_plot", asset=asset)
    second = strat.add_marker("buy", 100.0, symbol="circle", dt=dt, plot_name="default_plot", asset=asset)

    assert first is not None
    assert second is None
    assert len(strat._chart_markers_list) == 1


def test_get_lines_and_markers_df_include_detail_text_column() -> None:
    strat = _make_strategy_stub()
    dt = pd.Timestamp("2024-01-01")
    asset = Asset(symbol="SPY", asset_type="stock")

    strat.add_line("price", 100.0, dt=dt, asset=asset)
    strat.add_marker("buy", 100.0, dt=dt, asset=asset)

    lines_df = strat.get_lines_df()
    markers_df = strat.get_markers_df()

    assert "detail_text" in lines_df.columns
    assert "detail_text" in markers_df.columns


def test_plot_indicators_trade_derived_markers_preserve_detail_text_without_value_prefix(
    tmp_path, monkeypatch
) -> None:
    """Trade-derived markers (used_trade_derived_markers=True) should use detail_text as-is.

    When strategy emits no custom markers, plot_indicators derives markers from trades.
    Those markers already have rich detail_text from _build_trade_marker_tooltip.
    We must NOT wrap them with "Value: X" - that would be redundant and expose raw floats.
    """
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    # Strategy returns: 0% first day, 1% second day
    strategy_df = pd.DataFrame(
        {"return": [0.0, 0.01]},
        index=pd.DatetimeIndex(["2024-05-01 09:30:00", "2024-05-01 10:00:00"], tz="UTC"),
    )

    # Trade with all fields needed for _build_trade_marker_tooltip
    trades_df = pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2024-05-01 09:45:00", tz="UTC"),
                "side": "buy",
                "status": "fill",
                "filled_quantity": 10.0,
                "price": 170.50,
                "symbol": "AAPL",
                "type": "market",
                "asset.multiplier": 1,
                "asset.asset_type": "stock",
                "trade_cost": 1705.0,
                "trade_slippage": 0.0,
            }
        ]
    )

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=None,
        chart_lines_df=None,
        trades_df=trades_df,
        strategy_df=strategy_df,
        initial_budget=100_000.0,
        strategy_name="Test",
        show_indicators=True,
    )

    assert (tmp_path / "plot.csv").exists()
    out_df = pd.read_csv(tmp_path / "plot.csv")
    markers = out_df[out_df["type"] == "marker"]
    assert not markers.empty, "Expected at least one marker from trade-derived path"

    for _, row in markers.iterrows():
        detail = row.get("detail_text", "")
        assert not str(detail).startswith("Value: "), (
            f"Trade-derived marker detail_text must NOT start with 'Value: '; got: {detail!r}"
        )
        # Should contain trade details from _build_trade_marker_tooltip
        assert "AAPL" in str(detail) or "Price:" in str(detail)


def test_plot_indicators_custom_markers_still_get_value_prefix(tmp_path, monkeypatch) -> None:
    """Custom markers (from add_marker) must still get _format_indicator_plotly_text.

    When chart_markers_df is provided by the strategy, used_trade_derived_markers=False.
    Custom markers should get "Value: X" prefix (and optional detail_text appended).
    """
    mock_write = MagicMock()
    monkeypatch.setattr("plotly.graph_objects.Figure.write_html", mock_write)

    chart_markers_df = pd.DataFrame(
        [
            {
                "plot_name": "default_plot",
                "name": "Entry",
                "datetime": pd.Timestamp("2024-01-01 10:00:00"),
                "value": 150.25,
                "symbol": "circle",
                "color": "green",
                "detail_text": "Custom entry signal",
            },
            {
                "plot_name": "default_plot",
                "name": "Exit",
                "datetime": pd.Timestamp("2024-01-02 10:00:00"),
                "value": 155.0,
                "symbol": "circle",
                "color": "red",
            },
        ]
    )

    plot_indicators(
        plot_file_html=str(tmp_path / "plot.html"),
        chart_markers_df=chart_markers_df,
        chart_lines_df=None,
        trades_df=None,
        strategy_df=None,
        strategy_name="Test",
        show_indicators=True,
    )

    assert (tmp_path / "plot.csv").exists()
    out_df = pd.read_csv(tmp_path / "plot.csv")
    markers = out_df[out_df["type"] == "marker"]
    assert len(markers) == 2

    for _, row in markers.iterrows():
        detail = row.get("detail_text", "")
        assert str(detail).startswith("Value: "), (
            f"Custom marker detail_text must start with 'Value: '; got: {detail!r}"
        )
