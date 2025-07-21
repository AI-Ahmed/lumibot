import os
from typing import Optional

import pytz
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN

import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from lumibot.tools.lumibot_logger import get_logger
from lumibot.data_sources import DataSourceBacktesting, AlpacaData
from lumibot.entities import Asset, Bars
from lumibot import (
    LUMIBOT_CACHE_FOLDER,
)
from lumibot.tools.helpers import (
    date_n_trading_days_from_date,
    get_trading_days,
    get_trading_times,
    get_timezone_from_datetime,
    get_decimals,
    quantize_to_num_decimals,
)

logger = get_logger(__name__)

from lumibot.tools.alpaca_helpers import sanitize_base_and_quote_asset


class AlpacaBacktesting(DataSourceBacktesting):
    SOURCE = "ALPACA"
    MIN_TIMESTEP = "minute"
    TIMESTEP_MAPPING = [
        {"timestep": "day", "representations": [TimeFrame.Day]},
        {"timestep": "minute", "representations": [TimeFrame.Minute]},
    ]
    LUMIBOT_DEFAULT_QUOTE_ASSET = AlpacaData.LUMIBOT_DEFAULT_QUOTE_ASSET

    def __init__(
            self,
            datetime_start: datetime | None = None,
            datetime_end: datetime | None = None,
            backtesting_started: datetime | None = None,
            config: dict | None = None,
            api_key: str | None = None,
            show_progress_bar: bool = True,
            delay: int | None = None,
            pandas_data: dict | list = None,
            **kwargs
    ):
        """Initialize AlpacaBacktesting instance for backtesting with Alpaca data.
        
        Parameters
        ----------
        datetime_start : datetime, optional
            The starting datetime for the backtesting period (inclusive).
            Must be timezone-aware.
        datetime_end : datetime, optional
            The ending datetime for the backtesting period (inclusive).
            Must be timezone-aware and have the same tzinfo as datetime_start.
        backtesting_started : datetime, optional
            The datetime when backtesting was initiated.
        config : dict, required
            Configuration dictionary containing API keys and account details.
            Must include either OAuth token or API key/secret for authentication.
        api_key : str, optional
            API key for data access (typically included in config).
        show_progress_bar : bool, default True
            Whether to display a progress bar during data operations.
        delay : int, optional
            Delay in seconds between operations to simulate real-world activity.
        pandas_data : dict or list, optional
            Data to be loaded directly into pandas for analysis without API calls.
        **kwargs : dict
            Additional keyword arguments:
            
            - timestep : {'day', 'minute'}, default 'day'
              Interval for data sampling.
            - refresh_cache : bool, default False
              Whether to force cache refresh.
            - warm_up_trading_days : int, default 0
              Number of trading days for warm-up before the primary dataset.
            - market : str, default 'NASDAQ'
              Stock exchange or market identifier.
            - auto_adjust : bool, default True
              Whether to auto-adjust data for stock splits and dividends.
            - remove_incomplete_current_bar : bool, default False
              Whether to remove incomplete current bar from data.
              
        Raises
        ------
        ValueError
            If config is None, lacks valid paper account setup, or if
            datetime_start and datetime_end have different timezone info.
            
        Notes
        -----
        This class handles backtesting data and parameters for Alpaca data source.
        It sets up configurations, verifies account types, and prepares backtesting
        timings, timezones, and historical data clients.
        
        The backtesting period includes warm-up days before the actual start date
        to ensure strategies have sufficient historical data for initialization.
        """
        self._datetime = None

        # Call the base class.
        super().__init__(
            datetime_start=datetime_start,
            datetime_end=datetime_end,
            backtesting_started=backtesting_started,
            show_progress_bar=show_progress_bar,
            delay=delay,
            pandas_data=None,
        )

        self.market = (
                kwargs.get("market", None)
                or (config.get("MARKET") if config else None)
                or os.environ.get("MARKET")
                or "NASDAQ"
        )

        self._timestep: str = kwargs.get('timestep', 'day')
        warm_up_trading_days: int = kwargs.get('warm_up_trading_days', 0)

        self._auto_adjust: bool = kwargs.get('auto_adjust', True)
        self.CACHE_SUBFOLDER = 'alpaca'
        self._data_store: dict[str, pd.DataFrame] = {}
        self._refreshed_keys = {}
        self._refresh_cache: bool = kwargs.get('refresh_cache', False)
        self._remove_incomplete_current_bar = kwargs.get('remove_incomplete_current_bar', False)

        if config is None:
            raise ValueError("Config cannot be None. Please provide a valid configuration.")
        if not config.get("PAPER", True):
            raise ValueError("Backtesting is restricted to paper accounts. Pass in a paper account config.")

        # Initialize clients based on available authentication method
        oauth_token = config.get("OAUTH_TOKEN")
        api_key = config.get("API_KEY")
        api_secret = config.get("API_SECRET")
        
        if oauth_token:
            self._crypto_client = CryptoHistoricalDataClient(oauth_token=oauth_token)
            self._stock_client = StockHistoricalDataClient(oauth_token=oauth_token)
        elif api_key and api_secret:
            self._crypto_client = CryptoHistoricalDataClient(
                api_key=api_key,
                secret_key=api_secret
            )
            self._stock_client = StockHistoricalDataClient(
                api_key=api_key,
                secret_key=api_secret
            )
        else:
            raise ValueError("Either OAuth token or API key/secret must be provided for Alpaca authentication")

        # Create an AlpacaData instance for internal use
        self._alpaca_data = AlpacaData(config)

        # Ensure datetime_start and datetime_end have the same tzinfo
        if str(datetime_start.tzinfo) != str(datetime_end.tzinfo):
            raise ValueError("datetime_start and datetime_end must have the same tzinfo.")

        # Get timezone from datetime_start if it has one, otherwise use Lumibot default
        self.tzinfo = get_timezone_from_datetime(datetime_start)

        # We want self._data_datetime_start and self._data_datetime_end to be the start and end dates
        # of the data for the entire backtest including the warmup dates.

        # The start should be midnight.
        start_dt = datetime(
            year=datetime_start.year,
            month=datetime_start.month,
            day=datetime_start.day,
        )
        start_dt = self.tzinfo.localize(start_dt)  # Use localize instead of tzinfo in constructor

        # The end should be the last minute of the day.
        end_dt = datetime(
            year=datetime_end.year,
            month=datetime_end.month,
            day=datetime_end.day,
            hour=23,
            minute=59,
            second=59,
        )
        end_dt = self.tzinfo.localize(end_dt)  # Use localize instead of tzinfo in constructor

        if warm_up_trading_days > 0:
            warm_up_start_dt = date_n_trading_days_from_date(
                n_days=warm_up_trading_days,
                start_datetime=start_dt,
                market=self.market,
            )
            # Combine with a default time (midnight)
            warm_up_start_dt = datetime.combine(warm_up_start_dt, datetime.min.time())
            # Make it timezone-aware
            warm_up_start_dt = self.tzinfo.localize(warm_up_start_dt)
        else:
            warm_up_start_dt = start_dt

        self._data_datetime_start = warm_up_start_dt
        self._data_datetime_end = end_dt

        if self._timestep not in ['day', 'minute']:
            raise ValueError("Invalid timestep passed. Must be 'day' or 'minute'.")

        self._trading_days = get_trading_days(
            self.market,
            self._data_datetime_start,
            self._data_datetime_end + timedelta(days=1),  # end_date is exclusive in this function
            tzinfo=self.tzinfo
        )

        # I think lumibot's got a bug in the strategy_executor when backtesting daily strategies.
        # After the backtest is over, it calls on_market_close() which calls get_last_price.
        # So if you run the backtest until the last day of data, lumibot will crash when it tries to calculate
        # the portfolio value. To avoid that crash (and because im avoiding dealing with people complaining about
        # backtest behavior changing if i fix it) im just hacking this so the backtest ends before the data runs out.
        if self._timestep == 'day':
            end_shift = -3
        else:
            end_shift = -3

        # stop backtesting before the last trading date of the backtest
        # so there's one day of data the backtester has to calculate all its stuff.
        last_trading_day = self._trading_days.iloc[end_shift]['market_open']
        self.datetime_end = last_trading_day

        self.datetime_start = start_dt
        self._datetime = self.datetime_start

    def _sanitize_base_and_quote_asset(self, base_asset, quote_asset) -> tuple[Asset, Asset]:
        asset, quote = sanitize_base_and_quote_asset(base_asset, quote_asset)
        return asset, quote

    def get_last_price(
            self,
            asset: Asset,
            quote: Asset | None = None,
            exchange: str | None = None
    ) -> float | Decimal | None:
        """Get the last price for an asset.
        
        Parameters
        ----------
        asset : Asset
            The asset to get the price for.
        quote : Asset, optional
            The quote asset to price against. If None, uses the default quote asset.
        exchange : str, optional
            The exchange to get the price from (not used in backtesting).
            
        Returns
        -------
        float or Decimal or None
            The open price of the current bar, or None if no data is available.
            
        Notes
        -----
        In backtesting, this returns the open price of the current bar, which is 
        consistent with how market orders are filled in the backtesting broker.
        """

        asset, quote = self._sanitize_base_and_quote_asset(asset, quote)

        bars = self.get_historical_prices(
            asset=asset,
            length=1,  # Get one bar
            timestep=self._timestep,
            quote=quote,
            remove_incomplete_current_bar=False  # We want the incomplete bar (aka current bar) for get_last_price
        )

        if bars is None or bars.df.empty:
            return None

        # The backtesting_broker, fills market orders using the open price of the current bar, so
        # get_last_price should also return the open. (It would be weird to fill on the open but provide the close
        # as the last price). This approach works for daily and minute bars. For daily bars, this returns the open
        # price, even if now is 9:30 and the daily bar was indexed at 00:00. Thats the only weird thing. But it makes
        # sense. The open of the daily bar for stocks was not at 00:00. It was at 9:30 anyway.
        price = bars.df.iloc[0].open
        num_decimals = get_decimals(price)
        return quantize_to_num_decimals(price, num_decimals)

    def get_historical_prices(
            self,
            asset: Asset,
            length: int,
            timestep: str | None = None,
            timeshift: timedelta | None = None,
            quote: Asset | None = None,
            exchange: str | None = None,
            include_after_hours: bool = True,
            remove_incomplete_current_bar: Optional[bool] = None,
    ) -> Bars | None:
        """Get historical price bars for an asset.
        
        Parameters
        ----------
        asset : Asset
            The asset to get historical prices for.
        length : int
            The number of bars to retrieve. Must be positive.
        timestep : str, optional
            The time interval for bars. Either 'day' or 'minute'.
            If None, uses the default timestep set during initialization.
        timeshift : timedelta, optional
            Amount of time to shift the reference point backward.
            Example: timeshift=timedelta(days=7) gets data from 1 week ago.
        quote : Asset, optional
            The quote asset for pricing. If None, uses the default quote asset.
        exchange : str, optional
            The exchange to get data from (not used in backtesting).
        include_after_hours : bool, default True
            Whether to include after-hours data.
        remove_incomplete_current_bar : bool, optional
            Whether to remove the incomplete current bar from results.
            If None, uses the default setting from initialization.
            
        Returns
        -------
        Bars or None
            A Bars object containing the historical price data.
            
        Raises
        ------
        ValueError
            If length is not positive, or if not enough historical data is available.
            
        Notes
        -----
        This is a higher-level method that returns a normalized `Bars` object with
        consistent format across data sources. It handles timezone conversions and
        includes additional metadata processing.
        """
        if length <= 0:
            raise ValueError("Length must be positive.")

        # Default values for arguments
        if remove_incomplete_current_bar is None:
            remove_incomplete_current_bar = self._remove_incomplete_current_bar

        if timestep is None:
            timestep = self._timestep

        if quote is None:
            quote = self.LUMIBOT_DEFAULT_QUOTE_ASSET

        # Determine search target datetime
        search_datetime = self._datetime
        if timeshift:
            search_datetime = self._datetime - timeshift

        try:
            # Fetch historical prices during the backtest using the dedicated function
            df = self.get_historical_prices_between_dates(
                base_asset=asset,
                quote_asset=quote,
                timestep=timestep,
                data_datetime_start=self._data_datetime_start,
                data_datetime_end=self._data_datetime_end,
                auto_adjust=self._auto_adjust
            )
        except Exception as e:
            # Handle errors if fetching data fails
            raise RuntimeError(f"Unable to fetch historical prices during backtest: {e}")

        # Ensure sufficient bars are available
        if length > len(df):
            raise ValueError(
                f"Not enough historical data. Requested {length} bars but only {len(df)} available."
            )

        # Adjust the search based on timestep
        if timestep == 'day':
            # For daily bars
            search_date = search_datetime.date()
            dates = df.index.date
            current_index = dates.searchsorted(search_date)

            # Adjust for incomplete current bar
            if remove_incomplete_current_bar and current_index > 0 and dates[current_index] == search_date:
                current_index -= 1
        else:
            # For minute bars
            current_index = df.index.searchsorted(search_datetime)

            # Adjust for incomplete current bar
            if remove_incomplete_current_bar and current_index > 0 and df.index[current_index] == search_datetime:
                current_index -= 1

        # Handle data retrieval and slicing
        if current_index < 0:
            raise ValueError(f"Datetime {search_datetime} not found in the dataset.")

        if current_index >= len(df):
            raise ValueError(f"Datetime {search_datetime} exceeds the dataset range.")

        if length == 1:
            result_df = df.iloc[[current_index]]
        else:
            result_df = df.iloc[max(0, current_index - length + 1): current_index + 1]

        return Bars(result_df, self.SOURCE, asset=asset, quote=quote)

    def get_chains(self, asset, quote=None):
        """Get option chains for an asset.
        
        Parameters
        ----------
        asset : Asset
            The underlying asset for which to get option chains.
        quote : Asset, optional
            The quote asset for pricing.
            
        Returns
        -------
        dict
            Empty dictionary as options are not supported in backtesting.
            
        Notes
        -----
        This is a mock implementation as option chains are not supported
        in the AlpacaBacktesting class.
        """
        return {}

    def _get_asset_key(
            self,
            *,
            base_asset: Asset,
            quote_asset: Asset,
            timestep: str = None,
            market: str = None,
            tzinfo: pytz.tzinfo = None,
            data_datetime_start: datetime = None,
            data_datetime_end: datetime = None,
            auto_adjust: bool = None,
    ) -> str:
        """Generate a unique key for asset data identification.
        
        Parameters
        ----------
        base_asset : Asset
            Base asset of the trading pair.
        quote_asset : Asset, optional
            Quote asset of the trading pair. If None, uses default quote asset.
        timestep : str, optional
            Time interval for data. Either 'day' or 'minute'.
        market : str, optional
            Market or exchange identifier.
        tzinfo : pytz.tzinfo, optional
            Timezone information for the data.
        data_datetime_start : datetime, optional
            Start date of the data for backtesting.
        data_datetime_end : datetime, optional
            End date of the data for backtesting (inclusive).
        auto_adjust : bool, optional
            Whether auto-adjustment is applied to the data.
            
        Returns
        -------
        str
            A unique string key for identifying and caching the asset data.
            
        Raises
        ------
        ValueError
            If base_asset is None or if timestep is invalid.
        """

        if base_asset is None:
            raise ValueError("Base asset must be provided.")

        if quote_asset is None:
            quote_asset = self.LUMIBOT_DEFAULT_QUOTE_ASSET

        if market is None:
            market = self.market

        if data_datetime_start is None:
            data_datetime_start = self._data_datetime_start

        if data_datetime_end is None:
            data_datetime_end = self._data_datetime_end

        if tzinfo is None:
            tzinfo = self.tzinfo

        if auto_adjust is None:
            auto_adjust = self._auto_adjust

        if timestep is None:
            timestep = self._timestep

        if timestep not in ['day', 'minute']:
            raise ValueError(f"Invalid timestep {timestep}. Must be 'day' or 'minute'.")

        base_quote = f"{base_asset.symbol}-{base_asset.asset_type}_{quote_asset.symbol}-{quote_asset.asset_type}"
        market = market
        tzinfo_str = str(tzinfo).replace("_", "-")
        start_date_str = data_datetime_start.strftime("%Y-%m-%d")
        end_date_str = data_datetime_end.strftime("%Y-%m-%d")
        auto_adjust_str = "AA" if auto_adjust else ""

        key_parts = [
            base_quote, market, timestep, tzinfo_str,
            auto_adjust_str, start_date_str, end_date_str
        ]
        key = "_".join(part for part in key_parts if part).upper()
        key = key.replace("/", "-")
        return key

    def _download_and_cache_ohlcv_data(
            self,
            *,
            base_asset: Asset = None,
            quote_asset: Asset = None,
            timestep: str = None,
            market: str = None,
            tzinfo: pytz.tzinfo = None,
            data_datetime_start: datetime = None,
            data_datetime_end: datetime = None,
            auto_adjust: bool = None,
    ) -> pd.DataFrame:
        """Download and cache OHLCV data for an asset.
        
        Parameters
        ----------
        base_asset : Asset
            Base asset of the trading pair.
        quote_asset : Asset
            Quote asset of the trading pair.
        timestep : str
            Time interval for data. Either 'day' or 'minute'.
        market : str
            Market or exchange identifier.
        tzinfo : pytz.tzinfo
            Timezone information for the data.
        data_datetime_start : datetime
            Start date of the data for backtesting.
        data_datetime_end : datetime
            End date of the data for backtesting (inclusive).
        auto_adjust : bool
            Whether auto-adjustment is applied to the data.
            
        Returns
        -------
        pandas.DataFrame
            DataFrame containing the downloaded OHLCV data.
            
        Raises
        ------
        ValueError
            If any required parameter is None.
        RuntimeError
            If data fetching fails or no data is returned.
            
        Notes
        -----
        This method handles both crypto and stock data through the appropriate
        Alpaca client. It downloads data, processes it, and saves it to the cache
        directory for future use.
        """
        if base_asset is None:
            raise ValueError("The parameter 'base_asset' cannot be None.")
        if quote_asset is None:
            raise ValueError("The parameter 'quote_asset' cannot be None.")
        if timestep is None:
            raise ValueError("The parameter 'timestep' cannot be None.")
        if market is None:
            raise ValueError("The parameter 'market' cannot be None.")
        if tzinfo is None:
            raise ValueError("The parameter 'tzinfo' cannot be None.")
        if data_datetime_start is None:
            raise ValueError("The parameter 'data_datetime_start' cannot be None.")
        if data_datetime_end is None:
            raise ValueError("The parameter 'data_datetime_end' cannot be None.")
        if auto_adjust is None:
            raise ValueError("The parameter 'auto_adjust' cannot be None.")

        key = self._get_asset_key(
            base_asset=base_asset,
            quote_asset=quote_asset,
            timestep=timestep,
            market=market,
            tzinfo=tzinfo,
            data_datetime_start=data_datetime_start,
            data_datetime_end=data_datetime_end,
            auto_adjust=auto_adjust,
        )

        # Directory to save cached data.
        cache_dir = os.path.join(LUMIBOT_CACHE_FOLDER, self.CACHE_SUBFOLDER)
        os.makedirs(cache_dir, exist_ok=True)

        # File path based on the unique key
        filename = f"{key}.csv"
        filepath = os.path.join(cache_dir, filename)

        logger.info(f"Fetching and caching data for {key}")

        if base_asset.asset_type == 'crypto':
            client = self._crypto_client

            symbol = base_asset.symbol + '/' + quote_asset.symbol

            # noinspection PyArgumentList
            request_params = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=self._parse_source_timestep(timestep, reverse=True),
                start=data_datetime_start,
                end=data_datetime_end + timedelta(days=1),  # alpaca end dates are exclusive
            )
        else:
            client = self._stock_client
            adjustment = 'all' if auto_adjust else 'split'

            # noinspection PyArgumentList
            request_params = StockBarsRequest(
                symbol_or_symbols=base_asset.symbol,
                timeframe=self._parse_source_timestep(timestep, reverse=True),
                start=data_datetime_start,
                end=data_datetime_end + timedelta(days=1),  # alpaca end dates are exclusive,
                adjustment=adjustment,
            )

        try:
            if isinstance(request_params, CryptoBarsRequest):
                bars = client.get_crypto_bars(request_params)
            else:
                bars = client.get_stock_bars(request_params)
        except Exception as e:
            raise RuntimeError(f"Failed to fetch data for {key}: {e}")

        df = bars.df.reset_index()
        if df.empty:
            raise RuntimeError(f"No data fetched for {key}.")

        # Ensure 'timestamp' is a pandas timestamp object
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize(tzinfo)
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert(tzinfo)

        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        trading_times = get_trading_times(
            pcal=self._trading_days,
            timestep=timestep,
        )

        # Reindex the dataframe with a row for each bar we should have a trading iteration for.
        # Fill any empty bars with previous data.
        df = self._reindex_and_fill(df=df, trading_times=trading_times, timestep=timestep)

        # Filter data to include only rows between data_datetime_start and data_datetime_end
        df = df[(df['timestamp'] >= data_datetime_start) & (df['timestamp'] <= data_datetime_end)]

        # Save to cache
        df.to_csv(filepath, index=False)

        # Store in _data_store
        df.set_index('timestamp', inplace=True)
        self._data_store[key] = df
        logger.info(f"Finished fetching and caching data for {key}")
        return df

    def _load_ohlcv_into_data_store(self, key: str) -> bool:
        """Load OHLCV data from cache into the data store.
        
        Parameters
        ----------
        key : str
            Unique key identifying the cached data file.
            
        Returns
        -------
        bool
            True if data was successfully loaded, False otherwise.
            
        Notes
        -----
        This method attempts to load previously cached OHLCV data from a CSV file
        into the internal data store. It handles timezone conversion to ensure
        consistency with the configured timezone.
        
        If the file doesn't exist or cannot be properly loaded, the method
        returns False, indicating that the data needs to be downloaded.
        """
        # Directory to find the cached data file.
        cache_dir = os.path.join(LUMIBOT_CACHE_FOLDER, self.CACHE_SUBFOLDER)
        filename = f"{key}.csv"
        filepath = os.path.join(cache_dir, filename)

        # Check if the file exists
        if not os.path.exists(filepath):
            return False

        try:
            # Read CSV file with 'timestamp' column parsed as dates
            df = pd.read_csv(filepath, parse_dates=['timestamp'])

            # Convert timestamp column to datetime objects, interpreting them as UTC times
            # utc=True ensures proper handling of timezone-aware data
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

            # Convert timestamps from UTC to the timezone specified in self.tzinfo
            # For example: if self.tzinfo is 'America/New_York', converts UTC times to NY time
            df['timestamp'] = df['timestamp'].dt.tz_convert(self.tzinfo)

            df.set_index('timestamp', inplace=True)
            self._data_store[key] = df
            logger.info(f"Loaded cached data for key: {key} from cache.")
            return True
        except Exception as e:
            logger.error(f"Failed to load cached data for key: {key}. Error: {e}")
            return False

    def get_historical_prices_between_dates(
            self,
            *,
            base_asset: Asset = None,
            quote_asset: Asset = None,
            timestep: str = None,
            market: str = None,
            tzinfo: pytz.tzinfo = None,
            data_datetime_start: datetime = None,
            data_datetime_end: datetime = None,
            auto_adjust: bool = None,
    ) -> pd.DataFrame:
        """Get historical price data between specified dates.
        
        Parameters
        ----------
        base_asset : Asset
            Base asset of the trading pair.
        quote_asset : Asset, optional
            Quote asset of the trading pair. If None, uses default quote asset.
        timestep : str, optional
            Time interval for data. Either 'day' or 'minute'.
            If None, uses the default timestep.
        market : str, optional
            Market or exchange identifier.
            If None, uses the default market.
        tzinfo : pytz.tzinfo, optional
            Timezone information for the data.
            If None, uses the default timezone.
        data_datetime_start : datetime, optional
            Start date of the data for backtesting.
            If None, uses the default start date.
        data_datetime_end : datetime, optional
            End date of the data for backtesting (inclusive).
            If None, uses the default end date.
        auto_adjust : bool, optional
            Whether auto-adjustment is applied to the data.
            If None, uses the default setting.
            
        Returns
        -------
        pandas.DataFrame
            DataFrame containing the historical OHLCV data indexed by timestamp.
            
        Raises
        ------
        ValueError
            If base_asset is None.
            
        Notes
        -----
        This method either loads data from cache or downloads it if not available.
        It manages cache refreshing based on the refresh_cache setting.
        """

        if base_asset is None:
            raise ValueError("Base asset must be provided.")

        if quote_asset is None:
            quote_asset = self.LUMIBOT_DEFAULT_QUOTE_ASSET

        asset, quote = self._sanitize_base_and_quote_asset(base_asset, quote_asset)

        if timestep is None:
            timestep = self._timestep

        if market is None:
            market = self.market

        if tzinfo is None:
            tzinfo = self.tzinfo

        if data_datetime_start is None:
            data_datetime_start = self._data_datetime_start

        if data_datetime_end is None:
            data_datetime_end = self._data_datetime_end

        if auto_adjust is None:
            auto_adjust = self._auto_adjust

        key = self._get_asset_key(base_asset=asset, quote_asset=quote, timestep=timestep)

        if self._refresh_cache and key not in self._refreshed_keys:
            # If we need are refreshing cache and we didn't refresh this key's cache yet, refresh it.
            self._download_and_cache_ohlcv_data(
                base_asset=asset,
                quote_asset=quote,
                timestep=timestep,
                market=market,
                tzinfo=tzinfo,
                data_datetime_start=data_datetime_start,
                data_datetime_end=data_datetime_end,
                auto_adjust=auto_adjust
            )
            self._refreshed_keys[key] = True
        elif key not in self._data_store and not self._load_ohlcv_into_data_store(key):
            # If not refreshing or already refreshed, try to load from cache or download
            self._download_and_cache_ohlcv_data(
                base_asset=asset,
                quote_asset=quote,
                timestep=timestep,
                market=market,
                tzinfo=tzinfo,
                data_datetime_start=data_datetime_start,
                data_datetime_end=data_datetime_end,
                auto_adjust=auto_adjust
            )

        df = self._data_store[key]
        return df

    def _reindex_and_fill(
            self,
            df: pd.DataFrame,
            trading_times: pd.DatetimeIndex,
            timestep: str
    ) -> pd.DataFrame:
        """Reindex and fill missing data in OHLCV DataFrame.
        
        Parameters
        ----------
        df : pandas.DataFrame
            DataFrame containing OHLCV data with columns: timestamp, open, high, low, close, volume.
        trading_times : pandas.DatetimeIndex
            DatetimeIndex containing all trading times that should be included.
        timestep : str
            Time interval for data. Either 'day' or 'minute'.
            
        Returns
        -------
        pandas.DataFrame
            Reindexed DataFrame with filled missing values.
            
        Raises
        ------
        ValueError
            If required columns are missing or timestep is invalid.
            
        Notes
        -----
        This method ensures the DataFrame has entries for all required trading times.
        For daily bars, it preserves original timestamps but adds missing days.
        For minute bars, it reindexes to include all trading minutes.
        
        Missing values are filled using the following rules:
        - Missing volume values are filled with 0.0
        - Missing close prices are forward-filled
        - Missing open/high/low prices are filled with close prices
        - Any remaining missing open prices are backward-filled
        - Any remaining missing high/low/close prices are filled with open prices
        """
        if df.index.name == 'timestamp':
            df = df.reset_index()

        # Check if all required columns are present
        required_columns = {"timestamp", "open", "high", "low", "close", "volume"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            raise ValueError(f"The dataframe is missing the following required columns: {', '.join(missing_columns)}")

        if timestep not in ['day', 'minute']:
            raise ValueError(f"The timestep must be 'day' or 'minute'.")

        # For daily bars, we want to preserve original timestamps but add missing days
        if timestep == 'day':
            # Get just the dates from trading_times
            trading_dates = trading_times.date
            # Get dates from df timestamps
            df_dates = df['timestamp'].dt.date

            # Convert both to sets of dates for proper comparison
            trading_dates_set = set(trading_dates)
            df_dates_set = set(df_dates)

            # Find truly missing dates
            missing_dates = trading_dates_set - df_dates_set

            # Add rows for missing dates (at midnight)
            for date in missing_dates:
                # Get timezone from the first timestamp in df
                tz = df['timestamp'].iloc[0].tz

                missing_row = pd.DataFrame({
                    'timestamp': [pd.Timestamp(date).tz_localize(tz)],
                    'open': [None],
                    'high': [None],
                    'low': [None],
                    'close': [None],
                    'volume': [0.0]
                })

                # Remove any all-NA columns from `missing_row`
                missing_row = missing_row.dropna(axis=1, how='all')

                # Proceed with the concatenation
                df = pd.concat([df, missing_row], ignore_index=True)

            # Sort by timestamp
            df.sort_values('timestamp', inplace=True)
        else:
            # For non-daily bars, use the original reindexing logic
            if df.index.name != "timestamp":
                # Ensure timestamp is the index for reindexing
                df = df.set_index("timestamp")
            df = df.reindex(trading_times)
            df.index.name = 'timestamp'  # Restore the index name
            df.sort_values('timestamp', inplace=True)
            df.reset_index(inplace=True)

        # Fill missing volume values with 0.0
        df['volume'] = df['volume'].fillna(0.0)

        # Forward fill missing close prices
        df['close'] = df['close'].ffill()

        # Fill missing open, high, low with close prices
        for column in ['open', 'high', 'low']:
            df[column] = df[column].fillna(df['close'])

        # Backward fill remaining missing open prices
        df['open'] = df['open'].bfill()

        # Fill any remaining missing high, low, close with open prices
        for column in ['high', 'low', 'close']:
            df[column] = df[column].fillna(df['open'])

        return df
