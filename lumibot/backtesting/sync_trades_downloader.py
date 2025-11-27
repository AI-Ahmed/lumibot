"""
Synchronous Trades Data Downloader for HFT Backtesting.

This module provides a simple synchronous downloader that fetches trades data
on-demand using the Alpaca Python SDK. For multi-asset portfolios, use
ThreadPoolExecutor to download assets in parallel.

Key Features
------------
- Uses official Alpaca Python SDK for data fetching
- On-demand downloading with caching
- Memory-efficient sliding window
- No lock contention or race conditions
- Parallel multi-asset support via ThreadPoolExecutor

Notes
-----
This downloader uses the Alpaca SDK which provides:

1. Official API support with automatic updates
2. Built-in pagination handling
3. Proper data types and error handling
4. Rate limit management

References
----------
- Alpaca Python SDK: https://github.com/alpacahq/alpaca-py
- Alpaca Historical Data API: https://alpaca.markets/docs/api-references/market-data-api/
"""

import time
from datetime import timedelta
from typing import Dict, List, Tuple
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pendulum
from pendulum import DateTime

import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest

from lumibot.tools.lumibot_logger import get_logger
from lumibot.entities import Asset
from lumibot.credentials import (
    ALPACA_HISTORICAL_RATE_LIMIT,
    DEFAULT_TRADES_MEMORY_WINDOW_HOURS,
)


logger = get_logger(__name__)


class TimeRange:
    """Represents a half-open time interval [start, end).
    
    Parameters
    ----------
    start : DateTime
        Start datetime (INCLUSIVE)
    end : DateTime
        End datetime (EXCLUSIVE)
    """
    
    def __init__(self, start: DateTime, end: DateTime):
        self.start = pendulum.instance(start) if not isinstance(start, pendulum.DateTime) else start
        self.end = pendulum.instance(end) if not isinstance(end, pendulum.DateTime) else end
    
    def contains(self, dt: DateTime) -> bool:
        """Check if datetime is within range [start, end)."""
        dt = pendulum.instance(dt) if not isinstance(dt, pendulum.DateTime) else dt
        return self.start <= dt < self.end
    
    def covers(self, other: 'TimeRange') -> bool:
        """Check if this range fully covers another range."""
        return self.start <= other.start and self.end >= other.end
    
    def overlaps(self, other: 'TimeRange') -> bool:
        """Check if ranges overlap."""
        return self.start < other.end and other.start < self.end
    
    def merge(self, other: 'TimeRange') -> 'TimeRange':
        """Merge overlapping ranges."""
        return TimeRange(
            min(self.start, other.start),
            max(self.end, other.end)
        )
    
    def __repr__(self):
        return f"TimeRange[{self.start}, {self.end})"


class SyncTradesDownloader:
    """Synchronous trades downloader using Alpaca Python SDK.
    
    Downloads trades data on-demand using the official Alpaca SDK.
    Caches downloaded chunks to avoid re-fetching.
    
    Parameters
    ----------
    asset : Asset
        The asset to download trades for
    backtest_start : DateTime
        Start datetime of the backtest
    backtest_end : DateTime
        End datetime of the backtest
    api_key : str
        Alpaca API key
    api_secret : str
        Alpaca API secret
    tzinfo : str
        Timezone for the data (e.g., 'America/New_York')
    memory_window_hours : int, default 48
        Hours of trades to keep in memory
    chunk_size_minutes : int, default 15
        Minutes of data to download per chunk
    rate_limit : int, default 200
        API rate limit (requests per minute)
    
    Examples
    --------
    >>> downloader = SyncTradesDownloader(
    ...     asset=Asset("AAPL"),
    ...     backtest_start=datetime(2024, 1, 1),
    ...     backtest_end=datetime(2024, 1, 31),
    ...     api_key="your_key",
    ...     api_secret="your_secret",
    ...     tzinfo="America/New_York"
    ... )
    >>> trades = downloader.get_trades(start_dt, end_dt)
    """
    
    def __init__(
        self,
        asset: Asset,
        backtest_start: DateTime,
        backtest_end: DateTime,
        api_key: str,
        api_secret: str,
        tzinfo: str = "America/New_York",
        memory_window_hours: int = DEFAULT_TRADES_MEMORY_WINDOW_HOURS,
        chunk_size_minutes: int = 15,
        rate_limit: int = ALPACA_HISTORICAL_RATE_LIMIT,
    ):
        self.asset = asset
        self.backtest_start = pendulum.instance(backtest_start) if not isinstance(backtest_start, pendulum.DateTime) else backtest_start
        self.backtest_end = pendulum.instance(backtest_end) if not isinstance(backtest_end, pendulum.DateTime) else backtest_end
        
        self._api_key = api_key
        self._api_secret = api_secret
        self.tzinfo = tzinfo
        
        self.memory_window_hours = memory_window_hours
        self.chunk_size_minutes = chunk_size_minutes
        self.rate_limit = rate_limit
        
        # Calculate delay between requests to respect rate limit
        self.request_delay = 60.0 / rate_limit if rate_limit > 0 else 0.3
        self._last_request_time = 0
        
        # Data storage: chunk_start -> DataFrame
        self._data_cache: OrderedDict[DateTime, pd.DataFrame] = OrderedDict()
        self._downloaded_ranges: List[TimeRange] = []
        
        # Statistics
        self._total_trades_downloaded = 0
        self._total_chunks_downloaded = 0
        self._download_errors = 0
        
        # Initialize Alpaca SDK client
        self._stock_client = StockHistoricalDataClient(
            api_key=self._api_key,
            secret_key=self._api_secret
        )
    
    def get_trades(self, start: DateTime, end: DateTime) -> pd.DataFrame:
        """Get trades for the specified time range, downloading if needed.
        
        Parameters
        ----------
        start : DateTime
            Start of the requested range (inclusive)
        end : DateTime
            End of the requested range (exclusive)
        
        Returns
        -------
        pd.DataFrame
            DataFrame with trades data, indexed by timestamp
        """
        start = pendulum.instance(start) if not isinstance(start, pendulum.DateTime) else start
        end = pendulum.instance(end) if not isinstance(end, pendulum.DateTime) else end
        
        # Adjust to market hours for stocks
        start, end = self._adjust_to_market_hours(start, end)
        
        if start >= end:
            return pd.DataFrame()
        
        # Download any missing chunks
        self._ensure_data_available(start, end)
        
        # Retrieve from cache
        return self._get_from_cache(start, end)
    
    def _adjust_to_market_hours(self, start: DateTime, end: DateTime) -> Tuple[DateTime, DateTime]:
        """Adjust time range to market hours (9:30 AM - 4:00 PM ET).
        
        Parameters
        ----------
        start : DateTime
            Start datetime
        end : DateTime
            End datetime
            
        Returns
        -------
        Tuple[DateTime, DateTime]
            Adjusted start and end times
        """
        market_open_hour = 9
        market_open_minute = 30
        market_close_hour = 16
        market_close_minute = 0
        
        # Adjust start if before market open
        if start.hour < market_open_hour or (start.hour == market_open_hour and start.minute < market_open_minute):
            start = start.set(hour=market_open_hour, minute=market_open_minute, second=0, microsecond=0)
        
        # Adjust end if after market close
        if end.hour > market_close_hour or (end.hour == market_close_hour and end.minute > market_close_minute):
            end = end.set(hour=market_close_hour, minute=market_close_minute, second=0, microsecond=0)
        
        return start, end
    
    def _is_cached(self, start: DateTime, end: DateTime) -> bool:
        """Check if the requested range is fully cached.
        
        Parameters
        ----------
        start : DateTime
            Start of range
        end : DateTime
            End of range
            
        Returns
        -------
        bool
            True if all data in range is cached
        """
        requested = TimeRange(start, end)
        
        for cached_range in self._downloaded_ranges:
            if cached_range.covers(requested):
                return True
        
        return False
    
    def _ensure_data_available(self, start: DateTime, end: DateTime):
        """Download any missing chunks to cover the requested range.
        
        Parameters
        ----------
        start : DateTime
            Start of range
        end : DateTime
            End of range
        """
        if self._is_cached(start, end):
            return
        
        # Download chunks to cover the range
        chunk_duration = timedelta(minutes=self.chunk_size_minutes)
        current_pos = start
        
        while current_pos < end:
            chunk_end = min(current_pos + chunk_duration, end)
            
            # Check if this chunk is already cached
            if not self._is_chunk_cached(current_pos, chunk_end):
                self._download_chunk(current_pos, chunk_end)
            
            current_pos = chunk_end
    
    def _is_chunk_cached(self, start: DateTime, end: DateTime) -> bool:
        """Check if a specific chunk is cached."""
        chunk_range = TimeRange(start, end)
        for cached_range in self._downloaded_ranges:
            if cached_range.covers(chunk_range):
                return True
        return False
    
    def _download_chunk(self, start: DateTime, end: DateTime) -> pd.DataFrame:
        """Download a single chunk of trades data using Alpaca SDK.
        
        Parameters
        ----------
        start : DateTime
            Start of chunk (inclusive)
        end : DateTime
            End of chunk (exclusive)
            
        Returns
        -------
        pd.DataFrame
            Downloaded trades data
        """
        # Respect rate limit
        self._wait_for_rate_limit()
        
        try:
            logger.debug(f"Downloading trades for {self.asset.symbol} [{start}, {end})")
            
            # Use Alpaca SDK - handles pagination automatically
            request_params = StockTradesRequest(
                symbol_or_symbols=self.asset.symbol,
                start=start,
                end=end,
            )
            
            trades_response = self._stock_client.get_stock_trades(request_params)
            
            # Convert SDK response to DataFrame
            df = self._trades_to_dataframe(trades_response, start, end)
            
            # Cache the result
            self._cache_chunk(start, end, df)
            
            self._total_chunks_downloaded += 1
            self._total_trades_downloaded += len(df)
            
            logger.debug(
                f"Downloaded {len(df):,} trades for {self.asset.symbol} [{start}, {end})"
            )
            
            return df
            
        except Exception as e:
            logger.error(f"Download failed for {self.asset.symbol} [{start}, {end}): {e}")
            self._download_errors += 1
            return pd.DataFrame()
    
    def _wait_for_rate_limit(self):
        """Wait to respect API rate limit."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()
    
    def _trades_to_dataframe(self, trades_response, start: DateTime, end: DateTime) -> pd.DataFrame:
        """Convert Alpaca SDK trades response to DataFrame.
        
        Parameters
        ----------
        trades_response : TradeSet
            Response from Alpaca SDK get_stock_trades()
            TradeSet is a pydantic model with .data attribute containing Dict[str, List[Trade]]
        start : DateTime
            Start of range (for filtering)
        end : DateTime
            End of range (for filtering)
            
        Returns
        -------
        pd.DataFrame
            Processed trades DataFrame with columns: price, size, exchange, trade_id, conditions, tape
            
        Notes
        -----
        The Alpaca SDK returns TradeSet with Trade objects having attributes:
        - timestamp: datetime
        - price: float
        - size: int
        - exchange: str
        - id: int (trade_id)
        - conditions: list
        - tape: str
        """
        if not trades_response:
            return pd.DataFrame()
        
        # Get trades for our symbol from SDK response (TradeSet uses .data or __getitem__)
        symbol_trades = trades_response[self.asset.symbol] if self.asset.symbol in trades_response.data else []
        if not symbol_trades:
            return pd.DataFrame()
        
        # Convert Trade objects to list of dicts
        trades_data = []
        for trade in symbol_trades:
            trades_data.append({
                'timestamp': trade.timestamp,
                'price': float(trade.price),
                'size': int(trade.size),
                'exchange': trade.exchange,
                'trade_id': trade.id,
                'conditions': trade.conditions,
                'tape': trade.tape,
            })
        
        df = pd.DataFrame(trades_data)
        
        if df.empty:
            return pd.DataFrame()
        
        # Handle timezone conversion
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')
        df['timestamp'] = df['timestamp'].dt.tz_convert(self.tzinfo)
        
        # Set timestamp as index
        df.set_index('timestamp', inplace=True)
        df = df.sort_index()
        
        # Remove duplicates
        if not df.empty:
            df = df[~df.index.duplicated(keep='last')]
        
        # Filter to exact range [start, end)
        df = df[(df.index >= start) & (df.index < end)]
        
        return df
    
    def _cache_chunk(self, start: DateTime, end: DateTime, df: pd.DataFrame):
        """Cache a downloaded chunk and update ranges.
        
        Parameters
        ----------
        start : DateTime
            Chunk start
        end : DateTime
            Chunk end
        df : pd.DataFrame
            Downloaded data
        """
        self._data_cache[start] = df
        
        # Update downloaded ranges
        new_range = TimeRange(start, end)
        merged_ranges = []
        
        for existing_range in self._downloaded_ranges:
            if new_range.overlaps(existing_range):
                new_range = new_range.merge(existing_range)
            else:
                merged_ranges.append(existing_range)
        
        merged_ranges.append(new_range)
        merged_ranges.sort(key=lambda r: r.start)
        self._downloaded_ranges = merged_ranges
    
    def _get_from_cache(self, start: DateTime, end: DateTime) -> pd.DataFrame:
        """Retrieve trades from cache for the specified range.
        
        Parameters
        ----------
        start : DateTime
            Start of range
        end : DateTime
            End of range
            
        Returns
        -------
        pd.DataFrame
            Cached trades data
        """
        trades_list = []
        
        for chunk_start, df_chunk in self._data_cache.items():
            if df_chunk.empty:
                continue
            
            chunk_end_idx = df_chunk.index.max() if not df_chunk.empty else chunk_start
            
            # Check for overlap
            if chunk_end_idx < start or chunk_start >= end:
                continue
            
            # Filter to requested range
            mask = (df_chunk.index >= start) & (df_chunk.index < end)
            filtered = df_chunk[mask]
            
            if not filtered.empty:
                trades_list.append(filtered)
        
        if not trades_list:
            return pd.DataFrame()
        
        result = pd.concat(trades_list, axis=0)
        result = result.sort_index()
        result = result[~result.index.duplicated(keep='last')]
        
        return result
    
    def trim_old_data(self, before_datetime: DateTime):
        """Remove cached data older than the specified datetime.
        
        Parameters
        ----------
        before_datetime : DateTime
            Remove all data before this datetime
        """
        before_datetime = pendulum.instance(before_datetime) if not isinstance(before_datetime, pendulum.DateTime) else before_datetime
        
        # Remove old chunks from cache
        keys_to_remove = [k for k in self._data_cache.keys() if k < before_datetime]
        for key in keys_to_remove:
            del self._data_cache[key]
        
        # Update downloaded ranges
        updated_ranges = []
        for r in self._downloaded_ranges:
            if r.end > before_datetime:
                if r.start < before_datetime:
                    # Truncate range
                    updated_ranges.append(TimeRange(before_datetime, r.end))
                else:
                    updated_ranges.append(r)
        self._downloaded_ranges = updated_ranges
        
        if keys_to_remove:
            logger.debug(f"Trimmed {len(keys_to_remove)} old chunks for {self.asset.symbol}")
    
    def get_statistics(self) -> dict:
        """Get download statistics.
        
        Returns
        -------
        dict
            Statistics dictionary
        """
        return {
            'symbol': self.asset.symbol,
            'total_trades': self._total_trades_downloaded,
            'total_chunks': self._total_chunks_downloaded,
            'download_errors': self._download_errors,
            'cached_chunks': len(self._data_cache),
            'downloaded_ranges': len(self._downloaded_ranges),
        }
    
    def close(self):
        """Clean up resources (SDK handles connection pooling internally)."""
        pass  # Alpaca SDK manages its own connections


class MultiAssetTradesDownloader:
    """Manages parallel downloading across multiple assets.
    
    Uses ThreadPoolExecutor to download trades for multiple assets in parallel,
    while each individual asset downloads synchronously.
    
    Parameters
    ----------
    assets : List[Asset]
        List of assets to manage
    backtest_start : DateTime
        Start datetime of the backtest
    backtest_end : DateTime
        End datetime of the backtest
    api_key : str
        Alpaca API key
    api_secret : str
        Alpaca API secret
    max_workers : int, default 4
        Maximum parallel downloads
    **kwargs
        Additional arguments passed to SyncTradesDownloader
    
    Examples
    --------
    >>> downloader = MultiAssetTradesDownloader(
    ...     assets=[Asset("AAPL"), Asset("TSLA"), Asset("NVDA")],
    ...     backtest_start=datetime(2024, 1, 1),
    ...     backtest_end=datetime(2024, 1, 31),
    ...     api_key="your_key",
    ...     api_secret="your_secret",
    ... )
    >>> # Get trades for single asset
    >>> aapl_trades = downloader.get_trades(Asset("AAPL"), start, end)
    >>> # Get trades for all assets in parallel
    >>> all_trades = downloader.get_all_trades(start, end)
    """
    
    def __init__(
        self,
        assets: List[Asset],
        backtest_start: DateTime,
        backtest_end: DateTime,
        api_key: str,
        api_secret: str,
        max_workers: int = 4,
        **kwargs
    ):
        self.assets = assets
        self.max_workers = min(max_workers, len(assets))
        
        # Create a downloader for each asset
        self._downloaders: Dict[str, SyncTradesDownloader] = {}
        for asset in assets:
            self._downloaders[asset.symbol] = SyncTradesDownloader(
                asset=asset,
                backtest_start=backtest_start,
                backtest_end=backtest_end,
                api_key=api_key,
                api_secret=api_secret,
                **kwargs
            )
    
    def get_trades(self, asset: Asset, start: DateTime, end: DateTime) -> pd.DataFrame:
        """Get trades for a single asset.
        
        Parameters
        ----------
        asset : Asset
            The asset to get trades for
        start : DateTime
            Start of range
        end : DateTime
            End of range
            
        Returns
        -------
        pd.DataFrame
            Trades data
        """
        symbol = asset.symbol if isinstance(asset, Asset) else asset
        if symbol not in self._downloaders:
            raise ValueError(f"Asset {symbol} not found in downloader")
        
        return self._downloaders[symbol].get_trades(start, end)
    
    def get_all_trades(self, start: DateTime, end: DateTime) -> pd.DataFrame:
        """Get trades for all assets in parallel.
        
        Parameters
        ----------
        start : DateTime
            Start of range
        end : DateTime
            End of range
            
        Returns
        -------
        pd.DataFrame
            Combined trades data from all assets
        """
        all_trades_dfs = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(downloader.get_trades, start, end): symbol
                for symbol, downloader in self._downloaders.items()
            }
            
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    df = future.result()
                    if not df.empty:
                        df['symbol'] = symbol  # Add symbol column
                        all_trades_dfs.append(df)
                        logger.debug(f"Got {len(df)} trades for {symbol}")
                except Exception as e:
                    logger.error(f"Failed to get trades for {symbol}: {e}")
        
        if not all_trades_dfs:
            return pd.DataFrame()
        
        combined = pd.concat(all_trades_dfs, axis=0)
        return combined.sort_index()
    
    def prefetch_all(self, start: DateTime, end: DateTime):
        """Pre-download data for all assets in parallel.
        
        Parameters
        ----------
        start : DateTime
            Start of range
        end : DateTime
            End of range
        """
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                executor.submit(downloader.get_trades, start, end)
                for downloader in self._downloaders.values()
            ]
            # Wait for all to complete
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Prefetch error: {e}")
    
    def trim_old_data(self, before_datetime: DateTime):
        """Trim old data from all downloaders.
        
        Parameters
        ----------
        before_datetime : DateTime
            Remove data before this time
        """
        for downloader in self._downloaders.values():
            downloader.trim_old_data(before_datetime)
    
    def get_statistics(self) -> dict:
        """Get combined statistics from all downloaders.
        
        Returns
        -------
        dict
            Combined statistics
        """
        stats = {
            'total_assets': len(self._downloaders),
            'total_trades': 0,
            'total_chunks': 0,
            'total_errors': 0,
            'per_asset': {}
        }
        
        for symbol, downloader in self._downloaders.items():
            asset_stats = downloader.get_statistics()
            stats['total_trades'] += asset_stats['total_trades']
            stats['total_chunks'] += asset_stats['total_chunks']
            stats['total_errors'] += asset_stats['download_errors']
            stats['per_asset'][symbol] = asset_stats
        
        return stats
    
    def close(self):
        """Close all downloaders."""
        for downloader in self._downloaders.values():
            downloader.close()

