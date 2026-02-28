import contextlib
import math
import os
import re
import webbrowser

import numpy as np
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import pytz
import quantstats_lumi as qs
from plotly.subplots import make_subplots

from ..constants import LUMIBOT_DEFAULT_TIMEZONE
from lumibot.tools import to_datetime_aware

from .yahoo_helper import YahooHelper as yh

from lumibot.tools.lumibot_logger import get_logger
from lumibot.tools.parquet_utils import (
    coerce_object_columns_to_json_strings,
    is_parquet_required,
    write_parquet_with_logging,
)

logger = get_logger(__name__)

_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}

TERMINAL_TRADE_STATUSES_FOR_MARKERS = {
    "fill",
    "filled",
    "partial_fill",
    "cash_settled",
    "assigned",
    "assignment",
    "exercise",
    "exercised",
    "expired",
    "expire",
}


def _format_indicator_plotly_text(value: object, detail_text: object) -> str:
    """Format plotly hover text for indicator markers/lines.

    Strategies frequently omit `detail_text` for some points. When those points are collected
    into a pandas DataFrame, missing values are represented as `NaN` (a float), not `None`.
    This helper treats None/NaN/NA/empty strings as "no detail text" and always returns a
    string, never raising.
    """

    base = "Value: " + str(value)

    if detail_text is None:
        return base

    try:
        if bool(pd.isna(detail_text)):
            return base
    except Exception:
        # `pd.isna(list)` returns an array; `bool(array)` raises. Treat those as not-missing.
        pass

    detail_str = str(detail_text)
    if detail_str.strip() == "":
        return base

    return base + "<br>" + detail_str


def _build_trade_marker_tooltip(row: pd.Series):
    """Return tooltip text for a trade marker; None when the row lacks required data."""
    status_value = row.get("status")
    if pd.isna(status_value) or str(status_value).strip() == "":
        return None

    status_text = str(status_value)
    if status_text.lower() not in TERMINAL_TRADE_STATUSES_FOR_MARKERS:
        return None

    for key in ("filled_quantity", "price"):
        value = row.get(key)
        if pd.isna(value):
            return None

    try:
        filled_quantity_dec = Decimal(str(row["filled_quantity"]))
        price_dec = Decimal(str(row["price"]))
    except (InvalidOperation, TypeError, ValueError):
        return None

    multiplier_value = row.get("asset.multiplier")
    if pd.isna(multiplier_value) or multiplier_value == "":
        return None
    try:
        multiplier_dec = Decimal(str(multiplier_value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    try:
        amount_transacted_dec = price_dec * filled_quantity_dec * multiplier_dec
    except (InvalidOperation, TypeError, ValueError):
        return None

    trade_cost_value = row.get("trade_cost")
    trade_cost_dec = None
    if not (pd.isna(trade_cost_value) or trade_cost_value == ""):
        try:
            trade_cost_dec = Decimal(str(trade_cost_value))
        except (InvalidOperation, TypeError, ValueError):
            trade_cost_dec = None

    if trade_cost_dec is None:
        trade_cost_dec = amount_transacted_dec

    slippage_value = row.get("trade_slippage")
    slippage_dec = None
    if not (pd.isna(slippage_value) or slippage_value == ""):
        try:
            slippage_dec = Decimal(str(slippage_value))
        except (InvalidOperation, TypeError, ValueError):
            slippage_dec = None

    if row.get("asset.asset_type") == "option":
        try:
            return (
                status_text
                + "<br>"
                + str(filled_quantity_dec.quantize(Decimal("0.01")).__format__(",f"))
                + " "
                + str(row.get("symbol"))
                + " "
                + str(row.get("asset.right"))
                + " Option"
                + "<br>"
                + "Strike: "
                + str(row.get("asset.strike"))
                + "<br>"
                + "Expiration: "
                + str(row.get("asset.expiration"))
                + "<br>"
                + "Price: "
                + str(price_dec.quantize(Decimal("0.0001")).__format__(",f"))
                + "<br>"
                + "Order Type: "
                + str(row.get("type"))
                + "<br>"
                + "Amount Transacted: "
                + str(
                    (
                        price_dec
                        * filled_quantity_dec
                        * (multiplier_dec if multiplier_dec != Decimal("0") else Decimal("1"))
                    )
                    .quantize(Decimal("0.01"))
                    .__format__(",f")
                )
                + "<br>"
                + "Trade Cost: "
                + str(trade_cost_dec.quantize(Decimal("0.01")).__format__(",f"))
                + "<br>"
                + "Slippage: "
                + (
                    str(slippage_dec.quantize(Decimal("0.01")).__format__(",f"))
                    if slippage_dec is not None
                    else "0.00"
                )
                + "<br>"
            )
        except (InvalidOperation, TypeError, ValueError):
            return None

    if multiplier_dec == Decimal("0"):
        return None

    try:
        amount_transacted = amount_transacted_dec.quantize(Decimal("0.01")).__format__(",f")
        price_text = str(price_dec.quantize(Decimal("0.0001")).__format__(",f"))
        filled_qty_text = str(filled_quantity_dec.quantize(Decimal("0.01")).__format__(",f"))
        trade_cost_text = str(trade_cost_dec.quantize(Decimal("0.01")).__format__(",f"))
        slippage_text = (
            str(slippage_dec.quantize(Decimal("0.01")).__format__(",f"))
            if slippage_dec is not None
            else "0.00"
        )
    except (InvalidOperation, TypeError, ValueError):
        return None

    return (
        status_text
        + "<br>"
        + filled_qty_text
        + " "
        + str(row.get("symbol"))
        + "<br>"
        + "Price: "
        + price_text
        + "<br>"
        + "Order Type: "
        + str(row.get("type"))
        + "<br>"
        + "Amount Transacted: "
        + amount_transacted
        + "<br>"
        + "Trade Cost: "
        + trade_cost_text
        + "<br>"
        + "Slippage: "
        + slippage_text
        + "<br>"
    )


def total_return(_df):
    """Calculate the cumulative return in a dataframe
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily)
    """
    df = _df.copy()
    df = df.sort_index(ascending=True)
    df["cum_return"] = (1 + df["return"]).cumprod()

    total_ret = df["cum_return"].iloc[-1] - 1

    return total_ret


def cagr(_df):
    """Calculate the Compound Annual Growth Rate
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily)

    Example:
    >>> df = pd.DataFrame({"return": [0.1, 0.2, 0.3, 0.4, 0.5]})
    >>> cagr(df)
    0.3125


    """
    df = _df.copy()
    df = df.sort_index(ascending=True)
    df["cum_return"] = (1 + df["return"]).cumprod()
    total_ret = df["cum_return"].iloc[-1]
    try:
        start = pd.Timestamp(df.index[0])
        end = pd.Timestamp(df.index[-1])
        if start.tzinfo is None:
            start = start.tz_localize(pytz.UTC)
        else:
            start = start.tz_convert(pytz.UTC)
        if end.tzinfo is None:
            end = end.tz_localize(pytz.UTC)
        else:
            end = end.tz_convert(pytz.UTC)
        period_years = (end - start).days / 365.25
    except Exception:
        # Avoid tearing down backtests during end-of-run stats generation; return neutral CAGR.
        return 0
    if period_years == 0:
        return 0
    CAGR = (total_ret) ** (1 / period_years) - 1
    return CAGR


def volatility(_df):
    """Calculate the volatility (standard deviation)
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily)
    """
    df = _df.copy()
    try:
        start = pd.Timestamp(df.index[0])
        end = pd.Timestamp(df.index[-1])
        if start.tzinfo is None:
            start = start.tz_localize(pytz.UTC)
        else:
            start = start.tz_convert(pytz.UTC)
        if end.tzinfo is None:
            end = end.tz_localize(pytz.UTC)
        else:
            end = end.tz_convert(pytz.UTC)
        period_years = (end - start).days / 365.25
    except Exception:
        # Avoid tearing down backtests during end-of-run stats generation; return neutral volatility.
        return 0
    if period_years == 0:
        return 0
    ratio_to_annual = df["return"].count() / period_years
    vol = df["return"].std() * math.sqrt(ratio_to_annual)
    return vol


def sharpe(_df, risk_free_rate):
    """Calculate the Sharpe Rate, or (CAGR - risk_free_rate) / volatility
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily).
    risk_free_rate should be either LIBOR, or the shortest possible US Treasury Rate
    """
    ret = cagr(_df)
    vol = volatility(_df)
    if vol == 0:
        return 0
    sharpe = (ret - risk_free_rate) / vol
    return sharpe


def sortino(_df, risk_free_rate):
    """Calculate the Sortino ratio: (CAGR - risk_free_rate) / downside deviation.
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily).
    """
    ret = cagr(_df)
    df = _df.copy()
    df = df.sort_index(ascending=True)
    downside = df["return"][df["return"] < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    start = datetime.fromtimestamp(df.index.values[0].astype("O") / 1e9, pytz.UTC)
    end = datetime.fromtimestamp(df.index.values[-1].astype("O") / 1e9, pytz.UTC)
    period_years = max((end - start).days / 365.25, 1e-9)
    ratio_to_annual = df["return"].count() / period_years
    downside_vol = downside.std() * math.sqrt(ratio_to_annual)
    return (ret - risk_free_rate) / downside_vol


def max_drawdown(_df):
    """Calculate the Max Drawdown, or the biggest percentage drop
    from peak to trough.
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily)
    """
    if _df.shape[0] == 1:
        return {"drawdown": 0, "date": _df.index[0]}
    df = _df.copy()
    df = df.sort_index(ascending=True)
    df["cum_return"] = (1 + df["return"]).cumprod()
    df["cum_return_max"] = df["cum_return"].cummax()
    df["drawdown"] = df["cum_return_max"] - df["cum_return"]
    df["drawdown_pct"] = df["drawdown"] / df["cum_return_max"]

    drawdown = df["drawdown_pct"].max()
    if math.isnan(drawdown):
        drawdown = 0

    date = df["drawdown_pct"].idxmax()
    if type(date) == float and math.isnan(date):
        date = df.index[0]

    return {"drawdown": drawdown, "date": date}


def romad(_df):
    """Calculate the Return Over Maximum Drawdown (RoMaD)
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily)
    """
    ret = cagr(_df)
    mdd = max_drawdown(_df)
    if mdd["drawdown"] == 0:
        return 0
    romad = ret / mdd["drawdown"]
    return romad


def longest_drawdown_days(_df):
    """Calculate the longest drawdown period in number of days (or observations).
    The dataframe _df must include a column "return" that has the return for that time period.
    Returns the length of the longest contiguous period where equity was below the running peak.
    """
    if _df is None or _df.shape[0] < 2:
        return 0
    df = _df.copy()
    df = df.sort_index(ascending=True)
    df["cum_return"] = (1 + df["return"]).cumprod()
    df["cum_return_max"] = df["cum_return"].cummax()
    df["drawdown_pct"] = 1 - df["cum_return"] / df["cum_return_max"]
    in_dd = df["drawdown_pct"] > 0
    if not in_dd.any():
        return 0
    # Find longest run of True (in drawdown)
    runs = in_dd.ne(in_dd.shift()).cumsum()
    run_lengths = in_dd.groupby(runs).sum()
    return int(run_lengths.max()) if len(run_lengths) > 0 else 0


def _returns_series_from_df(_df):
    """Return a pandas Series of returns for use with quantstats (e.g. PSR)."""
    if _df is None or _df.empty or "return" not in _df.columns:
        return None
    return _df["return"].dropna()


def psr(_df, risk_free_rate, periods_per_year=252):
    """Probabilistic Sharpe Ratio (probability that true SR > 0).
    Uses quantstats. Returns a float in [0, 1] or 0 if computation fails.
    """
    series = _returns_series_from_df(_df)
    if series is None or len(series) < 2:
        return 0.0
    try:
        return float(qs.stats.probabilistic_sharpe_ratio(series, rf=risk_free_rate, periods=periods_per_year))
    except Exception:
        return 0.0


def dsr(_df, risk_free_rate, n_trials=100, periods_per_year=252):
    """Deflated Sharpe Ratio (adjusts for multiple testing).
    Uses FPAP if available, else returns None (caller may show '—').
    """
    series = _returns_series_from_df(_df)
    if series is None or len(series) < 10:
        return None
    sr = sharpe(_df, risk_free_rate)
    return _compute_dsr_from_series(series, risk_free_rate, periods_per_year, sr_estimates=sr, n_trials=n_trials)


def _compute_dsr_from_series(series, risk_free_rate, periods_per_year, sr_estimates=None, n_trials=100):
    """Compute Deflated Sharpe Ratio from returns series. Uses FPAP if available.
    sr_estimates: annualized Sharpe (e.g. from QuantStats) for consistency; if None, uses Lumibot sharpe().
    """
    if series is None or len(series) < 10:
        return None
    try:
        from fpap.backtests.statistics import compute_dsr, compute_moments

        m1, m2, m3, m4 = compute_moments(series)
        if sr_estimates is None:
            _df = pd.DataFrame({"return": series})
            sr_estimates = sharpe(_df, risk_free_rate)
        # FPAP compute_dsr: backtest_var is variance of SR estimate; m2 is second moment (variance)
        backtest_var = m2**2
        em_sr, dsr_val = compute_dsr(
            sr_estimates=sr_estimates,
            backtest_var=backtest_var,
            sample_length=len(series),
            N_trials=n_trials,
            skewness_of_returns=m3,
            kurtosis_of_returns=m4,
            freq=periods_per_year,
        )
        return float(dsr_val)
    except Exception:
        return None


def stats_summary(_df, risk_free_rate):
    return {
        "cagr": cagr(_df),
        "volatility": volatility(_df),
        "sharpe": sharpe(_df, risk_free_rate),
        "max_drawdown": max_drawdown(_df),
        "romad": romad(_df),
        "total_return": total_return(_df),
    }


def performance(_df, risk_free, prefix=""):
    """Calculate and print out all of our performance indicators
    The dataframe _df must include a column "return" that
    has the return for that time period (eg. daily)
    """
    cagr_adj = cagr(_df)
    vol_adj = volatility(_df)
    sharpe_adj = sharpe(_df, risk_free)
    maxdown_adj = max_drawdown(_df)
    romad_adj = romad(_df)

    print(f"{prefix} CAGR {cagr_adj*100:,.2f}%")
    print(f"{prefix} Volatility {vol_adj*100:,.2f}%")
    print(f"{prefix} Sharpe {sharpe_adj:0.2f}")
    print(f"{prefix} Max Drawdown {maxdown_adj['drawdown']*100:,.2f}% on {maxdown_adj['date']:%Y-%m-%d}")
    print(f"{prefix} RoMaD {romad_adj*100:,.2f}%")


def get_symbol_returns(symbol, start=datetime(1900, 1, 1), end=datetime.now()):
    """Get the returns for a symbol between two dates

    Parameters
    ----------
    symbol : str
        The symbol to get the returns for
    start : datetime, optional
        The start date, by default datetime(1900, 1, 1)
    end : datetime, optional
        The end date, by default datetime.now()

    Returns
    -------
    pd.DataFrame
        A dataframe with the returns for the symbol. Includes the columns:
        - pct_change: The percent change in the Close price
        - div_yield: The dividend yield
        - return: The pct_change + div_yield
        - symbol_cumprod: The cumulative product of (1 + return)

    """
    # Fetch the symbol data
    returns_df = yh.get_symbol_data(symbol,
                                    last_needed_datetime=end,
                                    debug=False)

    if returns_df is None:
        return None

    # Make sure we are working with a copy to avoid SettingWithCopyWarning
    returns_df = returns_df.copy()

    # Filter the DataFrame based on date range
    returns_df = returns_df.loc[(returns_df.index.date >= start.date()) & (returns_df.index.date <= end.date())]
    if returns_df.empty:
        return returns_df

    # Calculate percentage change and dividend yield
    returns_df.loc[:, "pct_change"] = returns_df["Close"].pct_change()
    returns_df.loc[:, "div_yield"] = returns_df["Dividends"] / returns_df["Close"]

    # Calculate total return and cumulative product
    returns_df.loc[:, "return"] = returns_df["pct_change"] + returns_df["div_yield"]
    returns_df.loc[:, "symbol_cumprod"] = (1 + returns_df["return"]).cumprod()

    # Set the initial cumulative product value to 1
    returns_df.loc[returns_df.index[0], "symbol_cumprod"] = 1

    return returns_df


SAFE_COLOR_CYCLE = [
    "#FF6B6B",  # coral
    "#F4A261",  # sand
    "#2EC4B6",  # teal
    "#7E57C2",  # purple
    "#F9C74F",  # gold
    "#34A0A4",  # aquamarine
    "#E63946",  # crimson
]
_BLACK_VALUES = {"black", "#000", "#000000", "rgb(0,0,0)", "rgba(0,0,0,1)"}


def _safe_color(raw_color, key_hint=""):
    """Return a color guaranteed to be visible against dark backgrounds."""
    if isinstance(raw_color, str):
        color_text = raw_color.strip().lower()
        if color_text and color_text not in _BLACK_VALUES:
            return raw_color
    if raw_color is not None and not isinstance(raw_color, str):
        return raw_color

    idx = abs(hash(key_hint)) % len(SAFE_COLOR_CYCLE)
    return SAFE_COLOR_CYCLE[idx]


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    normalized = str(raw_value).strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    return default


def _safe_subplot_vertical_spacing(rows: int, default_spacing: float = 0.15, epsilon: float = 1e-6) -> float:
    # Plotly requires vertical_spacing <= 1 / (rows - 1) for multi-row layouts.
    if rows <= 1:
        return 0.0

    max_allowed = (1.0 / float(rows - 1)) - epsilon
    if max_allowed <= 0:
        return 0.0

    return min(default_spacing, max_allowed)


def calculate_returns(symbol, start=datetime(1900, 1, 1), end=datetime.now()):
    start = to_datetime_aware(start)
    end = to_datetime_aware(end)
    benchmark_df = get_symbol_returns(symbol, start, end)

    risk_free_rate = get_risk_free_rate()

    performance(benchmark_df, risk_free_rate, symbol)


def plot_indicators(
    plot_file_html="indicators.html",
    chart_markers_df=None,
    chart_lines_df=None,
    chart_ohlc_df=None,
    strategy_name=None,
    show_indicators=True,
):
    # If show plot is False, then we don't want to open the plot in the browser
    if not show_indicators:
        logger.debug("show_indicators is False, not creating the plot file.")
        return

    logger.info("\nCreating indicators plot...")

    # Assign "default_plot" as plot_name for markers and lines that don't have one
    if chart_markers_df is not None and not chart_markers_df.empty:
        chart_markers_df = chart_markers_df.copy()
        if "plot_name" not in chart_markers_df.columns:
            chart_markers_df["plot_name"] = "default_plot"
        else:
            chart_markers_df["plot_name"] = chart_markers_df["plot_name"].fillna("default_plot")

    if chart_lines_df is not None and not chart_lines_df.empty:
        chart_lines_df = chart_lines_df.copy()
        if "plot_name" not in chart_lines_df.columns:
            chart_lines_df["plot_name"] = "default_plot"
        else:
            chart_lines_df["plot_name"] = chart_lines_df["plot_name"].fillna("default_plot")

    if chart_ohlc_df is not None and not chart_ohlc_df.empty:
        chart_ohlc_df = chart_ohlc_df.copy()
        if "plot_name" not in chart_ohlc_df.columns:
            chart_ohlc_df["plot_name"] = "default_plot"
        else:
            chart_ohlc_df["plot_name"] = chart_ohlc_df["plot_name"].fillna("default_plot")

    # Get unique plot_names from markers and lines
    plot_names = set()

    if chart_markers_df is not None and not chart_markers_df.empty:
        plot_names.update(chart_markers_df["plot_name"].unique())

    if chart_lines_df is not None and not chart_lines_df.empty:
        plot_names.update(chart_lines_df["plot_name"].unique())

    if chart_ohlc_df is not None and not chart_ohlc_df.empty:
        plot_names.update(chart_ohlc_df["plot_name"].unique())

    # Convert to sorted list to ensure consistent order. Ensure at least one subplot exists
    # even when the strategy emitted no chart data (empty indicators should still produce artifacts).
    plot_names = sorted(list(plot_names)) or ["default_plot"]
    num_subplots = len(plot_names)
    subplot_titles = plot_names

    vertical_spacing = _safe_subplot_vertical_spacing(num_subplots)
    if vertical_spacing < 0.15:
        logger.info(
            f"Adjusted indicators subplot vertical spacing from 0.15 to {vertical_spacing:.6f} for {num_subplots} rows."
        )

    try:
        # Create subplots without shared x-axes
        fig = make_subplots(
            rows=num_subplots,
            cols=1,
            subplot_titles=subplot_titles,
            shared_xaxes=False,  # Do not use shared x-axes
            vertical_spacing=vertical_spacing,
        )

        has_chart_data = False

        ###############################
        # Chart Markers
        ###############################

        def generate_marker_plotly_text(row):
            return _format_indicator_plotly_text(row.get("value"), row.get("detail_text"))

        # Plot the chart markers
        if chart_markers_df is not None and not chart_markers_df.empty:
            chart_markers_df["detail_text"] = chart_markers_df.apply(generate_marker_plotly_text, axis=1)

            # Group by plot_name first, then by name
            for plot_name, plot_df in chart_markers_df.groupby("plot_name"):
                # Loop over the marker names for this plot_name
                for marker_name, group_df in plot_df.groupby("name"):
                    group_df = group_df.copy()
                    # Get the marker symbol
                    marker_symbol = group_df["symbol"].iloc[0]

                    # Determine marker size(s), falling back to sensible defaults when unspecified
                    default_marker_size = 25
                    raw_sizes = group_df.get("size")
                    marker_size = default_marker_size

                    if raw_sizes is not None:
                        marker_sizes = pd.to_numeric(raw_sizes, errors="coerce")

                        if isinstance(marker_sizes, pd.Series):
                            marker_sizes = marker_sizes.fillna(default_marker_size).clip(lower=1)
                            unique_sizes = marker_sizes.unique()
                            if len(unique_sizes) == 1:
                                marker_size = float(unique_sizes[0])
                            else:
                                marker_size = marker_sizes.tolist()
                        else:
                            if pd.isna(marker_sizes) or marker_sizes <= 0:
                                marker_size = default_marker_size
                            else:
                                marker_size = float(marker_sizes)

                    if "color" not in group_df.columns:
                        group_df["color"] = None
                    group_df.loc[:, "color"] = group_df["color"].apply(
                        lambda val: _safe_color(val, f"{plot_name}:{marker_name}")
                    )

                    # Determine which subplot to use
                    row = plot_names.index(plot_name) + 1

                    # Create a new trace for this marker name
                    fig.add_trace(
                        go.Scatter(
                            x=group_df["datetime"],
                            y=group_df["value"],
                            mode="markers",
                            name=marker_name,
                            marker_color=group_df["color"],
                            marker_size=marker_size,
                            marker_symbol=marker_symbol,
                            hovertemplate=f"{marker_name}<br>%{{text}}<br>%{{x|%b %d %Y %I:%M:%S %p}}<extra></extra>",
                            text=group_df["detail_text"],
                        ),
                        row=row,
                        col=1
                    )

            has_chart_data = True

        ###############################
        # Chart Lines
        ###############################

        def generate_line_plotly_text(row):
            return _format_indicator_plotly_text(row.get("value"), row.get("detail_text"))

        # Plot the chart lines
        if chart_lines_df is not None and not chart_lines_df.empty:
            chart_lines_df["detail_text"] = chart_lines_df.apply(generate_line_plotly_text, axis=1)

            # Group by plot_name first, then by name
            for plot_name, plot_df in chart_lines_df.groupby("plot_name"):
                # Loop over the line names for this plot_name
                for line_name, group_df in plot_df.groupby("name"):
                    if "color" not in group_df.columns:
                        group_df = group_df.assign(color=None)
                    color = _safe_color(group_df["color"].iloc[0], f"{plot_name}:{line_name}")

                    # Determine which subplot to use
                    row = plot_names.index(plot_name) + 1

                    # Create a new trace for this line name
                    fig.add_trace(
                        go.Scatter(
                            x=group_df["datetime"],
                            y=group_df["value"],
                            mode="lines",
                            name=line_name,
                            line_color=color,
                            hovertemplate=f"{line_name}<br>%{{text}}<br>%{{x|%b %d %Y %I:%M:%S %p}}<extra></extra>",
                            text=group_df["detail_text"],
                        ),
                        row=row,
                        col=1
                    )

            has_chart_data = True

        ###############################
        # Chart OHLC
        ###############################

        def _generate_ohlc_hover_text(row):
            base = f"O: {row['open']}<br>H: {row['high']}<br>L: {row['low']}<br>C: {row['close']}"
            if row.get("detail_text") is None:
                return base
            return base + "<br>" + str(row.get("detail_text"))

        if chart_ohlc_df is not None and not chart_ohlc_df.empty:
            chart_ohlc_df = chart_ohlc_df.copy()

            for col in ("open", "high", "low", "close"):
                if col not in chart_ohlc_df.columns:
                    logger.warning(f"OHLC data missing required column '{col}', skipping OHLC plotting.")
                    chart_ohlc_df = None
                    break

            if chart_ohlc_df is not None and not chart_ohlc_df.empty:
                if "color" not in chart_ohlc_df.columns:
                    chart_ohlc_df["color"] = None

                # Default per-bar colors: green for bullish, red for bearish (matches Strategy.add_ohlc defaults).
                chart_ohlc_df["color"] = chart_ohlc_df["color"].where(
                    chart_ohlc_df["color"].notna(),
                    np.where(chart_ohlc_df["close"] >= chart_ohlc_df["open"], "green", "red"),
                )

                chart_ohlc_df["detail_text"] = chart_ohlc_df.apply(_generate_ohlc_hover_text, axis=1)

                # Group by plot_name first, then by series name.
                for plot_name, plot_df in chart_ohlc_df.groupby("plot_name"):
                    for ohlc_name, group_df in plot_df.groupby("name"):
                        row = plot_names.index(plot_name) + 1

                        # Preserve per-bar colors by splitting into separate traces per color.
                        color_groups = list(group_df.groupby("color"))
                        for idx, (bar_color, colored_df) in enumerate(color_groups):
                            trace_color = _safe_color(bar_color, f"{plot_name}:{ohlc_name}:{bar_color}")

                            fig.add_trace(
                                go.Candlestick(
                                    x=colored_df["datetime"],
                                    open=colored_df["open"],
                                    high=colored_df["high"],
                                    low=colored_df["low"],
                                    close=colored_df["close"],
                                    name=ohlc_name,
                                    showlegend=idx == 0,
                                    legendgroup=ohlc_name,
                                    increasing_line_color=trace_color,
                                    decreasing_line_color=trace_color,
                                    increasing_fillcolor=trace_color,
                                    decreasing_fillcolor=trace_color,
                                    hovertext=colored_df["detail_text"],
                                    hoverinfo="x+text",
                                ),
                                row=row,
                                col=1,
                            )

                has_chart_data = True

        ###############################
        # Chart Titles and Layouts
        ###############################

        # Set title and layout
        # Calculate height based on number of subplots
        # 400px per subplot
        height = max(800, num_subplots * 400)

        title_text = f"Indicators for {strategy_name}" if strategy_name else "Indicators"
        if not has_chart_data:
            title_text = title_text + " (no indicator data)"

        fig.update_layout(
            title_text=title_text,
            title_font_size=30,
            template="plotly_dark",
            height=height,  # Dynamic height based on number of subplots
            margin=dict(t=150),  # Add more space between title and first subplot
        )

        if has_chart_data:
            # Range selector buttons
            rangeselector_buttons = list([
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(count=6, label="6m", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1y", step="year", stepmode="backward"),
                dict(step="all"),
            ])

            # Update axes for all subplots
            for i in range(1, num_subplots + 1):
                # Get the plot name for this subplot
                plot_title = plot_names[i - 1]

                # Set y-axes titles for each subplot
                fig.update_yaxes(
                    title_text=plot_title,
                    secondary_y=False,
                    row=i,
                    col=1
                )

                # Add range selector and range slider to each subplot
                fig.update_xaxes(
                    rangeselector=dict(
                        buttons=rangeselector_buttons,
                        font=dict(color="black"),
                        activecolor="grey",
                        bgcolor="white",
                    ),
                    rangeslider=dict(
                        visible=True,
                        thickness=0.02  # Make the range slider height shorter to make line graph appear taller
                    ),
                    row=i,
                    col=1
                )

        disable_ui = _env_flag_enabled("LUMIBOT_DISABLE_UI", default=False) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
        write_indicators_html = _env_flag_enabled("LUMIBOT_WRITE_INDICATORS_HTML", default=True)

        if write_indicators_html:
            # Create graph (auto_open disabled for CI/tests).
            fig.write_html(plot_file_html, auto_open=show_indicators and not disable_ui)
        else:
            logger.info(
                "Skipping indicators HTML generation because LUMIBOT_WRITE_INDICATORS_HTML is disabled."
            )
    except Exception:
        logger.exception(
            "Indicators subplot rendering failed; continuing with indicators CSV/parquet export."
        )

    # Get the file name for the CSV file by removing the .html extension and adding .csv
    csv_file = plot_file_html.replace(".html", ".csv")

    # Export chart markers and lines to CSV - combine them and sort by datetime
    standard_columns = [
        "datetime",
        "name",
        "plot_name",
        "type",
        "value",
        "symbol",
        "size",
        "color",
        "detail_text",
        "open",
        "high",
        "low",
        "close",
    ]
    export_dfs = []
    if chart_markers_df is not None and not chart_markers_df.empty:
        markers_out = chart_markers_df.copy()
        markers_out["type"] = "marker"
        export_dfs.append(markers_out)
    if chart_lines_df is not None and not chart_lines_df.empty:
        lines_out = chart_lines_df.copy()
        lines_out["type"] = "line"
        export_dfs.append(lines_out)
    if chart_ohlc_df is not None and not chart_ohlc_df.empty:
        ohlc_out = chart_ohlc_df.copy()
        ohlc_out["type"] = "ohlc"
        export_dfs.append(ohlc_out)

    if export_dfs:
        combined_df = pd.concat(export_dfs, ignore_index=True).sort_values(by="datetime")
    else:
        # Always emit indicators.csv so downstream systems can reliably query it.
        # Some strategies produce no markers/lines/OHLC; treat this as "empty indicators", not a missing artifact.
        combined_df = pd.DataFrame(columns=standard_columns)

    combined_df.to_csv(csv_file, index=False)
    parquet_file = csv_file.replace(".csv", ".parquet")
    required = is_parquet_required()
    write_parquet_with_logging(
        df=combined_df,
        path=parquet_file,
        artifact="indicators",
        logger=logger,
        index=False,
        required=required,
        compression="zstd",
        sanitizer=coerce_object_columns_to_json_strings,
    )


def plot_returns(
    strategy_df,
    strategy_name,
    benchmark_df,
    benchmark_name,
    plot_file_html="backtest_result.html",
    trades_df=None,
    show_plot=True,
    initial_budget=1,
    # chart_markers_df=None,
    # chart_lines_df=None,
):
    # If show plot is False, then we don't want to open the plot in the browser
    if not show_plot:
        logger.info("show_plot is False, not creating the plot file or CSV.")
        return

    disable_ui = _env_flag_enabled("LUMIBOT_DISABLE_UI", default=False) or bool(os.environ.get("PYTEST_CURRENT_TEST"))

    logger.info("\nCreating trades plot and CSV...")

    # --- Start: CSV Generation for trades_df ---
    trades_csv_file = plot_file_html.replace(".html", ".csv")
    # Define standard columns for trades data
    standard_trade_columns = [
        "time", "side", "status", "filled_quantity", "symbol", "asset.asset_type",
        "asset.right", "asset.strike", "asset.expiration", "price", "type",
        "asset.multiplier", "trade_cost", "trade_slippage"
    ]

    if trades_df is None or trades_df.empty:
        logger.info(f"No trades provided. Empty trades CSV file will be created: {trades_csv_file}")
        # Create an empty DataFrame with standard headers for the CSV
        empty_trades_for_csv = pd.DataFrame(columns=standard_trade_columns)
        empty_trades_for_csv.to_csv(trades_csv_file, index=False)
        trades_parquet_file = trades_csv_file.replace(".csv", ".parquet")
        write_parquet_with_logging(
            df=empty_trades_for_csv,
            path=trades_parquet_file,
            artifact="trades",
            logger=logger,
            index=False,
            required=is_parquet_required(),
            compression="zstd",
            sanitizer=coerce_object_columns_to_json_strings,
        )
    else:
        # Prepare a copy of trades_df for CSV export, ensuring standard columns
        trades_df_for_csv = trades_df.copy()
        # Add any missing standard columns (filled with NA)
        for col in standard_trade_columns:
            if col not in trades_df_for_csv.columns:
                trades_df_for_csv[col] = pd.NA
        # Select and reorder to standard columns, dropping any non-standard ones
        trades_df_for_csv = trades_df_for_csv[standard_trade_columns]
        trades_df_for_csv.to_csv(trades_csv_file, index=False)
        logger.info(f"Trades data saved to CSV: {trades_csv_file}")
        trades_parquet_file = trades_csv_file.replace(".csv", ".parquet")
        write_parquet_with_logging(
            df=trades_df_for_csv,
            path=trades_parquet_file,
            artifact="trades",
            logger=logger,
            index=False,
            required=is_parquet_required(),
            compression="zstd",
            sanitizer=coerce_object_columns_to_json_strings,
        )
    # --- End: CSV Generation for trades_df ---

    dfs_concat = []

    _df1 = strategy_df.copy()
    _df1 = _df1.sort_index(ascending=True)
    _df1.index.name = "datetime"
    _df1[strategy_name] = (1 + _df1["return"]).cumprod()
    _df1.loc[_df1.index[0], strategy_name] = 1
    _df1[strategy_name] = _df1[strategy_name] * initial_budget
    dfs_concat.append(_df1)

    _df2 = benchmark_df.copy()
    _df2 = _df2.sort_index(ascending=True)
    _df2.index.name = "datetime"
    _df2[benchmark_name] = (1 + _df2["return"]).cumprod()

    _df2.loc[_df2.index[0], benchmark_name] = 1
    _df2[benchmark_name] = _df2[benchmark_name] * initial_budget

    dfs_concat.append(_df2[benchmark_name])
    df_final = pd.concat(dfs_concat, join="outer", axis=1)

    # Make all the benchmark_df columns lowercase
    benchmark_df.columns = benchmark_df.columns.str.lower()

    # Optional: scale OHLC series into the same units as the strategy budget.
    # Some benchmark sources (e.g. IBKR fallback-to-equity-curve) intentionally provide only
    # returns/cumprod and do not include OHLC. These series are not required for the plot itself.
    if {"close", "open", "high", "low"}.issubset(set(benchmark_df.columns)):
        close_ratio = initial_budget / benchmark_df["close"].iloc[0]
        open_ratio = initial_budget / benchmark_df["open"].iloc[0]
        high_ratio = initial_budget / benchmark_df["high"].iloc[0]
        low_ratio = initial_budget / benchmark_df["low"].iloc[0]

        df_final["Close"] = benchmark_df["close"] * close_ratio
        df_final["Open"] = benchmark_df["open"] * open_ratio
        df_final["High"] = benchmark_df["high"] * high_ratio
        df_final["Low"] = benchmark_df["low"] * low_ratio

    # Prepare trades data for merging into df_final for the plot
    # `processed_trades_for_merge` will be indexed by 'time' and contain standard trade columns (excluding 'time')
    if trades_df is None or trades_df.empty:
        logger.info("There were no trades in this backtest. Plot will not show trade markers.")
        # Create a DataFrame with standard trade columns (all NaN) and df_final's index (if any)
        # This ensures df_final gets all standard trade columns for consistent plotting.
        _columns_for_merge = [col for col in standard_trade_columns if col != "time"]
        if not df_final.index.empty:
            processed_trades_for_merge = pd.DataFrame(index=df_final.index, columns=_columns_for_merge)
        else: # df_final is empty, create an empty df with columns and time index
            processed_trades_for_merge = pd.DataFrame(columns=_columns_for_merge)
            processed_trades_for_merge.index = pd.to_datetime(processed_trades_for_merge.index) # ensure datetimeindex
        processed_trades_for_merge.index.name = "time"
    else:
        # We have trades, prepare a copy
        processed_trades_for_merge = trades_df.copy()
        if 'time' in processed_trades_for_merge.columns:
            processed_trades_for_merge['time'] = pd.to_datetime(processed_trades_for_merge['time'])
            processed_trades_for_merge = processed_trades_for_merge.set_index('time')
            
            # Ensure all standard columns (excluding 'time') are present, filling missing ones with NA
            _columns_to_ensure_in_merge = [col for col in standard_trade_columns if col != "time"]
            for col in _columns_to_ensure_in_merge:
                if col not in processed_trades_for_merge.columns:
                    processed_trades_for_merge[col] = pd.NA
            # Select only the standard columns for merging
            processed_trades_for_merge = processed_trades_for_merge[[col for col in _columns_to_ensure_in_merge if col in processed_trades_for_merge.columns]]
        else:
            logger.warning("Trades data provided but 'time' column is missing. Cannot merge trades for plotting. Plot will not show trade markers.")
            # Fallback to empty trades for merge to avoid errors and ensure consistent columns in df_final
            _columns_for_merge = [col for col in standard_trade_columns if col != "time"]
            if not df_final.index.empty:
                processed_trades_for_merge = pd.DataFrame(index=df_final.index, columns=_columns_for_merge)
            else:
                processed_trades_for_merge = pd.DataFrame(columns=_columns_for_merge)
                processed_trades_for_merge.index = pd.to_datetime(processed_trades_for_merge.index)
            processed_trades_for_merge.index.name = "time"

    df_final = df_final.merge(processed_trades_for_merge, how="outer", left_index=True, right_index=True)

    # Fix for minute timeframe backtests plotting
    # Converted to DatetimeIndex because index becomes Index type and UTC timezone in pd.concat
    # The x-axis is not displayed correctly in plotly when not converted to DatetimeIndex type
    df_final.index = pd.to_datetime(df_final.index, utc=True).tz_convert(LUMIBOT_DEFAULT_TIMEZONE)

    # fig = go.Figure()
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Updated format_positions function to handle lists and dicts
    def format_positions(positions):
        if isinstance(positions, list):
            formatted_positions = [
                f"{pos.get('asset', 'Unknown asset')}: {pos.get('quantity', 0):,.2f}" for pos in positions
            ]
            return "<br>".join(formatted_positions)
        elif isinstance(positions, dict):
            return f"{positions.get('asset', 'Unknown asset')}: {positions.get('quantity', 0):,.2f}"
        return "No positions"

    # Manually create a list of formatted positions
    formatted_positions_list = [format_positions(pos) for pos in df_final["positions"]]

    # Modify the strategy line to include positions
    fig.add_trace(
        go.Scatter(
            x=df_final.index,
            y=df_final[strategy_name],
            mode="lines",
            name=strategy_name,
            connectgaps=True,
            hovertemplate=(
                f"{strategy_name}<br>"
                "Portfolio Value: %{y:$,.4f}<br>"
                "%{x|%b %d %Y %I:%M:%S %p}<br>"
                "Positions:<br>"
                "%{text}<extra></extra>"
            ),
            text=formatted_positions_list,  # Apply the formatting function to positions
        )
    )

    # Benchmark line
    fig.add_trace(
        go.Scatter(
            x=df_final.index,
            y=df_final[benchmark_name],
            mode="lines",
            name=benchmark_name,
            connectgaps=True,
            hovertemplate=f"{benchmark_name}<br>Portfolio Value: %{{y:$,.4f}}<br>%{{x|%b %d %Y %I:%M:%S %p}}<extra></extra>",
        )
    )

    # Cash line
    fig.add_trace(
        go.Scatter(
            x=df_final.index,
            y=df_final["cash"],
            mode="lines",
            name="cash",
            connectgaps=True,
            hovertemplate="Cash<br>Value: %{y:$,.4f}<br>%{x|%b %d %Y %I:%M:%S %p}<extra></extra>",
        ),
        secondary_y=True,
    )

    # Use a % of the range of df_final[strategy_name] to shift the buy and sell ticks
    _max = df_final[strategy_name].max()
    _min = df_final[strategy_name].min()
    vshift = (_max - _min) * 0.10

    # Buy ticks
    buys = df_final.copy()
    buys[strategy_name] = buys[strategy_name].bfill()
    # Include all buy-type sides: buy, buy_to_open, buy_to_cover, buy_to_close
    buys = buys.loc[df_final["side"].isin(["buy", "buy_to_open", "buy_to_cover", "buy_to_close"])]

    def generate_buysell_plotly_text(row):
        # _build_trade_marker_tooltip (handles multiplier, slippage,
        # amount transacted, option details, and TERMINAL_TRADE_STATUSES).
        result = _build_trade_marker_tooltip(row)
        if result is not None:
            return result
        # Fallback: our simpler formatting for rows that fail upstream checks
        # (e.g. missing asset.multiplier for some sources, or status not in whitelist
        # but still executed). Only show for non-canceled, non-new orders.
        if row.get("status") in ("canceled", "new"):
            return None
        try:
            filled_quantity = row.get("filled_quantity")
            price = row.get("price")
            trade_cost = row.get("trade_cost")
            if pd.isna(filled_quantity) or pd.isna(price):
                return None
            try:
                filled_qty_str = str(Decimal(str(filled_quantity)).quantize(Decimal("0.01")).__format__(",f"))
            except (InvalidOperation, TypeError, ValueError):
                filled_qty_str = str(filled_quantity)
            try:
                price_str = str(Decimal(str(price)).quantize(Decimal("0.0001") if row.get("asset.asset_type") == "option" else Decimal("0.01")).__format__(",f"))
            except (InvalidOperation, TypeError, ValueError):
                price_str = str(price)
            try:
                cost_str = str(Decimal(str(trade_cost)).quantize(Decimal("0.01")).__format__(",f")) if not (pd.isna(trade_cost) or trade_cost == "") else "—"
            except (InvalidOperation, TypeError, ValueError):
                cost_str = str(trade_cost) if trade_cost is not None else "—"
            if row.get("asset.asset_type") == "option":
                return f"Option: {row.get('symbol', '')}<br>Quantity: {filled_qty_str}<br>Price: ${price_str}<br>Cost: ${cost_str}"
            return f"Quantity: {filled_qty_str}<br>Price: ${price_str}<br>Cost: ${cost_str}"
        except Exception:
            return None

    buy_ticks_df = buys.apply(generate_buysell_plotly_text, axis=1)

    # Plot the buy ticks
    if not buy_ticks_df.empty:
        buys["plotly_text_buys"] = buy_ticks_df

        # Remove any rows that have a None value for plotly_text_buys
        buys = buys.loc[buys["plotly_text_buys"].notnull()]

        buys.index.name = "datetime"
        buys = (
            buys.groupby(["datetime", strategy_name])["plotly_text_buys"].apply(lambda x: "<br>".join(x)).reset_index()
        )
        buys = buys.set_index("datetime")
        buys["buy_shift"] = buys[strategy_name] - vshift
        fig.add_trace(
            go.Scatter(
                x=buys.index,
                y=buys["buy_shift"],
                mode="markers",
                name="buy",
                marker_symbol="triangle-up",
                marker_color="green",
                marker_size=15,
                hovertemplate="Bought<br>%{text}<br>%{x|%b %d %Y %I:%M:%S %p}<extra></extra>",
                text=buys["plotly_text_buys"],
            )
        )

    ###############################
    # Plot the sell ticks
    ###############################

    # Sell ticks
    sells = df_final.copy()
    sells[strategy_name] = sells[strategy_name].bfill()
    # Include all sell-type sides: sell, sell_to_close, sell_short, sell_to_open
    sells = sells.loc[df_final["side"].isin(["sell", "sell_to_close", "sell_short", "sell_to_open"])]

    sells_ticks_df = sells.apply(generate_buysell_plotly_text, axis=1)

    # Plot the sell ticks
    if not sells_ticks_df.empty:
        sells["plotly_text_sells"] = sells_ticks_df

        # Remove any rows that have a None value for plotly_text_sells
        sells = sells.loc[sells["plotly_text_sells"].notnull()]

        sells.index.name = "datetime"
        sells = (
            sells.groupby(["datetime", strategy_name], group_keys=True)["plotly_text_sells"]
            .apply(lambda x: "<br>".join(x))
            .reset_index()
        )
        sells = sells.set_index("datetime")
        sells["sell_shift"] = sells[strategy_name] + vshift
        fig.add_trace(
            go.Scatter(
                x=sells.index,
                y=sells["sell_shift"],
                mode="markers",
                name="sell",
                marker_color="red",
                marker_size=15,
                marker_symbol="triangle-down",
                hovertemplate="Sold<br>%{text}<br>%{x|%b %d %Y %I:%M:%S %p}<extra></extra>",
                text=sells["plotly_text_sells"],
            )
        )

    ###############################
    # Chart Titles and Layouts
    ###############################

    # Set title and layout
    bm_text = f"Compared With {benchmark_name}" if benchmark_name else ""
    fig.update_layout(
        title_text=f"{strategy_name} {bm_text}",
        title_font_size=30,
        template="plotly_dark",
        xaxis_rangeselector_font_color="black",
        xaxis_rangeselector_activecolor="grey",
        xaxis_rangeselector_bgcolor="white",
    )

    # Set y-axes titles
    fig.update_yaxes(title_text="Strategy/Benchmark", secondary_y=False)
    fig.update_yaxes(title_text="Cash", secondary_y=True)
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeselector=dict(
            buttons=list(
                [
                    dict(count=1, label="1m", step="month", stepmode="backward"),
                    dict(count=6, label="6m", step="month", stepmode="backward"),
                    dict(count=1, label="YTD", step="year", stepmode="todate"),
                    dict(count=1, label="1y", step="year", stepmode="backward"),
                    dict(step="all"),
                ]
            )
        ),
    )

    # Create graph (auto_open disabled for CI/tests).
    fig.write_html(plot_file_html, auto_open=show_plot and not disable_ui)


def _prepare_tearsheet_returns(strategy_df: pd.DataFrame, benchmark_df: pd.DataFrame):
    if strategy_df is None or benchmark_df is None:
        return None

    if strategy_df.empty or benchmark_df.empty:
        return None

    # PERF/MEMORY: Backtests can carry very wide `strategy_df` frames (positions, orders, debug
    # columns, etc.). QuantStats only needs the portfolio value series and the benchmark cumprod.
    # Copying the full frame can spike RSS and has caused production backtests to OOM (exit code -9).
    try:
        _strategy_df = strategy_df.loc[:, ["portfolio_value"]].copy()
    except Exception:
        return None

    if "symbol_cumprod" in benchmark_df.columns:
        _benchmark_df = benchmark_df.loc[:, ["symbol_cumprod"]].copy()
    else:
        # Maintain backward-compat for benchmark frames that don't include `symbol_cumprod`.
        _benchmark_df = pd.DataFrame(index=benchmark_df.index)
        _benchmark_df["symbol_cumprod"] = 1

    _strategy_df.index = pd.to_datetime(_strategy_df.index)
    _benchmark_df.index = pd.to_datetime(_benchmark_df.index)

    df = pd.merge(_strategy_df, _benchmark_df, left_index=True, right_index=True, how="outer")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    df["portfolio_value"] = df["portfolio_value"].ffill()
    df["portfolio_value"] = df["portfolio_value"].bfill()

    if "symbol_cumprod" in df.columns:
        df["symbol_cumprod"] = df["symbol_cumprod"].ffill()
        first_symbol = df["symbol_cumprod"].dropna().iloc[0] if not df["symbol_cumprod"].dropna().empty else 1
    else:
        first_symbol = 1
        df["symbol_cumprod"] = 1

    df.loc[df.index[0], "symbol_cumprod"] = 1 if pd.isna(first_symbol) else first_symbol

    # Seed the resample with the true initial equity so that pct_change sees day 0 -> day 1 moves
    first_strategy_idx = _strategy_df.index.min()
    if pd.notna(first_strategy_idx):
        first_strategy_idx = pd.to_datetime(first_strategy_idx)
        initial_equity = _strategy_df.loc[first_strategy_idx, "portfolio_value"]
        # Some backtests record multiple portfolio snapshots at the same timestamp. In that case
        # `.loc[...]` returns a Series; pick the last value to preserve the later
        # `df.index.duplicated(keep="last")` de-dup semantics.
        if isinstance(initial_equity, pd.Series):
            initial_equity = initial_equity.iloc[-1]
        anchor_idx = first_strategy_idx.normalize() - pd.Timedelta(microseconds=1)
        anchor_row = pd.DataFrame(
            {
                "portfolio_value": [initial_equity],
                "symbol_cumprod": [first_symbol if not pd.isna(first_symbol) else 1],
            },
            index=[anchor_idx],
        )
        df = pd.concat([anchor_row, df], axis=0, sort=True)
        df = df[~df.index.duplicated(keep="last")]

    # Resample to daily cadence and forward-fill non-trading days.
    # NOTE: Use forward-fill (not backfill) so weekends/holidays carry the last known value.
    # Backfilling would leak future values into prior days and can distort volatility-matched charts.
    df = df.resample("D").last()
    df["portfolio_value"] = df["portfolio_value"].ffill()
    df["symbol_cumprod"] = df["symbol_cumprod"].ffill()
    df["strategy"] = df["portfolio_value"].pct_change(fill_method=None).fillna(0)
    df["benchmark"] = df["symbol_cumprod"].pct_change(fill_method=None).fillna(0)

    df_final = df.loc[:, ["strategy", "benchmark"]]
    df_final.index = pd.to_datetime(df_final.index)
    df_final.index = df_final.index.tz_localize(None)

    if df_final.empty or df_final["benchmark"].isnull().all() or df_final["strategy"].isnull().all():
        return None

    return df_final


def _enhance_tearsheet_parameters(tearsheet_file):
    """
    Post-process QuantStats tearsheet HTML to enhance parameter display with professional responsive design.
    
    Implements:
    - Responsive font sizing based on content length
    - Path truncation for long file paths
    - Tooltip on hover for full values
    - Professional word-wrapping
    - Dynamic table layout
    - Targets ONLY the "Parameters Used" table, not other tables
    
    Parameters
    ----------
    tearsheet_file : str
        Path to the tearsheet HTML file to enhance
        
    Notes
    -----
    This function modifies the HTML file in-place to add custom CSS and JavaScript
    for better parameter visualization in the sidebar.
    """
    try:
        # Read the generated HTML
        with open(tearsheet_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # CSS for responsive parameter display - ONLY targets params-table-enhanced class
        enhanced_css = """
        <style>
        /* =================== ENHANCED PARAMETER TABLE STYLING =================== */
        /* Only target the Parameters Used table, not other tables in the tearsheet */
        
        /* Target only the Parameters Used section */
        .params-table-enhanced table {
            width: 100% !important;
            table-layout: fixed !important;
            border-collapse: collapse !important;
        }
        
        /* Parameter table cells - only in enhanced section */
        .params-table-enhanced table td {
            padding: 8px 6px !important;
            vertical-align: top !important;
            position: relative !important;
        }
        
        /* Parameter name column (left) - keep original font size */
        .params-table-enhanced table td:first-child {
            width: 40% !important;
            word-wrap: break-word !important;
            overflow-wrap: break-word !important;
            hyphens: auto !important;
        }
        
        /* Parameter value column (right) */
        .params-table-enhanced table td:last-child {
            width: 60% !important;
            position: relative !important;
        }
        
        /* Value wrapper for truncation */
        .params-table-enhanced table td:last-child .param-value {
            display: block !important;
            max-width: 100% !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
            line-height: 1.4 !important;
            cursor: help !important;
        }
        
        /* Long value handling (paths, large numbers) */
        .params-table-enhanced table td:last-child .param-value.long-value {
            white-space: normal !important;
            word-break: break-all !important;
            font-size: 11px !important;
            line-height: 1.3 !important;
        }
        
        /* Very long value handling (>100 chars) */
        .params-table-enhanced table td:last-child .param-value.very-long-value {
            font-size: 10px !important;
            max-height: 60px !important;
            overflow-y: auto !important;
            white-space: pre-wrap !important;
            word-break: break-all !important;
        }
        
        /* Tooltip styling */
        .params-table-enhanced table td:last-child .param-value:hover::after {
            content: attr(data-full-value) !important;
            position: absolute !important;
            left: 0 !important;
            top: 100% !important;
            z-index: 1000 !important;
            background: #2c3e50 !important;
            color: white !important;
            padding: 10px 14px !important;
            border-radius: 6px !important;
            font-size: 12px !important;
            white-space: pre-wrap !important;
            word-break: break-word !important;
            max-width: 350px !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
            margin-top: 4px !important;
            line-height: 1.5 !important;
        }
        
        /* Path truncation for file paths */
        .params-table-enhanced table td:last-child .param-value.path-value {
            font-family: 'Courier New', monospace !important;
            direction: rtl !important;
            text-align: left !important;
            unicode-bidi: plaintext !important;
            font-size: 11px !important;
        }
        
        /* Numeric value styling - keep normal size */
        .params-table-enhanced table td:last-child .param-value.numeric-value {
            font-family: inherit !important;
        }
        
        /* Emoji preservation */
        .params-table-enhanced table td .param-value {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 
                         'Helvetica Neue', Arial, sans-serif, 'Apple Color Emoji', 
                         'Segoe UI Emoji', 'Segoe UI Symbol' !important;
        }
        
        /* Scrollbar styling for very long values */
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar {
            width: 5px !important;
            height: 5px !important;
        }
        
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar-track {
            background: #f1f1f1 !important;
            border-radius: 3px !important;
        }
        
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar-thumb {
            background: #888 !important;
            border-radius: 3px !important;
        }
        
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar-thumb:hover {
            background: #555 !important;
        }
        </style>
        """
        
        # JavaScript for dynamic value classification and truncation
        enhanced_js = """
        <script>
        (function() {
            'use strict';
            
            // Wait for DOM to be fully loaded
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', enhanceParameters);
            } else {
                enhanceParameters();
            }
            
            function enhanceParameters() {
                // Find the Parameters Used section specifically
                const allElements = document.querySelectorAll('*');
                let paramsSection = null;
                
                // Look for "Parameters Used" heading or table
                for (let elem of allElements) {
                    const text = elem.textContent || '';
                    if (text.includes('Parameters Used') && 
                        (elem.tagName === 'H2' || elem.tagName === 'H3' || elem.tagName === 'DIV')) {
                        paramsSection = elem;
                        break;
                    }
                }
                
                // If we found the section, find the table after it
                let paramsTable = null;
                if (paramsSection) {
                    let nextElem = paramsSection.nextElementSibling;
                    while (nextElem) {
                        if (nextElem.tagName === 'TABLE') {
                            paramsTable = nextElem;
                            break;
                        }
                        // Check if table is inside a container
                        const tableInside = nextElem.querySelector('table');
                        if (tableInside) {
                            paramsTable = tableInside;
                            break;
                        }
                        nextElem = nextElem.nextElementSibling;
                    }
                }
                
                // Fallback: look for table with PARAMETER/VALUE headers
                if (!paramsTable) {
                    const tables = document.querySelectorAll('table');
                    for (let table of tables) {
                        const headers = table.querySelectorAll('th');
                        for (let header of headers) {
                            const headerText = header.textContent.trim().toUpperCase();
                            if (headerText === 'PARAMETER' || headerText === 'VALUE') {
                                paramsTable = table;
                                break;
                            }
                        }
                        if (paramsTable) break;
                    }
                }
                
                if (!paramsTable) {
                    console.log('⚠️ Parameters table not found, skipping enhancement');
                    return;
                }
                
                // Wrap the table in a special container for targeted styling
                if (!paramsTable.parentElement.classList.contains('params-table-enhanced')) {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'params-table-enhanced';
                    paramsTable.parentNode.insertBefore(wrapper, paramsTable);
                    wrapper.appendChild(paramsTable);
                }
                
                // Process only this table
                const rows = paramsTable.querySelectorAll('tr');
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const valueCell = cells[cells.length - 1];
                        const originalValue = valueCell.textContent.trim();
                        
                        // Skip if already processed
                        if (valueCell.querySelector('.param-value')) {
                            return;
                        }
                        
                        // Create wrapper span
                        const wrapper = document.createElement('span');
                        wrapper.className = 'param-value';
                        wrapper.setAttribute('data-full-value', originalValue);
                        
                        // Classify and truncate value
                        let displayValue = originalValue;
                        const valueLength = originalValue.length;
                        
                        // Path detection (contains / or \\)
                        if (originalValue.includes('/') || originalValue.includes('\\\\')) {
                            wrapper.classList.add('path-value');
                            
                            // Truncate path intelligently - show last 2 parts
                            if (valueLength > 60) {
                                const parts = originalValue.split(/[\\/\\\\]/);
                                if (parts.length > 3) {
                                    displayValue = '.../' + parts.slice(-2).join('/');
                                }
                                wrapper.classList.add('long-value');
                            }
                        }
                        // Numeric detection
                        else if (!isNaN(originalValue) || /^[\\d,\\.]+$/.test(originalValue)) {
                            wrapper.classList.add('numeric-value');
                        }
                        // Long text handling
                        else if (valueLength > 100) {
                            wrapper.classList.add('very-long-value');
                        } else if (valueLength > 60) {
                            wrapper.classList.add('long-value');
                        }
                        
                        wrapper.textContent = displayValue;
                        valueCell.textContent = '';
                        valueCell.appendChild(wrapper);
                    }
                });
                
                console.log('✅ Tearsheet parameters enhanced for responsive display');
            }
        })();
        </script>
        """
        
        # Insert enhanced CSS and JS before closing head tag
        if '</head>' in html_content:
            html_content = html_content.replace('</head>', f'{enhanced_css}\n{enhanced_js}\n</head>')
        else:
            # Fallback: insert at the beginning of body
            html_content = html_content.replace('<body>', f'<body>\n{enhanced_css}\n{enhanced_js}')
        
        # Write enhanced HTML back
        with open(tearsheet_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"✅ Enhanced tearsheet parameters for responsive display: {tearsheet_file}")
        
    except Exception as e:
        logger.warning(f"Could not enhance tearsheet parameters (non-critical): {e}")
        # Non-critical error, tearsheet still works without enhancement


def _get_quantstats_metrics_for_summary(strategy_ser, benchmark_ser, risk_free_rate):
    """
    Prepare strategy and benchmark exactly like QuantStats html(), call metrics(),
    and return mtrx plus resolved benchmark/strategy column names.

    Uses quantstats_lumi private APIs (_prepare_returns, _prepare_benchmark, _match_dates).
    Risk: package upgrades may change these; then Summary/KPM alignment could break.

    Returns
    -------
    tuple or None
        (mtrx, b_col, s_col, returns_m, benchmark_m) or None on error.
        returns_m, benchmark_m are the prepared+matched series (for DSR).
    """
    if strategy_ser is None or strategy_ser.empty or benchmark_ser is None or benchmark_ser.empty:
        return None
    try:
        from quantstats_lumi import utils as _qs_utils
        from quantstats_lumi.reports import _match_dates, metrics as qs_metrics

        # Replicate html() flow: dropna, prepare_returns, prepare_benchmark, match_dates
        returns = strategy_ser.dropna()
        if returns.empty:
            return None
        returns = _qs_utils._prepare_returns(returns)
        benchmark = _qs_utils._prepare_benchmark(benchmark_ser, returns.index, risk_free_rate)
        returns, benchmark = _match_dates(returns, benchmark)
        if returns.empty or benchmark.empty:
            return None

        benchmark_title = str(benchmark_ser.name) if getattr(benchmark_ser, "name", None) else "Benchmark"
        strategy_title = str(strategy_ser.name) if getattr(strategy_ser, "name", None) else "Strategy"
        benchmark.name = benchmark_title
        returns.name = strategy_title

        result = qs_metrics(
            returns=returns,
            benchmark=benchmark,
            rf=risk_free_rate,
            display=False,
            mode="full",
            sep=True,
            internal="True",
            compounded=True,
            periods_per_year=365,
            prepare_returns=False,
            benchmark_title=benchmark_title,
            strategy_title=strategy_title,
        )
        mtrx = result[2:]

        # Resolve columns by name (order varies)
        if benchmark_title in mtrx.columns:
            b_col = benchmark_title
        elif len(mtrx.columns) >= 2:
            b_col = mtrx.columns[1] if mtrx.columns[0] == strategy_title else mtrx.columns[0]
        else:
            b_col = mtrx.columns[0]
        if strategy_title in mtrx.columns:
            s_col = strategy_title
        elif len(mtrx.columns) >= 2:
            s_col = mtrx.columns[0] if b_col == mtrx.columns[1] else mtrx.columns[1]
        else:
            s_col = mtrx.columns[0]
        return (mtrx, b_col, s_col, returns, benchmark)
    except Exception as e:
        logger.warning(f"Could not get QuantStats metrics for summary: {e}")
        return None


def _inject_benchmark_into_summary_metrics(tearsheet_file, df_stats_final, risk_free_rate):
    """
    Post-process the tearsheet HTML so the summary metrics block shows benchmark/strategy
    for each metric (e.g. Sharpe as 1.72/0.51). Uses QuantStats metrics() for consistency
    with Key Performance Metrics table. Also appends PSR and DSR rows if computed.

    Summary block must use the same prepared series and metrics contract as QuantStats
    html() so values match the Key Performance Metrics table.

    Parameters
    ----------
    tearsheet_file : str
        Path to the generated tearsheet HTML file.
    df_stats_final : pd.DataFrame
        DataFrame with "strategy" and "benchmark" columns (return series).
    risk_free_rate : float
        Annualized risk-free rate.

    Notes
    -----
    QuantStats html() data flow (must replicate for Summary/KPM alignment):
    1. if match_dates: returns = returns.dropna()
    2. returns = _utils._prepare_returns(returns)
    3. benchmark = _utils._prepare_benchmark(benchmark, returns.index, rf)
    4. if match_dates: returns, benchmark = _match_dates(returns, benchmark)
    5. metrics(returns, benchmark, prepare_returns=False, periods_per_year=365, ...)
    With prepare_returns=False, metrics() uses the already-prepared series. Column order
    in mtrx: identify by name (benchmark_title, strategy_title), not by index.
    Row names: CAGR% (Annual Return), Total Return, Max Drawdown, RoMaD, Longest DD Days,
    Sharpe, Sortino, Prob. Sharpe Ratio (or Prob. Sharpe Ratio %).
    """
    if df_stats_final is None or df_stats_final.empty:
        return
    if "strategy" not in df_stats_final.columns or "benchmark" not in df_stats_final.columns:
        return

    strategy_ser = df_stats_final["strategy"]
    benchmark_ser = df_stats_final["benchmark"]

    result = _get_quantstats_metrics_for_summary(strategy_ser, benchmark_ser, risk_free_rate)
    if result is None:
        return
    mtrx, b_col, s_col, returns_m, benchmark_m = result

    # Row names: QuantStats uses trailing spaces and %; try variants for robustness
    ROWS = {
        "cagr": ("CAGR% (Annual Return)", "CAGR% (Annual Return) "),
        "total_return": ("Total Return",),
        "max_drawdown": ("Max Drawdown", "Max Drawdown %"),
        "romad": ("RoMaD",),
        "longest_dd": ("Longest DD Days",),
        "sharpe": ("Sharpe",),
        "sortino": ("Sortino",),
        "prob_sr": ("Prob. Sharpe Ratio", "Prob. Sharpe Ratio %"),
    }

    def _extract(mtrx, row_names, col, as_pct=False):
        """Extract value from mtrx. If as_pct=True, return decimal (e.g. 0.0655 for 6.55%).
        Tries each row_name and variants (strip, rstrip) for index lookup.
        """
        names = row_names if isinstance(row_names, (list, tuple)) else (row_names,)
        for rn in names:
            for row_name in (rn, rn.strip(), rn.rstrip(), rn.replace(" %", "")):
                try:
                    if row_name not in mtrx.index:
                        continue
                    v = mtrx.loc[row_name, col]
                    if v == "-" or (isinstance(v, float) and (pd.isna(v) or abs(v) == np.inf)):
                        return None
                    s = str(v).replace("%", "").replace(",", "").strip()
                    if not s or s == "-":
                        return None
                    x = float(s)
                    if as_pct:
                        return x / 100.0  # QuantStats stores 6.55 for 6.55%
                    return x
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _fmt_pct(bm, st):
        bm_s = "—" if bm is None else f"{bm * 100:.2f}%"
        st_s = "—" if st is None else f"{st * 100:.2f}%"
        return f"{bm_s}/{st_s}"

    def _fmt_num(bm, st, decimals=2):
        bm_s = "—" if bm is None else f"{bm:.{decimals}f}"
        st_s = "—" if st is None else f"{st:.{decimals}f}"
        return f"{bm_s}/{st_s}"

    def _fmt_int(bm, st):
        bm_s = "—" if bm is None else str(int(bm))
        st_s = "—" if st is None else str(int(st))
        return f"{bm_s}/{st_s}"

    annual_ret = _fmt_pct(
        _extract(mtrx, ROWS["cagr"], b_col, as_pct=True),
        _extract(mtrx, ROWS["cagr"], s_col, as_pct=True),
    )
    total_ret = _fmt_pct(
        _extract(mtrx, ROWS["total_return"], b_col, as_pct=True),
        _extract(mtrx, ROWS["total_return"], s_col, as_pct=True),
    )
    b_mdd = _extract(mtrx, ROWS["max_drawdown"], b_col, as_pct=True)
    s_mdd = _extract(mtrx, ROWS["max_drawdown"], s_col, as_pct=True)
    max_dd = _fmt_pct(b_mdd, s_mdd)  # QuantStats stores negative; show as-is to match KPM
    romad_val = _fmt_num(
        _extract(mtrx, ROWS["romad"], b_col),
        _extract(mtrx, ROWS["romad"], s_col),
    )
    longest_dd = _fmt_int(
        _extract(mtrx, ROWS["longest_dd"], b_col),
        _extract(mtrx, ROWS["longest_dd"], s_col),
    )
    sharpe_val = _fmt_num(
        _extract(mtrx, ROWS["sharpe"], b_col),
        _extract(mtrx, ROWS["sharpe"], s_col),
    )
    sortino_val = _fmt_num(
        _extract(mtrx, ROWS["sortino"], b_col),
        _extract(mtrx, ROWS["sortino"], s_col),
    )
    # PSR: QuantStats stores 0–100; display as scale 100 + %
    b_psr = _extract(mtrx, ROWS["prob_sr"], b_col)
    s_psr = _extract(mtrx, ROWS["prob_sr"], s_col)
    psr_bm_s = "—" if b_psr is None else f"{b_psr:.2f}%"
    psr_st_s = "—" if s_psr is None else f"{s_psr:.2f}%"
    psr_val = f"{psr_bm_s}/{psr_st_s}"

    # DSR: FPAP returns 0–1; display as scale 100 + %
    periods_per_year = 365
    b_sharpe = _extract(mtrx, ROWS["sharpe"], b_col)
    s_sharpe = _extract(mtrx, ROWS["sharpe"], s_col)
    b_dsr = _compute_dsr_from_series(
        benchmark_m, risk_free_rate, periods_per_year, sr_estimates=b_sharpe
    )
    s_dsr = _compute_dsr_from_series(
        returns_m, risk_free_rate, periods_per_year, sr_estimates=s_sharpe
    )
    dsr_bm = "—" if b_dsr is None else f"{b_dsr * 100:.2f}%"
    dsr_st = "—" if s_dsr is None else f"{s_dsr * 100:.2f}%"
    dsr_val = f"{dsr_bm}/{dsr_st}"

    try:
        with open(tearsheet_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Could not read tearsheet for benchmark summary injection: {e}")
        return

    right_start = content.find('<div id="right">')
    params_start = content.find("<!-- Parameters section -->")
    if right_start == -1 or params_start == -1:
        return

    block = content[right_start:params_start]

    # Replace h1 (Annual Return) in metric-main. Use \g<1>/\g<3> to avoid \12 being parsed as group 12
    block = re.sub(
        r"(<div class=\"metric-main\">.*?<h1>)(.*?)(</h1>)",
        r"\g<1>" + annual_ret + r"\g<3>",
        block,
        count=1,
        flags=re.DOTALL,
    )

    # Replace each metric-sub h2 by matching the preceding metric-title (avoids wrong h2 matches)
    # Note: HTML has class="metric-title">Title <span... so we match metric-title">Title
    replacements = [
        (r'(metric-title">Total Return.*?</div>\s*<h2>)(.*?)(</h2>)', total_ret),
        (r'(metric-title">Max Drawdown.*?</div>\s*<h2>)(.*?)(</h2>)', max_dd),
        (r'(metric-title">RoMaD.*?</div>\s*<h2>)(.*?)(</h2>)', romad_val),
        (r'(metric-title">Longest DD Days.*?</div>\s*<h2>)(.*?)(</h2>)', longest_dd),
        (r'(metric-title">Sharpe.*?</div>\s*<h2>)(.*?)(</h2>)', sharpe_val),
        (r'(metric-title">Sortino.*?</div>\s*<h2>)(.*?)(</h2>)', sortino_val),
    ]
    for pattern, val in replacements:
        block = re.sub(pattern, r"\g<1>" + val + r"\g<3>", block, count=1, flags=re.DOTALL)

    # Insert PSR and DSR metric-sub divs before closing metric-sub-container
    # Block ends before "<!-- Parameters section -->", so we match the container's closing </div> and trailing whitespace
    psr_dsr_html = f"""
                <div class="metric-sub">
                    <div class="metric-title">PSR <span class="info-icon" title="Probabilistic Sharpe Ratio: probability that true SR > 0.">&#9432;</span></div>
                    <h2>{psr_val}</h2>
                </div>
                <div class="metric-sub">
                    <div class="metric-title">DSR <span class="info-icon" title="Deflated Sharpe Ratio: adjusts for multiple testing.">&#9432;</span></div>
                    <h2>{dsr_val}</h2>
                </div>
            </div>

            """

    # Replace the closing </div> of metric-sub-container (last in block) with PSR/DSR divs + same closing
    # Block excludes the comment; it ends with "            </div>\n\n            " (trailing spaces before comment)
    old_close = re.compile(
        r"(\n            </div>\s*\n\s*\n\s*)$",
        re.MULTILINE,
    )
    if old_close.search(block):
        block = old_close.sub(
            psr_dsr_html.rstrip() + r"\n\n            ",
            block,
            count=1,
        )

    content = content[:right_start] + block + content[params_start:]

    # Inject DSR row into KPM table so Summary and KPM show same DSR (scale 100 + %)
    dsr_bm_cell = dsr_bm if dsr_bm != "—" else "-"
    dsr_st_cell = dsr_st if dsr_st != "—" else "-"
    dsr_kpm_row = f'<tr><td>DSR</td><td>{dsr_bm_cell}</td><td>{dsr_st_cell}</td></tr>'
    content = re.sub(
        r'(<tr><td>Prob\. Sharpe Ratio</td><td>[^<]*</td><td>[^<]*</td></tr>)',
        r'\1\n' + dsr_kpm_row,
        content,
        count=1,
    )

    # Inject CSS: reduced font size, no truncation, info-icon visible
    summary_metrics_css = """
    /* Summary metrics: reduced font size, proportional, no truncation */
    #right .metric-sub-container { overflow: visible; min-width: 0; }
    #right .metric-sub { overflow: visible; min-width: 0; }
    #right .metric-sub h2 { font-size: clamp(0.7em, 1vw, 1.2em); overflow: visible; white-space: nowrap; }
    #right .metric-main h1 { font-size: clamp(0.9em, 1.5vw, 1.6em); overflow: visible; }
    #right .metric-sub .metric-title { overflow: visible; }
    #right .info-icon { pointer-events: auto; position: relative; z-index: 1; }
    """
    if "</head>" in content:
        content = content.replace("</head>", f"<style>{summary_metrics_css}</style>\n</head>", 1)
    elif "<body>" in content:
        content = content.replace("<body>", f"<body>\n<style>{summary_metrics_css}</style>", 1)

    try:
        with open(tearsheet_file, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"Could not write tearsheet after benchmark summary injection: {e}")

    # Add layout configurations to improve the chart appearance
    fig.update_layout(
        title=f"{strategy_name} Strategy Compared With {benchmark_name}",
        xaxis=dict(
            title="Date",
            title_font=dict(size=12),  # Changed from titlefont to title_font
            showgrid=True,
            gridcolor='rgba(230, 230, 230, 0.3)',
        ),
        yaxis=dict(
            title="Strategy/Benchmark",
            title_font=dict(size=12),  # Changed from titlefont to title_font
            showgrid=True,
            gridcolor='rgba(230, 230, 230, 0.3)',
            tickformat="$,.4f",  # Format y-axis ticks as currency
            rangemode="tozero",  # Start y-axis at zero
        ),
        yaxis2=dict(
            title="Cash",
            title_font=dict(size=12),  # Changed from titlefont to title_font
            showgrid=False,
            tickformat="$,.4f",  # Format y-axis ticks as currency
            rangemode="tozero",  # Start y-axis at zero
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        margin=dict(l=50, r=50, t=80, b=50),
        plot_bgcolor='rgba(250, 250, 250, 0.9)',
        hovermode="closest",
        height=600,
    )

    # Add a range slider for better navigation
    fig.update_layout(
        xaxis=dict(
            rangeslider=dict(visible=True),
            type="date"
        )
    )
    
    # Fix axis scaling issues by ensuring reasonable ranges
    y_values = df_final[[strategy_name, benchmark_name]].values.flatten()
    y_values = y_values[~np.isnan(y_values)]  # Remove NaN values
    
    if len(y_values) > 0:
        y_min = min(y_values)
        y_max = max(y_values)
        y_range = y_max - y_min
        
        # Set y-axis range with padding
        fig.update_layout(
            yaxis=dict(
                range=[max(0, y_min - 0.1 * y_range), y_max + 0.1 * y_range]
            )
        )
        
    # Fix cash axis scaling if needed
    cash_values = df_final["cash"].values
    cash_values = cash_values[~np.isnan(cash_values)]
    
    if len(cash_values) > 0:
        cash_min = min(cash_values)
        cash_max = max(cash_values)
        cash_range = cash_max - cash_min
        
        fig.update_layout(
            yaxis2=dict(
                range=[max(0, cash_min - 0.1 * cash_range), cash_max + 0.1 * cash_range]
            )
        )


def _enhance_tearsheet_parameters(tearsheet_file):
    """
    Post-process QuantStats tearsheet HTML to enhance parameter display with professional responsive design.
    
    Implements:
    - Responsive font sizing based on content length
    - Path truncation for long file paths
    - Tooltip on hover for full values
    - Professional word-wrapping
    - Dynamic table layout
    - Targets ONLY the "Parameters Used" table, not other tables
    
    Parameters
    ----------
    tearsheet_file : str
        Path to the tearsheet HTML file to enhance
        
    Notes
    -----
    This function modifies the HTML file in-place to add custom CSS and JavaScript
    for better parameter visualization in the sidebar.
    """
    try:
        # Read the generated HTML
        with open(tearsheet_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # CSS for responsive parameter display - ONLY targets params-table-enhanced class
        enhanced_css = """
        <style>
        /* =================== ENHANCED PARAMETER TABLE STYLING =================== */
        /* Only target the Parameters Used table, not other tables in the tearsheet */
        
        /* Target only the Parameters Used section */
        .params-table-enhanced table {
            width: 100% !important;
            table-layout: fixed !important;
            border-collapse: collapse !important;
        }
        
        /* Parameter table cells - only in enhanced section */
        .params-table-enhanced table td {
            padding: 8px 6px !important;
            vertical-align: top !important;
            position: relative !important;
        }
        
        /* Parameter name column (left) - keep original font size */
        .params-table-enhanced table td:first-child {
            width: 40% !important;
            word-wrap: break-word !important;
            overflow-wrap: break-word !important;
            hyphens: auto !important;
        }
        
        /* Parameter value column (right) */
        .params-table-enhanced table td:last-child {
            width: 60% !important;
            position: relative !important;
        }
        
        /* Value wrapper for truncation */
        .params-table-enhanced table td:last-child .param-value {
            display: block !important;
            max-width: 100% !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
            line-height: 1.4 !important;
            cursor: help !important;
        }
        
        /* Long value handling (paths, large numbers) */
        .params-table-enhanced table td:last-child .param-value.long-value {
            white-space: normal !important;
            word-break: break-all !important;
            font-size: 11px !important;
            line-height: 1.3 !important;
        }
        
        /* Very long value handling (>100 chars) */
        .params-table-enhanced table td:last-child .param-value.very-long-value {
            font-size: 10px !important;
            max-height: 60px !important;
            overflow-y: auto !important;
            white-space: pre-wrap !important;
            word-break: break-all !important;
        }
        
        /* Tooltip styling */
        .params-table-enhanced table td:last-child .param-value:hover::after {
            content: attr(data-full-value) !important;
            position: absolute !important;
            left: 0 !important;
            top: 100% !important;
            z-index: 1000 !important;
            background: #2c3e50 !important;
            color: white !important;
            padding: 10px 14px !important;
            border-radius: 6px !important;
            font-size: 12px !important;
            white-space: pre-wrap !important;
            word-break: break-word !important;
            max-width: 350px !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
            margin-top: 4px !important;
            line-height: 1.5 !important;
        }
        
        /* Path truncation for file paths */
        .params-table-enhanced table td:last-child .param-value.path-value {
            font-family: 'Courier New', monospace !important;
            direction: rtl !important;
            text-align: left !important;
            unicode-bidi: plaintext !important;
            font-size: 11px !important;
        }
        
        /* Numeric value styling - keep normal size */
        .params-table-enhanced table td:last-child .param-value.numeric-value {
            font-family: inherit !important;
        }
        
        /* Emoji preservation */
        .params-table-enhanced table td .param-value {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 
                         'Helvetica Neue', Arial, sans-serif, 'Apple Color Emoji', 
                         'Segoe UI Emoji', 'Segoe UI Symbol' !important;
        }
        
        /* Scrollbar styling for very long values */
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar {
            width: 5px !important;
            height: 5px !important;
        }
        
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar-track {
            background: #f1f1f1 !important;
            border-radius: 3px !important;
        }
        
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar-thumb {
            background: #888 !important;
            border-radius: 3px !important;
        }
        
        .params-table-enhanced table td:last-child .param-value.very-long-value::-webkit-scrollbar-thumb:hover {
            background: #555 !important;
        }
        </style>
        """
        
        # JavaScript for dynamic value classification and truncation
        enhanced_js = """
        <script>
        (function() {
            'use strict';
            
            // Wait for DOM to be fully loaded
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', enhanceParameters);
            } else {
                enhanceParameters();
            }
            
            function enhanceParameters() {
                // Find the Parameters Used section specifically
                const allElements = document.querySelectorAll('*');
                let paramsSection = null;
                
                // Look for "Parameters Used" heading or table
                for (let elem of allElements) {
                    const text = elem.textContent || '';
                    if (text.includes('Parameters Used') && 
                        (elem.tagName === 'H2' || elem.tagName === 'H3' || elem.tagName === 'DIV')) {
                        paramsSection = elem;
                        break;
                    }
                }
                
                // If we found the section, find the table after it
                let paramsTable = null;
                if (paramsSection) {
                    let nextElem = paramsSection.nextElementSibling;
                    while (nextElem) {
                        if (nextElem.tagName === 'TABLE') {
                            paramsTable = nextElem;
                            break;
                        }
                        // Check if table is inside a container
                        const tableInside = nextElem.querySelector('table');
                        if (tableInside) {
                            paramsTable = tableInside;
                            break;
                        }
                        nextElem = nextElem.nextElementSibling;
                    }
                }
                
                // Fallback: look for table with PARAMETER/VALUE headers
                if (!paramsTable) {
                    const tables = document.querySelectorAll('table');
                    for (let table of tables) {
                        const headers = table.querySelectorAll('th');
                        for (let header of headers) {
                            const headerText = header.textContent.trim().toUpperCase();
                            if (headerText === 'PARAMETER' || headerText === 'VALUE') {
                                paramsTable = table;
                                break;
                            }
                        }
                        if (paramsTable) break;
                    }
                }
                
                if (!paramsTable) {
                    console.log('⚠️ Parameters table not found, skipping enhancement');
                    return;
                }
                
                // Wrap the table in a special container for targeted styling
                if (!paramsTable.parentElement.classList.contains('params-table-enhanced')) {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'params-table-enhanced';
                    paramsTable.parentNode.insertBefore(wrapper, paramsTable);
                    wrapper.appendChild(paramsTable);
                }
                
                // Process only this table
                const rows = paramsTable.querySelectorAll('tr');
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const valueCell = cells[cells.length - 1];
                        const originalValue = valueCell.textContent.trim();
                        
                        // Skip if already processed
                        if (valueCell.querySelector('.param-value')) {
                            return;
                        }
                        
                        // Create wrapper span
                        const wrapper = document.createElement('span');
                        wrapper.className = 'param-value';
                        wrapper.setAttribute('data-full-value', originalValue);
                        
                        // Classify and truncate value
                        let displayValue = originalValue;
                        const valueLength = originalValue.length;
                        
                        // Path detection (contains / or \\)
                        if (originalValue.includes('/') || originalValue.includes('\\\\')) {
                            wrapper.classList.add('path-value');
                            
                            // Truncate path intelligently - show last 2 parts
                            if (valueLength > 60) {
                                const parts = originalValue.split(/[\\/\\\\]/);
                                if (parts.length > 3) {
                                    displayValue = '.../' + parts.slice(-2).join('/');
                                }
                                wrapper.classList.add('long-value');
                            }
                        }
                        // Numeric detection
                        else if (!isNaN(originalValue) || /^[\\d,\\.]+$/.test(originalValue)) {
                            wrapper.classList.add('numeric-value');
                        }
                        // Long text handling
                        else if (valueLength > 100) {
                            wrapper.classList.add('very-long-value');
                        } else if (valueLength > 60) {
                            wrapper.classList.add('long-value');
                        }
                        
                        wrapper.textContent = displayValue;
                        valueCell.textContent = '';
                        valueCell.appendChild(wrapper);
                    }
                });
                
                console.log('✅ Tearsheet parameters enhanced for responsive display');
            }
        })();
        </script>
        """
        
        # Insert enhanced CSS and JS before closing head tag
        if '</head>' in html_content:
            html_content = html_content.replace('</head>', f'{enhanced_css}\n{enhanced_js}\n</head>')
        else:
            # Fallback: insert at the beginning of body
            html_content = html_content.replace('<body>', f'<body>\n{enhanced_css}\n{enhanced_js}')
        
        # Write enhanced HTML back
        with open(tearsheet_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"✅ Enhanced tearsheet parameters for responsive display: {tearsheet_file}")
        
    except Exception as e:
        logger.warning(f"Could not enhance tearsheet parameters (non-critical): {e}")
        # Non-critical error, tearsheet still works without enhancement


def _get_quantstats_metrics_for_summary(strategy_ser, benchmark_ser, risk_free_rate):
    """
    Prepare strategy and benchmark exactly like QuantStats html(), call metrics(),
    and return mtrx plus resolved benchmark/strategy column names.

    Uses quantstats_lumi private APIs (_prepare_returns, _prepare_benchmark, _match_dates).
    Risk: package upgrades may change these; then Summary/KPM alignment could break.

    Returns
    -------
    tuple or None
        (mtrx, b_col, s_col, returns_m, benchmark_m) or None on error.
        returns_m, benchmark_m are the prepared+matched series (for DSR).
    """
    if strategy_ser is None or strategy_ser.empty or benchmark_ser is None or benchmark_ser.empty:
        return None
    try:
        from quantstats_lumi import utils as _qs_utils
        from quantstats_lumi.reports import _match_dates, metrics as qs_metrics

        # Replicate html() flow: dropna, prepare_returns, prepare_benchmark, match_dates
        returns = strategy_ser.dropna()
        if returns.empty:
            return None
        returns = _qs_utils._prepare_returns(returns)
        benchmark = _qs_utils._prepare_benchmark(benchmark_ser, returns.index, risk_free_rate)
        returns, benchmark = _match_dates(returns, benchmark)
        if returns.empty or benchmark.empty:
            return None

        benchmark_title = str(benchmark_ser.name) if getattr(benchmark_ser, "name", None) else "Benchmark"
        strategy_title = str(strategy_ser.name) if getattr(strategy_ser, "name", None) else "Strategy"
        benchmark.name = benchmark_title
        returns.name = strategy_title

        result = qs_metrics(
            returns=returns,
            benchmark=benchmark,
            rf=risk_free_rate,
            display=False,
            mode="full",
            sep=True,
            internal="True",
            compounded=True,
            periods_per_year=365,
            prepare_returns=False,
            benchmark_title=benchmark_title,
            strategy_title=strategy_title,
        )
        mtrx = result[2:]

        # Resolve columns by name (order varies)
        if benchmark_title in mtrx.columns:
            b_col = benchmark_title
        elif len(mtrx.columns) >= 2:
            b_col = mtrx.columns[1] if mtrx.columns[0] == strategy_title else mtrx.columns[0]
        else:
            b_col = mtrx.columns[0]
        if strategy_title in mtrx.columns:
            s_col = strategy_title
        elif len(mtrx.columns) >= 2:
            s_col = mtrx.columns[0] if b_col == mtrx.columns[1] else mtrx.columns[1]
        else:
            s_col = mtrx.columns[0]
        return (mtrx, b_col, s_col, returns, benchmark)
    except Exception as e:
        logger.warning(f"Could not get QuantStats metrics for summary: {e}")
        return None


def _inject_benchmark_into_summary_metrics(tearsheet_file, df_stats_final, risk_free_rate):
    """
    Post-process the tearsheet HTML so the summary metrics block shows benchmark/strategy
    for each metric (e.g. Sharpe as 1.72/0.51). Uses QuantStats metrics() for consistency
    with Key Performance Metrics table. Also appends PSR and DSR rows if computed.

    Summary block must use the same prepared series and metrics contract as QuantStats
    html() so values match the Key Performance Metrics table.

    Parameters
    ----------
    tearsheet_file : str
        Path to the generated tearsheet HTML file.
    df_stats_final : pd.DataFrame
        DataFrame with "strategy" and "benchmark" columns (return series).
    risk_free_rate : float
        Annualized risk-free rate.

    Notes
    -----
    QuantStats html() data flow (must replicate for Summary/KPM alignment):
    1. if match_dates: returns = returns.dropna()
    2. returns = _utils._prepare_returns(returns)
    3. benchmark = _utils._prepare_benchmark(benchmark, returns.index, rf)
    4. if match_dates: returns, benchmark = _match_dates(returns, benchmark)
    5. metrics(returns, benchmark, prepare_returns=False, periods_per_year=365, ...)
    With prepare_returns=False, metrics() uses the already-prepared series. Column order
    in mtrx: identify by name (benchmark_title, strategy_title), not by index.
    Row names: CAGR% (Annual Return), Total Return, Max Drawdown, RoMaD, Longest DD Days,
    Sharpe, Sortino, Prob. Sharpe Ratio (or Prob. Sharpe Ratio %).
    """
    if df_stats_final is None or df_stats_final.empty:
        return
    if "strategy" not in df_stats_final.columns or "benchmark" not in df_stats_final.columns:
        return

    strategy_ser = df_stats_final["strategy"]
    benchmark_ser = df_stats_final["benchmark"]

    result = _get_quantstats_metrics_for_summary(strategy_ser, benchmark_ser, risk_free_rate)
    if result is None:
        return
    mtrx, b_col, s_col, returns_m, benchmark_m = result

    # Row names: QuantStats uses trailing spaces and %; try variants for robustness
    ROWS = {
        "cagr": ("CAGR% (Annual Return)", "CAGR% (Annual Return) "),
        "total_return": ("Total Return",),
        "max_drawdown": ("Max Drawdown", "Max Drawdown %"),
        "romad": ("RoMaD",),
        "longest_dd": ("Longest DD Days",),
        "sharpe": ("Sharpe",),
        "sortino": ("Sortino",),
        "prob_sr": ("Prob. Sharpe Ratio", "Prob. Sharpe Ratio %"),
    }

    def _extract(mtrx, row_names, col, as_pct=False):
        """Extract value from mtrx. If as_pct=True, return decimal (e.g. 0.0655 for 6.55%).
        Tries each row_name and variants (strip, rstrip) for index lookup.
        """
        names = row_names if isinstance(row_names, (list, tuple)) else (row_names,)
        for rn in names:
            for row_name in (rn, rn.strip(), rn.rstrip(), rn.replace(" %", "")):
                try:
                    if row_name not in mtrx.index:
                        continue
                    v = mtrx.loc[row_name, col]
                    if v == "-" or (isinstance(v, float) and (pd.isna(v) or abs(v) == np.inf)):
                        return None
                    s = str(v).replace("%", "").replace(",", "").strip()
                    if not s or s == "-":
                        return None
                    x = float(s)
                    if as_pct:
                        return x / 100.0  # QuantStats stores 6.55 for 6.55%
                    return x
                except (KeyError, TypeError, ValueError):
                    continue
        return None

    def _fmt_pct(bm, st):
        bm_s = "—" if bm is None else f"{bm * 100:.2f}%"
        st_s = "—" if st is None else f"{st * 100:.2f}%"
        return f"{bm_s}/{st_s}"

    def _fmt_num(bm, st, decimals=2):
        bm_s = "—" if bm is None else f"{bm:.{decimals}f}"
        st_s = "—" if st is None else f"{st:.{decimals}f}"
        return f"{bm_s}/{st_s}"

    def _fmt_int(bm, st):
        bm_s = "—" if bm is None else str(int(bm))
        st_s = "—" if st is None else str(int(st))
        return f"{bm_s}/{st_s}"

    annual_ret = _fmt_pct(
        _extract(mtrx, ROWS["cagr"], b_col, as_pct=True),
        _extract(mtrx, ROWS["cagr"], s_col, as_pct=True),
    )
    total_ret = _fmt_pct(
        _extract(mtrx, ROWS["total_return"], b_col, as_pct=True),
        _extract(mtrx, ROWS["total_return"], s_col, as_pct=True),
    )
    b_mdd = _extract(mtrx, ROWS["max_drawdown"], b_col, as_pct=True)
    s_mdd = _extract(mtrx, ROWS["max_drawdown"], s_col, as_pct=True)
    max_dd = _fmt_pct(b_mdd, s_mdd)  # QuantStats stores negative; show as-is to match KPM
    romad_val = _fmt_num(
        _extract(mtrx, ROWS["romad"], b_col),
        _extract(mtrx, ROWS["romad"], s_col),
    )
    longest_dd = _fmt_int(
        _extract(mtrx, ROWS["longest_dd"], b_col),
        _extract(mtrx, ROWS["longest_dd"], s_col),
    )
    sharpe_val = _fmt_num(
        _extract(mtrx, ROWS["sharpe"], b_col),
        _extract(mtrx, ROWS["sharpe"], s_col),
    )
    sortino_val = _fmt_num(
        _extract(mtrx, ROWS["sortino"], b_col),
        _extract(mtrx, ROWS["sortino"], s_col),
    )
    # PSR: QuantStats stores 0–100; display as scale 100 + %
    b_psr = _extract(mtrx, ROWS["prob_sr"], b_col)
    s_psr = _extract(mtrx, ROWS["prob_sr"], s_col)
    psr_bm_s = "—" if b_psr is None else f"{b_psr:.2f}%"
    psr_st_s = "—" if s_psr is None else f"{s_psr:.2f}%"
    psr_val = f"{psr_bm_s}/{psr_st_s}"

    # DSR: FPAP returns 0–1; display as scale 100 + %
    periods_per_year = 365
    b_sharpe = _extract(mtrx, ROWS["sharpe"], b_col)
    s_sharpe = _extract(mtrx, ROWS["sharpe"], s_col)
    b_dsr = _compute_dsr_from_series(
        benchmark_m, risk_free_rate, periods_per_year, sr_estimates=b_sharpe
    )
    s_dsr = _compute_dsr_from_series(
        returns_m, risk_free_rate, periods_per_year, sr_estimates=s_sharpe
    )
    dsr_bm = "—" if b_dsr is None else f"{b_dsr * 100:.2f}%"
    dsr_st = "—" if s_dsr is None else f"{s_dsr * 100:.2f}%"
    dsr_val = f"{dsr_bm}/{dsr_st}"

    try:
        with open(tearsheet_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Could not read tearsheet for benchmark summary injection: {e}")
        return

    right_start = content.find('<div id="right">')
    params_start = content.find("<!-- Parameters section -->")
    if right_start == -1 or params_start == -1:
        return

    block = content[right_start:params_start]

    # Replace h1 (Annual Return) in metric-main. Use \g<1>/\g<3> to avoid \12 being parsed as group 12
    block = re.sub(
        r"(<div class=\"metric-main\">.*?<h1>)(.*?)(</h1>)",
        r"\g<1>" + annual_ret + r"\g<3>",
        block,
        count=1,
        flags=re.DOTALL,
    )

    # Replace each metric-sub h2 by matching the preceding metric-title (avoids wrong h2 matches)
    # Note: HTML has class="metric-title">Title <span... so we match metric-title">Title
    replacements = [
        (r'(metric-title">Total Return.*?</div>\s*<h2>)(.*?)(</h2>)', total_ret),
        (r'(metric-title">Max Drawdown.*?</div>\s*<h2>)(.*?)(</h2>)', max_dd),
        (r'(metric-title">RoMaD.*?</div>\s*<h2>)(.*?)(</h2>)', romad_val),
        (r'(metric-title">Longest DD Days.*?</div>\s*<h2>)(.*?)(</h2>)', longest_dd),
        (r'(metric-title">Sharpe.*?</div>\s*<h2>)(.*?)(</h2>)', sharpe_val),
        (r'(metric-title">Sortino.*?</div>\s*<h2>)(.*?)(</h2>)', sortino_val),
    ]
    for pattern, val in replacements:
        block = re.sub(pattern, r"\g<1>" + val + r"\g<3>", block, count=1, flags=re.DOTALL)

    # Insert PSR and DSR metric-sub divs before closing metric-sub-container
    # Block ends before "<!-- Parameters section -->", so we match the container's closing </div> and trailing whitespace
    psr_dsr_html = f"""
                <div class="metric-sub">
                    <div class="metric-title">PSR <span class="info-icon" title="Probabilistic Sharpe Ratio: probability that true SR > 0.">&#9432;</span></div>
                    <h2>{psr_val}</h2>
                </div>
                <div class="metric-sub">
                    <div class="metric-title">DSR <span class="info-icon" title="Deflated Sharpe Ratio: adjusts for multiple testing.">&#9432;</span></div>
                    <h2>{dsr_val}</h2>
                </div>
            </div>

            """

    # Replace the closing </div> of metric-sub-container (last in block) with PSR/DSR divs + same closing
    # Block excludes the comment; it ends with "            </div>\n\n            " (trailing spaces before comment)
    old_close = re.compile(
        r"(\n            </div>\s*\n\s*\n\s*)$",
        re.MULTILINE,
    )
    if old_close.search(block):
        block = old_close.sub(
            psr_dsr_html.rstrip() + r"\n\n            ",
            block,
            count=1,
        )

    content = content[:right_start] + block + content[params_start:]

    # Inject DSR row into KPM table so Summary and KPM show same DSR (scale 100 + %)
    dsr_bm_cell = dsr_bm if dsr_bm != "—" else "-"
    dsr_st_cell = dsr_st if dsr_st != "—" else "-"
    dsr_kpm_row = f'<tr><td>DSR</td><td>{dsr_bm_cell}</td><td>{dsr_st_cell}</td></tr>'
    content = re.sub(
        r'(<tr><td>Prob\. Sharpe Ratio</td><td>[^<]*</td><td>[^<]*</td></tr>)',
        r'\1\n' + dsr_kpm_row,
        content,
        count=1,
    )

    # Inject CSS: reduced font size, no truncation, info-icon visible
    summary_metrics_css = """
    /* Summary metrics: reduced font size, proportional, no truncation */
    #right .metric-sub-container { overflow: visible; min-width: 0; }
    #right .metric-sub { overflow: visible; min-width: 0; }
    #right .metric-sub h2 { font-size: clamp(0.7em, 1vw, 1.2em); overflow: visible; white-space: nowrap; }
    #right .metric-main h1 { font-size: clamp(0.9em, 1.5vw, 1.6em); overflow: visible; }
    #right .metric-sub .metric-title { overflow: visible; }
    #right .info-icon { pointer-events: auto; position: relative; z-index: 1; }
    """
    if "</head>" in content:
        content = content.replace("</head>", f"<style>{summary_metrics_css}</style>\n</head>", 1)
    elif "<body>" in content:
        content = content.replace("<body>", f"<body>\n<style>{summary_metrics_css}</style>", 1)

    try:
        with open(tearsheet_file, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.warning(f"Could not write tearsheet after benchmark summary injection: {e}")


def create_tearsheet(
    strategy_df: pd.DataFrame,
    strat_name: str,
    tearsheet_file: str,
    benchmark_df: pd.DataFrame,
    benchmark_asset,  # This is causing a circular import: Asset,
    show_tearsheet: bool,
    save_tearsheet: bool,
    risk_free_rate: float,
    strategy_parameters: dict = None,
    resample_rule: str = "D",  # Add resample_rule parameter with default "D" for daily
    lumibot_version: str | None = None,
    backtesting_data_source: str | None = None,
    backtesting_data_sources: str | None = None,
    backtest_time_seconds: float | None = None,
):
    # If show tearsheet is False, then we don't want to open the tearsheet in the browser
    # IMS create the tearsheet even if we are not showinbg it
    if not save_tearsheet:
        logger.info("save_tearsheet is False, not creating the tearsheet file.")
        return

    logger.info("\nCreating tearsheet...")

    def _write_placeholder_tearsheet(reason: str) -> None:
        """Write a small HTML file explaining why QuantStats was skipped/failed."""
        try:
            placeholder = f"""<!DOCTYPE html>
<html><head><title>{strat_name} tearsheet unavailable</title></head>
<body><h1>{strat_name}</h1><p>Tearsheet not generated.</p><p>{reason}</p></body>
</html>
"""
            with open(str(tearsheet_file), "w", encoding="utf-8") as f:
                f.write(placeholder)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to write placeholder tearsheet to %s: %s", tearsheet_file, exc)

    # Use memory-optimized _prepare_tearsheet_returns (column subset, no full frame copy).
    df_final = _prepare_tearsheet_returns(strategy_df, benchmark_df)

    if df_final is None:
        logger.warning("No data to create tearsheet; writing placeholder and skipping QuantStats.")
        _write_placeholder_tearsheet("Insufficient data to compute strategy/benchmark return series for this window.")
        return

    df_stats_final = df_final
    is_hft = resample_rule != "D"

    bm_text = f"Compared to {benchmark_asset}" if benchmark_asset else ""
    title = f"{strat_name} {bm_text}"

    # QuantStats (via seaborn/scipy) can raise (e.g., LinAlgError) when the return series is
    # degenerate, such as no trades and a flat portfolio value. In these cases we must not
    # crash the backtest; write a placeholder tearsheet instead.
    strategy_returns = df_final["strategy"].dropna()
    benchmark_returns = df_final["benchmark"].dropna()
    if strategy_returns.empty or benchmark_returns.empty or strategy_returns.nunique() <= 1 or benchmark_returns.nunique() <= 1:
        logger.warning(
            "Not enough return variation to generate QuantStats tearsheet (strategy unique=%d, benchmark unique=%d); writing placeholder.",
            int(strategy_returns.nunique()) if not strategy_returns.empty else 0,
            int(benchmark_returns.nunique()) if not benchmark_returns.empty else 0,
        )
        _write_placeholder_tearsheet("Return series is flat/degenerate (often caused by zero trades).")
        return

    # Set the name of the benchmark column so that quantstats can use it in the report
    df_final["benchmark"].name = str(benchmark_asset)
    df_stats_final["benchmark"].name = str(benchmark_asset)

    # Add HFT-specific metrics to parameters if this is HFT data (when called directly with resample_rule != "D")
    if is_hft:
        if strategy_parameters is None:
            strategy_parameters = {}
        try:
            trades_per_day = len(df_stats_final) / len(set(df_stats_final.index.date))
            strategy_parameters["Avg Trades Per Day"] = f"{trades_per_day:.2f}"
            intraday_vol = df_stats_final["strategy"].std() * np.sqrt(trades_per_day)
            strategy_parameters["Intraday Volatility"] = f"{intraday_vol:.4f}"
            strategy_parameters["Data Sampling"] = f"HFT ({resample_rule} resampling)"
        except Exception as e:
            logger.warning(f"Could not calculate HFT metrics: {e}")

    # Run quantstats reports suppressing any logs because it can be noisy for no reason.
    # On failure (e.g., singular covariance in KDE), write placeholder and optionally retry without KDE.
    result = None
    try:
        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
            result = qs.reports.html(
                df_final["strategy"],
                df_final["benchmark"],
                title=title,
                output=tearsheet_file,
                download_filename=tearsheet_file,
                rf=risk_free_rate,
                parameters=strategy_parameters,
                lumibot_version=lumibot_version,
                backtesting_data_source=backtesting_data_source,
                backtesting_data_sources=backtesting_data_sources,
                backtest_time_seconds=backtest_time_seconds,
            )
    except Exception as exc:
        message = str(exc)
        logger.warning("QuantStats tearsheet generation failed: %s", message)
        retried = False
        if any(token in message for token in ("gaussian_kde", "singular", "covariance matrix")):
            try:
                import quantstats_lumi._plotting.core as _qs_core
                import quantstats_lumi.plots as _qs_plots
                import quantstats_lumi.utils as _qs_utils

                def _histogram_no_kde(returns, benchmark=None, resample="ME", fontname="Arial", grayscale=False,
                                       figsize=(10, 5), ylabel=True, subtitle=True, compounded=True, savefig=None,
                                       show=True, prepare_returns=True):
                    if prepare_returns:
                        returns = _qs_utils._prepare_returns(returns)
                    if benchmark is not None:
                        benchmark = _qs_utils._prepare_returns(benchmark)
                    title_prefix = {"W": "Weekly ", "ME": "Monthly ", "Q": "Quarterly ", "YE": "Annual "}.get(resample, "")
                    return _qs_core.plot_histogram(
                        returns, benchmark, resample=resample, grayscale=grayscale, fontname=fontname,
                        title="Distribution of %sReturns" % title_prefix, kde=False, figsize=figsize,
                        ylabel=ylabel, subtitle=subtitle, compounded=compounded, savefig=savefig, show=show,
                    )

                _qs_plots.histogram = _histogram_no_kde
                with open(os.devnull, "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
                    result = qs.reports.html(
                        df_final["strategy"],
                        df_final["benchmark"],
                        title=title,
                        output=tearsheet_file,
                        download_filename=tearsheet_file,
                        rf=risk_free_rate,
                        parameters=strategy_parameters,
                        lumibot_version=lumibot_version,
                        backtesting_data_source=backtesting_data_source,
                        backtesting_data_sources=backtesting_data_sources,
                        backtest_time_seconds=backtest_time_seconds,
                    )
                retried = True
            except Exception as retry_exc:
                logger.warning("QuantStats retry (disable KDE) failed: %s", retry_exc)
        if not retried:
            _write_placeholder_tearsheet(f"QuantStats error: {exc}")
            return

    if result is not None:
        _enhance_tearsheet_parameters(tearsheet_file)
        _inject_benchmark_into_summary_metrics(tearsheet_file, df_stats_final, risk_free_rate)

    disable_ui = _env_flag_enabled("LUMIBOT_DISABLE_UI", default=False) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if show_tearsheet and not disable_ui:
        url = "file://" + os.path.abspath(str(tearsheet_file))
        webbrowser.open(url)

    return result


def get_risk_free_rate(dt: datetime = None):
    try:
        result = yh.get_risk_free_rate(dt=dt)
    except Exception as e:
        logger.error(f"Error getting the risk free rate: {e}")
        result = 0

    return result
