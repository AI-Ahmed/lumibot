import pytz
import datetime as dt
from typing import Union, Literal, OrderedDict

import pandas as pd
from pandas_market_calendars import get_calendar

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from lumibot.data_sources import PandasData
from lumibot.entities import Asset, Data

from loguru import logger as logging


START_BUFFER = dt.timedelta(days=5)

class AlpacaDataBacktesting(PandasData):
    """
    Backtesting implementation using Alpaca Markets API
    """
    
    def __init__(
        self,
        datetime_start,
        datetime_end,
        pandas_data=None,
        alpaca_api_key=None,
        alpaca_secret_key=None,
        max_memory=None,
        # TODO: Add more Alpaca-specific parameters (historical trades, etc.)
        **kwargs,
    ):
        super().__init__(
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            pandas_data=pandas_data,
            **kwargs
        )

        # Alpaca API configuration
        self._api_key    = alpaca_api_key
        self._secret_key = alpaca_secret_key

        # Memory limit, off by default
        self.MAX_STORAGE_BYTES = max_memory

        # Initialize Alpaca clients
        self.historical_client = StockHistoricalDataClient(
            self._api_key, 
            self._secret_key,
            # url_override="https://data.sandbox.alpaca.markets"
        )

    def _enforce_storage_limit(pandas_data: OrderedDict):
        storage_used = sum(data.df.memory_usage().sum() for data in pandas_data.values())
        logging.info(f"{storage_used = :,} bytes for {len(pandas_data)} items")
        while storage_used > AlpacaDataBacktesting.MAX_STORAGE_BYTES:
            k, d = pandas_data.popitem(last=False)
            mu = d.df.memory_usage().sum()
            storage_used -= mu
            logging.warning(f"Storage limit exceeded. Evicted LRU data: {k} used {mu:,} bytes")

    def _get_alpaca_timeframe(self, timestep):
        """Convert Lumibot timestep to Alpaca TimeFrame"""
        timeframe_map = {
            'minute': TimeFrame.Minute,
            'hour': TimeFrame.Hour,
            'day': TimeFrame.Day
        }
        return timeframe_map.get(timestep, TimeFrame.Minute)

    def _update_pandas_data(self,
                        asset,
                        quote,
                        length,
                        timestep,
                        start_dt=None,
                        is_benchmark=False,
                        history_type: Literal['bars', 'trades'] = "bars"):
        """Fetch and update data from Alpaca API"""
        if start_dt is None:
            start_dt = self.datetime_start
        
        # Normalize asset and quote representation
        search_asset = asset
        quote_asset = quote if quote is not None else Asset("USD", "forex")

        if isinstance(search_asset, tuple):
            asset_separated, quote_asset = search_asset
        else:
            search_asset = (search_asset, quote_asset)

        # Get appropriate start time with buffer
        start_datetime, ts_unit = self.get_start_datetime_and_ts_unit(
            length, timestep, start_dt, start_buffer=START_BUFFER
        )
        
        # Force not to update when called from `get_last_price`
        # AND update when called `True` from `get_historical_prices_between_dates`
        force_update = (length == 1) and not is_benchmark
        
        # Check if we have data for this asset and if it's recent enough
        update_needed = True
        if search_asset in self.pandas_data and not force_update:
            asset_data = self.pandas_data[search_asset]
            asset_data_df = asset_data.df
            
            # Get the earliest timestamp in our data
            if isinstance(asset_data_df.index, pd.MultiIndex):
                data_start_datetime = asset_data_df.index.levels[1].min()
                data_end_datetime = asset_data_df.index.levels[1].max()
            else:
                data_start_datetime = asset_data_df.index.min()
                data_end_datetime = asset_data_df.index.max()

            data_timestep = asset_data.timestep

            # Check if our data is fresh enough (within 1 minute of now)
            current_time = self.get_datetime()
            if (current_time - data_end_datetime).total_seconds() < 60 and data_timestep == ts_unit:
                # If we have fresh data and sufficient history, no update needed
                if (data_start_datetime <= start_datetime):
                    update_needed = False
        
        # If update is not needed, return early
        if not update_needed:
            return
            
        try:
            # Convert to Alpaca timeframe
            alpaca_tf = self._get_alpaca_timeframe(timestep)
            
            # Always request the most recent data
            if is_benchmark:
                start_datetime = self.datetime_start
                end_datetime = self.datetime_end
            else:
                end_datetime = self.datetime_end
            
            if history_type == "bars":
                # Always request fresh data
                bar_request = StockBarsRequest(
                    symbol_or_symbols=asset.symbol,
                    timeframe=alpaca_tf,
                    adjustment='all',
                    start=start_datetime,
                    end=end_datetime,
                    limit=length  # Request more data to ensure coverage
                )
                
                bars = self.historical_client.get_stock_bars(bar_request).df
            elif history_type == "trades":                            
                raise NotImplementedError("Historical trades not implemented yet")

            if not bars.empty:
                # Normalize the timezone handling
                if not isinstance(bars.index, pd.MultiIndex):
                    raise ValueError("Expected a MultiIndex for bars DataFrame")
                
                # Ensure all timestamps are in the correct timezone (America/New_York)
                timestamps = bars.index.get_level_values(1)
                symbols = bars.index.get_level_values(0)
                
                # Normalize the timestamps to NY timezone
                if timestamps.tz is None:
                    timestamps = pd.to_datetime(timestamps, utc=True)
                
                if timestamps.tz != pytz.timezone('America/New_York'):
                    timestamps = timestamps.tz_convert('America/New_York')
                    
                # Rebuild the index
                bars.index = pd.MultiIndex.from_arrays(
                    [symbols, timestamps],
                    names=bars.index.names
                )
                
                # Check existing data
                current_key = (asset, quote_asset)
                if current_key in self.pandas_data:
                    existing_data = self.pandas_data[current_key]
                    existing_df = existing_data.df
                    
                    # Ensure existing data has consistent timezone
                    if isinstance(existing_df.index, pd.MultiIndex):
                        existing_timestamps = existing_df.index.get_level_values(1)
                        existing_symbols = existing_df.index.get_level_values(0)
                        
                        # Normalize the existing timestamps to NY timezone
                        if existing_timestamps.tz != pytz.timezone('America/New_York'):
                            existing_timestamps = existing_timestamps.tz_convert('America/New_York')
                            
                            # Rebuild the index
                            existing_df.index = pd.MultiIndex.from_arrays(
                                [existing_symbols, existing_timestamps],
                                names=existing_df.index.names
                            )
                    
                    # Concatenate and deduplicate
                    combined_df = pd.concat([existing_df, bars])
                    combined_df = combined_df[~combined_df.index.duplicated(keep='last')]
                    combined_df = combined_df.sort_index(level=1)  # Sort by timestamp
                    bars = combined_df

                # Create Data object with the updated dataframe
                data = Data(asset, bars, timestep=timestep, quote=quote_asset)
                
                # Update pandas data store
                pandas_data_update = self._set_pandas_data_keys([data])
                self.pandas_data.update(pandas_data_update)
                
                # Manage storage limits
                if hasattr(self, 'MAX_STORAGE_BYTES') and self.MAX_STORAGE_BYTES:
                    self._enforce_storage_limit(self.pandas_data)
        except Exception as e:
            logging.error(f"Error fetching Alpaca data: {str(e)}")
            raise

    def _pull_source_symbol_bars(
        self,
        asset: Asset,
        length: int,
        timestep: str = "minute",
        timeshift: int = None,
        quote: Asset = None,
        exchange: str = None,
        include_after_hours: bool = True,
    ):
        """Retrieve historical bars from Alpaca"""
        # Get the current datetime and calculate the start datetime
        current_dt = self.get_datetime()

        self._update_pandas_data(asset, quote, length, timestep, current_dt)
        return super()._pull_source_symbol_bars(
            asset, length, timestep, timeshift,
            quote, exchange, include_after_hours
        )

    def get_historical_prices_between_dates(
        self,
        asset,
        timestep="minute",
        quote=None,
        exchange=None,
        include_after_hours=True,
        start_date=None,
        end_date=None,
        is_benchmark_asset=False
    ):
        """Get historical prices within specific date range"""
        try:
            # If both start and end dates are provided, calculate appropriate length
            if is_benchmark_asset:
                # Calculate length based on timestep
                if timestep == "minute":
                    # Convert time difference to minutes
                    length = int((end_date - start_date).total_seconds() / 60)
                elif timestep == "hour":
                    # Convert time difference to hours
                    length = int((end_date - start_date).total_seconds() / 3600)
                elif timestep == "day":
                    # Calculate number of days
                    length = (end_date - start_date).days
                else:
                    # Default to a reasonable value for unknown timesteps
                    length = 100
                    
                # Ensure minimum length for sufficient data
                length = max(length, 1)

                # Update pandas data with proper parameters
                self._update_pandas_data(
                    asset=asset,
                    quote=quote,
                    length=length,
                    timestep=timestep,
                    start_dt=start_date,
                    is_benchmark=is_benchmark_asset

                )
            else:
            # If dates aren't provided, just get the latest data
                self._update_pandas_data(
                    asset=asset,
                    quote=quote,
                    length=1,  # Get just 1 bar for context
                    timestep=timestep,
                )

            # Call the parent method to pull the data between dates
            response = super()._pull_source_symbol_bars_between_dates(
                asset, timestep, quote, exchange,
                include_after_hours, start_date, end_date,
                is_benchmark_asset=is_benchmark_asset
            )
            
            if response is None:
                return None

            bars = self._parse_source_symbol_bars(response, asset, quote=quote)
            return bars
        except Exception as e:
            logging.error(f"Error in get_historical_prices_between_dates: {e}")
            # Return empty DataFrame with proper columns as fallback
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

    def get_last_price(self,
                        asset,
                        timestep=None,
                        quote=None,
                        exchange="NYSE") -> Union[float, float, None]:
            """
            Get the latest price from Alpaca, adjusted for trading days.
            
            Parameters
            ----------
            asset : Asset object or str
                Asset object for which the last closed price will be
                retrieved.
            timestep: str 
                The time granularity (e.g., "minute").
            quote : Asset object
                Quote asset object for which the last closed price will be
                retrieved. This is required for cryptocurrency pairs.
            exchange : str
                Exchange name for which the last closed price will be
                retrieved. This is required for some cryptocurrency pairs. Default is "NYSE".
            
            Returns
            -------
                float or None: The last price, or None if no price is available.
            """
            try:
                dt = self.get_datetime()

                if timestep is None and self.MIN_TIMESTEP is not None:
                    timestep = self.MIN_TIMESTEP

                # Use a buffer to ensure we have enough historical data
                self._update_pandas_data(asset, quote, length=1, timestep=timestep, start_dt=dt)

                # Get closest available price
                return super().get_last_price(
                    asset=asset,
                    # timestep=timestep,
                    quote=quote,
                    exchange=exchange
                )
            except Exception as e:
                logging.error(f"Error in get_last_price: {e}")
                return None