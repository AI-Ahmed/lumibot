"""
This module test is not using pytest or unittest, but focus on
testing the AlpacaBacktesting class and the TradesDataStrategy class as 
actual Quant Dev would do.

HFT Performance Tracking & Enhanced Data Processing:

This file demonstrates the use of enhanced HFT performance tracking in Lumibot
with the new processed bars functionality and progressive trades loading.

The modifications include:

1. Configurable resampling frequency (resample_rule="1min" for 1-minute intervals)
2. Intraday metrics calculation for HFT strategies
3. Trade-level P&L tracking for detailed performance analysis
4. Enhanced tearsheet with HFT-specific insights
5. Processed bars storage with custom indicators using set_processed_bars()
6. Additional columns preservation (custom indicators, momentum, etc.)
7. Non-uniform timestep support with 'custom' timestep
8. **NEW: Progressive trades loading** - Downloads trades data in background to prevent freezing

Key Features Demonstrated:
- Volume bars creation from trades data (trades → volume bars instead of time bars)
- Custom indicators (VWAP, price_momentum, volume_momentum, relative_strength)
- Processed bars storage with set_processed_bars() method for strategy data
- Additional columns preservation in processed bars
- Benchmark uses standard pipeline (benchmark_asset="SPY" parameter)
- **Progressive trades loading** - Backtest starts immediately, data downloads in background

Progressive Loading Benefits:
- No terminal freezing: Backtest starts immediately with first chunk of data
- Memory efficient: Old trades automatically trimmed via sliding window
- Better UX: Progress feedback as data downloads in background
- Scalable: Works with large portfolios and long backtest periods

These modifications address the issue where HFT strategies showed 0% returns
in performance metrics despite active trading, due to daily resampling that
erased evidence of intraday trading activity. The new processed bars functionality
allows strategies to use custom processed bars (volume bars, information-driven bars, etc.) 
instead of standard time-based bars for better HFT performance.

The progressive loading system prevents the terminal from freezing when downloading
millions of trades records for HFT strategies.
"""

from datetime import datetime, timedelta, time
import pandas as pd
import numpy as np

from lumibot.backtesting import AlpacaBacktesting
from lumibot.strategies import Strategy
from lumibot.traders import Trader

from lumibot import log

try:
    from fpap.data.bars.st_bars import volume_bars, g_bars
except ImportError:
    volume_bars = None

    def g_bars(x: np.ndarray, y: int) -> np.ndarray:
        return (x // y) * y


class TradesDataStrategy(Strategy):
    """
    A strategy that demonstrates how to use trades data for HFT and feature engineering.
    
    This strategy:
    1. Fetches historical trades data for a stock
    2. Computes volume-weighted average price (VWAP) from trades
    3. Creates custom time bars (volume bars) from trades data
    4. Makes trading decisions based on custom indicators derived from trades
    """
    
    def initialize(self):
        """Initialize strategy parameters and variables."""
        # Set the trading frequency
        self.sleeptime = "15M"  # 15 minutes
        
        # Set the stock to trade (can be single symbol or list)
        self.symbol = self.parameters.get("symbol", "AAPL")
        
        # Normalize symbols to always be a list for consistent handling
        self.symbols = self.symbol if isinstance(self.symbol, list) else [self.symbol]
        
        # Parameters for feature engineering
        self.volume_bar_threshold = self.parameters.get("volume_bar_threshold", 10000)
        self.vwap_window = self.parameters.get("vwap_window", 100)
        
        # Trading parameters
        self.position_size = self.parameters.get("position_size", 0.1)  # 10% of portfolio per trade
        self.vwap_threshold = self.parameters.get("vwap_threshold", 0.0001)  # 0.01% threshold for VWAP crossover
        self.allow_short = self.parameters.get("allow_short", False)  # Default to not allowing short positions
        self.min_hold_bars = self.parameters.get("min_hold_bars", 1)  # Min bars to hold before selling (prevents fast flip-flop)
        
        # Per-symbol indicator storage for multi-asset HFT
        self.last_vwaps = {}  # {symbol: vwap_value}
        self.last_signals = {}  # {symbol: signal} - per-symbol signal tracking
        
        # Legacy single-symbol support (for backward compatibility)
        self.last_vwap = None
        self.last_signal = None
        
        # Initialize counters for debugging
        self.sell_signals_count = 0
        self.buy_signals_count = 0
        
        # Track bar index when each symbol was last bought (for min_hold_bars)
        self._last_buy_bar_index = {}  # {symbol: bar_index}
        self._iteration_count = 0
        
    def on_trading_iteration(self):
        """Main trading logic executed on each iteration.
        
        For multi-symbol strategies, this processes each symbol independently:
        1. Fetches trades for each symbol separately
        2. Computes per-symbol volume bars and VWAP
        3. Makes independent trading decisions per symbol
        """
        self._iteration_count += 1
        # Get the current datetime
        current_dt = self.get_datetime()
        
        # Check if we're within market hours (9:30 AM to 4:00 PM Eastern Time)
        current_time = current_dt.time()
        market_open = time(9, 30)
        market_close = time(16, 0)
        
        if not (market_open <= current_time < market_close):
            self.logger.info(f"Outside market hours ({current_time}), skipping trading iteration")
            return
        
        # Fetch and process trades data for the last 10 minutes
        start_dt = current_dt - timedelta(minutes=10)
        
        # Constrain to market hours (never request pre-market data)
        market_open_dt = current_dt.replace(hour=9, minute=30, second=0, microsecond=0)
        if start_dt < market_open_dt:
            start_dt = market_open_dt
            self.logger.debug(f"Adjusted lookback start to market open: {start_dt}")
        
        # Additional safety: Skip if current_dt is at or before market open
        if current_dt <= market_open_dt:
            self.logger.info(f"Current time {current_dt} is at or before market open, skipping")
            return
        
        # Only attempt to get trades data if we're using a data source that supports it
        if not hasattr(self.broker.data_source, "get_historical_trades_between_dates"):
            self.logger.info("This data source does not support fetching trades data")
            return
        
        # Configure chunk size from sleeptime (AlpacaBacktesting)
        if hasattr(self.broker.data_source, "configure_from_sleeptime") and hasattr(self, "_sleeptime"):
            self.broker.data_source.configure_from_sleeptime(self._sleeptime)

        # Batch fetch trades for all symbols (parallel download when supported)
        trades_df = self.broker.data_source.get_historical_trades_between_dates(
            base_asset=self.symbols,
            data_datetime_start=start_dt,
            data_datetime_end=current_dt,
        )
        if trades_df is None or trades_df.empty:
            self.logger.debug("No trades data from batch fetch")
            return
        # Dispatch to per-symbol processing (batch returns MultiIndex)
        if isinstance(trades_df.index, pd.MultiIndex) and trades_df.index.nlevels >= 1:
            symbols_in_data = trades_df.index.get_level_values(0).unique().tolist()
            for symbol in symbols_in_data:
                try:
                    sym_trades = trades_df.xs(symbol, level=0)
                except KeyError:
                    continue
                if sym_trades.empty:
                    continue
                processed = self.process_trades_data(sym_trades)
                self._process_symbol_iteration(symbol, start_dt, current_dt, trades_df=processed)
        else:
            # Single-symbol result (fallback)
            processed = self.process_trades_data(trades_df) if not trades_df.empty else None
            if processed is not None and not processed.empty:
                sym = self.symbols[0]
                self._process_symbol_iteration(sym, start_dt, current_dt, trades_df=processed)
    
    def _process_symbol_iteration(self, symbol, start_dt, current_dt, trades_df=None):
        """Process a single symbol's trading iteration.
        
        Parameters
        ----------
        symbol : str
            The symbol to process
        start_dt : datetime
            Start datetime for trades lookup
        current_dt : datetime
            Current datetime (end of trades lookup)
        trades_df : pandas.DataFrame, optional
            Pre-fetched processed trades for this symbol. If None, fetches via get_historical_trades.
        """
        if trades_df is None:
            trades_df = self.get_historical_trades(
                asset=symbol,
                data_datetime_start=start_dt,
                data_datetime_end=current_dt,
            )
        
        # Skip if no trades data available
        if trades_df is None or trades_df.empty:
            self.logger.debug(f"No trades data for {symbol}")
            return
            
        # Process trades to create volume bars
        if volume_bars:
            volume_bar = volume_bars(trades_df, self.volume_bar_threshold, datetime_col='timestamp')
        else:
            volume_bar = self._create_volume_bars(trades_df)
            
        if volume_bar is None or volume_bar.empty:
            self.logger.debug(f"No volume bars created for {symbol}")
            return
            
        # Calculate VWAP for this symbol
        if volume_bars:
            vwap = volume_bar['VWAP']
        else:
            vwap = self.compute_vwap(volume_bar)
        if vwap is None:
            self.logger.warning(f"VWAP calculation failed for {symbol}")
            return
            
        try:
            # Store per-symbol VWAP
            symbol_vwap = vwap.iloc[-1]
            self.last_vwaps[symbol] = symbol_vwap
            
            # Also update legacy single-symbol variable for backward compatibility
            self.last_vwap = symbol_vwap

            self.process_and_store_bars(volume_bar, symbol, current_dt)
            
            # Make trading decision for this specific symbol
            self._make_symbol_trading_decision(symbol, volume_bar)
        except Exception as e:
            self.logger.error(f"Error processing VWAP for {symbol}: {e}")
    
    def _create_volume_bars(self, trades_df):
        """Create volume bars from trades data."""
        try:
            volume_trades = trades_df.copy()
            volume_trades = volume_trades.reset_index(drop=False)
            
            # Check Volume column exists
            if 'Volume' not in volume_trades.columns:
                self.logger.warning(f"Volume column missing from DataFrame. Available columns: {volume_trades.columns.tolist()}")
                return None
                
            total_volumes = volume_trades['Volume'].cumsum()
            
            # Log data before groupby
            # self.logger.info(f"Before groupby - volume_bar shape: {volume_trades.shape}")
            
            # Group by volume bars
            volume_bar = volume_trades.groupby(g_bars(total_volumes, self.volume_bar_threshold)).agg({
                'timestamp': 'last',
                'Price': 'ohlc',
                'Volume': 'sum'
            }).reset_index(drop=True)
            
            # Drop the first level of columns (e.g. Price.open -> open)
            volume_bar.columns = volume_bar.columns.droplevel(0)
            # Rename agg result columns: 'last' -> 'timestamp', 'sum' -> 'Volume'
            # (droplevel(0) leaves 'last', 'open','high','low','close','sum')
            volume_bar = volume_bar.rename(columns={'last': 'timestamp', 'sum': 'Volume'})
            
            # Log data after groupby
            # self.logger.info(f"After groupby - volume_bar shape: {volume_bar.shape}")
            # self.logger.info(f"After groupby - volume_bar columns: {volume_bar.columns.tolist()}")
            
            return volume_bar
            
        except Exception as e:
            self.logger.error(f"Error in volume bar creation: {e}")
            return None
    
    def process_and_store_bars(self, volume_bar, symbol, current_dt):
        """
        Process volume bars with additional indicators and store them immediately using set_processed_bars.
        This is synchronized with the strategy's sleeptime interval.
        
        Parameters
        ----------
        volume_bar : pandas.DataFrame
            The volume bars data
        symbol : str or list
            The symbol(s) being processed
        current_dt : datetime
            Current datetime
        """
        try:
            # Handle multiple symbols
            symbols = symbol if isinstance(symbol, list) else [symbol]
            
            for sym in symbols:
                # Create enhanced bars with additional indicators
                enhanced_bars = self.create_enhanced_bars(volume_bar, sym, current_dt)
                
                if enhanced_bars is not None and not enhanced_bars.empty:
                    # Store immediately (sync with sleeptime interval)
                    from lumibot.entities import Asset
                    asset = Asset(sym, "stock")
                    
                    self.broker.data_source.set_processed_bars(
                        processed_bars=enhanced_bars,
                        base_asset=asset,
                        timestep='custom',  # Use custom for HFT non-uniform intervals
                    )
                    
                    self.logger.info(f"Stored {len(enhanced_bars)} processed bars for {sym} with custom indicators")
                        
        except Exception as e:
            self.logger.error(f"Error processing and storing bars: {e}")
    
    def create_enhanced_bars(self, volume_bar, symbol, current_dt):
        """
        Create enhanced bars with additional custom indicators.
        
        Parameters
        ----------
        volume_bar : pandas.DataFrame
            The volume bars data
        symbol : str
            The symbol being processed
        current_dt : datetime
            Current datetime
            
        Returns
        -------
        pandas.DataFrame
            Enhanced bars with additional indicators
        """
        try:
            if volume_bar is None or volume_bar.empty:
                return None
                
            # Create a copy for enhancement
            enhanced = volume_bar.copy()
            
            # Add timestamp if not present
            if 'timestamp' not in enhanced.columns:
                enhanced['timestamp'] = current_dt
                
            # Add VWAP if not present
            if 'VWAP' not in enhanced.columns:
                vwap = self.compute_vwap(enhanced)
                if vwap is not None:
                    enhanced['VWAP'] = vwap
            
            # Add custom HFT indicators (use per-symbol signal for multi-asset HFT)
            enhanced['symbol'] = symbol
            enhanced['strategy_signal'] = self.last_signals.get(symbol, self.last_signal or 'none')
            
            # Add price momentum indicator
            if 'close' in enhanced.columns and len(enhanced) > 1:
                enhanced['price_momentum'] = enhanced['close'].pct_change().fillna(0)
            else:
                enhanced['price_momentum'] = 0.0
                
            # Add volume momentum indicator
            if 'Volume' in enhanced.columns and len(enhanced) > 1:
                enhanced['volume_momentum'] = enhanced['Volume'].pct_change().fillna(0)
            else:
                enhanced['volume_momentum'] = 0.0
            
            # Add relative strength indicator (simple version)
            if 'close' in enhanced.columns and 'VWAP' in enhanced.columns:
                enhanced['relative_strength'] = (enhanced['close'] - enhanced['VWAP']) / enhanced['VWAP']
            else:
                enhanced['relative_strength'] = 0.0
                
            # Ensure we have all required OHLCV columns
            required_columns = ['open', 'high', 'low', 'close', 'volume']
            for col in required_columns:
                if col not in enhanced.columns:
                    if col == 'volume' and 'Volume' in enhanced.columns:
                        enhanced['volume'] = enhanced['Volume']
                    elif col in ['open', 'high', 'low'] and 'close' in enhanced.columns:
                        # For volume bars, OHLC might be the same as close
                        enhanced[col] = enhanced['close']
                    else:
                        # Set default values if we can't derive them
                        enhanced[col] = enhanced.get('close', 0.0) if col != 'volume' else 1.0
            
            # Set timestamp as index
            if 'timestamp' in enhanced.columns:
                enhanced.set_index('timestamp', inplace=True)
                
            return enhanced
            
        except Exception as e:
            self.logger.error(f"Error creating enhanced bars for {symbol}: {e}")
            return None
    
    
    def get_historical_trades(self, asset, data_datetime_start=None, data_datetime_end=None):
        """
        Get historical trades for an asset between specified dates.
        
        Parameters
        ----------
        asset : str or Asset
            Asset to get trades for
        data_datetime_start : datetime, optional
            Start datetime
        data_datetime_end : datetime, optional
            End datetime
            
        Returns
        -------
        pandas.DataFrame
            DataFrame containing the historical trades data
        """
        try:
            # Check if we're within market hours
            if data_datetime_end:
                current_time = data_datetime_end.time()
                market_open = time(9, 30)
                market_close = time(16, 0)
                
                if not (market_open <= current_time < market_close):
                    self.logger.info(f"Requested trades outside market hours ({current_time})")
                    return pd.DataFrame()

            trades_df = self.broker.data_source.get_historical_trades_between_dates(
                base_asset=asset,
                data_datetime_start=data_datetime_start,
                data_datetime_end=data_datetime_end
            )
            
            # self.logger.info(f"Raw trades data shape: {trades_df.shape}")
            
            if trades_df.empty:
                return pd.DataFrame()
                
            # Process the data to match expected format
            processed_df = self.process_trades_data(trades_df)
            return processed_df
            
        except ValueError as e:
            if "exceeds the dataset range" in str(e):
                self.logger.warning(f"Data not available for the requested time range: {e}")
                return pd.DataFrame()
        except Exception as e:
            self.logger.error(f"Error fetching trades data: {e}")
            return pd.DataFrame()

    def process_trades_data(self, df):
        """
        Process raw trades data into a standardized format
        
        Parameters
        ----------
        df : pandas.DataFrame
            Raw trades DataFrame
            
        Returns
        -------
        pandas.DataFrame
            Processed DataFrame
        """
        # self.logger.info(f"Processing trades data with columns: {df.columns.tolist()}")
        
        try:
            # Create a copy of the DataFrame to avoid modifying the original
            result = df.copy()
            
            # Handle the case of a reset index with 'timestamp' as a column
            if 'timestamp' not in result.columns and result.index.name == 'timestamp':
                result = result.reset_index()
            
            # SyncTradesDownloader now returns: price, size, exchange, trade_id, conditions, tape
            column_mapping = {
                'price': 'Price',    # From renamed Alpaca column (p -> price -> Price)
                'size': 'Volume',    # From renamed Alpaca column (s -> size -> Volume)
                'Price': 'Price',    # Keep as-is if already correct
                'Volume': 'Volume',  # Keep as-is if already correct
                'Tick': 'Symbol',
            }
            
            # Rename columns that need renaming
            for old_name, new_name in column_mapping.items():
                if old_name in result.columns and old_name != new_name:
                    result.rename(columns={old_name: new_name}, inplace=True)
            
            # Set timestamp as index if it's not already
            if result.index.name != 'timestamp' and 'timestamp' in result.columns:
                result.set_index('timestamp', inplace=True)
                
            # Ensure we have Price column
            if 'Price' not in result.columns:
                # Try to find a column that might contain price data
                price_column = next((col for col in result.columns if 
                                     col.lower() == 'price' or col.lower() == 'close' or col == 'p'), None)
                
                if price_column:
                    self.logger.info(f"Using {price_column} as Price column")
                    result['Price'] = result[price_column]
                else:
                    # If no price column found, try using the first numeric column
                    for col in result.columns:
                        if pd.api.types.is_numeric_dtype(result[col]):
                            self.logger.info(f"Using {col} as Price column (first numeric column)")
                            result['Price'] = result[col]
                            break
            
            # Ensure we have Volume column
            if 'Volume' not in result.columns:
                # Try to find size column first
                size_column = next((col for col in result.columns if 
                                   col.lower() == 'size' or col == 's'), None)
                if size_column:
                    self.logger.info(f"Using {size_column} as Volume column")
                    result['Volume'] = result[size_column]
                else:
                    self.logger.info("Setting default Volume to 1")
                    result['Volume'] = 1
                
            # self.logger.info(f"Processed data shape: {result.shape}")
            return result
            
        except Exception as e:
            self.logger.error(f"Error processing trades data: {e}")
            return pd.DataFrame()
    
    def compute_vwap(self, volume_bar):
        """
        Compute Volume-Weighted Average Price from trades data.
        
        Parameters
        ----------
        volume_bar : pandas.DataFrame
            DataFrame containing volume bars
            
        Returns
        -------
        pandas.Series or None
            The computed VWAP series or None if calculation fails
        """
        # self.logger.info(f"Computing VWAP from volume bar with columns: {volume_bar.columns.tolist()}")
        
        try:
            # Handle existing VWAP column
            if 'VWAP' in volume_bar.columns:
                return volume_bar['VWAP']
                
            # Extract the appropriate columns - we want Price.close and Volume
            if 'close' in volume_bar.columns and 'Volume' in volume_bar.columns:
                close_prices = volume_bar['close']
                volumes = volume_bar['Volume']
                
                # Compute VWAP
                vwap = (close_prices * volumes).cumsum() / volumes.cumsum()
                # self.logger.info("Calculated VWAP from MultiIndex columns")
                return vwap
            else:
                raise ValueError("Unable to compute VWAP. Missing required columns.")
                
        except Exception as e:
            self.logger.error(f"Error computing VWAP: {str(e)}")
            return None
    
    def _make_symbol_trading_decision(self, symbol, volume_bar):
        """Make trading decision for a specific symbol using its per-symbol VWAP.
        
        Parameters
        ----------
        symbol : str
            The symbol to make trading decision for
        volume_bar : pandas.DataFrame
            DataFrame containing volume bars for this symbol
        """
        # Get the per-symbol VWAP
        symbol_vwap = self.last_vwaps.get(symbol)
        
        if symbol_vwap is None or volume_bar.empty:
            self.logger.debug(f"Skipping trading decision for {symbol}: VWAP is None or volume bar is empty")
            return
        
        # Use volume_bar close as last_price fallback to reduce redundant data source calls
        last_price = None
        if "close" in volume_bar.columns and len(volume_bar) > 0:
            last_price = float(volume_bar["close"].iloc[-1])
        if last_price is None:
            last_price = self.get_last_price(symbol)
        if last_price is None:
            self.logger.warning(f"Could not get last price for {symbol}")
            return
        
        # Process trading decision with per-symbol VWAP
        self._process_trading_decision(symbol, last_price, symbol_vwap)
    
    def make_trading_decisions(self, volume_bar):
        """
        Make trading decisions based on VWAP and price relationship.
        
        Note: This method is kept for backward compatibility. For multi-symbol
        strategies, use _make_symbol_trading_decision() which is called from
        _process_symbol_iteration().
        
        This method implements a simple VWAP-based strategy:
        - Buy when price crosses above VWAP by threshold percentage
        - Sell when price crosses below VWAP by threshold percentage
        - Manage position sizing based on portfolio value
        
        Parameters
        ----------
        volume_bar : pandas.DataFrame
            DataFrame containing volume bars
        """
        # Skip if we don't have valid VWAP or volume bars
        if self.last_vwap is None or volume_bar.empty:
            self.logger.info("Skipping trading decision: VWAP is None or volume bar is empty")
            return
        
        # Get the last price
        if isinstance(self.symbol, str):
            last_price = self.get_last_price(self.symbol)
            if last_price is None:
                self.logger.warning(f"Could not get last price for {self.symbol}")
                return
        else:
            last_prices = self.get_last_prices(self.symbol)
            if not last_prices or all(price is None for price in last_prices.values()):
                self.logger.warning(f"Could not get last prices for {self.symbol}")
                return
            
            # Process each symbol individually with their per-symbol VWAP
            for symbol, price in last_prices.items():
                symbol_vwap = self.last_vwaps.get(symbol, self.last_vwap)
                self._process_trading_decision(symbol, price, symbol_vwap)
            
            # Return after processing all symbols
            return
        
        # For single symbol case, process the trading decision
        self._process_trading_decision(self.symbol, last_price, self.last_vwap)

    def _process_trading_decision(self, symbol, last_price, symbol_vwap=None):
        """
        Process trading decision for a single symbol using its per-symbol VWAP.
        
        Parameters
        ----------
        symbol : str
            The symbol to process
        last_price : float
            The last price for the symbol
        symbol_vwap : float, optional
            The per-symbol VWAP to use. If None, falls back to self.last_vwap
        """
        # Use per-symbol VWAP if provided, otherwise fall back to legacy single VWAP
        vwap = symbol_vwap if symbol_vwap is not None else self.last_vwap
        
        # Get current positions and portfolio value
        positions = self.get_positions()
        portfolio_value = self.get_portfolio_value()
        current_dt = self.get_datetime()
        
        # Safety check: Skip if we already have any short positions and they're not allowed
        if not self.allow_short and any(position.quantity < 0 for position in positions):
            self.logger.info(f"Skipping trading decision: Short positions detected but not allowed")
            return

        # Check if we have a position in the symbol
        position_quantity = 0
        for position in positions:
            if position.asset.symbol == symbol:
                position_quantity = position.quantity
                break
                
        # Additional safety check: Verify position quantity is not negative
        if position_quantity < 0 and not self.allow_short:
            self.logger.warning(f"Unexpected negative position for {symbol}: {position_quantity}. Skipping trading decision.")
            return
        
        # Get the per-symbol signal (or use legacy single signal)
        last_signal = self.last_signals.get(symbol, self.last_signal)
        
        # Safety check for VWAP and calculate deviation
        signal = None
        vwap_deviation = None
        
        if vwap is None or not isinstance(vwap, (int, float)) or vwap <= 0:
            self.logger.warning(f"DEBUG: Invalid VWAP value for {symbol}: {vwap}. Cannot calculate deviation.")
            # Force a sell signal if we have a position and VWAP is invalid
            if position_quantity > 0:
                signal = "sell"
                self.sell_signals_count += 1
                self.logger.info(f"DEBUG: Forced SELL signal #{self.sell_signals_count} for {symbol} due to invalid VWAP")
        else:
            # Calculate price deviation from VWAP as a percentage
            vwap_deviation = (last_price - vwap) / vwap
            
            # Log current state for debugging
            self.logger.info(f"[{symbol}] Trading decision: Price={last_price:.2f}, VWAP={vwap:.2f}, "
                            f"Deviation={vwap_deviation:.4f} ({vwap_deviation*100:.2f}%), Current position={position_quantity}")
            
            # Determine the signal based on price-VWAP relationship
            # Buy signal: Price crosses above VWAP by threshold
            if vwap_deviation > self.vwap_threshold:
                signal = "buy"
                self.buy_signals_count += 1
            # Sell signal: Price crosses below VWAP by threshold
            elif vwap_deviation < -self.vwap_threshold:
                signal = "sell"
                self.sell_signals_count += 1
        
        # Only trade if we have a new signal or need to exit a position
        if signal == "buy" and (position_quantity <= 0 or last_signal != signal):
            # Close any existing short position first
            if position_quantity < 0:
                self.logger.info(f"Closing existing short position of {position_quantity} shares for {symbol}")
                order = self.create_order(symbol, position_quantity, "buy")
                self.submit_order(order)
            
            # Calculate new long position size (only if we don't already have a long position)
            if position_quantity <= 0:
                # Calculate position size based on portfolio value and position sizing parameter
                position_value = portfolio_value * self.position_size
                buy_quantity = max(1, int(position_value / last_price))
                
                self.logger.info(f"🟢 [{symbol}] BUY SIGNAL: Buying {buy_quantity} shares at ${last_price:.2f} (VWAP: ${vwap:.2f})")
                
                # Create and submit buy order
                order = self.create_order(symbol, buy_quantity, "buy")
                self.submit_order(order)
                
                # Update per-symbol signal tracking and hold-period tracking
                self.last_signals[symbol] = "buy"
                self.last_signal = "buy"  # Legacy support
                self._last_buy_bar_index[symbol] = self._iteration_count
        
        elif signal == "sell":
            self.logger.info(f"🔴 [{symbol}] SELL SIGNAL detected: Price=${last_price:.2f}, VWAP=${vwap:.2f}, Position={position_quantity}")
            
            # CRITICAL: Never create sell order when position <= 0 and shorts not allowed (short-selling prevention)
            if position_quantity <= 0 and not self.allow_short:
                self.logger.debug(f"[{symbol}] Sell signal ignored: no position (position_quantity={position_quantity})")
                self.last_signals[symbol] = "sell"
                self.last_signal = "sell"
                return
            
            # Close any existing long position (with min_hold_bars and short-selling safeguards)
            if position_quantity > 0:
                # Min hold period: prevent fast selling right after a buy
                last_buy_bar = self._last_buy_bar_index.get(symbol, 0)
                bars_held = self._iteration_count - last_buy_bar
                if bars_held < self.min_hold_bars:
                    self.logger.info(
                        f"🔴 [{symbol}] SELL signal held: min_hold_bars={self.min_hold_bars}, "
                        f"bars_held={bars_held} (skip fast sell)"
                    )
                    return
                
                # Use integer quantity for stocks (avoids fractional-share edge cases)
                sell_quantity = max(1, int(round(position_quantity)))
                if sell_quantity <= 0:
                    self.logger.warning(f"[{symbol}] Invalid sell quantity {sell_quantity}, skipping")
                    return
                    
                self.logger.info(f"🔴 [{symbol}] Closing existing long position of {sell_quantity} shares")
                order = self.create_order(symbol, sell_quantity, "sell")
                self.submit_order(order)
                
                # Update per-symbol signal tracking
                self.last_signals[symbol] = "sell"
                self.last_signal = "sell"  # Legacy support
            # Only create short positions if explicitly allowed
            elif position_quantity == 0 and self.allow_short and last_signal != signal:
                # Calculate position size based on portfolio value and position sizing parameter
                position_value = portfolio_value * self.position_size
                sell_quantity = max(1, int(position_value / last_price))
                
                self.logger.info(f"🔴 [{symbol}] SELL SIGNAL: Selling {sell_quantity} shares at ${last_price:.2f} (VWAP: ${vwap:.2f})")
                
                # Create and submit sell order
                order = self.create_order(symbol, sell_quantity, "sell")
                self.submit_order(order)
                
                # Update per-symbol signal tracking
                self.last_signals[symbol] = "sell"
                self.last_signal = "sell"  # Legacy support
            # Only log the sell signal if we don't have a position to close and shorts aren't allowed
            elif position_quantity == 0 and not self.allow_short and last_signal != signal:
                self.logger.info(f"🔴 [{symbol}] SELL SIGNAL received but no position to close at ${last_price:.2f} (VWAP: ${vwap:.2f})")
                
                # Update per-symbol signal tracking
                self.last_signals[symbol] = "sell"
                self.last_signal = "sell"  # Legacy support
        else:
            # Log when no signal is generated for debugging
            if vwap_deviation is not None:
                self.logger.debug(f"[{symbol}] No signal: deviation {vwap_deviation:.4f} within threshold ±{self.vwap_threshold:.4f}")



if __name__ == "__main__":
    # Define the backtesting period
    # Use actual market days (weekdays) and set timezone to Eastern Time
    import pytz
    eastern = pytz.timezone('US/Eastern')
    
    # REDUCED TEST PERIOD for faster debugging (2 days instead of 3+ months)
    # backtesting_start = eastern.localize(datetime(2024, 5, 1))  # Wednesday
    # backtesting_end = eastern.localize(datetime(2024, 5, 2))    # Thursday
    
    # Original longer test periods (commented out for debugging)
    backtesting_start = eastern.localize(datetime(2024, 5, 1))  # Monday
    backtesting_end = eastern.localize(datetime(2024, 5, 9))    # Wednesday
    # backtesting_start = eastern.localize(datetime(2023, 5, 1))  # Monday
    # backtesting_end = eastern.localize(datetime(2024, 5, 1))    # Wednesday
    
    print(f"🔵 [TEST] Starting backtest from {backtesting_start} to {backtesting_end}")
    print(f"🔵 [TEST] Market hours: 9:30 AM - 4:00 PM ET")
    print(f"🔵 [TEST] Strategy sleeptime: 15M (first iteration expected at ~9:45 AM)")

    TradesDataStrategy.run_backtest(
        datasource_class=AlpacaBacktesting,
        backtesting_start=backtesting_start,
        backtesting_end=backtesting_end,
        parameters={
            # "symbol": ["AAPL"], # test with single symbol
            "symbol": ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "TSM"], # test with multiple symbols
            "bar_type": "volume",
            "volume_bar_threshold": 100_000,
            "vwap_window": 100,
            "allow_short": False,  # Set to False to prevent short positions
            "position_size": 0.1,
            "vwap_threshold": 0.001,  # Increased from 0.0001 to 0.001 (0.1%) for more realistic signals
            "min_hold_bars": 1,  # Min bars before selling (1=can sell next bar; 2+=prevents fast flip-flop)
        },
        benchmark_asset="SPY",
        risk_free_rate=0.025,
        show_progress_bar=True,
        quiet_logs=False,  # Set to False for debugging - shows INFO logs
        save_logfile=True,
        resample_rule="1min",  # Use 1-minute resampling for HFT strategy
        timestep="minute",  # IMPORTANT: for `AlpacaBacktesting` to work for HFT strategies
        refresh_cache=False,
    ) 