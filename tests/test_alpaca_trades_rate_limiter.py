"""
Unit tests for Alpaca HFT trades backtest performance features:

- GlobalRateLimiter: thread-safe rate limiting with configurable/unlimited modes
- SyncTradesDownloader: rate_limiter injection and rate_limit <= 0 (unlimited)
"""

import time
import unittest
from datetime import datetime
from unittest.mock import MagicMock

import pytz

from lumibot.backtesting.sync_trades_downloader import GlobalRateLimiter, SyncTradesDownloader
from lumibot.entities import Asset
from lumibot.credentials import ALPACA_HISTORICAL_RATE_LIMIT

_ET = pytz.timezone("America/New_York")
_DT_START = _ET.localize(datetime(2024, 5, 1, 9, 30))
_DT_END = _ET.localize(datetime(2024, 5, 2, 16, 0))


class TestGlobalRateLimiter(unittest.TestCase):
    """Unit tests for GlobalRateLimiter."""

    def test_unlimited_mode_returns_immediately(self):
        """When rate_limit is 0 or negative, acquire() returns immediately without blocking."""
        limiter = GlobalRateLimiter(rate_limit=0)
        t0 = time.time()
        limiter.acquire()
        limiter.acquire()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.1, "Unlimited mode should not block")

    def test_negative_rate_limit_unlimited(self):
        """Negative rate_limit means unlimited."""
        limiter = GlobalRateLimiter(rate_limit=-1)
        t0 = time.time()
        for _ in range(5):
            limiter.acquire()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.1)

    def test_default_rate_limit_200(self):
        """Default (None) uses 200 req/min, so request_delay = 0.3s."""
        limiter = GlobalRateLimiter(rate_limit=None)
        self.assertEqual(limiter._request_delay, 60.0 / ALPACA_HISTORICAL_RATE_LIMIT)

    def test_custom_rate_limit(self):
        """Positive rate_limit sets correct request_delay."""
        limiter = GlobalRateLimiter(rate_limit=60)
        self.assertEqual(limiter._request_delay, 1.0)

    def test_blocks_when_rate_limited(self):
        """With rate_limit=60 (1 req/s), second acquire within 1s should block."""
        limiter = GlobalRateLimiter(rate_limit=60)
        t0 = time.time()
        limiter.acquire()
        limiter.acquire()
        elapsed = time.time() - t0
        # Should have slept ~1s between the two acquires
        self.assertGreaterEqual(elapsed, 0.9)


class TestSyncTradesDownloaderRateLimiter(unittest.TestCase):
    """Unit tests for SyncTradesDownloader rate limiter injection."""

    def test_injected_rate_limiter_bypasses_internal(self):
        """When rate_limiter is injected, _wait_for_rate_limit uses it."""
        acquire_calls = []

        def mock_acquire():
            acquire_calls.append(1)

        mock_limiter = MagicMock()
        mock_limiter.acquire = mock_acquire

        downloader = SyncTradesDownloader(
            asset=Asset("AAPL"),
            backtest_start=_DT_START,
            backtest_end=_DT_END,
            api_key="test",
            api_secret="test",
            rate_limiter=mock_limiter,
        )
        downloader._wait_for_rate_limit()
        downloader._wait_for_rate_limit()
        self.assertEqual(len(acquire_calls), 2)

    def test_rate_limit_zero_skips_sleep_without_injection(self):
        """When rate_limit <= 0 and no injected limiter, _wait_for_rate_limit returns immediately."""
        downloader = SyncTradesDownloader(
            asset=Asset("AAPL"),
            backtest_start=_DT_START,
            backtest_end=_DT_END,
            api_key="test",
            api_secret="test",
            rate_limit=0,
        )
        t0 = time.time()
        downloader._wait_for_rate_limit()
        downloader._wait_for_rate_limit()
        elapsed = time.time() - t0
        self.assertLess(elapsed, 0.1)


if __name__ == "__main__":
    unittest.main()
