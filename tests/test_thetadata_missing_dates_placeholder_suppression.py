from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytz

from lumibot.tools import thetadata_helper


def test_get_missing_dates_suppresses_placeholders_before_first_real_date(monkeypatch) -> None:
    """
    Regression test: do not refetch placeholder-only dates before the first real cached date.

    Why this matters:
    - Some strategies require lookback padding before BACKTESTING_START (e.g. SMA200).
    - For certain symbols/providers, the earliest part of that padding range may legitimately have
      no data ("pre-coverage"). We store placeholder rows to record this.
    - Re-fetching those placeholder-only pre-coverage days causes repeated downloader-queue usage,
      which breaks the warm-cache invariant and slows CI/local runs.
    """
    trading_dates = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 4)]
    monkeypatch.setattr(thetadata_helper, "get_trading_dates", lambda asset, start, end: trading_dates)

    asset = SimpleNamespace(symbol="TQQQ", asset_type="stock")
    idx = pd.to_datetime(
        [
            # Use UTC timestamps that map to the *same* America/New_York trading day.
            # (00:00 UTC is the prior ET date, which would make the test flaky under market-local
            # date coverage logic.)
            "2020-01-01 21:00:00+00:00",  # placeholder (pre-coverage)
            "2020-01-02 21:00:00+00:00",  # placeholder (pre-coverage)
            "2020-01-03 21:00:00+00:00",  # real cache begins here
            "2020-01-04 21:00:00+00:00",  # placeholder after first real date -> should refetch
        ],
        utc=True,
    )
    df_all = pd.DataFrame({"missing": [1, 1, 0, 1]}, index=idx)

    missing = thetadata_helper.get_missing_dates(
        df_all,
        asset,
        start=datetime(2020, 1, 1, tzinfo=pytz.UTC),
        end=datetime(2020, 1, 5, tzinfo=pytz.UTC),
    )

    assert missing == [date(2020, 1, 4)]


def test_get_missing_dates_skips_refetch_for_placeholder_only_cache(monkeypatch) -> None:
    """
    Regression test: if the cache is *placeholder-only* for a requested range, do not refetch.

    This situation occurs when ThetaData returns "no data found" for a contract/range and we record
    placeholders to make that absence explicit. Re-fetching the same placeholder-only range on
    every run causes repeated downloader-queue usage and breaks the warm-cache invariant.
    """
    trading_dates = [date(2025, 10, 1), date(2025, 10, 2)]
    monkeypatch.setattr(thetadata_helper, "get_trading_dates", lambda asset, start, end: trading_dates)

    asset = SimpleNamespace(symbol="STRL", asset_type="option")
    idx = pd.to_datetime(
        [
            # Use UTC timestamps that map to the *same* America/New_York trading day.
            "2025-10-01 21:00:00+00:00",
            "2025-10-02 21:00:00+00:00",
        ],
        utc=True,
    )
    df_all = pd.DataFrame({"missing": [1, 1]}, index=idx)

    missing = thetadata_helper.get_missing_dates(
        df_all,
        asset,
        start=datetime(2025, 10, 1, tzinfo=pytz.UTC),
        end=datetime(2025, 10, 3, tzinfo=pytz.UTC),
    )

    assert missing == []


def test_get_missing_dates_suppresses_tail_placeholders_after_last_real_date_for_options(monkeypatch) -> None:
    """
    Regression test: do not refetch placeholder-only *tail* dates after the last real cached date.

    Why this matters:
    - Many illiquid options have no actionable quotes/trades on the final trading day(s).
    - We record placeholders to preserve trading-day coverage, but repeated refetch attempts cause
      endless downloader queue submissions (breaking the warm-cache invariant and CI acceptance gate).

    This test models the common case where:
    - The cache has real data through the prior local trading day (e.g. Jan 16),
    - There is only placeholder coverage on the requested day (e.g. Jan 17),
    - Some UTC timestamps on Jan 17 may still belong to the Jan 16 *local* trading day due to
      extended-hours spillover.
    """
    trading_dates = [date(2025, 1, 17)]
    monkeypatch.setattr(thetadata_helper, "get_trading_dates", lambda asset, start, end: trading_dates)

    asset = SimpleNamespace(symbol="SPX", asset_type="option", expiration=date(2025, 1, 17))
    idx = pd.to_datetime(
        [
            "2025-01-17 01:00:00+00:00",  # 2025-01-16 20:00 ET (real)
            "2025-01-17 21:00:00+00:00",  # 2025-01-17 16:00 ET (placeholder)
        ],
        utc=True,
    )
    df_all = pd.DataFrame({"missing": [0, 1]}, index=idx)

    missing = thetadata_helper.get_missing_dates(
        df_all,
        asset,
        start=datetime(2025, 1, 17, tzinfo=pytz.UTC),
        end=datetime(2025, 1, 18, tzinfo=pytz.UTC),
    )

    assert missing == []


def test_get_missing_dates_suppresses_placeholder_trading_days_for_stock_index_in_backtesting(monkeypatch) -> None:
    """
    Regression test: in backtests, treat placeholder trading days for stocks/indices as stable
    negative cache markers (do not refetch automatically).

    Why this matters:
    - Acceptance backtests require a strict warm-cache invariant (no downloader queue submissions).
    - Some historical windows legitimately contain placeholder days (vendor returned no data).
    - Re-fetching those same placeholder days on every warm run breaks determinism and can cause
      CI acceptance failures even when S3 caches are warm.
    """
    monkeypatch.setenv("IS_BACKTESTING", "True")

    trading_dates = [date(2025, 1, 2), date(2025, 1, 3)]
    monkeypatch.setattr(thetadata_helper, "get_trading_dates", lambda asset, start, end: trading_dates)

    asset = SimpleNamespace(symbol="SPX", asset_type="index")
    idx = pd.to_datetime(
        [
            "2025-01-02 21:00:00+00:00",  # real (covered)
            "2025-01-03 21:00:00+00:00",  # placeholder (known unavailable)
        ],
        utc=True,
    )
    df_all = pd.DataFrame({"missing": [0, 1]}, index=idx)

    missing = thetadata_helper.get_missing_dates(
        df_all,
        asset,
        start=datetime(2025, 1, 2, tzinfo=pytz.UTC),
        end=datetime(2025, 1, 4, tzinfo=pytz.UTC),
    )

    assert missing == []


def test_get_missing_dates_treats_midnight_end_as_exclusive(monkeypatch) -> None:
    """
    Regression test: midnight end bounds represent end-exclusive windows in backtests.

    Acceptance backtests pass BACKTESTING_END as an exclusive date (YYYY-MM-DD). Many internal
    paths represent that as a midnight datetime on the following day. If we treat that midnight
    as end-inclusive for trading-day coverage, we can incorrectly require the next trading day
    and enqueue a downloader request even with warm S3 caches.
    """
    asset = SimpleNamespace(symbol="SPX", asset_type="index")

    # Minimal cache coverage: one real row on 2025-12-24 (a trading day).
    idx = pd.to_datetime(["2025-12-24 21:00:00+00:00"], utc=True)
    df_all = pd.DataFrame({"missing": [0]}, index=idx)

    missing = thetadata_helper.get_missing_dates(
        df_all,
        asset,
        start=datetime(2025, 12, 24, tzinfo=pytz.UTC),
        # End-exclusive: this should NOT require 2025-12-26 coverage.
        end=datetime(2025, 12, 26, tzinfo=pytz.UTC),
    )

    assert missing == []
