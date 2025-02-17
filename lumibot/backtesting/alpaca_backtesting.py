from loguru import logger as logging
from decimal import Decimal
from typing import Union

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame
from lumibot.data_sources import PandasData
from lumibot.entities import Asset, Data


class AlpacaDataBacktesting(PandasData):
    """
    Backtesting implementation using Alpaca Markets API
    """
    
    def __init__(
        self,
        datetime_start,
        datetime_end,
        pandas_data=None,
        api_key=None,
        secret_key=None,
        max_memory=None,
        **kwargs,
    ):
        super().__init__(
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            pandas_data=pandas_data,
            **kwargs
        )
        
        # Alpaca API configuration
        self._api_key    = api_key
        self._secret_key = secret_key
        self.max_memory  = max_memory
        
        # Initialize Alpaca clients
        self.historical_client = StockHistoricalDataClient(
            self._api_key, 
            self._secret_key,
            # url_override="https://data.sandbox.alpaca.markets/v2/"
        )
        
    def _get_alpaca_timeframe(self, timestep):
        """Convert Lumibot timestep to Alpaca TimeFrame"""
        timeframe_map = {
            'minute': TimeFrame.Minute,
            'hour': TimeFrame.Hour,
            'day': TimeFrame.Day
        }
        return timeframe_map.get(timestep, TimeFrame.Minute)

    def _update_pandas_data(self, asset, quote, length, timestep, start_dt=None):
        """Fetch and update data from Alpaca API"""
        try:
            # Convert to Alpaca timeframe
            alpaca_tf = self._get_alpaca_timeframe(timestep)
            
            # Get historical bars
            bars = self.historical_client.get_stock_bars(
                symbol=asset.symbol,
                timeframe=alpaca_tf,
                start=start_dt,
                end=self.datetime_end,
                adjustment='all'
            ).df
            
            if not bars.empty:
                # Convert to Lumibot Data format
                data = Data(
                    asset,
                    bars,
                    timestep=timestep,
                    quote=quote
                )
                self.pandas_data[asset] = data
                
        except Exception as e:
            logging.error(f"Error fetching Alpaca data: {str(e)}")
            raise

    def _pull_source_symbol_bars(
        self,
        asset: Asset,
        length: int,
        timestep: str = "day",
        timeshift: int = None,
        quote: Asset = None,
        exchange: str = None,
        include_after_hours: bool = True,
    ):
        """Retrieve historical bars from Alpaca"""
        self._update_pandas_data(asset, quote, length, timestep)
        return super()._pull_source_symbol_bars(
            asset, length, timestep, timeshift, quote, exchange, include_after_hours
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
    ):
        """Get historical prices within specific date range"""
        self._update_pandas_data(asset, quote, 1, timestep)
        return super()._pull_source_symbol_bars_between_dates(
            asset, timestep, quote, exchange, include_after_hours, start_date, end_date
        )

    def get_last_price(
        self, 
        asset, 
        timestep="minute", 
        quote=None, 
        exchange=None, 
        **kwargs
    ) -> Union[float, Decimal, None]:
        """Get latest price from Alpaca"""
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=asset.symbol)
            quote = self.historical_client.get_stock_latest_quote(request)
            return Decimal(str(quote[asset.symbol].ask_price))
        except Exception as e:
            logging.error(f"Error getting last price: {str(e)}")
            return super().get_last_price(asset=asset, quote=quote, exchange=exchange)
