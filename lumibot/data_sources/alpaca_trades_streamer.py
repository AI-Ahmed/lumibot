"""
Real-Time Trades Streamer for Live/Paper Trading.

This module provides WebSocket-based real-time trades streaming using the
Alpaca Python SDK. Use this for paper trading and live trading scenarios
where you need actual market data in real-time.

Key Features
------------
- Real-time WebSocket streaming via Alpaca StockDataStream
- Automatic reconnection handling
- Thread-safe data buffer for strategy consumption
- Memory-efficient sliding window
- Multiple symbol support

Notes
-----
For backtesting, use SyncTradesDownloader with historical data instead.
This module is specifically designed for:
- Paper trading (paper=True)
- Live trading (paper=False)

References
----------
- Alpaca Real-Time Data: https://alpaca.markets/docs/api-references/market-data-api/stock-pricing-data/realtime/
- Alpaca Python SDK: https://github.com/alpacahq/alpaca-py
"""

import asyncio
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Set
from collections import deque
from queue import Queue, Empty

import pandas as pd
import pytz

from alpaca.data.live import StockDataStream

from lumibot.tools.lumibot_logger import get_logger
from lumibot.entities import Asset


logger = get_logger(__name__)


class AlpacaTimeTradesStreamer:
    """Real-time trades streamer using Alpaca WebSocket.
    
    Streams live trade data for paper/live trading scenarios.
    Data is buffered in a thread-safe manner for strategy consumption.
    
    Parameters
    ----------
    api_key : str
        Alpaca API key
    api_secret : str
        Alpaca API secret
    symbols : List[str]
        List of symbols to stream
    buffer_size : int, default 10000
        Maximum trades to keep in buffer per symbol
    tzinfo : str, default "America/New_York"
        Timezone for timestamp conversion
        
    Examples
    --------
    >>> streamer = AlpacaTimeTradesStreamer(
    ...     api_key="your_key",
    ...     api_secret="your_secret",
    ...     symbols=["AAPL", "TSLA"]
    ... )
    >>> streamer.start()
    >>> # Get recent trades
    >>> trades = streamer.get_trades("AAPL", minutes=5)
    >>> streamer.stop()
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: List[str] = None,
        buffer_size: int = 10000,
        tzinfo: str = "America/New_York",
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols: Set[str] = set(symbols or [])
        self._buffer_size = buffer_size
        self._tzinfo = pytz.timezone(tzinfo)
        
        # Thread-safe data buffers: symbol -> deque of trade dicts
        self._buffers: Dict[str, deque] = {}
        self._buffer_lock = threading.Lock()
        
        # Streaming state
        self._stream: Optional[StockDataStream] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        
        # Statistics
        self._total_trades_received = 0
        self._connection_errors = 0
        
        # Initialize buffers for known symbols
        for symbol in self._symbols:
            self._buffers[symbol] = deque(maxlen=buffer_size)
    
    def add_symbol(self, symbol: str) -> None:
        """Add a symbol to stream.
        
        Parameters
        ----------
        symbol : str
            Symbol to add
        """
        if symbol not in self._symbols:
            self._symbols.add(symbol)
            with self._buffer_lock:
                self._buffers[symbol] = deque(maxlen=self._buffer_size)
            
            # If already streaming, subscribe to new symbol
            if self._stream and self._connected:
                self._stream.subscribe_trades(self._on_trade, symbol)
                logger.info(f"Subscribed to real-time trades for {symbol}")
    
    def remove_symbol(self, symbol: str) -> None:
        """Remove a symbol from streaming.
        
        Parameters
        ----------
        symbol : str
            Symbol to remove
        """
        if symbol in self._symbols:
            self._symbols.discard(symbol)
            
            # If streaming, unsubscribe
            if self._stream and self._connected:
                self._stream.unsubscribe_trades(symbol)
                logger.info(f"Unsubscribed from real-time trades for {symbol}")
    
    def start(self) -> None:
        """Start the WebSocket streaming in a background thread."""
        if self._running:
            logger.warning("Streamer is already running")
            return
        
        self._running = True
        self._stream_thread = threading.Thread(
            target=self._run_stream,
            daemon=True,
            name="AlpacaTimeTradesStreamer"
        )
        self._stream_thread.start()
        logger.info(f"Started real-time trades streaming for {len(self._symbols)} symbols")
    
    def stop(self) -> None:
        """Stop the WebSocket streaming."""
        self._running = False
        
        if self._stream:
            try:
                self._stream.stop()
            except Exception as e:
                logger.warning(f"Error stopping stream: {e}")
        
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=5)
        
        self._connected = False
        logger.info("Stopped real-time trades streaming")
    
    def _run_stream(self) -> None:
        """Run the WebSocket stream (called in background thread)."""
        while self._running:
            try:
                # Create new stream instance
                self._stream = StockDataStream(
                    api_key=self._api_key,
                    secret_key=self._api_secret
                )
                
                # Subscribe to all symbols
                if self._symbols:
                    self._stream.subscribe_trades(self._on_trade, *self._symbols)
                
                self._connected = True
                logger.info(f"Connected to Alpaca WebSocket, streaming {len(self._symbols)} symbols")
                
                # Run the stream (blocking)
                self._stream.run()
                
            except Exception as e:
                self._connection_errors += 1
                self._connected = False
                logger.error(f"WebSocket error: {e}")
                
                if self._running:
                    # Wait before reconnecting
                    wait_time = min(30, 2 ** min(self._connection_errors, 5))
                    logger.info(f"Reconnecting in {wait_time} seconds...")
                    threading.Event().wait(wait_time)
    
    async def _on_trade(self, trade) -> None:
        """Handle incoming trade data from WebSocket.
        
        Parameters
        ----------
        trade : alpaca.data.models.Trade
            Trade object from Alpaca SDK
        """
        try:
            symbol = trade.symbol
            
            # Convert to dict for storage
            trade_dict = {
                'timestamp': trade.timestamp,
                'price': float(trade.price),
                'size': int(trade.size),
                'exchange': trade.exchange,
                'trade_id': trade.id,
                'conditions': trade.conditions,
                'tape': trade.tape,
            }
            
            # Add to buffer (thread-safe)
            with self._buffer_lock:
                if symbol not in self._buffers:
                    self._buffers[symbol] = deque(maxlen=self._buffer_size)
                self._buffers[symbol].append(trade_dict)
            
            self._total_trades_received += 1
            
        except Exception as e:
            logger.error(f"Error processing trade: {e}")
    
    def get_trades(
        self,
        symbol: str,
        start: datetime = None,
        end: datetime = None,
        minutes: int = None,
    ) -> pd.DataFrame:
        """Get buffered trades for a symbol.
        
        Parameters
        ----------
        symbol : str
            Symbol to get trades for
        start : datetime, optional
            Start time filter
        end : datetime, optional
            End time filter
        minutes : int, optional
            Get last N minutes of trades (alternative to start/end)
            
        Returns
        -------
        pd.DataFrame
            DataFrame with trades data indexed by timestamp
        """
        if symbol not in self._buffers:
            return pd.DataFrame()
        
        with self._buffer_lock:
            trades = list(self._buffers[symbol])
        
        if not trades:
            return pd.DataFrame()
        
        df = pd.DataFrame(trades)
        
        # Convert timestamp
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')
        df['timestamp'] = df['timestamp'].dt.tz_convert(self._tzinfo)
        
        df.set_index('timestamp', inplace=True)
        df = df.sort_index()
        
        # Apply time filters
        if minutes is not None:
            cutoff = datetime.now(self._tzinfo) - timedelta(minutes=minutes)
            df = df[df.index >= cutoff]
        else:
            if start is not None:
                if start.tzinfo is None:
                    start = self._tzinfo.localize(start)
                df = df[df.index >= start]
            if end is not None:
                if end.tzinfo is None:
                    end = self._tzinfo.localize(end)
                df = df[df.index < end]
        
        return df
    
    def get_latest_trade(self, symbol: str) -> Optional[dict]:
        """Get the most recent trade for a symbol.
        
        Parameters
        ----------
        symbol : str
            Symbol to get latest trade for
            
        Returns
        -------
        dict or None
            Latest trade dict or None if no trades
        """
        if symbol not in self._buffers:
            return None
        
        with self._buffer_lock:
            buffer = self._buffers.get(symbol)
            if buffer and len(buffer) > 0:
                return buffer[-1]
        
        return None
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get the most recent trade price for a symbol.
        
        Parameters
        ----------
        symbol : str
            Symbol to get price for
            
        Returns
        -------
        float or None
            Latest trade price or None if no trades
        """
        trade = self.get_latest_trade(symbol)
        return trade['price'] if trade else None
    
    def clear_buffer(self, symbol: str = None) -> None:
        """Clear trade buffer(s).
        
        Parameters
        ----------
        symbol : str, optional
            Symbol to clear. If None, clears all buffers.
        """
        with self._buffer_lock:
            if symbol:
                if symbol in self._buffers:
                    self._buffers[symbol].clear()
            else:
                for buf in self._buffers.values():
                    buf.clear()
    
    def get_statistics(self) -> dict:
        """Get streaming statistics.
        
        Returns
        -------
        dict
            Statistics dictionary
        """
        buffer_sizes = {}
        with self._buffer_lock:
            for symbol, buf in self._buffers.items():
                buffer_sizes[symbol] = len(buf)
        
        return {
            'running': self._running,
            'connected': self._connected,
            'symbols': list(self._symbols),
            'total_trades_received': self._total_trades_received,
            'connection_errors': self._connection_errors,
            'buffer_sizes': buffer_sizes,
        }
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected
    
    @property
    def is_running(self) -> bool:
        """Check if streamer is running."""
        return self._running


class MultiSymbolTradesStreamer:
    """Convenience wrapper for streaming multiple symbols.
    
    Provides a simpler interface for common multi-symbol streaming scenarios.
    
    Parameters
    ----------
    api_key : str
        Alpaca API key
    api_secret : str
        Alpaca API secret
    assets : List[Asset]
        List of Asset objects to stream
    **kwargs
        Additional arguments passed to AlpacaTimeTradesStreamer
        
    Examples
    --------
    >>> streamer = MultiSymbolTradesStreamer(
    ...     api_key="your_key",
    ...     api_secret="your_secret",
    ...     assets=[Asset("AAPL"), Asset("TSLA")]
    ... )
    >>> streamer.start()
    >>> trades = streamer.get_all_trades(minutes=5)
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        assets: List[Asset] = None,
        **kwargs
    ):
        symbols = [a.symbol for a in (assets or [])]
        self._streamer = AlpacaTimeTradesStreamer(
            api_key=api_key,
            api_secret=api_secret,
            symbols=symbols,
            **kwargs
        )
        self._assets = {a.symbol: a for a in (assets or [])}
    
    def add_asset(self, asset: Asset) -> None:
        """Add an asset to stream."""
        self._assets[asset.symbol] = asset
        self._streamer.add_symbol(asset.symbol)
    
    def start(self) -> None:
        """Start streaming."""
        self._streamer.start()
    
    def stop(self) -> None:
        """Stop streaming."""
        self._streamer.stop()
    
    def get_trades(self, asset: Asset, **kwargs) -> pd.DataFrame:
        """Get trades for a specific asset."""
        symbol = asset.symbol if isinstance(asset, Asset) else asset
        return self._streamer.get_trades(symbol, **kwargs)
    
    def get_all_trades(self, **kwargs) -> pd.DataFrame:
        """Get trades for all assets combined."""
        all_dfs = []
        for symbol in self._assets:
            df = self._streamer.get_trades(symbol, **kwargs)
            if not df.empty:
                df['symbol'] = symbol
                all_dfs.append(df)
        
        if not all_dfs:
            return pd.DataFrame()
        
        return pd.concat(all_dfs, axis=0).sort_index()
    
    def get_latest_prices(self) -> Dict[str, float]:
        """Get latest prices for all assets."""
        prices = {}
        for symbol in self._assets:
            price = self._streamer.get_latest_price(symbol)
            if price is not None:
                prices[symbol] = price
        return prices
    
    def get_statistics(self) -> dict:
        """Get streaming statistics."""
        return self._streamer.get_statistics()
    
    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._streamer.is_connected

