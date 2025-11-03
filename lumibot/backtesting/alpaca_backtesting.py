import os
import hashlib
import signal
import atexit
import threading
import bisect
import numpy as np
from typing import Optional
from collections import OrderedDict
from contextlib import contextmanager

import pytz
from datetime import datetime, timedelta
from decimal import Decimal

import pandas as pd
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest, StockTradesRequest
from alpaca.data.timeframe import TimeFrame

from lumibot.tools.lumibot_logger import get_logger
from lumibot.data_sources import DataSourceBacktesting, AlpacaData
from lumibot.entities import Asset, Bars, AssetsMapping
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

try:
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError:
    # Fallback if tenacity is not installed
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator
    stop_after_attempt = wait_exponential = retry_if_exception_type = None

try:
    from fpap.data.utils.data_processing import data_prep
except ImportError:
    data_prep = None

logger = get_logger(__name__)

from lumibot.tools.alpaca_helpers import sanitize_base_and_quote_asset
from lumibot.credentials import (
    ALPACA_CONFIG,
    DEFAULT_END_SHIFT_DAYS,
    DEFAULT_END_SHIFT_MINUTES,
    MIN_TRADING_DAYS_FOR_SHIFT,
    HFT_END_TIME_BUFFER_MINUTES,
    DEFAULT_CACHE_MAX_MEMORY_MB,
    DEFAULT_API_RETRY_ATTEMPTS,
    DEFAULT_API_RETRY_MIN_WAIT,
    DEFAULT_API_RETRY_MAX_WAIT,
    DATA_QUALITY_DROP_THRESHOLD,
    CACHE_VERSION,
)


class BoundedDataCache:
    """LRU cache with memory limit to prevent unbounded growth.
    
    Parameters
    ----------
    max_memory_mb : int, default 1000
        Maximum memory to use in megabytes.
    
    Notes
    -----
    This cache implements an LRU (Least Recently Used) eviction policy
    with memory-based limits to prevent memory exhaustion during long
    backtests with many assets.
    """
    
    def __init__(self, max_memory_mb: int = DEFAULT_CACHE_MAX_MEMORY_MB):
        self._cache = OrderedDict()
        self._max_bytes = max_memory_mb * 1024 * 1024
        self._current_bytes = 0
        self._lock = threading.Lock()
        
    def get(self, key: str) -> Optional[pd.DataFrame]:
        """Get item from cache (thread-safe).
        
        Parameters
        ----------
        key : str
            Cache key.
            
        Returns
        -------
        pd.DataFrame or None
            Cached DataFrame or None if not found.
        """
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)  # Mark as recently used
                return self._cache[key]
            return None
    
    def set(self, key: str, df: pd.DataFrame) -> None:
        """Store item in cache with LRU eviction (thread-safe).
        
        Parameters
        ----------
        key : str
            Cache key.
        df : pd.DataFrame
            DataFrame to cache.
            
        Notes
        -----
        Automatically evicts least recently used items if memory limit is exceeded.
        """
        with self._lock:
            df_bytes = df.memory_usage(deep=True).sum()
            
            # Evict LRU items until we have space
            while self._current_bytes + df_bytes > self._max_bytes and self._cache:
                evict_key, evict_df = self._cache.popitem(last=False)
                evict_bytes = evict_df.memory_usage(deep=True).sum()
                self._current_bytes -= evict_bytes
                logger.info(f"Evicted {evict_key} from cache ({evict_bytes / 1024 / 1024:.2f} MB)")
            
            # Remove old value if key exists
            if key in self._cache:
                old_bytes = self._cache[key].memory_usage(deep=True).sum()
                self._current_bytes -= old_bytes
                
            self._cache[key] = df
            self._current_bytes += df_bytes
            
    def contains(self, key: str) -> bool:
        """Check if key exists in cache (thread-safe).
        
        Parameters
        ----------
        key : str
            Cache key to check.
            
        Returns
        -------
        bool
            True if key exists, False otherwise.
        """
        with self._lock:
            return key in self._cache
            
    def clear(self) -> None:
        """Clear all cached data (thread-safe)."""
        with self._lock:
            self._cache.clear()
            self._current_bytes = 0
            logger.info("Cache cleared")
            
    def get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB.
        
        Returns
        -------
        float
            Current memory usage in megabytes.
        """
        with self._lock:
            return self._current_bytes / 1024 / 1024


class DataQualityTracker:
    """Track data quality metrics during backtesting.
    
    Notes
    -----
    Collects metrics on data integrity issues like invalid trades,
    missing data, and dropped records to alert users of potential issues.
    """
    
    def __init__(self):
        self._metrics = {
            'total_records': 0,
            'dropped_records': 0,
            'invalid_prices': 0,
            'invalid_sizes': 0,
            'duplicate_records': 0,
        }
        self._lock = threading.Lock()
        
    def record_data_load(self, total: int, dropped: int, reason: str = "unknown"):
        """Record data loading metrics.
        
        Parameters
        ----------
        total : int
            Total records loaded.
        dropped : int
            Number of records dropped.
        reason : str, default "unknown"
            Reason for dropping records.
        """
        with self._lock:
            self._metrics['total_records'] += total
            self._metrics['dropped_records'] += dropped
            
            # Log warning if drop rate exceeds threshold
            if total > 0:
                drop_rate = dropped / total
                if drop_rate > DATA_QUALITY_DROP_THRESHOLD:
                    logger.warning(
                        f"Data quality issue: {drop_rate:.1%} of records dropped ({dropped}/{total}) "
                        f"due to {reason}. This may impact backtest accuracy."
                    )
    
    def record_invalid_data(self, invalid_prices: int = 0, invalid_sizes: int = 0, duplicates: int = 0):
        """Record invalid data occurrences.
        
        Parameters
        ----------
        invalid_prices : int, default 0
            Number of invalid price records.
        invalid_sizes : int, default 0
            Number of invalid size records.
        duplicates : int, default 0
            Number of duplicate records.
        """
        with self._lock:
            self._metrics['invalid_prices'] += invalid_prices
            self._metrics['invalid_sizes'] += invalid_sizes
            self._metrics['duplicate_records'] += duplicates
            
    def get_report(self) -> dict:
        """Get data quality report.
        
        Returns
        -------
        dict
            Dictionary containing all collected metrics.
        """
        with self._lock:
            report = self._metrics.copy()
            if report['total_records'] > 0:
                report['drop_rate'] = report['dropped_records'] / report['total_records']
            else:
                report['drop_rate'] = 0.0
            return report


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
        
        # Use bounded cache with configurable memory limit
        cache_max_memory_mb = kwargs.get('cache_max_memory_mb', DEFAULT_CACHE_MAX_MEMORY_MB)
        self._data_store = BoundedDataCache(max_memory_mb=cache_max_memory_mb)
        
        # Thread-safe refreshed keys tracking
        self._refreshed_keys = {}
        self._refreshed_keys_lock = threading.Lock()
        
        self._refresh_cache: bool = kwargs.get('refresh_cache', False)
        self._remove_incomplete_current_bar = kwargs.get('remove_incomplete_current_bar', False)
        
        # Data quality tracking
        self._data_quality_tracker = DataQualityTracker()
        
        # Graceful interruption support
        self._interrupted = False
        self._setup_signal_handlers()

        if config is None:
            config = ALPACA_CONFIG
            
        # Check if the required config exists in the passed dict
        if not config.get("PAPER", True):
            raise ValueError("Backtesting is restricted to paper accounts. Pass in a paper account config.")
        if not config.get("API_KEY") or not config.get("API_SECRET"):
            raise ValueError("API key and secret are required for Alpaca authentication.")

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
        
        # For HFT backtests or very short periods, we need to be more careful about end_shift
        trading_days_count = len(self._trading_days)
        
        if trading_days_count < MIN_TRADING_DAYS_FOR_SHIFT:
            # For very short periods (HFT), just set end time slightly before the actual end time
            # to ensure there's enough data for final calculations
            logger.info(
                f"Short backtesting period detected with only {trading_days_count} trading day(s). "
                f"Adjusting end time to ensure proper data availability for HFT."
            )
            
            # For minute data, set the end time a few minutes before the actual end
            if self._timestep == 'minute':
                # If we have at least one trading day
                if trading_days_count > 0:
                    # Get the last market close and set end time before that
                    last_market_close = self._trading_days.iloc[-1]['market_close']
                    self.datetime_end = last_market_close - timedelta(minutes=HFT_END_TIME_BUFFER_MINUTES)
                else:
                    # Very rare case - no trading days found
                    # Just use a time shortly before the requested end date
                    self.datetime_end = datetime_end - timedelta(minutes=HFT_END_TIME_BUFFER_MINUTES)
            else:
                # For day timestep, use the first trading day if we have only one or two
                if trading_days_count > 0:
                    self.datetime_end = self._trading_days.iloc[0]['market_open']
                else:
                    # Very rare case - no trading days found
                    self.datetime_end = datetime_start
        else:
            # Original logic for normal backtesting periods
            if self._timestep == 'day':
                end_shift = -DEFAULT_END_SHIFT_DAYS
            else:
                # For minute timestep (HFT), use a smaller end_shift to preserve more of the requested date range
                end_shift = -DEFAULT_END_SHIFT_MINUTES

            # Ensure end_shift is still within bounds (defensive programming)
            end_shift = max(-trading_days_count, end_shift)

            # stop backtesting before the last trading date of the backtest
            # so there's one day of data the backtester has to calculate all its stuff.
            last_trading_day = self._trading_days.iloc[end_shift]['market_open']
            self.datetime_end = last_trading_day
        
        self.datetime_start = start_dt
        self._datetime = self.datetime_start
        
        # Register cleanup on exit
        atexit.register(self._cleanup)

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful interruption.
        
        Notes
        -----
        Handles SIGINT (Ctrl+C) and SIGTERM to allow graceful shutdown
        and cleanup of resources.
        """
        def signal_handler(signum, frame):
            logger.warning(f"Received signal {signum}, initiating graceful shutdown...")
            self._interrupted = True
            self._cleanup()
            
        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except (ValueError, OSError) as e:
            # Some environments don't support signal handling
            logger.debug(f"Could not set up signal handlers: {e}")
            
    def _cleanup(self) -> None:
        """Clean up resources on exit.
        
        Notes
        -----
        Called automatically on exit or when interrupted. Clears cache
        and logs final data quality metrics.
        """
        try:
            # Log data quality report
            if hasattr(self, '_data_quality_tracker'):
                report = self._data_quality_tracker.get_report()
                if report['total_records'] > 0:
                    logger.info(f"Data Quality Report: {report}")
                    
            # Log cache statistics
            if hasattr(self, '_data_store'):
                memory_mb = self._data_store.get_memory_usage_mb()
                logger.info(f"Final cache memory usage: {memory_mb:.2f} MB")
                
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
            
    def _check_interrupted(self) -> None:
        """Check if operation was interrupted.
        
        Raises
        ------
        KeyboardInterrupt
            If the operation was interrupted by user or signal.
        """
        if self._interrupted:
            raise KeyboardInterrupt("Operation interrupted by user")

    def _sanitize_base_and_quote_asset(self, base_asset, quote_asset) -> tuple[Asset, Asset]:
        """Sanitize base and quote assets.
        
        Parameters
        ----------
        base_asset : Asset
            Base asset to sanitize.
        quote_asset : Asset
            Quote asset to sanitize.
            
        Returns
        -------
        tuple[Asset, Asset]
            Sanitized base and quote assets.
        """
        asset, quote = sanitize_base_and_quote_asset(base_asset, quote_asset)
        return asset, quote
        
    @contextmanager
    def _safe_file_operation(self, filepath: str, mode: str = 'r'):
        """Context manager for safe file operations.
        
        Parameters
        ----------
        filepath : str
            Path to file.
        mode : str, default 'r'
            File open mode.
            
        Yields
        ------
        file
            Open file handle.
            
        Notes
        -----
        Ensures files are properly closed even if exceptions occur.
        """
        f = None
        try:
            f = open(filepath, mode)
            yield f
        finally:
            if f is not None:
                f.close()
                
    def _compute_checksum(self, filepath: str) -> str:
        """Compute MD5 checksum of a file.
        
        Parameters
        ----------
        filepath : str
            Path to file.
            
        Returns
        -------
        str
            MD5 checksum as hexadecimal string.
        """
        hash_md5 = hashlib.md5()
        with self._safe_file_operation(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
        
    def _save_checksum(self, filepath: str) -> None:
        """Save checksum for a cached file.
        
        Parameters
        ----------
        filepath : str
            Path to cached file.
            
        Notes
        -----
        Saves checksum in a .md5 file alongside the data file.
        """
        try:
            checksum = self._compute_checksum(filepath)
            checksum_file = filepath + '.md5'
            with self._safe_file_operation(checksum_file, 'w') as f:
                f.write(checksum)
        except Exception as e:
            logger.warning(f"Failed to save checksum for {filepath}: {e}")
            
    def _validate_checksum(self, filepath: str) -> bool:
        """Validate checksum of a cached file.
        
        Parameters
        ----------
        filepath : str
            Path to cached file.
            
        Returns
        -------
        bool
            True if checksum is valid or doesn't exist, False if corrupted.
            
        Notes
        -----
        Returns True if no checksum file exists (for backwards compatibility).
        """
        checksum_file = filepath + '.md5'
        
        if not os.path.exists(checksum_file):
            # No checksum file, assume valid (backwards compatibility)
            return True
            
        try:
            # Read expected checksum
            with self._safe_file_operation(checksum_file, 'r') as f:
                expected = f.read().strip()
                
            # Compute actual checksum
            actual = self._compute_checksum(filepath)
            
            if actual != expected:
                logger.error(f"Checksum mismatch for {filepath}. Cache may be corrupted.")
                return False
                
            return True
            
        except Exception as e:
            logger.warning(f"Error validating checksum for {filepath}: {e}")
            return True  # Fail open for backwards compatibility

    def get_last_price(
            self,
            asset: Asset | list[Asset],
            quote: Asset | None = None,
            exchange: str | None = None
    ) -> float | Decimal | dict | None:
        """Get the last price for an asset or list of assets.
        
        Parameters
        ----------
        asset : Asset or list[Asset]
            The asset or list of assets to get the price for.
        quote : Asset, optional
            The quote asset to price against. If None, uses the default quote asset.
        exchange : str, optional
            The exchange to get the price from (not used in backtesting).
            
        Returns
        -------
        float or Decimal or dict or None
            If asset is a single Asset: The open price of the current bar, or None if no data is available.
            If asset is a list: A dictionary mapping each asset to its last price.
            
        Notes
        -----
        In backtesting, this returns the open price of the current bar, which is 
        consistent with how market orders are filled in the backtesting broker.
        """
        # Handle list of assets by calling get_last_prices
        if isinstance(asset, list):
            return self.get_last_prices(assets=asset, quote=quote, exchange=exchange)

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
        
    def get_last_prices(
            self,
            assets: list[Asset],
            quote: Asset | None = None,
            exchange: str | None = None
    ) -> dict:
        """Get the last prices for a list of assets.
        
        Parameters
        ----------
        assets : list[Asset]
            The list of assets to get prices for.
        quote : Asset, optional
            The quote asset to price against. If None, uses the default quote asset.
        exchange : str, optional
            The exchange to get the price from (not used in backtesting).
            
        Returns
        -------
        dict
            A dictionary mapping each asset to its last price.
            
        Notes
        -----
        This method is optimized for multiple assets in HFT scenarios.
        """
        result = {}
        
        # Process each asset individually
        for asset in assets:
            # Sanitize the asset and quote
            sanitized_asset, sanitized_quote = self._sanitize_base_and_quote_asset(
                asset, quote if quote is not None else self.LUMIBOT_DEFAULT_QUOTE_ASSET
            )
            
            # Get the price for this asset - call the internal method to avoid recursion
            bars = self.get_historical_prices(
                asset=sanitized_asset,
                length=1,  # Get one bar
                timestep=self._timestep,
                quote=sanitized_quote,
                remove_incomplete_current_bar=False  # We want the incomplete bar (aka current bar) for get_last_price
            )
            
            price = None
            if bars is not None and not bars.df.empty:
                price = bars.df.iloc[0].open
                num_decimals = get_decimals(price)
                price = quantize_to_num_decimals(price, num_decimals)
            
            # Store the result
            result[asset] = price
            
        return AssetsMapping(result)

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
        
        For high-frequency trading or feature engineering requiring raw trades data,
        use the ``get_historical_trades_between_dates`` method instead.
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

        # Adjust the search based on timestep using O(log n) binary search
        if timestep == 'day':
            # For daily bars - convert index to dates for comparison
            # Use numpy for faster date extraction (cached on first call)
            if not hasattr(df.index, '_cached_dates'):
                df.index._cached_dates = df.index.date
            dates = df.index._cached_dates
            search_date = search_datetime.date()
            
            # Use bisect for O(log n) search
            current_index = bisect.bisect_right(dates, search_date) - 1

            # Adjust for incomplete current bar
            if remove_incomplete_current_bar and current_index >= 0 and dates[current_index] == search_date:
                current_index -= 1
        else:
            # For minute bars - use bisect on timestamp index
            # Convert to comparable format for binary search
            timestamps = df.index.values
            search_ts = search_datetime.value if hasattr(search_datetime, 'value') else pd.Timestamp(search_datetime).value
            
            # Use numpy searchsorted for O(log n) search
            current_index = np.searchsorted(timestamps, search_ts, side='right') - 1

            # Adjust for incomplete current bar
            if remove_incomplete_current_bar and current_index >= 0:
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
        """Generate a unique key for asset data identification using SHA256 hash.
        
        Parameters
        ----------
        base_asset : Asset
            Base asset of the trading pair.
        quote_asset : Asset, optional
            Quote asset of the trading pair. If None, uses default quote asset.
        timestep : str, optional
            Time interval for data. Either 'day', 'minute', or 'fractional' (for trades data).
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
            A unique SHA256-based string key for identifying and caching the asset data.
            Format: {CACHE_VERSION}_{symbol}_{hash[:16]}
            
        Raises
        ------
        ValueError
            If base_asset is None or if timestep is invalid.
            
        Notes
        -----
        Uses SHA256 hashing to create short, collision-resistant cache keys.
        The cache version prefix allows for cache invalidation when format changes.
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

        if timestep not in ['day', 'minute', 'fractional', 'custom']:
            raise ValueError(f"Invalid timestep {timestep}. Must be 'day', 'minute', 'fractional' (for trades data), or 'custom' (for processed bars).")

        # Build key components for hashing
        base_quote = f"{base_asset.symbol}-{base_asset.asset_type}_{quote_asset.symbol}-{quote_asset.asset_type}"
        tzinfo_str = str(tzinfo).replace("_", "-")
        start_date_str = data_datetime_start.strftime("%Y-%m-%d-%H-%M-%S")
        end_date_str = data_datetime_end.strftime("%Y-%m-%d-%H-%M-%S")
        auto_adjust_str = "AA" if auto_adjust else "NA"

        # Create a deterministic string to hash
        key_components = f"{base_quote}|{market}|{timestep}|{tzinfo_str}|{auto_adjust_str}|{start_date_str}|{end_date_str}"
        
        # Generate SHA256 hash (take first 16 chars for readability)
        hash_obj = hashlib.sha256(key_components.encode('utf-8'))
        hash_str = hash_obj.hexdigest()[:16]
        
        # Create readable key with version, symbol, and hash
        symbol_safe = base_asset.symbol.replace("/", "-").replace("\\", "-")
        key = f"{CACHE_VERSION}_{symbol_safe}_{timestep}_{hash_str}"
        
        return key

    def _parse_source_timestep(self, timestep, reverse=False):
        """Transform the data source timestep variable into lumibot representation.
        
        Parameters
        ----------
        timestep : str
            The timestep to parse.
        reverse : bool, default False
            If True, convert from lumibot representation to source representation.
            If False, convert from source representation to lumibot representation.
            
        Returns
        -------
        str or TimeFrame
            The parsed timestep.
            
        Notes
        -----
        This method overrides the parent class method to handle the 'fractional' timestep
        used for trades data.
        """
        # Special handling for 'fractional' timestep (used for trades data)
        if timestep == "fractional":
            if reverse:
                # When reverse=True, we're converting from lumibot to source representation
                # For trades data, we don't have a specific TimeFrame, so return None
                # This is handled specially in get_historical_trades_between_dates
                return None
            else:
                # When reverse=False, we're converting from source to lumibot representation
                # This shouldn't happen for trades data, but return 'fractional' for consistency
                return "fractional"
        
        # For all other timesteps, use the parent class implementation
        for item in self.TIMESTEP_MAPPING:
            if reverse:
                if timestep == item["timestep"]:
                    return item["representations"][0]
            else:
                if timestep in item["representations"]:
                    return item["timestep"]

        # If we get here, the timestep is not supported
        raise ValueError(f"Unsupported timestep: {timestep}")
        
    def _api_call_with_retry(self, api_func, *args, **kwargs):
        """Execute API call with retry logic.
        
        Parameters
        ----------
        api_func : callable
            API function to call.
        *args : tuple
            Positional arguments for the API function.
        **kwargs : dict
            Keyword arguments for the API function.
            
        Returns
        -------
        Any
            Result from the API call.
            
        Raises
        ------
        Exception
            If all retry attempts fail.
            
        Notes
        -----
        Implements exponential backoff with jitter for API rate limiting.
        If tenacity is not installed, calls the function directly without retries.
        """
        if stop_after_attempt is not None:
            # tenacity is available, use retry logic
            @retry(
                stop=stop_after_attempt(DEFAULT_API_RETRY_ATTEMPTS),
                wait=wait_exponential(
                    multiplier=1,
                    min=DEFAULT_API_RETRY_MIN_WAIT,
                    max=DEFAULT_API_RETRY_MAX_WAIT
                ),
                reraise=True
            )
            def _execute_with_retry():
                self._check_interrupted()  # Check for interruption before API call
                try:
                    return api_func(*args, **kwargs)
                except Exception as e:
                    # Log the error for debugging
                    logger.warning(f"API call failed (will retry): {str(e)[:100]}")
                    raise
            
            return _execute_with_retry()
        else:
            # tenacity not available, call directly
            self._check_interrupted()
            return api_func(*args, **kwargs)

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
            # Use retry logic for API calls
            if isinstance(request_params, CryptoBarsRequest):
                bars = self._api_call_with_retry(client.get_crypto_bars, request_params)
            else:
                bars = self._api_call_with_retry(client.get_stock_bars, request_params)
        except Exception as e:
            error_msg = (
                f"Failed to fetch OHLCV data for {base_asset.symbol} "
                f"[{data_datetime_start} to {data_datetime_end}]. "
                f"Error: {e}. "
                f"Suggestion: Check API credentials, network connection, and Alpaca service status."
            )
            raise RuntimeError(error_msg) from e

        df = bars.df.reset_index()
        if df.empty:
            error_msg = (
                f"No OHLCV data returned for {base_asset.symbol} "
                f"[{data_datetime_start} to {data_datetime_end}]. "
                f"Suggestion: Verify the asset exists and has data for this date range on Alpaca."
            )
            raise RuntimeError(error_msg)

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
        original_len = len(df)
        df = df[(df['timestamp'] >= data_datetime_start) & (df['timestamp'] <= data_datetime_end)]
        filtered_len = len(df)
        
        # Track data quality metrics
        if original_len > 0:
            dropped = original_len - filtered_len
            self._data_quality_tracker.record_data_load(original_len, dropped, "date range filtering")

        # Save to cache with checksum
        df.to_csv(filepath, index=False)
        self._save_checksum(filepath)

        # Store in _data_store (bounded cache)
        df.set_index('timestamp', inplace=True)
        self._data_store.set(key, df)
        
        memory_mb = self._data_store.get_memory_usage_mb()
        logger.info(f"Finished fetching and caching data for {key}. Cache memory: {memory_mb:.2f} MB")
        return df

    def _download_and_cache_trades_data(self,
                                        *,
                                        base_asset: Asset = None,
                                        quote_asset: Asset = None,
                                        market: str = None,
                                        tzinfo: pytz.tzinfo = None,
                                        data_datetime_start: datetime = None,
                                        data_datetime_end: datetime = None) -> pd.DataFrame:
        """Download and cache trades data for an asset.
        
        Parameters
        ----------
        base_asset : Asset
            Base asset of the trading pair.
        quote_asset : Asset
            Quote asset of the trading pair.
        market : str
            Market or exchange identifier.
        tzinfo : pytz.tzinfo
            Timezone information for the data.
        data_datetime_start : datetime
            Start date of the data for backtesting.
        data_datetime_end : datetime
            End date of the data for backtesting (inclusive).
            
        Returns
        -------
        pandas.DataFrame
            DataFrame containing the downloaded trades data.

        Notes
        -----
        This method handles is similar to ``_download_and_cache_ohlcv_data`` but
        uses the ``StockTradesRequest`` instead of ``StockBarsRequest`` to collect trades data.
        """
        # Log information for user awareness
        logger.warning(
            "Fetching trades data. Note: It's the strategy's responsibility to process "
            "this raw trades data for feature engineering or custom bar construction."
        )

        if base_asset is None:
            raise ValueError("The parameter 'base_asset' cannot be None.")
        if quote_asset is None:
            raise ValueError("The parameter 'quote_asset' cannot be None.")
        if market is None:
            raise ValueError("The parameter 'market' cannot be None.")
        if tzinfo is None:
            raise ValueError("The parameter 'tzinfo' cannot be None.")
        if data_datetime_start is None:
            raise ValueError("The parameter 'data_datetime_start' cannot be None.")
        if data_datetime_end is None:
            raise ValueError("The parameter 'data_datetime_end' cannot be None.")

        key = self._get_asset_key(
            base_asset=base_asset,
            quote_asset=quote_asset,
            timestep="fractional",
            market=market,
            tzinfo=tzinfo,
            data_datetime_start=data_datetime_start,
            data_datetime_end=data_datetime_end,
        )

        # Directory to save cached data.
        cache_dir = os.path.join(LUMIBOT_CACHE_FOLDER, self.CACHE_SUBFOLDER)
        os.makedirs(cache_dir, exist_ok=True)

        # File path based on the unique key
        filename = f"{key}.parquet"
        filepath = os.path.join(cache_dir, filename)

        logger.info(f"Fetching and caching data for {key}")

        client = self._stock_client

        request_params = StockTradesRequest(
            symbol_or_symbols=base_asset.symbol,
            start=data_datetime_start,
            end=data_datetime_end + timedelta(minutes=1),  # alpaca end dates are exclusive, but we want to include the last bar
            currency=quote_asset.symbol,  # Use symbol as currency
        )

        try:
            trades = self._api_call_with_retry(client.get_stock_trades, request_params)
        except Exception as e:
            error_msg = (
                f"Failed to fetch trades data for {base_asset.symbol} "
                f"[{data_datetime_start} to {data_datetime_end}]. "
                f"Error: {e}. "
                f"Suggestion: Check API credentials, verify asset supports trade data, and check Alpaca service status."
            )
            raise RuntimeError(error_msg) from e

        df = trades.df.reset_index()

        if df.empty:
            raise RuntimeError(f"No data fetched for {key}.")

        # Process the data
        try:
            if data_prep is not None:
                try:
                    # To be compatible with fpap, we need to keep the following columns
                    df = df[['timestamp', 'symbol', 'id', 'price', 'size', 'exchange', 'tape', 'conditions']]
                    df = data_prep(df)

                    # Normalize column names back to lowercase after data_prep
                    # data_prep may rename: Datetime->timestamp, Price->price, Volume->size
                    column_mapping = {
                        'Datetime': 'timestamp',
                        'Price': 'price',
                        'Volume': 'size'
                    }
                    df = df.rename(columns=column_mapping)
                except Exception as e:
                    logger.warning(f"Error in data_prep: {e}. Falling back to default processing.")
                    # Fallback to default processing
            
            # Based on the API response structure, the timestamp is in column 't'
            # Price is in column 'p', size is in column 's'
            if 't' in df.columns and 'timestamp' not in df.columns:
                df = df.rename(columns={'t': 'timestamp', 'p': 'price', 's': 'size'})
            
            # Ensure 'timestamp' column exists and is properly formatted
            if 'timestamp' not in df.columns:
                # If timestamp is the index, reset it to make it a column
                if df.index.name == 'timestamp':
                    df = df.reset_index()
                # If timestamp is still not a column, check for alternative column names
                elif 'time' in df.columns:
                    df = df.rename(columns={'time': 'timestamp'})
                elif 'date' in df.columns:
                    df = df.rename(columns={'date': 'timestamp'})
                elif 'datetime' in df.columns:
                    df = df.rename(columns={'datetime': 'timestamp'})
                else:
                    # If no suitable column is found, use the first column as timestamp
                    df = df.reset_index()
                    df = df.rename(columns={df.columns[0]: 'timestamp'})
            
            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
        except Exception as e:
            logger.warning(f"Error processing dataframe: {e}. Creating empty dataframe.")
            # Return an empty dataframe with the right structure based on the API response
            df = pd.DataFrame(columns=['timestamp', 'price', 'size'])
            # Initialize with empty values to avoid timestamp conversion error
            df['timestamp'] = []
            
        # Sometime there are duplicated records due to the nature of the HFT data
        # We need to drop them
        if not df.empty:
            original_count = len(df)
            df = df[~df.timestamp.duplicated(keep='last')]
            duplicates = original_count - len(df)
            
            if duplicates > 0:
                self._data_quality_tracker.record_invalid_data(duplicates=duplicates)
                logger.debug(f"Removed {duplicates} duplicate trade records")
            
            # Ensure we have the minimum required columns for trades data
            required_columns = ['timestamp', 'price', 'size']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                logger.error(f"Missing required columns in trades data: {missing_columns}")
                # Create a minimal DataFrame with required columns
                df = pd.DataFrame(columns=required_columns)
                df['timestamp'] = pd.to_datetime([])
                df['price'] = pd.Series([], dtype=float)
                df['size'] = pd.Series([], dtype=float)
            
            # Validate data types and remove invalid rows
            if not df.empty:
                # Remove rows with invalid prices or sizes
                original_count = len(df)
                df_clean = df.dropna(subset=['price', 'size'])
                invalid_na = original_count - len(df_clean)
                
                df_valid_price = df_clean[df_clean['price'] > 0]
                invalid_price = len(df_clean) - len(df_valid_price)
                
                df_valid = df_valid_price[df_valid_price['size'] > 0]
                invalid_size = len(df_valid_price) - len(df_valid)
                
                df = df_valid
                
                total_invalid = invalid_na + invalid_price + invalid_size
                if total_invalid > 0:
                    self._data_quality_tracker.record_invalid_data(
                        invalid_prices=invalid_price,
                        invalid_sizes=invalid_size
                    )
                    self._data_quality_tracker.record_data_load(
                        original_count, total_invalid, "invalid prices/sizes"
                    )
                    logger.warning(
                        f"Removed {total_invalid} invalid trade records "
                        f"({invalid_price} invalid prices, {invalid_size} invalid sizes, {invalid_na} NaN)"
                    )
        
        # Save to cache (parquet is faster than csv for large datasets) only if we have data
        if not df.empty:
            df.to_parquet(filepath, index=False)
            self._save_checksum(filepath)
            
            # Store in _data_store (bounded cache)
            df.set_index('timestamp', inplace=True)
            self._data_store.set(key, df)
            
            memory_mb = self._data_store.get_memory_usage_mb()
            logger.info(f"Finished fetching and caching data for {key} - {len(df)} trades. Cache memory: {memory_mb:.2f} MB")
        else:
            # Store empty DataFrame to avoid repeated attempts
            empty_df = pd.DataFrame(columns=['price', 'size'])
            empty_df.index = pd.DatetimeIndex([], name='timestamp')
            self._data_store.set(key, empty_df)
            logger.warning(f"No valid trades data available for {key}")
        
        return self._data_store.get(key)

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
            
        # Validate checksum
        if not self._validate_checksum(filepath):
            logger.warning(f"Checksum validation failed for {key}. Will re-download.")
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
            self._data_store.set(key, df)
            logger.info(f"Loaded cached data for key: {key} from cache.")
            return True
        except Exception as e:
            logger.error(f"Failed to load cached data for key: {key}. Error: {e}")
            return False

    def _load_trades_into_data_store(self, key: str) -> bool:
        """Load trades data from cache into the data store.
        
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
        This method attempts to load previously cached trades data from a parquet file
        into the internal data store. It handles timezone conversion to ensure
        consistency with the configured timezone.
        
        If the file doesn't exist or cannot be properly loaded, the method
        """
        # Directory to find the cached data file.
        cache_dir = os.path.join(LUMIBOT_CACHE_FOLDER, self.CACHE_SUBFOLDER)
        filename = f"{key}.parquet"
        filepath = os.path.join(cache_dir, filename)

        # Check if the file exists
        if not os.path.exists(filepath):
            return False
            
        # Validate checksum
        if not self._validate_checksum(filepath):
            logger.warning(f"Checksum validation failed for trades {key}. Will re-download.")
            return False

        try:
            df = pd.read_parquet(filepath)
            
            # Validate the loaded data
            if df.empty:
                logger.warning(f"Cached trades data is empty for key: {key}")
                # Store empty DataFrame with proper structure
                empty_df = pd.DataFrame(columns=['price', 'size'])
                empty_df.index = pd.DatetimeIndex([], name='timestamp')
                self._data_store.set(key, empty_df)
                return True
                
            # Ensure timestamp exists
            if 'timestamp' not in df.columns and df.index.name != 'timestamp':
                logger.error(f"Cached trades data missing timestamp for key: {key}")
                return False
                
            # Check for required columns or their alternatives
            required_columns = ['price', 'size']
            column_mapping = {
                'price': ['price', 'Price', 'p', 'close', 'Close'],
                'size': ['size', 'Size', 's', 'volume', 'Volume']
            }
            
            # Try to map columns if the required ones don't exist
            for req_col in required_columns:
                if req_col not in df.columns:
                    # Try to find an alternative column
                    alt_cols = column_mapping.get(req_col, [])
                    found = False
                    for alt_col in alt_cols:
                        if alt_col in df.columns:
                            # Map the alternative column to the required name
                            df[req_col] = df[alt_col]
                            found = True
                            logger.info(f"Mapped column {alt_col} to {req_col} for key: {key}")
                            break
                    
                    # If no alternative found, create a default column
                    if not found:
                        if req_col == 'price' and any(col in df.columns for col in ['open', 'high', 'low', 'close']):
                            # Use close if available, otherwise first available price column
                            for price_col in ['close', 'open', 'high', 'low']:
                                if price_col in df.columns:
                                    df[req_col] = df[price_col]
                                    logger.info(f"Created {req_col} from {price_col} for key: {key}")
                                    found = True
                                    break
                        elif req_col == 'size' and any(col in df.columns for col in ['volume', 'qty', 'quantity']):
                            # Use volume if available, otherwise first available quantity column
                            for vol_col in ['volume', 'qty', 'quantity']:
                                if vol_col in df.columns:
                                    df[req_col] = df[vol_col]
                                    logger.info(f"Created {req_col} from {vol_col} for key: {key}")
                                    found = True
                                    break
                        
                        # If still not found, create a default column with placeholder values
                        if not found:
                            df[req_col] = 1.0 if req_col == 'size' else df.index.to_series().diff().dt.total_seconds()
                            logger.warning(f"Created default {req_col} column for key: {key}")
            
            # Verify required columns now exist
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                logger.error(f"Failed to create required columns {missing_columns} for key: {key}")
                return False
            
            # Ensure timestamp is properly formatted
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df.set_index('timestamp', inplace=True)
            elif df.index.name == 'timestamp':
                df.index = pd.to_datetime(df.index)
            else:
                logger.error(f"Cannot find timestamp column or index for key: {key}")
                return False
                
            # Validate data integrity
            original_count = len(df)
            df = df.dropna(subset=['price', 'size'])
            df = df[df['price'] > 0]
            df = df[df['size'] > 0]
            
            if len(df) < original_count:
                dropped = original_count - len(df)
                self._data_quality_tracker.record_data_load(original_count, dropped, "cached data validation")
                logger.warning(f"Removed {dropped} invalid cached trade records for key: {key}")
            
            self._data_store.set(key, df)
            logger.info(f"Loaded cached trades data for key: {key} from cache - {len(df)} trades.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load cached trades data for key: {key}. Error: {e}")
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
        
        For high-frequency trading or feature engineering requiring raw trades data,
        use the ``get_historical_trades_between_dates`` method instead.
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

        # Get OHLCV data key
        ohlcv_key = self._get_asset_key(base_asset=asset, quote_asset=quote, timestep=timestep)

        # Thread-safe check and update of refreshed keys
        with self._refreshed_keys_lock:
            need_refresh = self._refresh_cache and ohlcv_key not in self._refreshed_keys
            if need_refresh:
                self._refreshed_keys[ohlcv_key] = True
        
        if need_refresh:
            # Refresh cache for this key
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
        elif not self._data_store.contains(ohlcv_key) and not self._load_ohlcv_into_data_store(ohlcv_key):
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

        # Get the OHLCV DataFrame from cache
        ohlcv_df = self._data_store.get(ohlcv_key)
        if ohlcv_df is None:
            error_msg = (
                f"Failed to retrieve OHLCV data for {asset.symbol} from cache. "
                f"This is unexpected after download. Please report this issue."
            )
            raise RuntimeError(error_msg)
        return ohlcv_df
        
    def get_historical_trades_between_dates(
            self,
            *,
            base_asset: Asset | list[Asset] = None,
            quote_asset: Asset = None,
            market: str = None,
            tzinfo: pytz.tzinfo = None,
            data_datetime_start: datetime = None,
            data_datetime_end: datetime = None,
    ) -> pd.DataFrame:
        """Get historical trades data between specified dates.
        
        Parameters
        ----------
        base_asset : Asset or list[Asset]
            Base asset or list of base assets of the trading pair.
        quote_asset : Asset, optional
            Quote asset of the trading pair. If None, uses default quote asset.
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
            
        Returns
        -------
        pandas.DataFrame
            DataFrame containing the historical trades data indexed by timestamp.
            If base_asset is a list, returns a concatenated DataFrame with all assets.
            
        Raises
        ------
        ValueError
            If base_asset is None or if the asset type is not 'stock'.
            
        Notes
        -----
        This method fetches raw trades data for high-frequency trading or custom
        feature engineering. It's the user's responsibility to process this data
        appropriately in their strategy.
        
        Currently only supports stock assets, not crypto.
        """
        if base_asset is None:
            raise ValueError("Base asset must be provided.")

        if quote_asset is None:
            quote_asset = self.LUMIBOT_DEFAULT_QUOTE_ASSET

        if market is None:
            market = self.market

        if tzinfo is None:
            tzinfo = self.tzinfo

        # Store the requested datetime range for filtering
        requested_start = data_datetime_start if data_datetime_start is not None else self._data_datetime_start
        requested_end = data_datetime_end if data_datetime_end is not None else self._data_datetime_end

        # Use backtest-wide dates for caching (one cache per symbol for entire backtest period)
        cache_start = self._data_datetime_start
        cache_end = self._data_datetime_end

        # Handle list of assets (optimized with list accumulation)
        if isinstance(base_asset, list):
            all_trades_dfs = []
            assets = [self._sanitize_base_and_quote_asset(asset, quote_asset) for asset in base_asset]

            # Pre-allocate list for better performance
            all_trades_dfs = []
            
            for asset, quote in assets:
                self._check_interrupted()  # Check for interruption in loop
                
                # Get trades for each asset individually
                asset_trades_df = self.get_historical_trades_between_dates(
                    base_asset=asset,
                    quote_asset=quote,
                    market=market,
                    tzinfo=tzinfo,
                    data_datetime_start=requested_start,
                    data_datetime_end=requested_end
                )
                
                if not asset_trades_df.empty:
                    all_trades_dfs.append(asset_trades_df)
            
            # Combine all dataframes efficiently (single concat operation)
            if all_trades_dfs:
                # Use copy=False to avoid unnecessary copying
                combined_df = pd.concat(all_trades_dfs, axis=0, copy=False)
                return combined_df.sort_index()
            else:
                return pd.DataFrame()

        asset, quote = self._sanitize_base_and_quote_asset(base_asset, quote_asset)
        
        if asset.asset_type != 'stock':
            raise ValueError("Trades data is currently only supported for stock assets.")

        # Get trades data key using backtest-wide dates for consistent caching
        trades_key = self._get_asset_key(
            base_asset=asset, 
            quote_asset=quote, 
            timestep="fractional",  # Special timestep indicator for trades data
            market=market,
            tzinfo=tzinfo,
            data_datetime_start=cache_start,  # Use backtest-wide dates
            data_datetime_end=cache_end        # Use backtest-wide dates
        )

        # Thread-safe check and update of refreshed keys
        with self._refreshed_keys_lock:
            need_refresh = self._refresh_cache and trades_key not in self._refreshed_keys
            if need_refresh:
                self._refreshed_keys[trades_key] = True
        
        # Check if we need to refresh or fetch trades data
        if need_refresh:
            self._download_and_cache_trades_data(
                base_asset=asset,
                quote_asset=quote,
                market=market,
                tzinfo=tzinfo,
                data_datetime_start=cache_start,
                data_datetime_end=cache_end
            )
        elif not self._data_store.contains(trades_key) and not self._load_trades_into_data_store(trades_key):
            self._download_and_cache_trades_data(
                base_asset=asset,
                quote_asset=quote,
                market=market,
                tzinfo=tzinfo,
                data_datetime_start=cache_start,
                data_datetime_end=cache_end
            )
        
        # Get the trades DataFrame (contains ALL trades for the backtest period)
        trades_df = self._data_store.get(trades_key)
        
        if trades_df is None or trades_df.empty:
            logger.warning(f"No trades data available for {asset.symbol} between {requested_start} and {requested_end}")
            return pd.DataFrame()
        
        # Filter the DataFrame to the requested datetime range using O(log n) binary search
        try:
            # Use numpy searchsorted for efficient binary search on sorted index
            timestamps = trades_df.index.values
            start_ts = requested_start.value if hasattr(requested_start, 'value') else pd.Timestamp(requested_start).value
            end_ts = requested_end.value if hasattr(requested_end, 'value') else pd.Timestamp(requested_end).value
            
            # Binary search for start and end indices
            start_idx = np.searchsorted(timestamps, start_ts, side='left')
            end_idx = np.searchsorted(timestamps, end_ts, side='right')
            
            # Slice using iloc for better performance (avoids index lookup)
            if start_idx >= len(trades_df) or end_idx <= 0 or start_idx >= end_idx:
                logger.debug(f"No trades data in requested range [{requested_start} to {requested_end}] for {asset.symbol}")
                return pd.DataFrame()
            
            filtered_df = trades_df.iloc[start_idx:end_idx]
            return filtered_df
            
        except Exception as e:
            error_msg = (
                f"Error filtering trades data for {asset.symbol} "
                f"[{requested_start} to {requested_end}]: {e}. "
                f"Suggestion: Verify datetime range and check data integrity."
            )
            logger.error(error_msg)
            return pd.DataFrame()

    def set_processed_bars(
        self,
        processed_bars: pd.DataFrame,
        *,
        base_asset: Asset,
        quote_asset: Asset = None,
        timestep: str = None,
        market: str = None,
        tzinfo: pytz.tzinfo = None,
        data_datetime_start: datetime = None,
        data_datetime_end: datetime = None,
        auto_adjust: bool = None,
    ) -> None:
        """Store processed bars (e.g., from Information-driven bars) for use with other AlpacaBacktesting methods.
        
        Parameters
        ----------
        processed_bars : pandas.DataFrame
            DataFrame containing the processed OHLCV bars with required columns: timestamp, open, high, low, close, volume.
            Optional columns: vwap (Volume-Weighted Average Price).
            Additional columns (custom indicators, features, etc.) are preserved and stored.
            The DataFrame should have a timestamp index or timestamp column.
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
            If None, uses the default auto_adjust setting.
            
        Raises
        ------
        ValueError
            If processed_bars is empty, missing required columns, or has invalid data.
            
        Notes
        -----
        This method allows strategies to store custom processed bars (e.g., Information-driven bars)
        that can then be accessed by other AlpacaBacktesting methods like `get_last_price`,
        `get_last_prices`, `get_historical_prices`, etc.
        
        The processed bars will be stored in the internal data store using the same key format
        as regular OHLCV data, making them seamlessly accessible to all other methods.
        
        Example
        -------
        >>> # In your strategy after processing trades data
        >>> processed_bars = process_trades(trades_df, ...)  # Your custom processing
        >>> self.broker.data_source.set_processed_bars(
        ...     processed_bars=processed_bars,
        ...     base_asset=self.symbol,
        ...     timestep='minute'
        ... )
        """
        # Validate input parameters
        if processed_bars is None or processed_bars.empty:
            raise ValueError("processed_bars cannot be None or empty")
            
        # Set default values
        if quote_asset is None:
            quote_asset = self.LUMIBOT_DEFAULT_QUOTE_ASSET
        if timestep is None:
            # For HFT strategies with non-uniform intervals, use 'custom' timestep
            timestep = 'custom' if hasattr(self, '_is_hft_strategy') else self._timestep
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
            
        # Sanitize assets
        asset, quote = self._sanitize_base_and_quote_asset(base_asset, quote_asset)
        
        # Validate required columns - VWAP is optional for HFT strategies that might not calculate it
        required_columns = {'open', 'high', 'low', 'close', 'volume'}
        optional_columns = {'vwap'}  # VWAP is optional but commonly used in HFT
        df_columns = set(processed_bars.columns)
        
        # Check if timestamp is in columns or index
        has_timestamp = 'timestamp' in df_columns or processed_bars.index.name == 'timestamp'
        if not has_timestamp:
            raise ValueError("processed_bars must have a 'timestamp' column or timestamp index")
            
        # Check for required OHLCV columns
        missing_columns = required_columns - df_columns
        if missing_columns:
            raise ValueError(f"processed_bars is missing required columns: {missing_columns}")
            
        # Log information about additional columns (beyond required ones)
        additional_columns = df_columns - required_columns - optional_columns - {'timestamp'}
        if additional_columns:
            logger.info(f"Processed bars for {base_asset.symbol} contain additional columns: {additional_columns}")
            
        # Create a copy to avoid modifying the original
        df = processed_bars.copy()
        
        # Keep all columns - don't filter out additional ones as they might be useful for strategies
        # This allows HFT strategies to store custom indicators, features, etc.
        
        # Ensure timestamp is the index
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            if df.index.name != 'timestamp':
                df.set_index('timestamp', inplace=True)
        elif df.index.name == 'timestamp':
            df.index = pd.to_datetime(df.index)
        else:
            raise ValueError("Cannot find valid timestamp column or index")
            
        # Ensure timezone consistency
        if df.index.tz is None:
            df.index = df.index.tz_localize(tzinfo)
        else:
            df.index = df.index.tz_convert(tzinfo)
            
        # Validate data types and remove invalid rows
        original_count = len(df)
        
        # Remove rows with NaN OHLC values
        df = df.dropna(subset=['open', 'high', 'low', 'close'])
        
        # Remove rows with invalid OHLC values (negative or zero prices)
        df = df[(df['open'] > 0) & (df['high'] > 0) & (df['low'] > 0) & (df['close'] > 0)]
        
        # Ensure high >= low and high >= open, close and low <= open, close
        df = df[(df['high'] >= df['low']) & 
                (df['high'] >= df['open']) & 
                (df['high'] >= df['close']) & 
                (df['low'] <= df['open']) & 
                (df['low'] <= df['close'])]
        
        # Fill missing volume with 0
        df['volume'] = df['volume'].fillna(0.0)
        
        if len(df) < original_count:
            logger.warning(f"Removed {original_count - len(df)} invalid bars from processed_bars for {asset.symbol}")
            
        if df.empty:
            raise ValueError("All processed_bars were invalid and removed during validation")
            
        # Sort by timestamp to ensure proper order
        df = df.sort_index()
        
        # Generate the key for storing the data
        key = self._get_asset_key(
            base_asset=asset,
            quote_asset=quote,
            timestep=timestep,
            market=market,
            tzinfo=tzinfo,
            data_datetime_start=data_datetime_start,
            data_datetime_end=data_datetime_end,
            auto_adjust=auto_adjust,
        )
        
        # Store the processed bars in the data store (bounded cache)
        self._data_store.set(key, df)
        memory_mb = self._data_store.get_memory_usage_mb()
        logger.info(f"Stored {len(df)} processed bars for {asset.symbol}. Cache memory: {memory_mb:.2f} MB")
        
        # Also save to cache for persistence
        try:
            cache_dir = os.path.join(LUMIBOT_CACHE_FOLDER, self.CACHE_SUBFOLDER)
            os.makedirs(cache_dir, exist_ok=True)
            filename = f"{key}.csv"
            filepath = os.path.join(cache_dir, filename)
            
            # Reset index to save timestamp as column
            df_to_save = df.reset_index()
            df_to_save.to_csv(filepath, index=False)
            self._save_checksum(filepath)
            logger.info(f"Cached processed bars to: {filepath}")
        except Exception as e:
            logger.warning(f"Failed to cache processed bars: {e}")

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
