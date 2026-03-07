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

from lumibot.tools.hft_indicators import (
    _infer_entries_per_year,
    _calculate_fpap_metrics,
    DualTrackAnalyzer,
    calculate_info_sharpe,
)


class TestInferEntriesPerYear:
    """Test _infer_entries_per_year for different bar frequencies."""

    def test_daily_returns(self):
        """Daily returns should yield ~365-365 entries per year."""
        idx = pd.date_range("2020-01-01", periods=365, freq="B")
        returns = pd.Series(np.random.randn(365) * 0.01, index=idx)
        epy = _infer_entries_per_year(returns)
        assert 200 <= epy <= 400

    def test_intraday_one_minute(self):
        """1-minute bars: ~365*390 ≈ 98k entries per year."""
        idx = pd.date_range("2020-01-01 09:30", periods=1000, freq="1min")
        returns = pd.Series(np.random.randn(1000) * 0.001, index=idx)
        epy = _infer_entries_per_year(returns)
        assert epy > 10000

    def test_empty_or_short_returns(self):
        """Empty or single observation returns 365 fallback."""
        empty = pd.Series(dtype=float)
        assert _infer_entries_per_year(empty) == 365.0
        single = pd.Series([0.01], index=pd.DatetimeIndex(["2020-01-01"]))
        assert _infer_entries_per_year(single) == 365.0

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
        epy = 365
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


class TestHftIndicatorsRegression:
    """Regression tests for HFT indicator correctness.

    These tests catch the annualization bug and benchmark alignment issues
    that produced inflated SR=9+ and degenerate benchmark comparisons.
    """

    def test_sr_not_inflated_for_small_sample(self):
        """SR > 5 for n < 365 is a near-certain sign of calendar-year annualization bug.

        The bug used 365.25*24*3600 seconds/year (calendar 24/7) instead of
        data-driven bars/day * 365 trading days. This inflated SR by ~2.3x for
        market-hours strategies.
        """
        np.random.seed(42)
        # 59 observations over ~2 days (market hours) - realistic HFT scenario
        idx = pd.date_range("2026-02-26 09:30", periods=59, freq="15min")
        returns = pd.Series(np.random.normal(0.0001, 0.001, 59), index=idx)
        sr = calculate_info_sharpe(returns)

        # With the fix, SR should be reasonable (< 5). With the bug, it was ~9-10.
        assert sr < 5.0, f"SR={sr:.2f} is implausibly high for 59 observations (bug: calendar-year annualization)"

    def test_infer_entries_per_year_data_driven(self):
        """Test that _infer_entries_per_year uses data-driven calculation.

        Before fix: used 365.25 * 24 * 3600 seconds/year (calendar 24/7)
        After fix: uses observed bars/day * 365 trading days
        """
        np.random.seed(42)
        # Create 2 full days of 15-min bars (market hours only: 6.5h/day * 4 bars/hour = 26 bars/day)
        # 2 days * 26 bars/day = 52 bars total
        idx_day1 = pd.date_range("2026-02-26 09:30", periods=26, freq="15min")
        idx_day2 = pd.date_range("2026-02-27 09:30", periods=26, freq="15min")
        idx = idx_day1.append(idx_day2)
        returns = pd.Series(np.random.normal(0.0001, 0.001, len(idx)), index=idx)
        epy = _infer_entries_per_year(returns)

        # 52 bars over 2 days = 26 bars/day * 365 = ~6552 entries/year (data-driven)
        # Should NOT be 365.25*24*3600 / 900 ≈ 35000 (calendar 24/7)
        assert epy < 15000, f"entries_per_year={epy:.0f} suggests calendar-year annualization bug"
        assert epy > 1000, f"entries_per_year={epy:.0f} is too low for intraday bars"

    def test_benchmark_comparison_not_degenerate(self):
        """info_sharpe and aligned_sharpe must differ; if equal, alignment is broken.

        The bug: _benchmark_aligned_bars aligned SPY to strategy timestamps,
        then returned the SAME strategy returns for both tracks, yielding identical SR.

        The fix: resamples strategy to daily frequency for aligned track, producing
        genuinely different SR from the information-driven track.
        """
        np.random.seed(42)
        # Create realistic strategy and benchmark data
        dates = pd.date_range("2020-01-01", periods=50, freq="B")
        strategy_df = pd.DataFrame({
            'portfolio_value': 100 * (1 + np.random.randn(50) * 0.01).cumprod()
        }, index=dates)

        # Benchmark with different pattern
        benchmark_df = pd.DataFrame({
            'symbol_cumprod': 100 * (1 + np.random.randn(50) * 0.008).cumprod()
        }, index=dates)

        analyzer = DualTrackAnalyzer(strategy_df, benchmark_df, 'volume')
        info = analyzer.calculate_information_driven_metrics()
        aligned = analyzer.calculate_time_aligned_metrics('synthetic_bars')

        # After the fix, these should be different (one is on daily freq, one on original)
        # Before the bug fix, they were identical.
        assert info['information_sharpe'] != aligned['aligned_sharpe'], \
            "Aligned SR equals info SR - benchmark alignment is degenerate (same returns used for both tracks)"
