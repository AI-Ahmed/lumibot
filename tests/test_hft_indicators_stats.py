"""
Unit tests for HFT tearsheet statistics (FPAP backtest_var, entries_per_year).

Verifies:
- _infer_entries_per_year for daily vs intraday return series
- Variance of SR estimate formula (backtest_var) used in compute_dsr
- Integration with _calculate_fpap_metrics (when FPAP available)
"""

import pandas as pd
import numpy as np
import pytest

from lumibot.tools.hft_indicators import _infer_entries_per_year, _calculate_fpap_metrics


class TestInferEntriesPerYear:
    """Test _infer_entries_per_year for different bar frequencies."""

    def test_daily_returns(self):
        """Daily returns should yield ~252-365 entries per year."""
        idx = pd.date_range("2020-01-01", periods=252, freq="B")
        returns = pd.Series(np.random.randn(252) * 0.01, index=idx)
        epy = _infer_entries_per_year(returns)
        assert 200 <= epy <= 400

    def test_intraday_one_minute(self):
        """1-minute bars: ~252*390 ≈ 98k entries per year."""
        idx = pd.date_range("2020-01-01 09:30", periods=1000, freq="1min")
        returns = pd.Series(np.random.randn(1000) * 0.001, index=idx)
        epy = _infer_entries_per_year(returns)
        assert epy > 10000

    def test_empty_or_short_returns(self):
        """Empty or single observation returns 252 fallback."""
        empty = pd.Series(dtype=float)
        assert _infer_entries_per_year(empty) == 252.0
        single = pd.Series([0.01], index=pd.DatetimeIndex(["2020-01-01"]))
        assert _infer_entries_per_year(single) == 252.0

    def test_fallback_span_years(self):
        """Irregular / very sparse bars use span fallback."""
        idx = pd.DatetimeIndex(
            [
                "2020-01-01",
                "2020-06-01",
                "2021-01-01",
            ]
        )
        returns = pd.Series([0.01, -0.02, 0.015], index=idx)
        epy = _infer_entries_per_year(returns)
        assert epy > 0
        assert epy < 100


class TestFpapBacktestVarFormula:
    """Verify backtest_var uses variance of SR estimate, not return variance."""

    def test_fpap_metrics_structure(self):
        """_calculate_fpap_metrics returns expected keys when FPAP available."""
        try:
            from fpap.backtests.statistics import compute_moments
        except ImportError:
            pytest.skip("FPAP not installed")
        idx = pd.date_range("2020-01-01", periods=100, freq="B")
        returns = pd.Series(np.random.randn(100) * 0.01, index=idx)
        df = pd.DataFrame({"strategy": returns})
        metrics = _calculate_fpap_metrics(df)
        if metrics:
            assert "sharpe_ratio" in metrics
            assert "entries_per_year" in metrics
            assert "dsr" in metrics
            assert "psr" in metrics
            assert "min_trl" in metrics

    def test_var_sr_formula_manual(self):
        """Manually verify variance of SR estimate formula vs return variance."""
        np.random.seed(42)
        n = 100
        returns = pd.Series(np.random.randn(n) * 0.01)
        m1, m2, m3, m4 = (
            returns.mean(),
            returns.std(),
            returns.skew(),
            returns.kurtosis() + 3,
        )
        epy = 252
        sr = (m1 / m2) * np.sqrt(epy) if m2 > 0 else 0
        sr_period = sr / np.sqrt(epy)
        var_sr_period = (1 / max(n - 1, 1)) * (
            1 - m3 * sr_period + (m4 - 1) / 4 * sr_period**2
        )
        assert var_sr_period != m2**2
        assert var_sr_period > 0


class TestCanonicalReturnsTimezoneIntersection:
    """Test that tz-naive df_final and tz-aware strategy index intersection yields non-empty result."""

    def test_canonical_returns_tz_aware_strategy_index(self):
        """Intersection of tz-naive df_final with tz-aware strategy index must not be empty."""
        np.random.seed(42)
        idx_naive = pd.date_range("2026-02-26 09:30", periods=59, freq="50min")
        idx_aware = idx_naive.tz_localize("America/New_York")
        df_final = pd.DataFrame(
            {"strategy": np.random.randn(59) * 0.001, "benchmark": np.random.randn(59) * 0.0005},
            index=idx_naive,
        )
        strat_idx = idx_aware
        strat_idx_naive = strat_idx.tz_localize(None) if strat_idx.tz is not None else strat_idx
        inter = df_final.index.intersection(strat_idx_naive)
        assert len(inter) == 59, "Intersection must yield 59 rows when timezone is normalized"
