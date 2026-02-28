import contextlib
import math
import os
import re
import webbrowser

import numpy as np
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
import plotly.graph_objects as go
import pytz
import quantstats_lumi as qs
from plotly.subplots import make_subplots

from ..constants import LUMIBOT_DEFAULT_TIMEZONE
from lumibot.tools import to_datetime_aware
from plotly.subplots import make_subplots

from .yahoo_helper import YahooHelper as yh

from lumibot.tools.lumibot_logger import get_logger
logger = get_logger(__name__)


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
    start = datetime.fromtimestamp(df.index.values[0].astype("O") / 1e9, pytz.UTC)
    end = datetime.fromtimestamp(df.index.values[-1].astype("O") / 1e9, pytz.UTC)
    period_years = (end - start).days / 365.25
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
    start = datetime.fromtimestamp(df.index.values[0].astype("O") / 1e9, pytz.UTC)
    end = datetime.fromtimestamp(df.index.values[-1].astype("O") / 1e9, pytz.UTC)
    period_years = (end - start).days / 365.25
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

    # Calculate percentage change and dividend yield
    returns_df.loc[:, "pct_change"] = returns_df["Close"].pct_change()
    returns_df.loc[:, "div_yield"] = returns_df["Dividends"] / returns_df["Close"]

    # Calculate total return and cumulative product
    returns_df.loc[:, "return"] = returns_df["pct_change"] + returns_df["div_yield"]
    returns_df.loc[:, "symbol_cumprod"] = (1 + returns_df["return"]).cumprod()

    # Set the initial cumulative product value to 1
    returns_df.loc[returns_df.index[0], "symbol_cumprod"] = 1

    return returns_df


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

    # Get unique plot_names from markers and lines
    plot_names = set()

    if chart_markers_df is not None and not chart_markers_df.empty:
        plot_names.update(chart_markers_df["plot_name"].unique())

    if chart_lines_df is not None and not chart_lines_df.empty:
        plot_names.update(chart_lines_df["plot_name"].unique())

    # Convert to sorted list to ensure consistent order
    plot_names = sorted(list(plot_names))

    # Ensure num_subplots is at least 1 to avoid ValueError in make_subplots
    num_subplots = max(1, len(plot_names))
    subplot_titles = plot_names if num_subplots > 0 else ["default_plot"]

    # Create subplots without shared x-axes
    fig = make_subplots(
        rows=num_subplots,
        cols=1,
        subplot_titles=subplot_titles,
        shared_xaxes=False,  # Do not use shared x-axes
        vertical_spacing=0.15,  # Increase spacing between subplots to prevent range slider overlap,
    )

    has_chart_data = False

    ###############################
    # Chart Markers
    ###############################

    def generate_marker_plotly_text(row):
        if row["detail_text"] is None:
            return "Value: " + str(row["value"])
        else:
            return "Value: " + str(row["value"]) + "<br>" + row["detail_text"]

    # Plot the chart markers
    if chart_markers_df is not None and not chart_markers_df.empty:
        chart_markers_df["detail_text"] = chart_markers_df.apply(generate_marker_plotly_text, axis=1)

        # Group by plot_name first, then by name
        for plot_name, plot_df in chart_markers_df.groupby("plot_name"):
            # Loop over the marker names for this plot_name
            for marker_name, group_df in plot_df.groupby("name"):
                # Get the marker symbol
                marker_symbol = group_df["symbol"].iloc[0]

                # Get the marker size
                marker_size = group_df["size"].iloc[0]
                marker_size = marker_size if marker_size else 25

                # If color is not set, set it to white
                group_df.loc[:, "color"] = group_df["color"].fillna("white")

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
        if row["detail_text"] is None:
            return "Value: " + str(row["value"])
        else:
            return "Value: " + str(row["value"]) + "<br>" + row["detail_text"]

    # Plot the chart lines
    if chart_lines_df is not None and not chart_lines_df.empty:
        chart_lines_df["detail_text"] = chart_lines_df.apply(generate_line_plotly_text, axis=1)

        # Group by plot_name first, then by name
        for plot_name, plot_df in chart_lines_df.groupby("plot_name"):
            # Loop over the line names for this plot_name
            for line_name, group_df in plot_df.groupby("name"):
                # Get the color for this line name
                color = group_df["color"].iloc[0]

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
    # Chart Titles and Layouts
    ###############################

    if has_chart_data:
        # Set title and layout
        # Calculate height based on number of subplots
        # 400px per subplot
        height = max(800, num_subplots * 400)

        fig.update_layout(
            title_text=f"Indicators for {strategy_name}",
            title_font_size=30,
            template="plotly_dark",
            height=height,  # Dynamic height based on number of subplots
            margin=dict(t=150)  # Add more space between title and first subplot
        )

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

        # Create graph
        fig.write_html(plot_file_html, auto_open=show_indicators)

        # Get the file name for the CSV file by removing the .html extension and adding .csv
        csv_file = plot_file_html.replace(".html", ".csv")

        # Export chart markers and lines to CSV - combine them and sort by datetime
        if chart_markers_df is not None and not chart_markers_df.empty and chart_lines_df is not None and not chart_lines_df.empty:
            # Add type column to both dataframes
            chart_markers_df = chart_markers_df.copy()
            chart_markers_df["type"] = "marker"

            chart_lines_df = chart_lines_df.copy()
            chart_lines_df["type"] = "line"

            # Both markers and lines exist - combine them and sort by datetime
            combined_df = pd.concat([chart_markers_df, chart_lines_df], ignore_index=True)
            combined_df = combined_df.sort_values(by="datetime")
            combined_df.to_csv(csv_file, index=False)
        elif chart_markers_df is not None and not chart_markers_df.empty:
            # Only markers exist
            chart_markers_df = chart_markers_df.copy()
            chart_markers_df["type"] = "marker"
            chart_markers_df = chart_markers_df.sort_values(by="datetime")
            chart_markers_df.to_csv(csv_file, index=False)
        elif chart_lines_df is not None and not chart_lines_df.empty:
            # Only lines exist
            chart_lines_df = chart_lines_df.copy()
            chart_lines_df["type"] = "line"
            chart_lines_df = chart_lines_df.sort_values(by="datetime")
            chart_lines_df.to_csv(csv_file, index=False)


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

    logger.info("\nCreating trades plot and CSV...")

    # --- Start: CSV Generation for trades_df ---
    trades_csv_file = plot_file_html.replace(".html", ".csv")
    # Define standard columns for trades data
    standard_trade_columns = [
        "time", "side", "status", "filled_quantity", "symbol", "asset.asset_type",
        "asset.right", "asset.strike", "asset.expiration", "price", "type",
        "asset.multiplier", "trade_cost"
    ]

    if trades_df is None or trades_df.empty:
        logger.info(f"No trades provided. Empty trades CSV file will be created: {trades_csv_file}")
        # Create an empty DataFrame with standard headers for the CSV
        empty_trades_for_csv = pd.DataFrame(columns=standard_trade_columns)
        empty_trades_for_csv.to_csv(trades_csv_file, index=False)
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

    # Get the ratio of the strategy to the initial_budget
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
    buys = buys.loc[df_final["side"] == "buy"]

    def generate_buysell_plotly_text(row):
        if row["status"] != "canceled" and row["status"] != "new":
            try:
                if row["asset.asset_type"] == "option":
                    try:
                        filled_quantity = str(Decimal(row["filled_quantity"]).quantize(Decimal("0.01")).__format__(",f"))
                    except (InvalidOperation, TypeError, ValueError):
                        filled_quantity = str(row["filled_quantity"])
                        
                    try:
                        price = str(Decimal(row["price"]).quantize(Decimal("0.0001")).__format__(",f"))
                    except (InvalidOperation, TypeError, ValueError):
                        price = str(row["price"])
                        
                    try:
                        trade_cost = str(Decimal(row["trade_cost"]).quantize(Decimal("0.01")).__format__(",f"))
                    except (InvalidOperation, TypeError, ValueError):
                        trade_cost = str(row["trade_cost"])
                        
                    return f"Option: {row['symbol']}<br>Quantity: {filled_quantity}<br>Price: ${price}<br>Cost: ${trade_cost}"
                else:
                    try:
                        filled_quantity = str(Decimal(row["filled_quantity"]).quantize(Decimal("0.01")).__format__(",f"))
                    except (InvalidOperation, TypeError, ValueError):
                        filled_quantity = str(row["filled_quantity"])
                        
                    try:
                        price = str(Decimal(row["price"]).quantize(Decimal("0.01")).__format__(",f"))
                    except (InvalidOperation, TypeError, ValueError):
                        price = str(row["price"])
                        
                    try:
                        trade_cost = str(Decimal(row["trade_cost"]).quantize(Decimal("0.01")).__format__(",f"))
                    except (InvalidOperation, TypeError, ValueError):
                        trade_cost = str(row["trade_cost"])
                        
                    return f"Quantity: {filled_quantity}<br>Price: ${price}<br>Cost: ${trade_cost}"
            except Exception as e:
                return f"Error formatting trade: {str(e)}"
        return "Order not executed"

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
    sells = sells.loc[df_final["side"] == "sell"]

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

    # Create graph
    fig.write_html(plot_file_html, auto_open=show_plot)

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
):
    # If show tearsheet is False, then we don't want to open the tearsheet in the browser
    # IMS create the tearsheet even if we are not showinbg it
    if not save_tearsheet:
        logger.info("save_tearsheet is False, not creating the tearsheet file.")
        return

    logger.info("\nCreating tearsheet...")

    # Check if df1 or df2 are empty and return if they are
    if strategy_df is None or benchmark_df is None or strategy_df.empty or benchmark_df.empty:
        logger.error("No data to create tearsheet, skipping")
        return

    _strategy_df = strategy_df.copy()
    _benchmark_df = benchmark_df.copy()

    # Convert _strategy_df and _benchmark_df indexes to a date object instead of datetime
    _strategy_df.index = pd.to_datetime(_strategy_df.index)

    # Merge the strategy and benchmark dataframes on the index column
    df = pd.merge(_strategy_df, _benchmark_df, left_index=True, right_index=True, how="outer")

    df.index = pd.to_datetime(df.index)
    df["portfolio_value"] = df["portfolio_value"].ffill()

    # If the portfolio_value is NaN, backfill it because sometimes the benchmark starts before the strategy
    df["portfolio_value"] = df["portfolio_value"].bfill()

    df["symbol_cumprod"] = df["symbol_cumprod"].ffill()
    df.loc[df.index[0], "symbol_cumprod"] = 1

    # Use the configurable resample_rule parameter instead of hardcoded "D"
    logger.info(f"Resampling data using rule: {resample_rule}")
    
    # Check for HFT data (high frequency)
    is_hft = False
    if len(df) > 0:
        # Check if we have multiple data points per day
        dates = df.index.date
        unique_dates = set(dates)
        if len(df) / len(unique_dates) > 5:  # More than 5 data points per day on average
            is_hft = True
            logger.info("HFT data detected - using specialized processing")
    
    # For HFT data, we need to be more careful with resampling
    if is_hft:
        # First, ensure we preserve the original data for accurate statistics
        df = df.dropna()
        df_original = df.copy()
        
        # Resample for visualization purposes (can't resample already resampled data)
        # df = df.resample(resample_rule).last()
        
        # Calculate returns on the resampled data
        df["strategy"] = df["portfolio_value"].bfill().pct_change(fill_method=None).fillna(0)
        df["benchmark"] = df["symbol_cumprod"].bfill().pct_change(fill_method=None).fillna(0)
        
        # For statistics calculation, use the original data to preserve intraday movements
        df_stats = df_original.copy()
        df_stats["strategy"] = df_stats["portfolio_value"].bfill().pct_change(fill_method=None).fillna(0)
        df_stats["benchmark"] = df_stats["symbol_cumprod"].bfill().pct_change(fill_method=None).fillna(0)
    else:
        # Standard resampling for non-HFT data
        df = df.resample(resample_rule).last()
        df["strategy"] = df["portfolio_value"].bfill().pct_change(fill_method=None).fillna(0)
        df["benchmark"] = df["symbol_cumprod"].bfill().pct_change(fill_method=None).fillna(0)
        df_stats = df.copy()

    # Merge the strategy and benchmark columns into a new dataframe called df_final
    df_final = df.loc[:, ["strategy", "benchmark"]]

    # df_final = df.loc[:, ["strategy", "benchmark"]]
    df_final.index = pd.to_datetime(df_final.index)
    df_final.index = df_final.index.tz_localize(None)

    # For statistics, use df_stats
    df_stats_final = df_stats.loc[:, ["strategy", "benchmark"]]
    df_stats_final.index = pd.to_datetime(df_stats_final.index)
    df_stats_final.index = df_stats_final.index.tz_localize(None)

    # Check if df_final is empty and return if it is
    if df_final.empty or df_final["benchmark"].isnull().all() or df_final["strategy"].isnull().all():
        logger.warning("No data to create tearsheet, skipping")
        return

    # Check if df_stats_final is empty and use df_final as fallback if needed
    if df_stats_final.empty or df_stats_final["benchmark"].isnull().all() or df_stats_final["strategy"].isnull().all():
        logger.warning("No statistics data available, using visualization data for calculations")
        df_stats_final = df_final.copy()

    # Uncomment for debugging
    # _df1.to_csv(f"df1.csv")
    # _df2.to_csv(f"df2.csv")
    # df.to_csv(f"df.csv")
    # df_final.to_csv(f"df_final.csv")
    # df_stats_final.to_csv(f"df_stats_final.csv")  # Save statistics data for debugging

    bm_text = f"Compared to {benchmark_asset}" if benchmark_asset else ""
    title = f"{strat_name} {bm_text}"
    
    '''
    # Check if all the values are equal to 0
    if df_final["benchmark"].sum() == 0:
        logger.error("Not enough data to create a tearsheet, at least 2 days of data are required. Skipping")
        return

    # Check if all the values are equal to 0
    if df_final["strategy"].sum() == 0:
        logger.error("Not enough data to create a tearsheet, at least 2 days of data are required. Skipping")
        return
    '''
    # Set the name of the benchmark column so that quantstats can use it in the report
    df_final["benchmark"].name = str(benchmark_asset)
    df_stats_final["benchmark"].name = str(benchmark_asset)

    # Add HFT-specific metrics to parameters if this is HFT data
    if is_hft:
        if strategy_parameters is None:
            strategy_parameters = {}
        
        # Calculate intraday metrics
        try:
            # Calculate average trades per day
            trades_per_day = len(df_stats_final) / len(set(df_stats_final.index.date))
            strategy_parameters["Avg Trades Per Day"] = f"{trades_per_day:.2f}"
            
            # Calculate intraday volatility
            intraday_vol = df_stats_final["strategy"].std() * np.sqrt(trades_per_day)
            strategy_parameters["Intraday Volatility"] = f"{intraday_vol:.4f}"
            
            # Add resampling information
            strategy_parameters["Data Sampling"] = f"HFT ({resample_rule} resampling)"
        except Exception as e:
            logger.warning(f"Could not calculate HFT metrics: {e}")

    # Run quantstats reports surpressing any logs because it can be noisy for no reason
    with open(os.devnull, "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        result = qs.reports.html(
            df_stats_final["strategy"],  # Use the statistics dataframe for calculations
            df_stats_final["benchmark"],  # Use the statistics dataframe for calculations
            title=title,
            output=tearsheet_file,
            download_filename=tearsheet_file,  # Consider if you need a different name for clarity
            rf=risk_free_rate,
            parameters=strategy_parameters,
        )
    
    # Post-process tearsheet for responsive parameters
    _enhance_tearsheet_parameters(tearsheet_file)

    # Inject benchmark/strategy summary metrics into the summary block
    _inject_benchmark_into_summary_metrics(tearsheet_file, df_stats_final, risk_free_rate)

    if show_tearsheet:
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
