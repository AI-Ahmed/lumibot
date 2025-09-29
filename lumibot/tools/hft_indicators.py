import contextlib
import math
import numpy as np
import os
import webbrowser

import pendulum
import pandas as pd
import plotly.graph_objects as go
import quantstats_lumi as qs
from plotly.subplots import make_subplots


from ..constants import LUMIBOT_DEFAULT_TIMEZONE
from lumibot.tools import to_datetime_aware
from plotly.subplots import make_subplots

from .yahoo_helper import YahooHelper as yh

from lumibot.tools.lumibot_logger import get_logger
logger = get_logger(__name__)


from .indicators import (
    total_return, cagr, max_drawdown, romad, volatility, sharpe, stats_summary,
    calculate_returns, performance, get_symbol_returns, plot_indicators, plot_returns
)


# =================== 🔥 INSTITUTIONAL HFT SOLUTION 🔥 ===================

class DualTrackAnalyzer:
    """
    🏛️ INSTITUTIONAL DUAL-TRACK ANALYZER
    Comprehensive analyzer supporting both information-driven and time-aligned metrics.
    """
    
    def __init__(self, strategy_data, benchmark_data, bar_type='volume'):
        self.strategy_data = strategy_data
        self.benchmark_data = benchmark_data
        self.bar_type = bar_type
        self.info_metrics = None
        self.aligned_metrics = None
        logger.info(f"🏛️ Initialized Institutional Analyzer for {bar_type.upper()} bars")
    
    def detect_bar_type(self):
        """Auto-detect information-driven bar type from strategy data."""
        timestamps = self.strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        # Calculate coefficient of variation for time intervals
        cv = time_diffs.std() / time_diffs.mean() if time_diffs.mean() > 0 else 0
        
        # Detect patterns
        if cv > 1.0:
            return "imbalance"  # Highly irregular = imbalance bars
        elif cv > 0.5:
            return "volume"     # Moderately irregular = volume bars
        elif cv > 0.3:
            return "dollar"     # Slightly irregular = dollar bars
        else:
            return "time"       # Regular = time bars
    
    def calculate_information_driven_metrics(self):
        """Calculate metrics preserving original bar structure with benchmark comparison."""
        returns = self.strategy_data['portfolio_value'].pct_change().fillna(0)
        
        # Calculate information-driven benchmark metrics using original bar timing
        info_benchmark_data = self._get_information_driven_benchmark()
        
        self.info_metrics = {
            'total_bars': len(self.strategy_data),
            'average_bar_duration': self._calculate_avg_bar_duration(),
            'information_sharpe': returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0,
            'bar_type': self.bar_type,
            'microstructure_efficiency': self._calculate_microstructure_efficiency(),
            'information_density': len(self.strategy_data) / self._calculate_time_span_days(),
            'bar_efficiency_score': self._calculate_bar_efficiency_score(),
            'temporal_clustering': self._calculate_temporal_clustering(),
            # Add information-driven benchmark metrics
            'info_beta': self._calculate_beta(returns, info_benchmark_data['benchmark_returns']),
            'info_alpha': self._calculate_alpha(returns, info_benchmark_data['benchmark_returns']),
            'info_information_ratio': self._calculate_information_ratio(returns, info_benchmark_data['benchmark_returns']),
            'info_tracking_error': (returns - info_benchmark_data['benchmark_returns']).std() * np.sqrt(252),
        }
        
        logger.info(f"📊 Information-driven metrics calculated: Sharpe={self.info_metrics['information_sharpe']:.4f}")
        return self.info_metrics
    
    def calculate_time_aligned_metrics(self, alignment_method='synthetic_bars'):
        """Calculate benchmark comparison metrics using time alignment."""
        aligned_data = self._align_data(alignment_method)
        
        strategy_returns = aligned_data['strategy_returns']
        benchmark_returns = aligned_data['benchmark_returns']
        
        self.aligned_metrics = {
            'aligned_sharpe': self._calculate_sharpe(strategy_returns),
            'beta': self._calculate_beta(strategy_returns, benchmark_returns),
            'alpha': self._calculate_alpha(strategy_returns, benchmark_returns),
            'tracking_error': (strategy_returns - benchmark_returns).std() * np.sqrt(252),
            'information_ratio': self._calculate_information_ratio(strategy_returns, benchmark_returns),
            'alignment_method': alignment_method,
        }
        
        logger.info(f"⚖️ Time-aligned metrics calculated: Sharpe={self.aligned_metrics['aligned_sharpe']:.4f}, Beta={self.aligned_metrics['beta']:.4f}")
        return self.aligned_metrics
    
    def _align_data(self, method):
        """Align strategy and benchmark data using specified method."""
        if method == 'forward_fill':
            return self._forward_fill_alignment()
        elif method == 'period_end':
            return self._period_end_alignment()
        elif method == 'synthetic_bars':
            return self._synthetic_benchmark_bars()
        else:
            logger.warning(f"Unknown alignment method: {method}, using synthetic_bars")
            return self._synthetic_benchmark_bars()
    
    def _get_information_driven_benchmark(self):
        """Get benchmark data using ORIGINAL information-driven bar timing (no synthetic alignment)."""
        logger.debug("🔬 Creating pure information-driven benchmark (original timing)...")
        
        # For true information-driven analysis, we use the benchmark data AS-IS
        # at the original strategy bar timestamps without synthetic interpolation
        strategy_times = self.strategy_data.index
        strategy_returns = self.strategy_data['portfolio_value'].pct_change().fillna(0)
        
        # Get benchmark returns at the closest available times (no interpolation)
        # This preserves the information structure of the bars
        if 'symbol_cumprod' in self.benchmark_data.columns:
            # Use forward-fill only (no interpolation) to maintain bar structure integrity
            benchmark_reindexed = self.benchmark_data['symbol_cumprod'].reindex(
                strategy_times, method='ffill'
            ).bfill()
            benchmark_returns = benchmark_reindexed.pct_change().fillna(0)
        else:
            # Handle other benchmark structures
            numeric_cols = self.benchmark_data.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                benchmark_col = numeric_cols[0]
                benchmark_reindexed = self.benchmark_data[benchmark_col].reindex(
                    strategy_times, method='ffill'
                ).bfill()
                benchmark_returns = benchmark_reindexed.pct_change().fillna(0)
            else:
                logger.error("No numeric columns found in benchmark data")
                benchmark_returns = pd.Series(0, index=strategy_times)
        
        logger.debug(f"📊 Info-driven benchmark: mean={benchmark_returns.mean():.6f}, std={benchmark_returns.std():.6f}")
        
        return {
            'strategy_returns': strategy_returns,
            'benchmark_returns': benchmark_returns
        }
    
    def _synthetic_benchmark_bars(self):
        """🎯 MOST SOPHISTICATED: Create synthetic benchmark bars matching strategy timing."""
        logger.info("🔬 Creating synthetic benchmark bars using strategy timing...")
        
        # Get strategy timestamps
        strategy_times = self.strategy_data.index
        
        # Use proper interpolation for benchmark data at strategy timestamps
        # First, ensure benchmark data is properly sorted by time
        benchmark_sorted = self.benchmark_data.sort_index()
        
        # Use interpolation to get benchmark values at exact strategy timestamps
        # This creates realistic benchmark returns that align with strategy timing
        if 'symbol_cumprod' in benchmark_sorted.columns:
            # Interpolate benchmark values at strategy times
            benchmark_interpolated = benchmark_sorted['symbol_cumprod'].reindex(
                strategy_times, method='nearest', tolerance=pd.Timedelta('1h')
            ).ffill().bfill()
            
            # Calculate benchmark returns using interpolated values
            benchmark_returns = benchmark_interpolated.pct_change().fillna(0)
        else:
            # Handle other benchmark data structures
            numeric_cols = benchmark_sorted.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                benchmark_col = numeric_cols[0]
                benchmark_interpolated = benchmark_sorted[benchmark_col].reindex(
                    strategy_times, method='nearest', tolerance=pd.Timedelta('1h')
                ).ffill().bfill()
                benchmark_returns = benchmark_interpolated.pct_change().fillna(0)
            else:
                logger.error("No numeric columns found in benchmark data")
                benchmark_returns = pd.Series(0, index=strategy_times)
        
        # Calculate strategy returns
        strategy_returns = self.strategy_data['portfolio_value'].pct_change().fillna(0)
        
        logger.debug(f"📊 Synthetic bars created: {len(strategy_returns)} strategy points, {len(benchmark_returns)} benchmark points")
        logger.debug(f"📊 Benchmark return stats: mean={benchmark_returns.mean():.6f}, std={benchmark_returns.std():.6f}")
        
        return {
            'strategy_returns': strategy_returns,
            'benchmark_returns': benchmark_returns
        }
    
    def _forward_fill_alignment(self):
        """Forward-fill strategy values to benchmark timestamps."""
        logger.info("📈 Using forward-fill alignment...")
        
        merged = pd.merge(self.strategy_data, self.benchmark_data, 
                         left_index=True, right_index=True, how='outer')
        merged['portfolio_value'] = merged['portfolio_value'].ffill()
        
        if 'symbol_cumprod' in merged.columns:
            merged['benchmark_value'] = merged['symbol_cumprod'].ffill()
        else:
            numeric_cols = merged.select_dtypes(include=[np.number]).columns
            benchmark_col = [col for col in numeric_cols if 'portfolio_value' not in col][0]
            merged['benchmark_value'] = merged[benchmark_col].ffill()
        
        return {
            'strategy_returns': merged['portfolio_value'].pct_change().fillna(0),
            'benchmark_returns': merged['benchmark_value'].pct_change().fillna(0)
        }
    
    def _period_end_alignment(self):
        """Align both strategy and benchmark to common period endings."""
        logger.info("🕒 Using period-end alignment...")
        
        # Determine benchmark frequency
        benchmark_freq = pd.infer_freq(self.benchmark_data.index) or 'D'
        
        # Resample both to common frequency
        strategy_resampled = self.strategy_data.resample(benchmark_freq).last()
        benchmark_resampled = self.benchmark_data.resample(benchmark_freq).last()
        
        strategy_returns = strategy_resampled['portfolio_value'].pct_change().fillna(0)
        
        if 'symbol_cumprod' in benchmark_resampled.columns:
            benchmark_returns = benchmark_resampled['symbol_cumprod'].pct_change().fillna(0)
        else:
            numeric_cols = benchmark_resampled.select_dtypes(include=[np.number]).columns
            benchmark_returns = benchmark_resampled[numeric_cols[0]].pct_change().fillna(0)
        
        return {
            'strategy_returns': strategy_returns,
            'benchmark_returns': benchmark_returns
        }
    
    def _calculate_avg_bar_duration(self):
        """Calculate average duration between bars using high-precision timing."""
        timestamps = self.strategy_data.index
        
        # Use pendulum for higher precision
        if len(timestamps) < 2:
            return 0
        time_diffs = []
        for i in range(1, len(timestamps)):
            duration = hft_duration_seconds(timestamps[i-1], timestamps[i])
            time_diffs.append(duration)
        return sum(time_diffs) / len(time_diffs) if time_diffs else 0
    
    def _calculate_microstructure_efficiency(self):
        """Calculate microstructure efficiency score."""
        returns = self.strategy_data['portfolio_value'].pct_change().fillna(0)
        return returns.std() / np.sqrt(len(returns)) if len(returns) > 0 else 0
    
    def _calculate_time_span_days(self):
        """Calculate total time span in days using high-precision timing."""
        if len(self.strategy_data) < 2:
            return 1
            
        start_time = self.strategy_data.index[0]
        end_time = self.strategy_data.index[-1]
        total_seconds = hft_duration_seconds(start_time, end_time)
        return total_seconds / (24 * 60 * 60)  # Convert to days
    
    def _calculate_bar_efficiency_score(self):
        """Calculate how efficiently bars capture information."""
        timestamps = self.strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        if len(time_diffs) == 0 or time_diffs.mean() == 0:
            return 0
            
        # Lower coefficient of variation = higher efficiency
        cv = time_diffs.std() / time_diffs.mean()
        return 1 / (1 + cv)  # Normalize to 0-1 range
    
    def _calculate_temporal_clustering(self):
        """Calculate temporal clustering coefficient."""
        timestamps = self.strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        if len(time_diffs) < 3:
            return 0
            
        # Calculate autocorrelation of time differences
        return time_diffs.autocorr(lag=1) if not time_diffs.isna().all() else 0
    
    def _calculate_sharpe(self, returns):
        """Calculate annualized Sharpe ratio."""
        if returns.std() == 0:
            return 0
        return returns.mean() / returns.std() * np.sqrt(252)
    
    def _calculate_beta(self, strategy_returns, benchmark_returns):
        """Calculate beta coefficient with improved numerical stability."""
        try:
            # Remove any NaN or infinite values
            strategy_clean = strategy_returns.dropna()
            benchmark_clean = benchmark_returns.dropna()
            
            # Align the series
            aligned_data = pd.DataFrame({'strategy': strategy_clean, 'benchmark': benchmark_clean}).dropna()
            
            if len(aligned_data) < 10:  # Need minimum observations
                return 0.0
                
            strategy_aligned = aligned_data['strategy']
            benchmark_aligned = aligned_data['benchmark']
            
            # Calculate covariance and variance
            covariance = np.cov(strategy_aligned, benchmark_aligned)[0][1]
            benchmark_variance = np.var(benchmark_aligned)
            
            if benchmark_variance == 0 or np.isnan(benchmark_variance) or np.isinf(benchmark_variance):
                return 0.0
                
            beta = covariance / benchmark_variance
            
            # Cap extreme beta values
            return np.clip(beta, -5.0, 5.0)
            
        except Exception as e:
            logger.warning(f"Error calculating beta: {e}")
            return 0.0
    
    def _calculate_alpha(self, strategy_returns, benchmark_returns):
        """Calculate alpha coefficient with improved handling."""
        try:
            beta = self._calculate_beta(strategy_returns, benchmark_returns)
            strategy_mean = strategy_returns.mean()
            benchmark_mean = benchmark_returns.mean()
            
            if np.isnan(strategy_mean) or np.isnan(benchmark_mean) or np.isnan(beta):
                return 0.0
                
            alpha = strategy_mean - beta * benchmark_mean
            return alpha if not np.isnan(alpha) else 0.0
            
        except Exception as e:
            logger.warning(f"Error calculating alpha: {e}")
            return 0.0
    
    def _calculate_information_ratio(self, strategy_returns, benchmark_returns):
        """Calculate information ratio vs benchmark with improved stability."""
        try:
            # Calculate excess returns
            excess_returns = strategy_returns - benchmark_returns
            excess_clean = excess_returns.dropna()
            
            if len(excess_clean) < 10:  # Need minimum observations
                return 0.0
                
            excess_mean = excess_clean.mean()
            tracking_error = excess_clean.std()
            
            if tracking_error == 0 or np.isnan(tracking_error) or np.isinf(tracking_error):
                return 0.0
                
            ir = excess_mean / tracking_error
            
            # Cap extreme values
            return np.clip(ir, -10.0, 10.0) if not np.isnan(ir) else 0.0
            
        except Exception as e:
            logger.warning(f"Error calculating information ratio: {e}")
            return 0.0


def create_institutional_hft_tearsheet(
    strategy_df: pd.DataFrame,
    strat_name: str,
    tearsheet_file: str,
    benchmark_df: pd.DataFrame,
    benchmark_asset,
    show_tearsheet: bool,
    save_tearsheet: bool,
    risk_free_rate: float,
    strategy_parameters: dict = None,
    bar_type: str = "volume",
    benchmark_alignment: str = "synthetic_bars",
):
    """
    🏛️ PROFESSIONAL INSTITUTIONAL TEARSHEET FOR HFT STRATEGIES
    
    Preserves information structure of alternative bars while providing proper benchmark comparison.
    """
    
    if not save_tearsheet:
        logger.info("save_tearsheet is False, not creating the tearsheet file.")
        return
    
    logger.info(f"\n🏛️ Creating INSTITUTIONAL HFT Tearsheet for {bar_type.upper()} bars...")
    
    # Check if data exists
    if strategy_df is None or benchmark_df is None or strategy_df.empty or benchmark_df.empty:
        logger.error("No data to create tearsheet, skipping")
        return
    
    # Initialize analyzer with correct bar type
    analyzer = DualTrackAnalyzer(strategy_df, benchmark_df, bar_type)
    # Override with the correct bar type if different
    analyzer.bar_type = bar_type
    
    # Auto-detect bar type if not specified
    if bar_type == "auto":
        bar_type = analyzer.detect_bar_type()
        analyzer.bar_type = bar_type
        logger.info(f"🔍 Auto-detected bar type: {bar_type.upper()}")
    
    # =================== SECTION 1: INFORMATION-DRIVEN ANALYSIS ===================
    logger.info(f"🔬 Analyzing strategy using original {bar_type} bar structure...")
    info_metrics = analyzer.calculate_information_driven_metrics()
    
    # =================== SECTION 2: TIME-ALIGNED BENCHMARK COMPARISON ===================
    logger.info(f"⚖️ Creating time-aligned analysis for benchmark comparison...")
    aligned_metrics = analyzer.calculate_time_aligned_metrics(benchmark_alignment)
    
    # =================== SECTION 3: ENHANCED PARAMETER REPORTING ===================
    if strategy_parameters is None:
        strategy_parameters = {}
    
    # Add institutional-grade parameters
    strategy_parameters.update({
        "🏛️ ANALYSIS TYPE": "Institutional Dual-Track",
        "📊 BAR TYPE": bar_type.upper(),
        "🔄 BENCHMARK ALIGNMENT": benchmark_alignment.replace("_", " ").title(),
        "📈 INFORMATION-DRIVEN SHARPE": f"{info_metrics['information_sharpe']:.4f}",
        "📉 TIME-ALIGNED SHARPE": f"{aligned_metrics['aligned_sharpe']:.4f}",
        "🎯 BETA VS BENCHMARK": f"{aligned_metrics['beta']:.4f}",
        "⚡ INFORMATION BARS COUNT": f"{info_metrics['total_bars']:,}",
        "🔬 BAR EFFICIENCY SCORE": f"{info_metrics['bar_efficiency_score']:.4f}",
        "📊 INFORMATION DENSITY": f"{info_metrics['information_density']:.2f} bars/day",
        "🕒 AVG BAR DURATION": f"{info_metrics['average_bar_duration']:.1f}s",
        "🎯 INSTITUTIONAL GRADE": "✅ Professional Standard",
    })
    
    # =================== SECTION 4: PREPARE DATA FOR QUANTSTATS ===================
    # Get aligned data for tearsheet generation
    aligned_data = analyzer._align_data(benchmark_alignment)
    
    # Create final dataframe for QuantStats
    df_final = pd.DataFrame({
        'strategy': aligned_data['strategy_returns'],
        'benchmark': aligned_data['benchmark_returns']
    })
    
    df_final.index = df_final.index.tz_localize(None) if df_final.index.tz is not None else df_final.index
    df_final["strategy"].name = strat_name
    df_final["benchmark"].name = str(benchmark_asset)
    
    # =================== SECTION 5: GENERATE TEARSHEET ===================
    title = f"🏛️ INSTITUTIONAL HFT: {strat_name} ({bar_type.upper()} bars) vs {benchmark_asset}"
    
    with open(os.devnull, "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
        result = qs.reports.html(
            df_final["strategy"],
            df_final["benchmark"],
            title=title,
            output=tearsheet_file,
            download_filename=tearsheet_file,
            rf=risk_free_rate,
            parameters=strategy_parameters,
        )
    
    # =================== SECTION 6: CREATE SUPPLEMENTARY REPORTS ===================
    info_driven_file = tearsheet_file.replace('.html', '_information_structure.html')
    create_information_structure_report(info_metrics, aligned_metrics, info_driven_file, bar_type, strat_name)
    
    if show_tearsheet:
        url = "file://" + os.path.abspath(str(tearsheet_file))
        webbrowser.open(url)
        
        # Also open the information-driven report
        info_url = "file://" + os.path.abspath(str(info_driven_file))
        webbrowser.open(info_url)
    
    logger.info(f"✅ Generated institutional dual tearsheet system:")
    logger.info(f"   📈 Time-aligned (benchmark comparison): {tearsheet_file}")
    logger.info(f"   🔬 Information-driven ({bar_type} bars): {info_driven_file}")
    
    # Return result consistent with original indicators.py behavior
    # QuantStats returns DataFrame with to_csv method, just like the original
    return result


def create_information_structure_report(info_metrics, aligned_metrics, output_file, bar_type, strategy_name):
    """Create a separate report focused on information-driven analysis."""
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🔬 Information Structure Analysis: {strategy_name}</title>
        <meta charset="utf-8">
        <style>
            body {{ 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                margin: 40px; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: #333;
            }}
            .container {{
                background: white;
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                max-width: 1200px;
                margin: 0 auto;
            }}
            .header {{ 
                color: #2E86AB; 
                font-size: 32px; 
                font-weight: bold; 
                text-align: center;
                margin-bottom: 30px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
            }}
            .section {{
                background: #f8f9fa; 
                padding: 25px; 
                border-radius: 10px; 
                margin: 25px 0;
                border-left: 5px solid #2E86AB;
            }}
            .section-title {{
                font-size: 24px;
                font-weight: bold;
                color: #2E86AB;
                margin-bottom: 20px;
                display: flex;
                align-items: center;
            }}
            .metric-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 20px;
                margin: 20px 0;
            }}
            .metric-card {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                border-top: 4px solid #28a745;
            }}
            .metric-label {{
                font-weight: bold;
                color: #495057;
                font-size: 14px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 8px;
            }}
            .metric-value {{
                font-size: 28px;
                font-weight: bold;
                color: #28a745;
            }}
            .comparison-table {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                background: white;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .comparison-table th {{
                background: #2E86AB;
                color: white;
                padding: 15px;
                text-align: left;
                font-weight: bold;
            }}
            .comparison-table td {{
                padding: 12px 15px;
                border-bottom: 1px solid #e9ecef;
            }}
            .comparison-table tr:hover {{
                background: #f8f9fa;
            }}
            .highlight {{
                color: #28a745;
                font-weight: bold;
                font-size: 18px;
            }}
            .footer {{
                text-align: center;
                margin-top: 40px;
                color: #6c757d;
                font-style: italic;
            }}
            .emoji {{
                font-size: 1.2em;
                margin-right: 8px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                🔬 Information Structure Analysis<br>
                <div style="font-size: 24px; color: #6c757d; margin-top: 10px;">
                    {strategy_name} • {bar_type.upper()} Bars
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">
                    <span class="emoji">📊</span>Information-Driven Metrics
                </div>
                <div class="metric-grid">
                    <div class="metric-card">
                        <div class="metric-label">Information Sharpe Ratio</div>
                        <div class="metric-value">{info_metrics['information_sharpe']:.4f}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Total Information Bars</div>
                        <div class="metric-value">{info_metrics['total_bars']:,}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Bar Efficiency Score</div>
                        <div class="metric-value">{info_metrics['bar_efficiency_score']:.4f}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Information Density</div>
                        <div class="metric-value">{info_metrics['information_density']:.2f}/day</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Average Bar Duration</div>
                        <div class="metric-value">{info_metrics['average_bar_duration']:.1f}s</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Temporal Clustering</div>
                        <div class="metric-value">{info_metrics['temporal_clustering']:.4f}</div>
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">
                    <span class="emoji">⚖️</span>Dual-Track Comparison
                </div>
                <table class="comparison-table">
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th>Information-Driven</th>
                            <th>Time-Aligned</th>
                            <th>Difference</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>Sharpe Ratio</strong></td>
                            <td>{info_metrics['information_sharpe']:.4f}</td>
                            <td>{aligned_metrics['aligned_sharpe']:.4f}</td>
                            <td class="highlight">{info_metrics['information_sharpe'] - aligned_metrics['aligned_sharpe']:+.4f}</td>
                        </tr>
                        <tr>
                            <td><strong>Analysis Method</strong></td>
                            <td>Original {bar_type.upper()} Structure</td>
                            <td>{aligned_metrics['alignment_method'].replace('_', ' ').title()}</td>
                            <td>-</td>
                        </tr>
                        <tr>
                            <td><strong>Beta vs Benchmark</strong></td>
                            <td>{info_metrics['info_beta']:.4f} (Info-Driven)</td>
                            <td>{aligned_metrics['beta']:.4f}</td>
                            <td class="highlight">{info_metrics['info_beta'] - aligned_metrics['beta']:+.4f}</td>
                        </tr>
                        <tr>
                            <td><strong>Alpha vs Benchmark</strong></td>
                            <td>{info_metrics['info_alpha']:.6f} (Info-Driven)</td>
                            <td>{aligned_metrics['alpha']:.6f}</td>
                            <td class="highlight">{info_metrics['info_alpha'] - aligned_metrics['alpha']:+.6f}</td>
                        </tr>
                        <tr>
                            <td><strong>Information Ratio</strong></td>
                            <td>{info_metrics['info_information_ratio']:.4f} (Info-Driven)</td>
                            <td>{aligned_metrics['information_ratio']:.4f}</td>
                            <td class="highlight">{info_metrics['info_information_ratio'] - aligned_metrics['information_ratio']:+.4f}</td>
                        </tr>
                        <tr>
                            <td><strong>Tracking Error</strong></td>
                            <td>{info_metrics['info_tracking_error']:.4f} (Info-Driven)</td>
                            <td>{aligned_metrics['tracking_error']:.4f}</td>
                            <td class="highlight">{info_metrics['info_tracking_error'] - aligned_metrics['tracking_error']:+.4f}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            
            <div class="section">
                <div class="section-title">
                    <span class="emoji">🎯</span>Bar Characteristics Analysis
                </div>
                <p style="font-size: 16px; line-height: 1.6; color: #495057;">
                    <strong>{bar_type.upper()} bars</strong> capture information through non-uniform sampling based on {bar_type} thresholds 
                    rather than fixed time intervals. This approach provides several advantages:
                </p>
                <ul style="font-size: 16px; line-height: 1.8; color: #495057;">
                    <li><strong>Information Density:</strong> Higher concentration of trading signals during active periods</li>
                    <li><strong>Noise Reduction:</strong> Filters out low-activity periods that add little information</li>
                    <li><strong>Market Microstructure:</strong> Better captures true market dynamics and liquidity patterns</li>
                    <li><strong>Adaptive Sampling:</strong> Automatically adjusts to market volatility and activity levels</li>
                </ul>
                
                <div style="background: #e7f3ff; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #007bff;">
                    <h4 style="color: #007bff; margin: 0 0 10px 0;">💡 Key Insight</h4>
                    <p style="margin: 0; font-size: 16px; color: #495057;">
                        The Bar Efficiency Score of <strong>{info_metrics['bar_efficiency_score']:.4f}</strong> indicates 
                        {'high' if info_metrics['bar_efficiency_score'] > 0.7 else 'moderate' if info_metrics['bar_efficiency_score'] > 0.4 else 'low'} 
                        efficiency in capturing information through {bar_type} sampling. 
                        {'This suggests the strategy effectively utilizes information-driven timing.' if info_metrics['bar_efficiency_score'] > 0.5 else 'Consider optimizing bar thresholds for better information capture.'}
                    </p>
                </div>
            </div>
            
            <div class="footer">
                🏛️ Generated by Institutional HFT Analysis Framework<br>
                Professional-grade performance evaluation for information-driven strategies
            </div>
        </div>
    </body>
    </html>
    """
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    logger.info(f"📄 Information structure report created: {output_file}")


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
    """
    🔥 ENHANCED TEARSHEET WITH INSTITUTIONAL HFT SUPPORT
    
    Auto-detects HFT strategies and applies appropriate analysis method.
    Fully integrated with lumibot's data processing framework.
    """
    # If show tearsheet is False, then we don't want to open the tearsheet in the browser
    if not save_tearsheet:
        logger.info("save_tearsheet is False, not creating the tearsheet file.")
        return

    logger.info("\n🔥 Creating Enhanced Tearsheet with HFT Support...")

    # Check if df1 or df2 are empty and return if they are
    if strategy_df is None or benchmark_df is None or strategy_df.empty or benchmark_df.empty:
        logger.error("No data to create tearsheet, skipping")
        return

    # =================== USE LUMIBOT'S EXACT DATA PROCESSING ===================
    # Import and use the exact same data processing as indicators.py
    from .indicators import create_tearsheet as original_create_tearsheet
    
    # =================== AUTO-DETECT HFT CHARACTERISTICS ===================
    is_hft = detect_hft_characteristics(strategy_df)
    
    if is_hft:
        logger.info("🏛️ HFT STRATEGY DETECTED - Using Institutional Analysis")
        
        # For HFT, we still use lumibot's data processing but add HFT analysis
        _strategy_df = strategy_df.copy()
        _benchmark_df = benchmark_df.copy()

        # Convert indexes to datetime (same as indicators.py)
        _strategy_df.index = pd.to_datetime(_strategy_df.index)

        # Merge the strategy and benchmark dataframes (same as indicators.py)
        df = pd.merge(_strategy_df, _benchmark_df, left_index=True, right_index=True, how="outer")

        df.index = pd.to_datetime(df.index)
        df["portfolio_value"] = df["portfolio_value"].ffill()
        df["portfolio_value"] = df["portfolio_value"].bfill()

        df["symbol_cumprod"] = df["symbol_cumprod"].ffill()
        df.loc[df.index[0], "symbol_cumprod"] = 1

        # For HFT, preserve high-frequency data (no daily resampling)
        logger.info(f"Preserving HFT frequency data (no resampling)")
        df["strategy"] = df["portfolio_value"].bfill().pct_change(fill_method=None).fillna(0)
        df["benchmark"] = df["symbol_cumprod"].bfill().pct_change(fill_method=None).fillna(0)

        # Create final dataframe (same structure as indicators.py)
        df_final = df.loc[:, ["strategy", "benchmark"]]
        df_final.index = pd.to_datetime(df_final.index)
        df_final.index = df_final.index.tz_localize(None)

        # Check if df_final is empty
        if df_final.empty or df_final["benchmark"].isnull().all() or df_final["strategy"].isnull().all():
            logger.warning("No data to create tearsheet, skipping")
            return

        # Set names for quantstats (same as indicators.py)
        df_final["benchmark"].name = str(benchmark_asset)
        df_final["strategy"].name = strat_name

        # Auto-detect bar type
        analyzer = DualTrackAnalyzer(strategy_df, benchmark_df)
        detected_bar_type = analyzer.detect_bar_type()
        
        # Enhanced parameters with HFT metrics
        if strategy_parameters is None:
            strategy_parameters = {}
        
        # Add HFT-specific metrics (similar to indicators.py HFT section)
        try:
            trades_per_day = len(df_final) / len(set(df_final.index.date))
            intraday_vol = df_final["strategy"].std() * np.sqrt(trades_per_day)
            
            strategy_parameters.update({
                "📊 Analysis Type": f"🏛️ Institutional HFT ({detected_bar_type.upper()} bars)",
                "📈 Data Points": f"{len(df_final):,}",
                "⚡ Avg Observations/Day": f"{trades_per_day:.2f}",
                "📊 Intraday Volatility": f"{intraday_vol:.4f}",
                "🔬 Bar Type": detected_bar_type.upper(),
            })
        except Exception as e:
            logger.warning(f"Could not calculate HFT metrics: {e}")

        title = f"🏛️ INSTITUTIONAL HFT: {strat_name} ({detected_bar_type.upper()} bars) vs {benchmark_asset}"

        # Generate primary tearsheet using QuantStats (same as indicators.py)
        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
            result = qs.reports.html(
                df_final["strategy"],
                df_final["benchmark"],
                title=title,
                output=tearsheet_file,
                download_filename=tearsheet_file,
                rf=risk_free_rate,
                parameters=strategy_parameters,
            )

        # Generate supplementary HFT analysis report
        info_driven_file = tearsheet_file.replace('.html', '_information_structure.html')
        
        # Calculate dual-track metrics for supplementary report
        info_metrics = analyzer.calculate_information_driven_metrics()
        aligned_metrics = analyzer.calculate_time_aligned_metrics('synthetic_bars')
        create_information_structure_report(info_metrics, aligned_metrics, info_driven_file, detected_bar_type, strat_name)

        if show_tearsheet:
            url = "file://" + os.path.abspath(str(tearsheet_file))
            webbrowser.open(url)
            
            # Also open the information-driven report
            info_url = "file://" + os.path.abspath(str(info_driven_file))
            webbrowser.open(info_url)

        logger.info(f"✅ Generated institutional dual tearsheet system:")
        logger.info(f"   📈 Primary tearsheet: {tearsheet_file}")
        logger.info(f"   🔬 Information analysis: {info_driven_file}")

        # Return result consistent with original indicators.py behavior
        return result
    
    # =================== DELEGATE TO ORIGINAL FOR NON-HFT ===================
    logger.info("📊 Standard strategy detected - using original lumibot analysis")
    
    # For non-HFT strategies, use the original indicators.py function exactly
    return original_create_tearsheet(
        strategy_df=strategy_df,
        strat_name=strat_name,
        tearsheet_file=tearsheet_file,
        benchmark_df=benchmark_df,
        benchmark_asset=benchmark_asset,
        show_tearsheet=show_tearsheet,
        save_tearsheet=save_tearsheet,
        risk_free_rate=risk_free_rate,
        strategy_parameters=strategy_parameters,
        resample_rule=resample_rule,
    )


def detect_hft_characteristics(df):
    """
    🔍 AUTO-DETECT HFT CHARACTERISTICS
    
    Auto-detect if strategy uses HFT/information-driven bars.
    
    Parameters
    ----------
    df : pd.DataFrame
        Strategy data to analyze
        
    Returns
    -------
    bool
        True if HFT characteristics detected, False otherwise
    """
    try:
        # Check for high frequency (more than 5 observations per day on average)
        if len(df) < 2:
            return False
            
        dates = df.index.date
        unique_dates = set(dates)
        obs_per_day = len(df) / len(unique_dates) if len(unique_dates) > 0 else 0
        
        # Check for irregular timing patterns
        time_diffs = df.index.to_series().diff().dt.total_seconds().dropna()
        cv = time_diffs.std() / time_diffs.mean() if len(time_diffs) > 0 and time_diffs.mean() > 0 else 0
        
        # HFT criteria: High frequency + irregular timing
        is_hft = obs_per_day > 5 and cv > 0.5
        
        logger.info(f"🔍 HFT Detection: obs_per_day={obs_per_day:.1f}, cv={cv:.3f}, is_hft={is_hft}")
        return is_hft
        
    except Exception as e:
        logger.warning(f"Error in HFT detection: {e}")
        return False


def calculate_hft_specific_metrics(strategy_data, bar_type):
    """
    📊 CALCULATE HFT-SPECIFIC METRICS
    
    Calculate metrics specific to information-driven bars.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with returns
    bar_type : str
        Type of information bar
        
    Returns
    -------
    dict
        Dictionary containing HFT-specific metrics
    """
    try:
        returns = strategy_data["strategy_returns"]
        
        # Basic metrics
        total_bars = len(strategy_data)
        time_span = (strategy_data.index[-1] - strategy_data.index[0]).total_seconds()
        avg_bar_duration_minutes = (time_span / total_bars / 60) if total_bars > 0 else 0
        
        # HFT-specific calculations
        hft_sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
        
        # Bar efficiency calculation
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        bar_efficiency = 1 / (1 + time_diffs.std() / time_diffs.mean()) if len(time_diffs) > 0 and time_diffs.mean() > 0 else 0
        
        metrics = {
            'total_bars': total_bars,
            'hft_sharpe': hft_sharpe,
            'bar_efficiency': bar_efficiency,
            'avg_bar_duration_minutes': avg_bar_duration_minutes,
            'bar_type': bar_type,
        }
        
        logger.info(f"📊 HFT metrics calculated: Sharpe={hft_sharpe:.4f}, Efficiency={bar_efficiency:.3f}")
        return metrics
        
    except Exception as e:
        logger.error(f"Error calculating HFT metrics: {e}")
        return {
            'total_bars': 0,
            'hft_sharpe': 0,
            'bar_efficiency': 0,
            'avg_bar_duration_minutes': 0,
            'bar_type': bar_type,
        }


def calculate_benchmark_comparison_metrics(aligned_data):
    """
    ⚖️ CALCULATE BENCHMARK COMPARISON METRICS
    
    Calculate metrics for time-aligned benchmark comparison.
    
    Parameters
    ----------
    aligned_data : dict
        Dictionary with 'strategy_returns' and 'benchmark_returns'
        
    Returns
    -------
    dict
        Dictionary containing benchmark comparison metrics
    """
    try:
        strategy_returns = aligned_data['strategy_returns']
        benchmark_returns = aligned_data['benchmark_returns']
        
        # Sharpe ratio
        aligned_sharpe = strategy_returns.mean() / strategy_returns.std() * np.sqrt(252) if strategy_returns.std() > 0 else 0
        
        # Beta calculation
        covariance = np.cov(strategy_returns, benchmark_returns)[0][1] if len(strategy_returns) > 1 else 0
        benchmark_variance = np.var(benchmark_returns) if len(benchmark_returns) > 1 else 1
        beta = covariance / benchmark_variance if benchmark_variance != 0 else 0
        
        # Alpha calculation
        alpha = strategy_returns.mean() - beta * benchmark_returns.mean()
        
        # Information ratio
        excess_returns = strategy_returns - benchmark_returns
        tracking_error = excess_returns.std() if len(excess_returns) > 1 else 0
        information_ratio_vs_benchmark = excess_returns.mean() / tracking_error if tracking_error != 0 else 0
        
        metrics = {
            'aligned_sharpe': aligned_sharpe,
            'beta': beta,
            'alpha': alpha,
            'information_ratio_vs_benchmark': information_ratio_vs_benchmark,
            'tracking_error': tracking_error * np.sqrt(252),
        }
        
        logger.info(f"⚖️ Benchmark metrics calculated: Sharpe={aligned_sharpe:.4f}, Beta={beta:.4f}")
        return metrics
        
    except Exception as e:
        logger.error(f"Error calculating benchmark metrics: {e}")
        return {
            'aligned_sharpe': 0,
            'beta': 0,
            'alpha': 0,
            'information_ratio_vs_benchmark': 0,
            'tracking_error': 0,
        }


def create_information_driven_report(strategy_data, output_file, bar_type, metrics):
    """
    📄 CREATE INFORMATION-DRIVEN REPORT
    
    Create a detailed HTML report focused on information-driven analysis.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data
    output_file : str
        Output file path
    bar_type : str
        Type of information bar
    metrics : dict
        HFT-specific metrics
    """
    try:
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>🔬 Information-Driven Analysis: {bar_type.upper()} Bars</title>
            <meta charset="utf-8">
            <style>
                body {{ 
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                    margin: 40px; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: #333;
                }}
                .container {{
                    background: white;
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                    max-width: 1200px;
                    margin: 0 auto;
                }}
                .header {{ 
                    color: #2E86AB; 
                    font-size: 32px; 
                    font-weight: bold; 
                    text-align: center;
                    margin-bottom: 30px;
                }}
                .metric-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                    gap: 20px;
                    margin: 20px 0;
                }}
                .metric-card {{
                    background: #f8f9fa;
                    padding: 20px;
                    border-radius: 8px;
                    border-left: 4px solid #28a745;
                }}
                .metric-label {{
                    font-weight: bold;
                    color: #495057;
                    font-size: 14px;
                    margin-bottom: 8px;
                }}
                .metric-value {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #28a745;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    🔬 Information-Driven Analysis<br>
                    <div style="font-size: 20px; color: #6c757d;">{bar_type.upper()} Bars</div>
                </div>
                
                <div class="metric-grid">
                    <div class="metric-card">
                        <div class="metric-label">HFT Sharpe Ratio</div>
                        <div class="metric-value">{metrics.get('hft_sharpe', 0):.4f}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Total Information Bars</div>
                        <div class="metric-value">{metrics.get('total_bars', 0):,}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Bar Efficiency</div>
                        <div class="metric-value">{metrics.get('bar_efficiency', 0):.3f}</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-label">Avg Bar Duration</div>
                        <div class="metric-value">{metrics.get('avg_bar_duration_minutes', 0):.2f} min</div>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 40px; color: #6c757d;">
                    🏛️ Generated by Institutional HFT Analysis Framework
                </div>
            </div>
        </body>
        </html>
        """
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        logger.info(f"📄 Information-driven report created: {output_file}")
        
    except Exception as e:
        logger.error(f"Error creating information-driven report: {e}")


def analyze_bar_characteristics(strategy_data, bar_type):
    """
    🔬 ANALYZE BAR CHARACTERISTICS
    
    Analyze the characteristics of information-driven bars.
    Provides insights into bar efficiency and information content.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with timestamps
    bar_type : str
        Type of information bar ('volume', 'dollar', 'imbalance', 'runs')
        
    Returns
    -------
    dict
        Dictionary containing bar characteristics analysis
    """
    if bar_type == 'volume':
        return analyze_volume_bars(strategy_data)
    elif bar_type == 'dollar':
        return analyze_dollar_bars(strategy_data)
    elif bar_type == 'imbalance':
        return analyze_imbalance_bars(strategy_data)
    elif bar_type == 'runs':
        return analyze_runs_bars(strategy_data)
    else:
        return analyze_generic_bars(strategy_data)


def analyze_volume_bars(strategy_data):
    """
    📊 ANALYZE VOLUME BARS
    
    Analyze volume bar characteristics for HFT strategies.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with volume information
        
    Returns
    -------
    dict
        Volume bar analysis results
    """
    try:
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        return {
            'avg_bar_duration_seconds': time_diffs.mean(),
            'bar_duration_std': time_diffs.std(),
            'min_bar_duration': time_diffs.min(),
            'max_bar_duration': time_diffs.max(),
            'bar_efficiency_score': calculate_bar_efficiency_score(time_diffs),
            'temporal_clustering': calculate_temporal_clustering(timestamps),
            'volume_consistency': calculate_volume_consistency(strategy_data),
        }
    except Exception as e:
        logger.error(f"Error analyzing volume bars: {e}")
        return {}


def analyze_dollar_bars(strategy_data):
    """
    💰 ANALYZE DOLLAR BARS
    
    Analyze dollar bar characteristics for HFT strategies.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with dollar volume information
        
    Returns
    -------
    dict
        Dollar bar analysis results
    """
    try:
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        return {
            'avg_bar_duration_seconds': time_diffs.mean(),
            'bar_duration_std': time_diffs.std(),
            'min_bar_duration': time_diffs.min(),
            'max_bar_duration': time_diffs.max(),
            'bar_efficiency_score': calculate_bar_efficiency_score(time_diffs),
            'temporal_clustering': calculate_temporal_clustering(timestamps),
            'dollar_volume_stability': calculate_dollar_volume_stability(strategy_data),
        }
    except Exception as e:
        logger.error(f"Error analyzing dollar bars: {e}")
        return {}


def analyze_imbalance_bars(strategy_data):
    """
    ⚖️ ANALYZE IMBALANCE BARS
    
    Analyze imbalance bar characteristics for HFT strategies.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with imbalance information
        
    Returns
    -------
    dict
        Imbalance bar analysis results
    """
    try:
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        return {
            'avg_bar_duration_seconds': time_diffs.mean(),
            'bar_duration_std': time_diffs.std(),
            'min_bar_duration': time_diffs.min(),
            'max_bar_duration': time_diffs.max(),
            'bar_efficiency_score': calculate_bar_efficiency_score(time_diffs),
            'temporal_clustering': calculate_temporal_clustering(timestamps),
            'imbalance_detection_rate': calculate_imbalance_detection_rate(strategy_data),
        }
    except Exception as e:
        logger.error(f"Error analyzing imbalance bars: {e}")
        return {}


def analyze_runs_bars(strategy_data):
    """
    🏃 ANALYZE RUNS BARS
    
    Analyze runs bar characteristics for HFT strategies.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with runs information
        
    Returns
    -------
    dict
        Runs bar analysis results
    """
    try:
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        return {
            'avg_bar_duration_seconds': time_diffs.mean(),
            'bar_duration_std': time_diffs.std(),
            'min_bar_duration': time_diffs.min(),
            'max_bar_duration': time_diffs.max(),
            'bar_efficiency_score': calculate_bar_efficiency_score(time_diffs),
            'temporal_clustering': calculate_temporal_clustering(timestamps),
            'run_length_distribution': calculate_run_length_distribution(strategy_data),
        }
    except Exception as e:
        logger.error(f"Error analyzing runs bars: {e}")
        return {}


def analyze_generic_bars(strategy_data):
    """
    🔧 ANALYZE GENERIC BARS
    
    Analyze generic bar characteristics when type is unknown.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data
        
    Returns
    -------
    dict
        Generic bar analysis results
    """
    try:
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        return {
            'avg_bar_duration_seconds': time_diffs.mean(),
            'bar_duration_std': time_diffs.std(),
            'min_bar_duration': time_diffs.min(),
            'max_bar_duration': time_diffs.max(),
            'bar_efficiency_score': calculate_bar_efficiency_score(time_diffs),
            'temporal_clustering': calculate_temporal_clustering(timestamps),
        }
    except Exception as e:
        logger.error(f"Error analyzing generic bars: {e}")
        return {}


def calculate_bar_efficiency_score(time_diffs):
    """
    ⚡ CALCULATE BAR EFFICIENCY SCORE
    
    Calculate how efficiently bars capture information.
    Lower variance in duration = higher efficiency for information capture.
    
    Parameters
    ----------
    time_diffs : pd.Series
        Time differences between bars in seconds
        
    Returns
    -------
    float
        Bar efficiency score (0-1 range)
    """
    if len(time_diffs) == 0 or time_diffs.mean() == 0:
        return 0
    coefficient_of_variation = time_diffs.std() / time_diffs.mean()
    return 1 / (1 + coefficient_of_variation)  # Normalize to 0-1 range


def calculate_temporal_clustering(timestamps):
    """
    🕒 CALCULATE TEMPORAL CLUSTERING
    
    Calculate temporal clustering coefficient for bar timing.
    
    Parameters
    ----------
    timestamps : pd.DatetimeIndex
        Timestamps of bars
        
    Returns
    -------
    float
        Temporal clustering coefficient
    """
    try:
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        if len(time_diffs) < 3:
            return 0
            
        # Calculate autocorrelation of time differences
        return time_diffs.autocorr(lag=1) if not time_diffs.isna().all() else 0
    except Exception as e:
        logger.warning(f"Error calculating temporal clustering: {e}")
        return 0


def calculate_volume_consistency(strategy_data):
    """
    📊 CALCULATE VOLUME CONSISTENCY
    
    Calculate consistency of volume across bars.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data
        
    Returns
    -------
    float
        Volume consistency score
    """
    try:
        if 'volume' in strategy_data.columns:
            volume = strategy_data['volume']
            return 1 - (volume.std() / volume.mean()) if volume.mean() > 0 else 0
        return 0.5  # Default for missing volume data
    except Exception as e:
        logger.warning(f"Error calculating volume consistency: {e}")
        return 0


def calculate_dollar_volume_stability(strategy_data):
    """
    💰 CALCULATE DOLLAR VOLUME STABILITY
    
    Calculate stability of dollar volume across bars.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data
        
    Returns
    -------
    float
        Dollar volume stability score
    """
    try:
        if 'dollar_volume' in strategy_data.columns:
            dollar_vol = strategy_data['dollar_volume']
            return 1 - (dollar_vol.std() / dollar_vol.mean()) if dollar_vol.mean() > 0 else 0
        return 0.5  # Default for missing dollar volume data
    except Exception as e:
        logger.warning(f"Error calculating dollar volume stability: {e}")
        return 0


def calculate_imbalance_detection_rate(strategy_data):
    """
    ⚖️ CALCULATE IMBALANCE DETECTION RATE
    
    Calculate rate of imbalance detection across bars.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data
        
    Returns
    -------
    float
        Imbalance detection rate
    """
    try:
        if 'imbalance' in strategy_data.columns:
            imbalance = strategy_data['imbalance']
            return (imbalance.abs() > imbalance.abs().median()).mean()
        return 0.5  # Default for missing imbalance data
    except Exception as e:
        logger.warning(f"Error calculating imbalance detection rate: {e}")
        return 0


def calculate_run_length_distribution(strategy_data):
    """
    🏃 CALCULATE RUN LENGTH DISTRIBUTION
    
    Calculate distribution of run lengths.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data
        
    Returns
    -------
    dict
        Run length distribution statistics
    """
    try:
        if 'run_length' in strategy_data.columns:
            run_lengths = strategy_data['run_length']
            return {
                'mean_run_length': run_lengths.mean(),
                'std_run_length': run_lengths.std(),
                'max_run_length': run_lengths.max(),
            }
        return {'mean_run_length': 1, 'std_run_length': 0, 'max_run_length': 1}
    except Exception as e:
        logger.warning(f"Error calculating run length distribution: {e}")
        return {'mean_run_length': 1, 'std_run_length': 0, 'max_run_length': 1}


# =================== 🔥 PENDULUM TIME UTILITIES FOR HFT 🔥 ===================

def get_hft_timezone(timezone_str=None):
    """
    🕐 GET HFT TIMEZONE
    
    Get timezone object using pendulum for better HFT accuracy.
    
    Parameters
    ----------
    timezone_str : str, optional
        Timezone string, defaults to LUMIBOT_DEFAULT_TIMEZONE
        
    Returns
    -------
    timezone object
        Pendulum timezone if available, else pytz timezone
    """
    if timezone_str is None:
        timezone_str = LUMIBOT_DEFAULT_TIMEZONE

    return pendulum.timezone(timezone_str)


def hft_now(timezone_str=None):
    """
    ⏰ HFT NOW
    
    Get current time with high precision for HFT applications.
    
    Parameters
    ----------
    timezone_str : str, optional
        Timezone string
        
    Returns
    -------
    datetime
        Current time with high precision
    """
    if timezone_str:
        return pendulum.now(timezone_str)
    return pendulum.now()


def hft_duration_seconds(start_time, end_time):
    """
    ⏱️ HFT DURATION SECONDS
    
    Calculate precise duration between timestamps for HFT analysis.
    
    Parameters
    ----------
    start_time : datetime
        Start timestamp
    end_time : datetime
        End timestamp
        
    Returns
    -------
    float
        Duration in seconds with high precision
    """
    if hasattr(start_time, 'timestamp') and hasattr(end_time, 'timestamp'):
        return end_time.timestamp() - start_time.timestamp()
    else:
        # Convert to pendulum if not already
        start_p = pendulum.instance(start_time) if not isinstance(start_time, pendulum.DateTime) else start_time
        end_p = pendulum.instance(end_time) if not isinstance(end_time, pendulum.DateTime) else end_time
        return (end_p - start_p).total_seconds()


def convert_to_hft_timezone(timestamps, target_timezone=None):
    """
    🌐 CONVERT TO HFT TIMEZONE
    
    Convert timestamps to target timezone with high precision.
    
    Parameters
    ----------
    timestamps : pd.DatetimeIndex or list
        Timestamps to convert
    target_timezone : str, optional
        Target timezone string
        
    Returns
    -------
    pd.DatetimeIndex
        Converted timestamps
    """
    if target_timezone is None:
        target_timezone = LUMIBOT_DEFAULT_TIMEZONE
        
    try:
        tz = pendulum.timezone(target_timezone)
        if isinstance(timestamps, pd.DatetimeIndex):
            return timestamps.tz_convert(tz)
        else:
            return pd.to_datetime(timestamps).tz_convert(tz)
    except Exception as e:
        logger.warning(f"Pendulum conversion error: {e}, using pandas default")
        return pd.to_datetime(timestamps).tz_convert(target_timezone)


def calculate_hft_time_metrics(timestamps):
    """
    📊 CALCULATE HFT TIME METRICS
    
    Calculate high-precision time metrics for HFT analysis.
    
    Parameters
    ----------
    timestamps : pd.DatetimeIndex
        Timestamps to analyze
        
    Returns
    -------
    dict
        Dictionary containing time metrics with high precision
    """
    try:
        if len(timestamps) < 2:
            return {
                'avg_interval_microseconds': 0,
                'std_interval_microseconds': 0,
                'min_interval_microseconds': 0,
                'max_interval_microseconds': 0,
                'total_duration_seconds': 0,
                'frequency_hz': 0,
            }
        
        # Calculate time differences with microsecond precision
        # Convert to pendulum for higher precision
        time_diffs = []
        for i in range(1, len(timestamps)):
            start_p = pendulum.instance(timestamps[i-1])
            end_p = pendulum.instance(timestamps[i])
            diff_microseconds = (end_p - start_p).total_seconds() * 1_000_000
            time_diffs.append(diff_microseconds)
        
        time_diffs = pd.Series(time_diffs)
        
        total_duration = hft_duration_seconds(timestamps[0], timestamps[-1])
        frequency_hz = len(timestamps) / total_duration if total_duration > 0 else 0
        
        return {
            'avg_interval_microseconds': time_diffs.mean(),
            'std_interval_microseconds': time_diffs.std(),
            'min_interval_microseconds': time_diffs.min(),
            'max_interval_microseconds': time_diffs.max(),
            'total_duration_seconds': total_duration,
            'frequency_hz': frequency_hz,
        }
        
    except Exception as e:
        logger.error(f"Error calculating HFT time metrics: {e}")
        return {
            'avg_interval_microseconds': 0,
            'std_interval_microseconds': 0,
            'min_interval_microseconds': 0,
            'max_interval_microseconds': 0,
            'total_duration_seconds': 0,
            'frequency_hz': 0,
        }


def generate_institutional_tearsheet(analyzer, output_file, show_tearsheet=True):
    """
    🏛️ GENERATE INSTITUTIONAL TEARSHEET
    
    Generate comprehensive institutional-grade tearsheet with dual analysis.
    This is the main function referenced in the HFT_UPDATES.md report.
    
    Parameters
    ----------
    analyzer : DualTrackAnalyzer
        Initialized analyzer with strategy and benchmark data
    output_file : str
        Output file path for the tearsheet
    show_tearsheet : bool, optional
        Whether to open the tearsheet in browser, by default True
        
    Returns
    -------
    dict
        Dictionary containing analysis results and file paths
    """
    try:
        # Calculate both metric sets
        info_metrics = analyzer.calculate_information_driven_metrics()
        aligned_metrics = analyzer.calculate_time_aligned_metrics()
        
        # Prepare parameters for tearsheet
        parameters = {
            '📊 Analysis Type': 'Dual-Track Institutional',
            '🔬 Information-Driven Sharpe': f"{info_metrics['information_sharpe']:.4f}",
            '📈 Time-Aligned Sharpe': f"{aligned_metrics['aligned_sharpe']:.4f}",
            '🎯 Beta vs Benchmark': f"{aligned_metrics['beta']:.4f}",
            '⚡ Information Bars': f"{info_metrics['total_bars']:,}",
            '📊 Bar Type': info_metrics['bar_type'].upper(),
            '🔄 Alignment Method': 'Forward Fill + Synthetic',
        }
        
        # Generate primary tearsheet using time-aligned data
        aligned_data = analyzer._align_data('synthetic_bars')
        
        # Create final dataframe for QuantStats
        df_final = pd.DataFrame({
            'strategy': aligned_data['strategy_returns'],
            'benchmark': aligned_data['benchmark_returns']
        })
        
        df_final.index = df_final.index.tz_localize(None) if df_final.index.tz is not None else df_final.index
        df_final["strategy"].name = "HFT Strategy"
        df_final["benchmark"].name = "Benchmark"
        
        with open(os.devnull, "w") as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
            qs.reports.html(
                df_final["strategy"],
                df_final["benchmark"],
                title=f"Institutional HFT Analysis: {analyzer.bar_type.upper()} Bars",
                output=output_file,
                rf=0.02,  # Risk-free rate
                parameters=parameters
            )
        
        # Generate supplementary information-driven report
        info_file = output_file.replace('.html', '_information_structure.html')
        create_information_structure_report(info_metrics, aligned_metrics, info_file, analyzer.bar_type, "HFT Strategy")
        
        if show_tearsheet:
            import webbrowser
            url = "file://" + os.path.abspath(str(output_file))
            webbrowser.open(url)
            
            info_url = "file://" + os.path.abspath(str(info_file))
            webbrowser.open(info_url)
            
        return {
            'primary_tearsheet': output_file,
            'information_report': info_file,
            'metrics_summary': {**info_metrics, **aligned_metrics}
        }
        
    except Exception as e:
        logger.error(f"Error generating institutional tearsheet: {e}")
        return {}


def run_institutional_analysis(strategy_df, benchmark_df, bar_type='volume'):
    """
    🏛️ RUN INSTITUTIONAL ANALYSIS
    
    Complete workflow for institutional HFT analysis as specified in HFT_UPDATES.md.
    
    Parameters
    ----------
    strategy_df : pd.DataFrame
        Strategy data with portfolio values
    benchmark_df : pd.DataFrame
        Benchmark data for comparison
    bar_type : str, optional
        Type of information bars, by default 'volume'
        
    Returns
    -------
    dict
        Complete analysis results
    """
    logger.info("🏛️ Starting Institutional HFT Analysis...")
    
    try:
        # Step 1: Initialize analyzer
        analyzer = DualTrackAnalyzer(strategy_df, benchmark_df, bar_type)
        
        # Step 2: Analyze bar characteristics
        bar_characteristics = analyze_bar_characteristics(strategy_df, bar_type)
        logger.info(f"📊 Bar Analysis Complete: {bar_characteristics.get('bar_efficiency_score', 0):.3f} efficiency")
        
        # Step 3: Calculate dual metrics
        info_metrics = analyzer.calculate_information_driven_metrics()
        aligned_metrics = analyzer.calculate_time_aligned_metrics('synthetic_bars')
        
        # Step 4: Generate reports
        results = generate_institutional_tearsheet(analyzer, 'institutional_analysis.html')
        
        # Step 5: Summary output
        logger.info("\n📈 Analysis Summary:")
        logger.info(f"   Information-Driven Sharpe: {info_metrics['information_sharpe']:.4f}")
        logger.info(f"   Time-Aligned Sharpe: {aligned_metrics['aligned_sharpe']:.4f}")
        logger.info(f"   Beta vs Benchmark: {aligned_metrics['beta']:.4f}")
        logger.info(f"   Total Information Bars: {info_metrics['total_bars']:,}")
        
        # Combine all results
        results.update({
            'bar_characteristics': bar_characteristics,
            'info_metrics': info_metrics,
            'aligned_metrics': aligned_metrics,
            'analyzer': analyzer
        })
        
        return results
        
    except Exception as e:
        logger.error(f"Error in institutional analysis: {e}")
        return {}


def analyze_information_driven_performance(strategy_data, bar_type):
    """
    📊 ANALYZE INFORMATION-DRIVEN PERFORMANCE
    
    Analyze strategy performance using original information-driven structure.
    Preserves volume/dollar/imbalance bar timing and characteristics.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with original bar structure
    bar_type : str
        Type of information bars
        
    Returns
    -------
    dict
        Information-driven performance metrics
    """
    try:
        # Preserve original sampling structure
        original_returns = strategy_data['portfolio_value'].pct_change().fillna(0)
        
        # Calculate structure-preserving metrics
        metrics = {
            'information_sharpe': calculate_info_sharpe(original_returns),
            'bar_efficiency': calculate_bar_efficiency_score(
                strategy_data.index.to_series().diff().dt.total_seconds().dropna()
            ),
            'microstructure_alpha': calculate_microstructure_alpha(original_returns),
            'information_density': len(strategy_data) / calculate_time_span_days_simple(strategy_data)
        }
        
        logger.info(f"📊 Information-driven analysis complete for {bar_type} bars")
        return metrics
        
    except Exception as e:
        logger.error(f"Error in information-driven performance analysis: {e}")
        return {}


def calculate_info_sharpe(returns):
    """Calculate information Sharpe ratio."""
    if returns.std() == 0:
        return 0
    return returns.mean() / returns.std() * np.sqrt(252)


def calculate_microstructure_alpha(returns):
    """Calculate microstructure alpha."""
    return returns.std() / np.sqrt(len(returns)) if len(returns) > 0 else 0


def calculate_time_span_days_simple(strategy_data):
    """Calculate time span in days (simple version)."""
    if len(strategy_data) < 2:
        return 1
    return (strategy_data.index[-1] - strategy_data.index[0]).days + 1


# =================== 🎨 HFT-SPECIFIC VISUALIZATIONS 🎨 ===================

def create_hft_bar_timing_plot(strategy_data, bar_type, output_file=None, show_plot=True):
    """
    📊 CREATE HFT BAR TIMING PLOT
    
    Visualize the timing characteristics of information-driven bars.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with timestamps
    bar_type : str
        Type of information bars
    output_file : str, optional
        Output file for the plot
    show_plot : bool, optional
        Whether to display the plot
        
    Returns
    -------
    plotly.graph_objects.Figure
        The plotly figure object
    """
    try:
        timestamps = strategy_data.index
        time_diffs = timestamps.to_series().diff().dt.total_seconds().dropna()
        
        # Create subplots
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                f'{bar_type.upper()} Bar Duration Distribution',
                'Bar Timing Over Time',
                'Bar Efficiency Analysis',
                'Intraday Bar Frequency'
            ),
            specs=[[{"secondary_y": False}, {"secondary_y": False}],
                   [{"secondary_y": False}, {"secondary_y": False}]]
        )
        
        # Plot 1: Duration distribution
        fig.add_trace(
            go.Histogram(x=time_diffs, nbinsx=50, name='Duration Distribution'),
            row=1, col=1
        )
        
        # Plot 2: Timing over time
        fig.add_trace(
            go.Scatter(
                x=timestamps[1:], 
                y=time_diffs, 
                mode='lines',
                name='Bar Duration',
                line=dict(color='blue', width=1)
            ),
            row=1, col=2
        )
        
        # Plot 3: Bar efficiency
        efficiency_scores = []
        window_size = min(100, len(time_diffs) // 10)
        for i in range(window_size, len(time_diffs)):
            window_data = time_diffs.iloc[i-window_size:i]
            efficiency = calculate_bar_efficiency_score(window_data)
            efficiency_scores.append(efficiency)
        
        if efficiency_scores:
            fig.add_trace(
                go.Scatter(
                    x=timestamps[window_size+1:window_size+1+len(efficiency_scores)],
                    y=efficiency_scores,
                    mode='lines',
                    name='Rolling Efficiency',
                    line=dict(color='green', width=2)
                ),
                row=2, col=1
            )
        
        # Plot 4: Intraday frequency
        if len(timestamps) > 0:
            hours = [t.hour for t in timestamps]
            hour_counts = pd.Series(hours).value_counts().sort_index()
            
            fig.add_trace(
                go.Bar(x=hour_counts.index, y=hour_counts.values, name='Bars per Hour'),
                row=2, col=2
            )
        
        # Update layout
        fig.update_layout(
            title=f"🔬 HFT Bar Analysis: {bar_type.upper()} Bars",
            showlegend=True,
            height=800,
            width=1200
        )
        
        # Update axes labels
        fig.update_xaxes(title_text="Duration (seconds)", row=1, col=1)
        fig.update_yaxes(title_text="Frequency", row=1, col=1)
        fig.update_xaxes(title_text="Time", row=1, col=2)
        fig.update_yaxes(title_text="Duration (seconds)", row=1, col=2)
        fig.update_xaxes(title_text="Time", row=2, col=1)
        fig.update_yaxes(title_text="Efficiency Score", row=2, col=1)
        fig.update_xaxes(title_text="Hour of Day", row=2, col=2)
        fig.update_yaxes(title_text="Number of Bars", row=2, col=2)
        
        if output_file:
            fig.write_html(output_file)
            logger.info(f"📊 HFT bar timing plot saved to {output_file}")
        
        if show_plot:
            fig.show()
        
        return fig
        
    except Exception as e:
        logger.error(f"Error creating HFT bar timing plot: {e}")
        return None


def create_hft_performance_comparison_plot(info_metrics, aligned_metrics, output_file=None, show_plot=True):
    """
    📈 CREATE HFT PERFORMANCE COMPARISON PLOT
    
    Visualize the dual-track performance comparison.
    
    Parameters
    ----------
    info_metrics : dict
        Information-driven metrics
    aligned_metrics : dict
        Time-aligned metrics
    output_file : str, optional
        Output file for the plot
    show_plot : bool, optional
        Whether to display the plot
        
    Returns
    -------
    plotly.graph_objects.Figure
        The plotly figure object
    """
    try:
        # Prepare data for comparison
        metrics_names = ['Sharpe Ratio', 'Information Density', 'Bar Efficiency', 'Beta']
        info_values = [
            info_metrics.get('information_sharpe', 0),
            info_metrics.get('information_density', 0) / 100,  # Normalize
            info_metrics.get('bar_efficiency_score', 0),
            0  # Beta not applicable for info-driven
        ]
        aligned_values = [
            aligned_metrics.get('aligned_sharpe', 0),
            0,  # Information density not applicable
            0,  # Bar efficiency not applicable
            aligned_metrics.get('beta', 0)
        ]
        
        fig = go.Figure()
        
        # Add bars for information-driven metrics
        fig.add_trace(go.Bar(
            name='Information-Driven',
            x=metrics_names,
            y=info_values,
            marker_color='lightblue'
        ))
        
        # Add bars for time-aligned metrics
        fig.add_trace(go.Bar(
            name='Time-Aligned',
            x=metrics_names,
            y=aligned_values,
            marker_color='lightcoral'
        ))
        
        fig.update_layout(
            title='🏛️ Institutional Dual-Track Performance Comparison',
            xaxis_title='Metrics',
            yaxis_title='Values',
            barmode='group',
            height=600,
            width=800
        )
        
        if output_file:
            fig.write_html(output_file)
            logger.info(f"📈 Performance comparison plot saved to {output_file}")
        
        if show_plot:
            fig.show()
        
        return fig
        
    except Exception as e:
        logger.error(f"Error creating performance comparison plot: {e}")
        return None


def create_hft_microstructure_plot(strategy_data, output_file=None, show_plot=True):
    """
    🔬 CREATE HFT MICROSTRUCTURE PLOT
    
    Visualize microstructure characteristics of HFT data.
    
    Parameters
    ----------
    strategy_data : pd.DataFrame
        Strategy data with portfolio values
    output_file : str, optional
        Output file for the plot
    show_plot : bool, optional
        Whether to display the plot
        
    Returns
    -------
    plotly.graph_objects.Figure
        The plotly figure object
    """
    try:
        returns = strategy_data['portfolio_value'].pct_change().fillna(0)
        
        # Create subplots
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=(
                'Returns Distribution',
                'Returns Autocorrelation',
                'Volatility Clustering',
                'High-Frequency Statistics'
            )
        )
        
        # Plot 1: Returns distribution
        fig.add_trace(
            go.Histogram(x=returns, nbinsx=100, name='Returns Distribution'),
            row=1, col=1
        )
        
        # Plot 2: Autocorrelation
        lags = range(1, min(50, len(returns) // 4))
        autocorr = [returns.autocorr(lag=lag) for lag in lags]
        
        fig.add_trace(
            go.Scatter(x=list(lags), y=autocorr, mode='lines+markers', name='Autocorrelation'),
            row=1, col=2
        )
        
        # Plot 3: Volatility clustering (rolling std)
        rolling_vol = returns.rolling(window=min(100, len(returns) // 10)).std()
        
        fig.add_trace(
            go.Scatter(
                x=strategy_data.index,
                y=rolling_vol,
                mode='lines',
                name='Rolling Volatility',
                line=dict(color='red', width=1)
            ),
            row=2, col=1
        )
        
        # Plot 4: High-frequency statistics
        stats_text = f"""
        Mean Return: {returns.mean():.6f}
        Std Return: {returns.std():.6f}
        Skewness: {returns.skew():.4f}
        Kurtosis: {returns.kurtosis():.4f}
        Min Return: {returns.min():.6f}
        Max Return: {returns.max():.6f}
        """
        
        fig.add_annotation(
            text=stats_text,
            xref="x domain", yref="y domain",
            x=0.5, y=0.5, xanchor='center', yanchor='middle',
            showarrow=False,
            font=dict(size=12),
            row=2, col=2
        )
        
        fig.update_layout(
            title='🔬 HFT Microstructure Analysis',
            showlegend=True,
            height=800,
            width=1200
        )
        
        if output_file:
            fig.write_html(output_file)
            logger.info(f"🔬 Microstructure plot saved to {output_file}")
        
        if show_plot:
            fig.show()
        
        return fig
        
    except Exception as e:
        logger.error(f"Error creating microstructure plot: {e}")
        return None