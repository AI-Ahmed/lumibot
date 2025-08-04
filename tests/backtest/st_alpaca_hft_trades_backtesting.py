"""
This module test is not using pytest or unittest, but focus on
testing the AlpacaBacktesting class and the TradesDataStrategy class as 
actual Quant Dev would do.

HFT Performance Tracking:

This file demonstrates the use of enhanced HFT performance tracking in Lumibot.

The modifications include:

1. Configurable resampling frequency (resample_rule="1min" for 1-minute intervals)
2. Intraday metrics calculation for HFT strategies
3. Trade-level P&L tracking for detailed performance analysis
4. Enhanced tearsheet with HFT-specific insights

These modifications address the issue where HFT strategies showed 0% returns
in performance metrics despite active trading, due to daily resampling that
erased evidence of intraday trading activity.
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
        
        # Set the stock to trade
        self.symbol = self.parameters.get("symbol", "AAPL")
        
        # Parameters for feature engineering
        self.volume_bar_threshold = self.parameters.get("volume_bar_threshold", 10000)
        self.vwap_window = self.parameters.get("vwap_window", 100)
        
        # Trading parameters
        self.position_size = self.parameters.get("position_size", 0.1)  # 10% of portfolio per trade
        self.vwap_threshold = self.parameters.get("vwap_threshold", 0.0001)  # 0.01% threshold for VWAP crossover
        self.allow_short = self.parameters.get("allow_short", False)  # Default to not allowing short positions
        
        # Store the last computed indicators
        self.last_vwap = None
        self.custom_bars = []
        self.last_signal = None  # Track the last trading signal
        
        self.logger.info(f"Initialized TradesDataStrategy with symbol {self.symbol}")
    
    def on_trading_iteration(self):
        """Main trading logic executed on each iteration."""
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
        
        # Only attempt to get trades data if we're using a data source that supports it
        if not hasattr(self.broker.data_source, "get_historical_trades_between_dates"):
            self.logger.info("This data source does not support fetching trades data")
            return
            
        # Get historical trades
        trades_df = self.get_historical_trades(
            asset=self.symbol,
            data_datetime_start=start_dt,
            data_datetime_end=current_dt
        )
        
        # Skip if no trades data available
        if trades_df is None or trades_df.empty:
            self.logger.info("Trades DataFrame is empty")
            return
            
        # Log trades dataframe structure for debugging
        # self.logger.info(f"Trades DataFrame columns: {trades_df.columns.tolist()}")
        # self.logger.info(f"Trades DataFrame first row: {trades_df.iloc[0].to_dict()}")
        
        # Process trades to create volume bars
        if volume_bars:
            volume_bar = volume_bars(trades_df, self.volume_bar_threshold, datetime_col='timestamp')
        else:
            volume_bar = self._create_volume_bars(trades_df)
            
        if volume_bar is None or volume_bar.empty:
            return
            
        # Calculate VWAP
        if volume_bars:
            vwap = volume_bar['VWAP']
        else:
            vwap = self.compute_vwap(volume_bar)
        if vwap is None:
            self.logger.warning("VWAP calculation failed")
            return
            
        try:
            self.last_vwap = vwap.iloc[-1]
            # self.logger.info(f"VWAP calculated successfully: {self.last_vwap}")
            
            # Store volume bars for later use
            self.custom_bars.extend(volume_bar)
            # self.logger.info(f"Created {len(volume_bar)} new volume bars")
            
            # Make trading decisions based on the indicators
            self.make_trading_decisions(volume_bar)
        except Exception as e:
            self.logger.error(f"Error processing VWAP: {e}")
    
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
            
            # Drop the first level of columns
            volume_bar.columns = volume_bar.columns.droplevel(0)
            
            # Log data after groupby
            # self.logger.info(f"After groupby - volume_bar shape: {volume_bar.shape}")
            # self.logger.info(f"After groupby - volume_bar columns: {volume_bar.columns.tolist()}")
            
            return volume_bar
            
        except Exception as e:
            self.logger.error(f"Error in volume bar creation: {e}")
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
            
            # Map columns if needed
            column_mapping = {
                'Tick': 'Symbol',
                'Price': 'Price',  # Keep as is if exists
                'Volume': 'Volume', # Keep as is if exists
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
                                     'price' in col.lower() or 'close' in col.lower() or 'p' == col.lower()), None)
                
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
    
    def make_trading_decisions(self, volume_bar):
        """
        Make trading decisions based on VWAP and price relationship.
        
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
            
            # Process each symbol individually
            for symbol, price in last_prices.items():
                self._process_trading_decision(symbol, price)
            
            # Return after processing all symbols
            return
        
        # For single symbol case, process the trading decision
        self._process_trading_decision(self.symbol, last_price)
    
    def _process_trading_decision(self, symbol, last_price):
        """
        Process trading decision for a single symbol.
        
        Parameters
        ----------
        symbol : str
            The symbol to process
        last_price : float
            The last price for the symbol
        """
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
        
        # Calculate price deviation from VWAP as a percentage
        vwap_deviation = (last_price - self.last_vwap) / self.last_vwap
        
        # Log current state
        # self.logger.info(f"Trading decision: Price={last_price:.2f}, VWAP={self.last_vwap:.2f}, "
        #                 f"Deviation={vwap_deviation:.4f}, Current position={position_quantity}")
        
        # Determine the signal based on price-VWAP relationship
        signal = None
        
        # Buy signal: Price crosses above VWAP by threshold
        if vwap_deviation > self.vwap_threshold:
            signal = "buy"
        # Sell signal: Price crosses below VWAP by threshold
        elif vwap_deviation < -self.vwap_threshold:
            signal = "sell"
        
        # Only trade if we have a new signal or need to exit a position
        if signal == "buy" and (position_quantity <= 0 or self.last_signal != signal):
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
                
                self.logger.info(f"BUY SIGNAL: Buying {buy_quantity} shares of {symbol} at ${last_price:.2f} (VWAP: ${self.last_vwap:.2f})")
                
                # Create and submit buy order
                order = self.create_order(symbol, buy_quantity, "buy")
                self.submit_order(order)
                self.last_signal = "buy"
        
        elif signal == "sell":
            # Close any existing long position first
            if position_quantity > 0:
                self.logger.info(f"Closing existing long position of {position_quantity} shares for {symbol}")
                order = self.create_order(symbol, position_quantity, "sell")
                self.submit_order(order)
                self.last_signal = "sell"
            # Only create short positions if explicitly allowed
            elif position_quantity == 0 and self.allow_short and self.last_signal != signal:
                # Calculate position size based on portfolio value and position sizing parameter
                position_value = portfolio_value * self.position_size
                sell_quantity = max(1, int(position_value / last_price))
                
                self.logger.info(f"SELL SIGNAL: Selling {sell_quantity} shares of {symbol} at ${last_price:.2f} (VWAP: ${self.last_vwap:.2f})")
                
                # Create and submit sell order
                order = self.create_order(symbol, sell_quantity, "sell")
                self.submit_order(order)
                self.last_signal = "sell"
            # Only log the sell signal if we don't have a position to close and shorts aren't allowed
            elif position_quantity == 0 and not self.allow_short and self.last_signal != signal:
                self.logger.info(f"SELL SIGNAL received but no position to close for {symbol} at ${last_price:.2f} (VWAP: ${self.last_vwap:.2f})")
                self.last_signal = "sell"
        


if __name__ == "__main__":
    # Define the backtesting period
    # Use actual market days (weekdays) and set timezone to Eastern Time
    import pytz
    eastern = pytz.timezone('US/Eastern')
    
    # Wednesday to Friday in one year
    # backtesting_start = eastern.localize(datetime(2024, 5, 1))  # Monday
    # backtesting_end = eastern.localize(datetime(2024, 8, 9))    # Wednesday
    backtesting_start = eastern.localize(datetime(2023, 5, 1))  # Monday
    backtesting_end = eastern.localize(datetime(2024, 5, 1))    # Wednesday

    TradesDataStrategy.run_backtest(
        datasource_class=AlpacaBacktesting,
        backtesting_start=backtesting_start,
        backtesting_end=backtesting_end,
        parameters={
            "symbol": ["AAPL", "MSFT", "GOOGL", "TSLA"],
            # "symbol": ["AAPL"],
            "volume_bar_threshold": 10_000,
            "vwap_window": 100,
            "allow_short": False,  # Set to False to prevent short positions
            "position_size": 0.01,
            "vwap_threshold": 0.001
        },
        benchmark_asset="SPY",
        risk_free_rate=0.025,
        show_progress_bar=True,
        quiet_logs=False,
        save_logfile=False,
        resample_rule="1min",  # Use 1-minute resampling for HFT strategy
        timestep="minute",  # IMPORTANT: for `AlpacaBacktesting` to work for HFT strategies
        # minutes_before_opening=30,  # Start checking 30 minutes before market open
        # minutes_before_closing=5    # Stop trading 5 minutes before market close
    ) 