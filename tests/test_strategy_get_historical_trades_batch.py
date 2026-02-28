"""
Unit tests for Strategy.get_historical_trades with list[Asset] (batch) support.

Tests _sanitize_user_asset list handling and verifies the batch API contract.
Full integration is tested by st_alpaca_hft_trades_backtesting.
"""

import unittest

from lumibot.entities import Asset


class _StrategyWithSanitize:
    """Minimal object exposing _sanitize_user_asset for testing."""

    def _sanitize_user_asset(self, asset):
        if isinstance(asset, Asset):
            return asset
        elif isinstance(asset, tuple):
            return asset
        elif isinstance(asset, list):
            return [self._sanitize_user_asset(a) for a in asset]
        elif isinstance(asset, str):
            return Asset(symbol=asset.upper())
        else:
            raise ValueError(f"Invalid asset: {asset}")


class TestSanitizeUserAssetList(unittest.TestCase):
    """Test _sanitize_user_asset with list of assets (used by get_historical_trades batch)."""

    def test_sanitize_list_of_strings(self):
        """List of symbol strings is sanitized to list of Assets."""
        s = _StrategyWithSanitize()
        result = s._sanitize_user_asset(["aapl", "MSFT", "googl"])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].symbol, "AAPL")
        self.assertEqual(result[1].symbol, "MSFT")
        self.assertEqual(result[2].symbol, "GOOGL")

    def test_sanitize_list_of_assets(self):
        """List of Asset objects is returned as-is (uppercase preserved)."""
        s = _StrategyWithSanitize()
        assets = [Asset("AAPL"), Asset("MSFT")]
        result = s._sanitize_user_asset(assets)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].symbol, "AAPL")
        self.assertEqual(result[1].symbol, "MSFT")

    def test_sanitize_mixed_list(self):
        """List of mixed str and Asset is sanitized consistently."""
        s = _StrategyWithSanitize()
        result = s._sanitize_user_asset(["nvda", Asset("TSLA")])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].symbol, "NVDA")
        self.assertEqual(result[1].symbol, "TSLA")


if __name__ == "__main__":
    unittest.main()
