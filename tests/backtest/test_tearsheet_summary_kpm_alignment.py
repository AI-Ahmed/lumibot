"""
Validate that Summary metrics block matches Key Performance Metrics table in tearsheet.

Uses same preparation as QuantStats html(): prepare_returns, prepare_benchmark,
match_dates, then metrics(prepare_returns=False). Asserts Summary values equal KPM values.
"""
import os
import re
import tempfile

import pandas as pd
import pytest

from lumibot.tools.indicators import create_tearsheet


def _parse_summary_from_html(html: str) -> dict:
    """Extract summary block values (benchmark/strategy format)."""
    summary = {}
    # Annual Return (h1)
    m = re.search(r'metric-title">Annual Return.*?<h1>([^<]+)</h1>', html, re.DOTALL)
    if m:
        summary["annual_return"] = m.group(1).strip()
    # metric-sub h2s: Total Return, Max Drawdown, RoMaD, Longest DD Days, Sharpe, Sortino, PSR
    patterns = [
        ("total_return", r'metric-title">Total Return.*?<h2>([^<]+)</h2>'),
        ("max_drawdown", r'metric-title">Max Drawdown.*?<h2>([^<]+)</h2>'),
        ("romad", r'metric-title">RoMaD.*?<h2>([^<]+)</h2>'),
        ("longest_dd", r'metric-title">Longest DD Days.*?<h2>([^<]+)</h2>'),
        ("sharpe", r'metric-title">Sharpe.*?<h2>([^<]+)</h2>'),
        ("sortino", r'metric-title">Sortino.*?<h2>([^<]+)</h2>'),
        ("psr", r'metric-title">PSR.*?<h2>([^<]+)</h2>'),
    ]
    for key, pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            summary[key] = m.group(1).strip()
    return summary


def _parse_kpm_from_html(html: str) -> dict:
    """Extract Key Performance Metrics table values. Table has Metric | SPY | Strategy."""
    kpm = {}
    # Find the KPM table - rows like <tr><td>Sharpe</td><td>1.72</td><td>0.51</td></tr>
    rows = re.findall(
        r"<tr><td>([^<]+)</td><td>([^<]*)</td><td>([^<]*)</td></tr>",
        html,
    )
    want = {"Sharpe", "RoMaD", "Longest DD Days", "Sortino", "CAGR% (Annual Return)",
            "Total Return", "Max Drawdown", "Prob. Sharpe Ratio"}
    for metric, col1, col2 in rows:
        m = metric.strip()
        if m in want:
            kpm[m] = (col1.strip(), col2.strip())
    return kpm


def _values_close(a: str, b: str, tol: float = 0.02) -> bool:
    """Compare two string values numerically if possible."""
    try:
        va = float(a.replace("%", "").replace(",", "").strip())
        vb = float(b.replace("%", "").replace(",", "").strip())
        return abs(va - vb) <= tol
    except ValueError:
        return a.strip() == b.strip()


def _summary_val_to_pair(summary_val: str) -> tuple:
    """Convert 'bm/str' format to (bm, str)."""
    parts = summary_val.split("/")
    if len(parts) == 2:
        return (parts[0].strip(), parts[1].strip())
    return (summary_val, "")


def test_tearsheet_summary_matches_kpm():
    """Summary block values should match Key Performance Metrics table."""
    np = __import__("numpy")
    np.random.seed(123)
    n = 252
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    strategy_ret = pd.Series(np.random.randn(n) * 0.01, index=dates)
    benchmark_ret = pd.Series(np.random.randn(n) * 0.008, index=dates)
    # create_tearsheet expects portfolio_value and symbol_cumprod (price series)
    strategy_df = pd.DataFrame(
        {"portfolio_value": (1 + strategy_ret).cumprod()}, index=dates
    )
    benchmark_df = pd.DataFrame(
        {"symbol_cumprod": (1 + benchmark_ret).cumprod()}, index=dates
    )

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        tearsheet_file = f.name
    try:
        create_tearsheet(
            strategy_df=strategy_df,
            strat_name="TestStrategy",
            tearsheet_file=tearsheet_file,
            benchmark_df=benchmark_df,
            benchmark_asset="SPY",
            show_tearsheet=False,
            save_tearsheet=True,
            risk_free_rate=0.025,
        )
        with open(tearsheet_file, "r", encoding="utf-8") as f:
            html = f.read()
    finally:
        try:
            os.unlink(tearsheet_file)
        except OSError:
            pass

    summary = _parse_summary_from_html(html)
    kpm = _parse_kpm_from_html(html)

    assert "sharpe" in summary, "Sharpe should be in summary"
    assert "Sharpe" in kpm, "Sharpe should be in KPM"

    # Map summary keys to KPM keys
    checks = [
        ("sharpe", "Sharpe"),
        ("sortino", "Sortino"),
        ("romad", "RoMaD"),
        ("total_return", "Total Return"),
        ("max_drawdown", "Max Drawdown"),
        ("longest_dd", "Longest DD Days"),
        ("psr", "Prob. Sharpe Ratio"),
        ("annual_return", "CAGR% (Annual Return)"),
    ]
    for sum_key, kpm_key in checks:
        if sum_key not in summary or kpm_key not in kpm:
            continue
        bm_s, st_s = _summary_val_to_pair(summary[sum_key])
        bm_k, st_k = kpm[kpm_key]
        assert _values_close(bm_s, bm_k), (
            f"{kpm_key} benchmark: summary={bm_s} vs KPM={bm_k}"
        )
        assert _values_close(st_s, st_k), (
            f"{kpm_key} strategy: summary={st_s} vs KPM={st_k}"
        )
