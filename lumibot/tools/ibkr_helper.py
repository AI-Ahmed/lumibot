from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from lumibot.constants import LUMIBOT_CACHE_FOLDER, LUMIBOT_DEFAULT_PYTZ
from lumibot.entities import Asset
from lumibot.tools.backtest_cache import CacheMode, get_backtest_cache
from lumibot.tools.ibkr_secdef import (
    IBKR_US_FUTURES_EXCHANGES,
    IbkrFuturesExchangeAmbiguousError,
    select_futures_exchange_from_secdef_search_payload,
)
from lumibot.tools.parquet_series_cache import ParquetSeriesCache
from lumibot.tools.data_downloader_queue_client import queue_request

logger = logging.getLogger(__name__)

CACHE_SUBFOLDER = "ibkr"

# IBKR Client Portal Gateway caps historical responses at ~1000 datapoints per call.
IBKR_HISTORY_MAX_POINTS = 1000
IBKR_DEFAULT_CRYPTO_VENUE = "ZEROHASH"
IBKR_DEFAULT_HISTORY_SOURCE = "Trades"
IBKR_DEFAULT_FUTURES_EXCHANGE_FALLBACK = "CME"

IBKR_CONID_NEGATIVE_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h (persisted via BacktestCacheManager when enabled)


class IbkrFuturesConidLookupError(RuntimeError):
    """Raised when IBKR cannot resolve a futures conid for a root/expiration.

    This is treated as a data-availability issue (not a platform crash) so backtests can
    complete while logging loudly.
    """


_FUTURES_EXCHANGE_CACHE: Dict[str, str] = {}
_FUTURES_EXCHANGE_CACHE_LOADED = False

_NEGATIVE_CONID_CACHE: Dict[str, Dict[str, Any]] = {}
_NEGATIVE_CONID_CACHE_LOADED = False

def _enable_futures_bid_ask_derivation() -> bool:
    """Whether to derive bid/ask quotes for futures from Bid_Ask + Midpoint history.

    Default is disabled because:
    - Futures backtests in LumiBot are intended to fill off TRADES/OHLC by default.
    - IBKR Client Portal Bid_Ask/Midpoint history can be flaky and adds 2x request volume.
    """
    return os.environ.get("LUMIBOT_IBKR_ENABLE_FUTURES_BID_ASK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }

def _futures_exchange_cache_file() -> Path:
    return Path(LUMIBOT_CACHE_FOLDER) / CACHE_SUBFOLDER / "futures_exchanges.json"


def _load_futures_exchange_cache() -> None:
    global _FUTURES_EXCHANGE_CACHE_LOADED
    if _FUTURES_EXCHANGE_CACHE_LOADED:
        return
    _FUTURES_EXCHANGE_CACHE_LOADED = True
    path = _futures_exchange_cache_file()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        for k, v in payload.items():
            if not k or not v:
                continue
            _FUTURES_EXCHANGE_CACHE[str(k).strip().upper()] = str(v).strip().upper()


def _persist_futures_exchange_cache() -> None:
    path = _futures_exchange_cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_FUTURES_EXCHANGE_CACHE, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return
    try:
        cache = get_backtest_cache()
        cache.on_local_update(path, payload={"provider": "ibkr", "type": "futures_exchanges"})
    except Exception:
        pass


def _negative_conid_cache_file() -> Path:
    return Path(LUMIBOT_CACHE_FOLDER) / CACHE_SUBFOLDER / "conids_negative.json"


def _load_negative_conid_cache() -> None:
    """Load negative conid cache (best-effort) and prune stale entries."""
    global _NEGATIVE_CONID_CACHE_LOADED
    if _NEGATIVE_CONID_CACHE_LOADED:
        return
    _NEGATIVE_CONID_CACHE_LOADED = True

    path = _negative_conid_cache_file()
    cache_manager = get_backtest_cache()
    try:
        cache_manager.ensure_local_file(path, payload={"provider": "ibkr", "type": "conids_negative"})
    except Exception:
        pass

    if not path.exists():
        return

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return

    now = float(time.time())
    changed = False
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(value, dict):
            continue
        ts = value.get("ts")
        try:
            ts_f = float(ts)
        except Exception:
            ts_f = None
        if ts_f is None or (now - ts_f) > IBKR_CONID_NEGATIVE_CACHE_TTL_SECONDS:
            changed = True
            continue
        _NEGATIVE_CONID_CACHE[key] = value

    if changed:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_NEGATIVE_CONID_CACHE, indent=2, sort_keys=True), encoding="utf-8")
            try:
                cache_manager.on_local_update(path, payload={"provider": "ibkr", "type": "conids_negative"})
            except Exception:
                pass
        except Exception:
            pass


def _record_negative_conid(*, key: str, reason: str, message: str) -> None:
    """Persist a negative cache marker so we stop hammering IBKR for invalid roots/expirations."""
    if not key:
        return
    _load_negative_conid_cache()
    now = float(time.time())
    _NEGATIVE_CONID_CACHE[key] = {"ts": now, "reason": str(reason), "message": str(message)}

    path = _negative_conid_cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_NEGATIVE_CONID_CACHE, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return
    try:
        cache_manager = get_backtest_cache()
        cache_manager.on_local_update(path, payload={"provider": "ibkr", "type": "conids_negative"})
    except Exception:
        pass


def _resolve_futures_exchange(symbol: str) -> str:
    symbol_upper = str(symbol or "").strip().upper()
    if not symbol_upper:
        raise RuntimeError("IBKR futures exchange resolution requires a non-empty symbol")

    _load_futures_exchange_cache()
    cached = _FUTURES_EXCHANGE_CACHE.get(symbol_upper)
    if cached:
        return cached

    base_url = _downloader_base_url()
    url = f"{base_url}/ibkr/iserver/secdef/search"
    payload = queue_request(url=url, querystring={"symbol": symbol_upper, "secType": "FUT"}, headers=None, timeout=None)
    if payload is None:
        raise RuntimeError(f"IBKR secdef/search returned no payload for FUT symbol={symbol_upper!r}")

    exchange = select_futures_exchange_from_secdef_search_payload(symbol_upper, payload)
    _FUTURES_EXCHANGE_CACHE[symbol_upper] = exchange
    _persist_futures_exchange_cache()
    return exchange


def _us_futures_closed_interval(start_local: datetime, end_local: datetime) -> bool:
    """Return True if US futures are fully closed in [start_local, end_local).

    This is a deliberately simple rule-based calendar used to avoid repeated downloader fetches
    for known closed windows (daily maintenance + weekends). It is not intended to encode every
    CME holiday/early-close rule; those can still produce longer gaps that require vendor data.
    """
    try:
        start_ts = pd.Timestamp(start_local)
        end_ts = pd.Timestamp(end_local)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize(LUMIBOT_DEFAULT_PYTZ)
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize(LUMIBOT_DEFAULT_PYTZ)
        start_ts = start_ts.tz_convert("America/New_York")
        end_ts = end_ts.tz_convert("America/New_York")
        if end_ts <= start_ts:
            return True
    except Exception:
        return False

    def _next_open(ts: pd.Timestamp) -> pd.Timestamp:
        ts = ts.tz_convert("America/New_York")
        dow = int(ts.weekday())  # Mon=0 .. Sun=6
        t = ts.time()

        # Saturday: closed all day; next open is Sunday 18:00 ET.
        if dow == 5:
            days = 1
            candidate = (ts + pd.Timedelta(days=days)).normalize() + pd.Timedelta(hours=18)
            return candidate.tz_localize("America/New_York") if candidate.tzinfo is None else candidate

        # Sunday: closed until 18:00 ET.
        if dow == 6:
            open_ts = ts.normalize() + pd.Timedelta(hours=18)
            open_ts = open_ts.tz_localize("America/New_York") if open_ts.tzinfo is None else open_ts
            return ts if ts >= open_ts else open_ts

        # Weekdays: closed daily 17:00–18:00 ET.
        if t >= datetime.min.replace(hour=17, minute=0, second=0).time() and t < datetime.min.replace(hour=18, minute=0, second=0).time():
            reopen = ts.normalize() + pd.Timedelta(hours=18)
            reopen = reopen.tz_localize("America/New_York") if reopen.tzinfo is None else reopen
            return reopen

        return ts

    try:
        next_open = _next_open(start_ts)
        return bool(next_open >= end_ts)
    except Exception:
        return False


@dataclass(frozen=True)
class IbkrConidKey:
    asset_type: str
    symbol: str
    quote_symbol: str
    exchange: str
    expiration: str

    def to_key(self) -> str:
        return "|".join(
            [
                self.asset_type or "",
                self.symbol or "",
                self.quote_symbol or "",
                self.exchange or "",
                self.expiration or "",
            ]
        )


def get_price_data(
    *,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str] = None,
    include_after_hours: bool = True,
    source: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch IBKR historical bars (via the Data Downloader) and cache to parquet.

    This helper mirrors the ThetaData cache pattern:
    - local parquet under `LUMIBOT_CACHE_FOLDER/ibkr/...`
    - optional S3 mirroring via BacktestCacheManager
    - best-effort negative caching via `missing=True` placeholder rows (only for true NO_DATA)
    """
    start_utc = _to_utc(start_dt)
    end_utc = _to_utc(end_dt)
    if start_utc > end_utc:
        start_utc, end_utc = end_utc, start_utc
    start_local = start_utc.astimezone(LUMIBOT_DEFAULT_PYTZ)
    end_local = end_utc.astimezone(LUMIBOT_DEFAULT_PYTZ)

    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    asset_auto_expiry = getattr(asset, "auto_expiry", None)
    if asset_type == "future" and getattr(asset, "expiration", None) is None and not asset_auto_expiry:
        raise ValueError(
            "IBKR futures require an explicit expiration on Asset(asset_type='future') unless "
            "`auto_expiry` is set. Use asset_type='cont_future' for continuous futures."
        )
    effective_exchange = exchange
    if asset_type in {"future", "cont_future"} and not effective_exchange:
        try:
            effective_exchange = _resolve_futures_exchange(getattr(asset, "symbol", ""))
        except IbkrFuturesExchangeAmbiguousError:
            raise
        except Exception as exc:
            fallback = (os.environ.get("IBKR_FUTURES_EXCHANGE") or IBKR_DEFAULT_FUTURES_EXCHANGE_FALLBACK).strip().upper()
            logger.warning(
                "IBKR futures exchange auto-resolution failed for %s: %s. Falling back to %s",
                getattr(asset, "symbol", None),
                exc,
                fallback,
            )
            effective_exchange = fallback
    if asset_type == "crypto" and not effective_exchange:
        effective_exchange = (os.environ.get("IBKR_CRYPTO_VENUE") or IBKR_DEFAULT_CRYPTO_VENUE).strip().upper()

    # Treat the env var as explicit too.
    #
    # WHY: If the user explicitly chooses a history source via env vars (for example `Trades`),
    # we must not silently derive/augment bid/ask from other sources because that would change
    # execution semantics in backtests.
    env_source_raw = os.environ.get("IBKR_HISTORY_SOURCE")
    env_source_was_explicit = False
    if env_source_raw is not None:
        trimmed = env_source_raw.strip()
        if trimmed and trimmed.lower() != "none":
            env_source_was_explicit = True

    source_was_explicit = source is not None or env_source_was_explicit
    history_source = _normalize_history_source(source)

    # Normalize timestep classification once so callers can pass "day", "1d", "1day", etc.
    try:
        _bar, _bar_seconds, timestep_component = _timestep_to_ibkr_bar(timestep)
    except Exception:
        timestep_component = _timestep_component(timestep)

    # Continuous futures
    #
    # IMPORTANT (expired explicit futures support):
    # IBKR's Client Portal API does not reliably expose conids for *expired* futures contracts.
    # To backtest `cont_future` deterministically (and to support explicit expired contracts),
    # LumiBot uses a local conid registry (`ibkr/conids.json`) populated via a one-time TWS
    # backfill. `cont_future` data is stitched by resolving each contract month using LumiBot's
    # roll schedule (see `_resolve_cont_future_segments`), then fetching bars per-expiration.

    is_roll_wrapper = bool(
        asset_type == "cont_future"
        or (
            asset_type == "future"
            and getattr(asset, "expiration", None) is None
            and asset_auto_expiry
        )
    )

    # Cont-futures + Auto-expiry futures
    #
    # Behavior: for IBKR we treat `Asset(asset_type='cont_future')` and
    # `Asset(asset_type='future', auto_expiry=...)` as synthetic roll wrappers.
    #
    # Rationale: backtests must match live semantics and must not depend on `date.today()` for
    # selecting a contract month.
    if is_roll_wrapper:
        segments = _resolve_cont_future_segments(asset=asset, start_dt=start_utc, end_dt=end_utc, exchange=effective_exchange)
        if not segments:
            logger.error(
                "IBKR futures roll wrapper could not resolve any roll segments for %s (type=%s exchange=%s). Returning empty bars.",
                getattr(asset, "symbol", None),
                asset_type,
                effective_exchange,
            )
            return pd.DataFrame()
        # Ensure the *user-facing* cont_future asset (used in orders/positions) carries the
        # correct contract metadata (multiplier/min_tick). Otherwise PnL and tick rounding will
        # be wrong even if we fetch bars for the right underlying expirations.
        #
        # We copy metadata from the first roll segment, since multiplier/minTick are stable
        # across expirations for a given root (e.g., MES, ES).
        try:
            first_asset, _, _ = segments[0]
            _maybe_apply_future_contract_metadata(asset=first_asset, exchange=effective_exchange)
            first_multiplier = getattr(first_asset, "multiplier", None)
            if first_multiplier not in (None, 0, 1):
                try:
                    asset.multiplier = first_multiplier  # type: ignore[assignment]
                except Exception:
                    pass
            first_min_tick = getattr(first_asset, "min_tick", None)
            if first_min_tick not in (None, 0):
                try:
                    setattr(asset, "min_tick", first_min_tick)
                except Exception:
                    pass
        except Exception:
            pass

        frames: list[pd.DataFrame] = []
        for i, (seg_asset, seg_start, seg_end) in enumerate(segments):
            # Clamp each segment to the requested window.
            seg_start = _to_utc(seg_start)
            seg_end = _to_utc(seg_end)

            # IMPORTANT: `Strategy.get_last_price()` is evaluated at bar boundaries, and our
            # futures backtesting semantics treat "last price at dt" as the last completed bar
            # (i.e., previous bar close).
            #
            # At roll boundaries, the *first* bar of the new contract often occurs exactly one
            # minute before the roll trigger (`roll_dt + 1 minute` in `futures_roll`), so the
            # previous-bar lookup at the roll timestamp needs the new contract's final pre-roll
            # minute available.
            #
            # Fix: for every segment after the first, widen the fetch window by 1 minute on the
            # left so the stitched series contains that preceding bar. We rely on "keep=last"
            # de-duping so the newer contract overrides overlaps deterministically.
            if i > 0:
                seg_start = seg_start - timedelta(minutes=1)

            seg_start = max(seg_start, start_utc)
            seg_end = min(_to_utc(seg_end), end_utc)
            if seg_start >= seg_end:
                continue
            df_seg = get_price_data(
                asset=seg_asset,
                quote=quote,
                timestep=timestep,
                start_dt=seg_start,
                end_dt=seg_end,
                exchange=effective_exchange,
                include_after_hours=include_after_hours,
                source=source,
            )
            if df_seg is not None and not df_seg.empty:
                frames.append(df_seg)
        if not frames:
            return pd.DataFrame()
        stitched = pd.concat(frames, axis=0)
        stitched = stitched[~stitched.index.duplicated(keep="last")]
        stitched = stitched.sort_index()
        return stitched.loc[(stitched.index >= start_local) & (stitched.index <= end_local)]

    # IMPORTANT (IBKR crypto daily semantics):
    # IBKR's `bar=1d` history is not a clean midnight-to-midnight 24/7 day series for crypto.
    # Daily-cadence strategies in LumiBot typically advance the simulation clock at midnight in
    # the strategy timezone. If we treat IBKR daily bars as authoritative, the series often
    # "ends" at a non-midnight timestamp and can lag by days, which triggers Data.checker()
    # stale-end errors and repeated refreshes (extremely slow; looks like "missing BTC data").
    #
    # Fix: for crypto only, derive daily bars from intraday history and align them to midnight
    # buckets in `LUMIBOT_DEFAULT_PYTZ`.
    if asset_type == "crypto" and str(timestep_component).endswith("day"):
        return _get_crypto_daily_bars(
            asset=asset,
            quote=quote,
            start_dt=start_utc,
            end_dt=end_utc,
            exchange=effective_exchange,
            include_after_hours=include_after_hours,
            source=history_source,
        )

    if asset_type in {"future", "cont_future"} and str(timestep_component).endswith("day"):
        _maybe_apply_future_contract_metadata(asset=asset, exchange=effective_exchange)
        return _get_futures_daily_bars(
            asset=asset,
            quote=quote,
            start_dt=start_utc,
            end_dt=end_utc,
            exchange=effective_exchange,
            include_after_hours=include_after_hours,
            source=history_source,
        )

    if asset_type in {"future", "cont_future"}:
        _maybe_apply_future_contract_metadata(asset=asset, exchange=effective_exchange)

    cache_file = _cache_file_for(
        asset=asset,
        quote=quote,
        timestep=timestep,
        exchange=effective_exchange,
        source=history_source,
    )
    cache_manager = get_backtest_cache()

    try:
        cache_manager.ensure_local_file(
            cache_file,
            payload=_remote_payload(asset, quote, timestep, effective_exchange, history_source),
        )
    except Exception:
        pass

    df_cache = _read_cache_frame(cache_file)
    # If this series came from a cached parquet (e.g., prefilled via TWS), it may not include the
    # synthetic bid/ask fallback columns that we add when decoding Client Portal history. For
    # non-explicit history sources, populate bid/ask from close so quote-based fill logic (and
    # SMART_LIMIT) remains functional without forcing extra history requests.
    if (
        not source_was_explicit
        and not df_cache.empty
        and "close" in df_cache.columns
        and (("bid" not in df_cache.columns) or ("ask" not in df_cache.columns))
    ):
        df_cache = df_cache.copy()
        close = pd.to_numeric(df_cache.get("close"), errors="coerce")

        if "bid" in df_cache.columns:
            bid = pd.to_numeric(df_cache.get("bid"), errors="coerce")
        else:
            bid = pd.Series(index=df_cache.index, dtype="float64")
        df_cache["bid"] = bid.where(~bid.isna(), close)

        if "ask" in df_cache.columns:
            ask = pd.to_numeric(df_cache.get("ask"), errors="coerce")
        else:
            ask = pd.Series(index=df_cache.index, dtype="float64")
        df_cache["ask"] = ask.where(~ask.isna(), close)
    if not df_cache.empty:
        coverage_start = df_cache.index.min()
        coverage_end = df_cache.index.max()
    else:
        coverage_start = None
        coverage_end = None

    # Detect disjoint cached segments.
    #
    # IBKR parquet caches (especially when hydrated from remote S3) can contain disjoint segments
    # where `coverage_start..coverage_end` spans a large range but the requested window is only
    # partially covered (or not covered near one boundary). If we only look at global min/max
    # coverage we can incorrectly treat a request as a cache hit and return empty/underfilled bars.
    window_slice = pd.DataFrame()
    window_cov_start = None
    window_cov_end = None
    try:
        if coverage_start is not None and coverage_end is not None:
            window_slice = df_cache.loc[(df_cache.index >= start_local) & (df_cache.index <= end_local)]
            if not window_slice.empty:
                window_cov_start = window_slice.index.min()
                window_cov_end = window_slice.index.max()
    except Exception:
        window_slice = pd.DataFrame()
        window_cov_start = None
        window_cov_end = None

    # IBKR history can legitimately omit the very last bar(s) of a window (e.g., missing the final
    # 1–2 minutes of the day). When this happens, repeatedly trying to "fill to the end" creates
    # unnecessary downloader traffic and can wedge CI acceptance runs.
    #
    # Treat the cached series as "good enough" if it's within 2 bars of the requested end.
    #
    # Futures also have an expected daily maintenance gap (~1 hour). If a request window begins
    # during a closed period and the cache starts at the next session open, do not try to fetch
    # the closed interval (it will return empty and can trigger retry loops).
    end_tolerance = timedelta(0)
    start_tolerance = timedelta(0)
    bar_step = timedelta(0)
    try:
        ibkr_bar, _, _ = _timestep_to_ibkr_bar(timestep)

        def _bar_delta(bar: str) -> timedelta:
            b = (bar or "").strip().lower()
            if b.endswith("min"):
                return timedelta(minutes=int(b.removesuffix("min") or "1"))
            if b.endswith("h"):
                return timedelta(hours=int(b.removesuffix("h") or "1"))
            if b.endswith("d"):
                return timedelta(days=int(b.removesuffix("d") or "1"))
            return timedelta(0)

        bar_step = _bar_delta(ibkr_bar)
        end_tolerance = bar_step * 3
        start_tolerance = bar_step * 3
        if asset_type in {"future", "cont_future"}:
            start_tolerance = max(start_tolerance, timedelta(hours=1))
    except Exception:
        end_tolerance = timedelta(0)
        start_tolerance = timedelta(0)
        bar_step = timedelta(0)

    window_start_gap_closed = (
        asset_type in {"future", "cont_future"}
        and window_cov_start is not None
        and start_local < window_cov_start
        and _us_futures_closed_interval(start_local, window_cov_start)
    )
    cache_start_gap_closed = (
        asset_type in {"future", "cont_future"}
        and coverage_start is not None
        and start_local < coverage_start
        and _us_futures_closed_interval(start_local, coverage_start)
    )
    window_end_gap_closed = (
        asset_type in {"future", "cont_future"}
        and window_cov_end is not None
        and end_local > window_cov_end
        and _us_futures_closed_interval(window_cov_end + bar_step, end_local)
    )
    cache_end_gap_closed = (
        asset_type in {"future", "cont_future"}
        and coverage_end is not None
        and end_local > coverage_end
        and _us_futures_closed_interval(coverage_end + bar_step, end_local)
    )

    needs_fetch = (
        coverage_start is None
        or coverage_end is None
        # If the requested window has no rows at all (even though the overall cache has a broad
        # min/max range), treat it as a cache miss and fetch that specific segment.
        or (coverage_start is not None and coverage_end is not None and window_slice.empty)
        # Missing coverage near the requested boundaries (disjoint segments within the window).
        or (
            window_cov_start is not None
            and start_local < window_cov_start
            and not window_start_gap_closed
            and (start_tolerance <= timedelta(0) or (window_cov_start - start_local) > start_tolerance)
        )
        or (
            window_cov_end is not None
            and end_local > window_cov_end
            and not window_end_gap_closed
            and (end_tolerance <= timedelta(0) or (end_local - window_cov_end) > end_tolerance)
        )
        or (
            coverage_start is not None
            and start_local < coverage_start
            and not cache_start_gap_closed
            and (start_tolerance <= timedelta(0) or (coverage_start - start_local) > start_tolerance)
        )
        or (
            end_local > coverage_end
            and not cache_end_gap_closed
            and (end_tolerance <= timedelta(0) or (end_local - coverage_end) > end_tolerance)
        )
    )

    if needs_fetch:
        segments: list[tuple[datetime, datetime]] = []
        if coverage_start is None or coverage_end is None or window_slice.empty:
            segments.append((start_utc, end_utc))
        else:
            # Prefer window-local coverage for disjoint-segment detection.
            effective_start = window_cov_start or coverage_start
            effective_end = window_cov_end or coverage_end

            # If the requested window has no overlap with the cached window, do NOT try to "bridge"
            # the gap. Fetch exactly the requested window and merge it into the cache as a disjoint
            # segment. Bridging can turn a 1-hour request into months of downloads.
            if end_local < effective_start or start_local > effective_end:
                segments.append((start_utc, end_utc))
            else:
                if effective_start is not None and start_local < effective_start:
                    segments.append((start_utc, effective_start.astimezone(timezone.utc)))
                if effective_end is not None and end_local > effective_end:
                    segments.append((effective_end.astimezone(timezone.utc), end_utc))

        for seg_start, seg_end in segments:
            if seg_start >= seg_end:
                continue
            prev_max = df_cache.index.max() if not df_cache.empty else None
            try:
                fetched = _fetch_history_between_dates(
                    asset=asset,
                    quote=quote,
                    timestep=timestep,
                    start_dt=seg_start,
                    end_dt=seg_end,
                    exchange=effective_exchange,
                    include_after_hours=include_after_hours,
                    source=history_source,
                    source_was_explicit=source_was_explicit,
                )
            except Exception as exc:
                # Avoid crashing the entire backtest on entitlement/session issues. Return an empty
                # frame so strategies can continue with a loud error in logs.
                logger.error(
                    "IBKR history fetch failed for %s/%s timestep=%s exchange=%s source=%s: %s",
                    getattr(asset, "symbol", None),
                    getattr(quote, "symbol", None) if quote else None,
                    timestep,
                    effective_exchange,
                    history_source,
                    exc,
                )
                fetched = pd.DataFrame()
            if fetched is not None and not fetched.empty:
                merged = _merge_frames(df_cache, fetched)
                _write_cache_frame(cache_file, merged)
                df_cache = merged
                # IBKR can return the "latest available" bars even when the requested cursor_end is
                # beyond the true available range (holiday/early close/entitlement gaps). In that
                # case, `fetched` may contain *no newer bars* than the existing cache. Without an
                # explicit negative cache marker, the caller will keep re-submitting the same
                # history request as the backtest clock advances.
                try:
                    new_max = df_cache.index.max() if not df_cache.empty else None
                    if prev_max is not None and new_max is not None and new_max <= prev_max:
                        prev_max_utc = _to_utc(prev_max.to_pydatetime() if hasattr(prev_max, "to_pydatetime") else prev_max)
                        seg_start_utc = _to_utc(seg_start)
                        seg_end_utc = _to_utc(seg_end)
                        is_tail_extension = abs((seg_start_utc - prev_max_utc).total_seconds()) <= 1.0 and seg_end_utc > prev_max_utc
                        if is_tail_extension:
                            missing_start = prev_max_utc + timedelta(seconds=1)
                            if missing_start < seg_end_utc:
                                _record_missing_window(
                                    asset=asset,
                                    quote=quote,
                                    timestep=timestep,
                                    exchange=effective_exchange,
                                    source=history_source,
                                    start_dt=missing_start,
                                    end_dt=seg_end_utc,
                                )
                                df_cache = _read_cache_frame(cache_file)
                except Exception:
                    pass

    if df_cache.empty:
        return df_cache

    # Best-effort: derive actionable bid/ask quotes for crypto *minute* bars so quote-based fills
    # behave realistically (buy at ask, sell at bid). IBKR history does not return separate
    # bid/ask fields, so we reconstruct them from Bid_Ask + Midpoint when needed.
    #
    # IMPORTANT (performance): do not do this for daily series (and avoid doing it for large
    # multi-month windows unless required) because it multiplies request volume.
    if (not source_was_explicit) and asset_type == "crypto" and str(timestep_component).endswith("minute"):
        df_aug, changed = _maybe_augment_crypto_bid_ask(
            df_cache=df_cache,
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start_utc,
            end_dt=end_utc,
            exchange=effective_exchange,
            include_after_hours=include_after_hours,
        )
        if changed:
            _write_cache_frame(cache_file, df_aug)
            df_cache = df_aug

    if (
        _enable_futures_bid_ask_derivation()
        and (not source_was_explicit)
        and asset_type in {"future", "cont_future"}
        and str(timestep_component).endswith(("minute", "hour"))
    ):
        df_aug, changed = _maybe_augment_futures_bid_ask(
            df_cache=df_cache,
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start_utc,
            end_dt=end_utc,
            exchange=effective_exchange,
            include_after_hours=include_after_hours,
        )
        if changed:
            _write_cache_frame(cache_file, df_aug)
            df_cache = df_aug

    # Remove placeholder rows from the returned frame (but keep them in cache).
    frame = df_cache.loc[(df_cache.index >= start_local) & (df_cache.index <= end_local)].copy()
    if "missing" in frame.columns:
        frame = frame[~frame["missing"].fillna(False)]
        frame = frame.drop(columns=["missing"], errors="ignore")
    return frame


def _frame_has_actionable_bid_ask(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    if "bid" not in df.columns or "ask" not in df.columns:
        return False
    bid = pd.to_numeric(df["bid"], errors="coerce")
    ask = pd.to_numeric(df["ask"], errors="coerce")
    spread = ask - bid
    return bool((spread > 0).any())


def _resolve_cont_future_segments(*, asset: Asset, start_dt: datetime, end_dt: datetime, exchange: Optional[str]) -> list[tuple[Asset, datetime, datetime]]:
    """Resolve a `cont_future` asset into a list of explicit futures contract segments.

    This follows LumiBot's roll schedule (`lumibot.tools.futures_roll`) so that backtests
    match live broker semantics and remain consistent across backtesting environments.
    """
    try:
        from lumibot.tools import futures_roll
    except Exception:
        return []

    start_utc = _to_utc(start_dt)
    end_utc = _to_utc(end_dt)
    if start_utc > end_utc:
        start_utc, end_utc = end_utc, start_utc

    try:
        schedule = futures_roll.build_roll_schedule(asset, start_utc, end_utc, year_digits=2)
    except Exception:
        schedule = []
    if not schedule:
        return []

    segments: list[tuple[Asset, datetime, datetime]] = []
    for contract_symbol, seg_start, seg_end in schedule:
        year, month = _parse_contract_year_month(contract_symbol)
        expiration = _contract_expiration_date(asset.symbol, year=year, month=month)
        contract_asset = Asset(asset.symbol, asset_type=Asset.AssetType.FUTURE, expiration=expiration)
        # Validate that we can resolve an explicit conid for this contract month.
        try:
            _resolve_conid(asset=contract_asset, quote=None, exchange=exchange)
        except Exception as exc:
            # Do not crash the whole backtest because one contract month cannot resolve.
            #
            # This most commonly happens when:
            # - The root symbol is invalid (no contracts returned), or
            # - IBKR cannot discover a conid for an expired contract month via Client Portal.
            logger.error(
                "IBKR roll segment conid resolution failed for %s %s (%s): %s",
                getattr(asset, "symbol", None),
                expiration,
                contract_symbol,
                exc,
            )
            continue
        segments.append((contract_asset, _to_utc(seg_start), _to_utc(seg_end)))
    return segments


def _parse_contract_year_month(contract_symbol: str) -> tuple[int, int]:
    """Parse a futures contract symbol (e.g., MESZ25) into (year, month)."""
    symbol = (contract_symbol or "").strip().upper()
    if len(symbol) < 3:
        raise ValueError(f"Invalid contract symbol: {contract_symbol!r}")

    month_code = symbol[-3:-2]
    year_text = symbol[-2:]
    try:
        year_two = int(year_text)
    except Exception as exc:
        raise ValueError(f"Invalid futures year in {contract_symbol!r}") from exc

    # Assumption: 20xx is the relevant range for our backtests.
    year = 2000 + year_two

    try:
        from lumibot.tools import futures_roll

        reverse = {v: k for k, v in getattr(futures_roll, "_FUTURES_MONTH_CODES", {}).items()}
    except Exception:
        reverse = {"H": 3, "M": 6, "U": 9, "Z": 12}

    month = reverse.get(month_code)
    if month is None:
        raise ValueError(f"Invalid futures month code {month_code!r} in {contract_symbol!r}")
    return year, int(month)


def _contract_expiration_date(root_symbol: str, *, year: int, month: int):
    """Best-effort expiration date for a futures contract based on the roll rules."""
    try:
        from lumibot.tools import futures_roll

        rule = futures_roll.ROLL_RULES.get(str(root_symbol).upper())
        anchor = getattr(rule, "anchor", None) if rule else None

        if anchor == "third_last_business_day":
            expiry = futures_roll._third_last_business_day(year, month)
        elif anchor == "last_friday":
            expiry = futures_roll._last_friday_trading_day(year, month)
        elif anchor == "cl_last_trade":
            expiry = futures_roll._cl_last_trade_date(year, month)
        elif anchor == "mcl_last_trade":
            expiry = futures_roll._mcl_last_trade_date(year, month)
        else:
            # Default anchor for CME equity index futures is third Friday.
            expiry = futures_roll._third_friday(year, month)
        return expiry.date()
    except Exception:
        # Safe fallback: third Friday.
        from datetime import date, timedelta

        first = date(year, month, 1)
        days_until_friday = (4 - first.weekday()) % 7
        first_friday = first + timedelta(days=days_until_friday)
        third_friday = first_friday + timedelta(days=14)
        return third_friday


def _get_cached_bars_for_source(
    *,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
    source: str,
) -> pd.DataFrame:
    start_utc = _to_utc(start_dt)
    end_utc = _to_utc(end_dt)
    if start_utc > end_utc:
        start_utc, end_utc = end_utc, start_utc
    start_local = start_utc.astimezone(LUMIBOT_DEFAULT_PYTZ)
    end_local = end_utc.astimezone(LUMIBOT_DEFAULT_PYTZ)

    history_source = _normalize_history_source(source)
    cache_file = _cache_file_for(asset=asset, quote=quote, timestep=timestep, exchange=exchange, source=history_source)
    cache_manager = get_backtest_cache()
    try:
        cache_manager.ensure_local_file(
            cache_file,
            payload=_remote_payload(asset, quote, timestep, exchange, history_source),
        )
    except Exception:
        pass

    df_cache = _read_cache_frame(cache_file)
    if not df_cache.empty:
        coverage_start = df_cache.index.min()
        coverage_end = df_cache.index.max()
    else:
        coverage_start = None
        coverage_end = None

    end_tolerance = timedelta(0)
    try:
        ibkr_bar, _, _ = _timestep_to_ibkr_bar(timestep)

        def _bar_delta(bar: str) -> timedelta:
            b = (bar or "").strip().lower()
            if b.endswith("min"):
                return timedelta(minutes=int(b.removesuffix("min") or "1"))
            if b.endswith("h"):
                return timedelta(hours=int(b.removesuffix("h") or "1"))
            if b.endswith("d"):
                return timedelta(days=int(b.removesuffix("d") or "1"))
            return timedelta(0)

        end_tolerance = _bar_delta(ibkr_bar) * 3
    except Exception:
        end_tolerance = timedelta(0)

    needs_fetch = (
        coverage_start is None
        or coverage_end is None
        or start_local < coverage_start
        or (end_local > coverage_end and (end_tolerance <= timedelta(0) or (end_local - coverage_end) > end_tolerance))
    )

    if needs_fetch:
        segments: list[tuple[datetime, datetime]] = []
        if coverage_start is None or coverage_end is None:
            segments.append((start_utc, end_utc))
        else:
            if end_local < coverage_start or start_local > coverage_end:
                segments.append((start_utc, end_utc))
            else:
                if start_local < coverage_start:
                    segments.append((start_utc, coverage_start.astimezone(timezone.utc)))
                if end_local > coverage_end:
                    segments.append((coverage_end.astimezone(timezone.utc), end_utc))

        for seg_start, seg_end in segments:
            if seg_start >= seg_end:
                continue
            prev_max = df_cache.index.max() if not df_cache.empty else None
            try:
                fetched = _fetch_history_between_dates(
                    asset=asset,
                    quote=quote,
                    timestep=timestep,
                    start_dt=seg_start,
                    end_dt=seg_end,
                    exchange=exchange,
                    include_after_hours=include_after_hours,
                    source=history_source,
                    source_was_explicit=True,
                )
            except Exception as exc:
                logger.error(
                    "IBKR history fetch failed for %s/%s timestep=%s exchange=%s source=%s: %s",
                    getattr(asset, "symbol", None),
                    getattr(quote, "symbol", None) if quote else None,
                    timestep,
                    exchange,
                    history_source,
                    exc,
                )
                fetched = pd.DataFrame()
            if fetched is not None and not fetched.empty:
                merged = _merge_frames(df_cache, fetched)
                _write_cache_frame(cache_file, merged)
                df_cache = merged
                # IBKR can return the "latest available" bars even when the requested cursor_end is
                # beyond the true available range (holiday/early close/entitlement gaps). In that
                # case, `fetched` may contain *no newer bars* than the existing cache, and if we do
                # nothing we'll keep re-submitting the same history request in a loop as the
                # backtest clock advances.
                #
                # Negative-cache this "stale end" by recording a missing window that extends
                # coverage to the requested bound. The placeholder rows are filtered out before
                # returning bars, so this does not create synthetic liquidity.
                try:
                    new_max = df_cache.index.max() if not df_cache.empty else None
                    if prev_max is not None and new_max is not None and new_max <= prev_max:
                        prev_max_utc = _to_utc(prev_max.to_pydatetime() if hasattr(prev_max, "to_pydatetime") else prev_max)
                        seg_start_utc = _to_utc(seg_start)
                        seg_end_utc = _to_utc(seg_end)
                        is_tail_extension = abs((seg_start_utc - prev_max_utc).total_seconds()) <= 1.0 and seg_end_utc > prev_max_utc
                        if is_tail_extension:
                            # Start the missing window just *after* the last real bar to avoid
                            # clobbering the bar at `prev_max` when merging placeholder rows.
                            missing_start = prev_max_utc + timedelta(seconds=1)
                            if missing_start >= seg_end_utc:
                                continue
                            _record_missing_window(
                                asset=asset,
                                quote=quote,
                                timestep=timestep,
                                exchange=exchange,
                                source=history_source,
                                start_dt=missing_start,
                                end_dt=seg_end_utc,
                            )
                            # Keep the in-memory view in sync for any further segment checks.
                            df_cache = _read_cache_frame(cache_file)
                except Exception:
                    pass

    if df_cache.empty:
        return df_cache

    frame = df_cache.loc[(df_cache.index >= start_local) & (df_cache.index <= end_local)].copy()
    if "missing" in frame.columns:
        frame = frame[~frame["missing"].fillna(False)]
        frame = frame.drop(columns=["missing"], errors="ignore")
    return frame


def _maybe_augment_crypto_bid_ask(
    *,
    df_cache: pd.DataFrame,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
) -> tuple[pd.DataFrame, bool]:
    if df_cache is None or df_cache.empty:
        return df_cache, False
    if _frame_has_actionable_bid_ask(df_cache):
        return df_cache, False

    try:
        bid_ask = _get_cached_bars_for_source(
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start_dt,
            end_dt=end_dt,
            exchange=exchange,
            include_after_hours=include_after_hours,
            source="Bid_Ask",
        )
        midpoint = _get_cached_bars_for_source(
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start_dt,
            end_dt=end_dt,
            exchange=exchange,
            include_after_hours=include_after_hours,
            source="Midpoint",
        )
    except Exception:
        return df_cache, False

    derived = _derive_bid_ask_from_bid_ask_and_midpoint(bid_ask, midpoint)
    if derived is None or derived.empty:
        return df_cache, False

    updated = df_cache.copy()
    updated.loc[derived.index, "bid"] = derived["bid"]
    updated.loc[derived.index, "ask"] = derived["ask"]

    # Any residual NaNs fall back to the trade/mark close.
    if "close" in updated.columns:
        updated["bid"] = pd.to_numeric(updated.get("bid"), errors="coerce").where(
            ~pd.to_numeric(updated.get("bid"), errors="coerce").isna(),
            pd.to_numeric(updated.get("close"), errors="coerce"),
        )
        updated["ask"] = pd.to_numeric(updated.get("ask"), errors="coerce").where(
            ~pd.to_numeric(updated.get("ask"), errors="coerce").isna(),
            pd.to_numeric(updated.get("close"), errors="coerce"),
        )

    if not _frame_has_actionable_bid_ask(updated):
        return df_cache, False

    return updated, True


def _maybe_augment_futures_bid_ask(
    *,
    df_cache: pd.DataFrame,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
) -> tuple[pd.DataFrame, bool]:
    if not _enable_futures_bid_ask_derivation():
        return df_cache, False
    if df_cache is None or df_cache.empty:
        return df_cache, False
    if _frame_has_actionable_bid_ask(df_cache):
        return df_cache, False

    try:
        bid_ask = _get_cached_bars_for_source(
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start_dt,
            end_dt=end_dt,
            exchange=exchange,
            include_after_hours=include_after_hours,
            source="Bid_Ask",
        )
        midpoint = _get_cached_bars_for_source(
            asset=asset,
            quote=quote,
            timestep=timestep,
            start_dt=start_dt,
            end_dt=end_dt,
            exchange=exchange,
            include_after_hours=include_after_hours,
            source="Midpoint",
        )
    except Exception:
        return df_cache, False

    derived = _derive_bid_ask_from_bid_ask_and_midpoint(bid_ask, midpoint)
    if derived is None or derived.empty:
        return df_cache, False

    updated = df_cache.copy()
    updated.loc[derived.index, "bid"] = derived["bid"]
    updated.loc[derived.index, "ask"] = derived["ask"]

    if "close" in updated.columns:
        updated["bid"] = pd.to_numeric(updated.get("bid"), errors="coerce").where(
            ~pd.to_numeric(updated.get("bid"), errors="coerce").isna(),
            pd.to_numeric(updated.get("close"), errors="coerce"),
        )
        updated["ask"] = pd.to_numeric(updated.get("ask"), errors="coerce").where(
            ~pd.to_numeric(updated.get("ask"), errors="coerce").isna(),
            pd.to_numeric(updated.get("close"), errors="coerce"),
        )

    if not _frame_has_actionable_bid_ask(updated):
        return df_cache, False

    return updated, True


def _fetch_history_between_dates(
    *,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
    source: str,
    source_was_explicit: bool,
) -> pd.DataFrame:
    conid = _resolve_conid(asset=asset, quote=quote, exchange=exchange)
    bar, bar_seconds, _cache_timestep = _timestep_to_ibkr_bar(timestep)
    period = _max_period_for_bar(bar)
    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    # IBKR's `continuous=true` is IBKR-specific roll behavior. For LumiBot `cont_future` assets
    # we prefer our own synthetic roll (explicit contract series per expiration) so parity is
    # stable across data providers. Only request IBKR "continuous" when we truly do not have an
    # explicit expiration to anchor the contract.
    continuous = bool(asset_type == "cont_future" and getattr(asset, "expiration", None) is None)

    cursor_end = _to_utc(end_dt)
    start_dt = _to_utc(start_dt)
    chunks: list[pd.DataFrame] = []

    # Fetch backwards (end -> start) to accommodate IBKR's 1000 datapoint cap.
    while cursor_end > start_dt:
        payload = _ibkr_history_request(
            conid=conid,
            period=period,
            bar=bar,
            start_time=cursor_end,
            exchange=exchange,
            include_after_hours=include_after_hours,
            continuous=continuous,
            source=source,
        )

        # IBKR typically returns {"data":[...]} (empty list means no data).
        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            # True no-data: write placeholders so we don't hammer IBKR for the same range.
            _record_missing_window(
                asset=asset,
                quote=quote,
                timestep=timestep,
                exchange=exchange,
                source=source,
                start_dt=start_dt,
                end_dt=cursor_end,
            )
            return pd.DataFrame()

        df = _history_payload_to_frame(data, source_was_explicit=source_was_explicit)
        if df.empty:
            _record_missing_window(
                asset=asset,
                quote=quote,
                timestep=timestep,
                exchange=exchange,
                source=source,
                start_dt=start_dt,
                end_dt=cursor_end,
            )
            return pd.DataFrame()

        chunks.append(df)

        earliest = df.index.min()
        if earliest is None:
            break
        if earliest <= start_dt:
            break

        # Move cursor backwards.
        #
        # IBKR history bounds are effectively inclusive, and we de-dupe on merge anyway, so it is
        # safer to continue from `earliest` instead of subtracting a whole bar (which can skip the
        # requested start boundary for coarse bars like 1h/1d).
        next_cursor_end = earliest
        if next_cursor_end >= cursor_end:
            next_cursor_end = earliest - pd.Timedelta(seconds=bar_seconds)
        cursor_end = next_cursor_end

        # Do not assume `len(df) < 1000` implies we're at the start of history.
        # IBKR can return fewer bars due to gaps/vendor behavior; breaking early can leave large
        # holes (and can trigger stale-end refresh loops in daily backtests).

    if not chunks:
        return pd.DataFrame()

    merged = pd.concat(chunks, axis=0).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    # IMPORTANT: Do not clamp to the requested window here.
    #
    # IBKR can return the "latest available" bars even when the requested window is in the
    # future (or otherwise outside the available range). We still want to persist those bars
    # to the cache to warm future requests and avoid repeatedly hammering the downloader.
    #
    # The caller (`get_price_data`) performs the final slice for the requested time range.
    return merged


def _ibkr_history_request(
    *,
    conid: int,
    period: str,
    bar: str,
    start_time: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
    continuous: bool,
    source: str,
) -> Dict[str, Any]:
    base_url = _downloader_base_url()
    url = f"{base_url}/ibkr/iserver/marketdata/history"
    # IBKR Client Portal history endpoint interprets `startTime` as UTC.
    #
    # If we format `startTime` in a local timezone (e.g. America/New_York) while IBKR treats it
    # as UTC, paginating in 1000-bar chunks can create DST-sized holes (~4h in summer, ~5h in
    # winter). We have observed these holes as ~4h02/~5h02 gaps at chunk boundaries in cached
    # parquet files, which then cascades into stale-bar execution and parity failures.
    start_time_utc = _to_utc(start_time)
    query = {
        "conid": str(int(conid)),
        "period": period,
        "bar": bar,
        "outsideRth": "true" if include_after_hours else "false",
        "source": source,
        "startTime": start_time_utc.strftime("%Y%m%d-%H:%M:%S"),
    }
    if continuous:
        query["continuous"] = "true"
    if exchange:
        query["exchange"] = str(exchange)

    result = queue_request(url=url, querystring=query, headers=None, timeout=None)
    if result is None:
        return {}
    if isinstance(result, dict) and result.get("error"):
        # Do not treat entitlement errors as NO_DATA; surface them to the caller.
        raise RuntimeError(f"IBKR history error: {result.get('error')}")
    return result


def _history_payload_to_frame(data: Any, *, source_was_explicit: bool) -> pd.DataFrame:
    df = pd.DataFrame(data)
    if df.empty:
        return df

    # IBKR payload columns: t(ms), o, h, l, c, v.
    rename = {"t": "timestamp", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    df = df.rename(columns=rename)
    if "timestamp" not in df.columns:
        return pd.DataFrame()

    ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
    df = df.drop(columns=["timestamp"], errors="ignore")
    df.index = ts
    df = df[~df.index.isna()]
    df = df.sort_index()
    df.index = df.index.tz_convert(LUMIBOT_DEFAULT_PYTZ)
    df["missing"] = False
    # Default quote fields:
    #
    # When callers did not explicitly request a history `source`, we populate bid/ask with the
    # close as a fallback so quote-based fill logic remains functional even before we derive a
    # real spread (Bid_Ask + Midpoint).
    #
    # When the caller explicitly requests a history `source` (e.g., `source="Trades"` in a
    # deterministic parity suite), we intentionally DO NOT synthesize bid/ask so the engine
    # uses OHLC fills.
    if (not source_was_explicit) and "close" in df.columns:
        df["bid"] = df["close"]
        df["ask"] = df["close"]
    return df


def _derive_bid_ask_from_bid_ask_and_midpoint(
    bid_ask: pd.DataFrame,
    midpoint: pd.DataFrame,
) -> pd.DataFrame:
    """Derive per-bar bid/ask quotes using IBKR Bid_Ask + Midpoint history.

    IBKR's Client Portal history endpoint returns OHLC bars for different "sources":
    - Trades: prints-based bars
    - Midpoint: midpoint bars
    - Bid_Ask: IBKR-style BID_ASK bars (historically: open/low use bid, close/high use ask)

    The payload does NOT include separate bid/ask fields. For backtesting fills, we want a
    stable bid/ask at each bar timestamp. The best-effort reconstruction is:
    - ask_close = Bid_Ask.close
    - mid_close = Midpoint.close
    - bid_close = 2 * mid_close - ask_close

    The result is clamped defensively to avoid negative/inverted spreads.
    """
    if bid_ask is None or bid_ask.empty or midpoint is None or midpoint.empty:
        return pd.DataFrame()
    if "close" not in bid_ask.columns or "close" not in midpoint.columns:
        return pd.DataFrame()

    joined = (
        pd.concat(
            [
                bid_ask[["close"]].rename(columns={"close": "ask_close"}),
                midpoint[["close"]].rename(columns={"close": "mid_close"}),
            ],
            axis=1,
            join="inner",
        )
        .dropna()
        .copy()
    )
    if joined.empty:
        return pd.DataFrame()

    ask = pd.to_numeric(joined["ask_close"], errors="coerce")
    mid = pd.to_numeric(joined["mid_close"], errors="coerce")
    bid = 2 * mid - ask

    out = pd.DataFrame(index=joined.index)
    out["bid"] = bid
    out["ask"] = ask

    invalid = (
        out["bid"].isna()
        | out["ask"].isna()
        | (out["bid"] <= 0)
        | (out["ask"] <= 0)
        | (out["bid"] > out["ask"])
    )
    if invalid.any():
        mid_valid = mid > 0
        use_mid = invalid & mid_valid
        out.loc[use_mid, "bid"] = mid[use_mid]
        out.loc[use_mid, "ask"] = mid[use_mid]

        # If midpoint itself is invalid (<=0), leave as NaN so callers can fall back to
        # the trade/mark close instead of propagating negative prices into fills.
        use_nan = invalid & ~mid_valid
        if use_nan.any():
            out.loc[use_nan, "bid"] = float("nan")
            out.loc[use_nan, "ask"] = float("nan")

    return out


def _merge_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        return incoming
    if incoming is None or incoming.empty:
        return existing
    merged = pd.concat([existing, incoming], axis=0).sort_index()
    merged = merged[~merged.index.duplicated(keep="last")]
    if "missing" in merged.columns:
        merged["missing"] = merged["missing"].fillna(False)
    return merged


def _record_missing_window(
    *,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    exchange: Optional[str],
    source: str,
    start_dt: datetime,
    end_dt: datetime,
) -> None:
    # Add a bracketing placeholder window (two rows) to cache.
    cache_file = _cache_file_for(asset=asset, quote=quote, timestep=timestep, exchange=exchange, source=source)
    cache_manager = get_backtest_cache()
    try:
        cache_manager.ensure_local_file(cache_file, payload=_remote_payload(asset, quote, timestep, exchange, source))
    except Exception:
        pass

    df = _read_cache_frame(cache_file)
    placeholder = pd.DataFrame(
        {
            "open": [pd.NA, pd.NA],
            "high": [pd.NA, pd.NA],
            "low": [pd.NA, pd.NA],
            "close": [pd.NA, pd.NA],
            "volume": [pd.NA, pd.NA],
            "missing": [True, True],
        },
        index=pd.DatetimeIndex([_to_utc(start_dt), _to_utc(end_dt)]).tz_convert(LUMIBOT_DEFAULT_PYTZ),
    )
    merged = _merge_frames(df, placeholder)
    _write_cache_frame(cache_file, merged)


def _crypto_day_bounds(start_local: datetime, end_local: datetime) -> tuple[datetime, datetime]:
    """Return inclusive midnight-to-midnight day bucket bounds in `LUMIBOT_DEFAULT_PYTZ`.

    LumiBot treats BACKTESTING_END as exclusive. If the requested end timestamp is exactly
    midnight, exclude that day from the derived daily series.
    """
    start_day = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if end_local == end_day:
        end_day = end_day - pd.Timedelta(days=1)
    if end_day < start_day:
        end_day = start_day
    return start_day, end_day


def _derive_daily_from_intraday(
    intraday: pd.DataFrame,
    *,
    start_day: datetime,
    end_day: datetime,
) -> pd.DataFrame:
    """Derive daily OHLCV bars from an intraday OHLCV dataframe (crypto: 24/7 days)."""
    idx = pd.date_range(start=start_day, end=end_day, freq="D", tz=LUMIBOT_DEFAULT_PYTZ)
    if intraday is None or intraday.empty:
        out = pd.DataFrame(index=idx, columns=["open", "high", "low", "close", "volume", "missing"])
        out["missing"] = True
        return out

    df = intraday.copy()
    df.index = pd.to_datetime(df.index, utc=True, errors="coerce").tz_convert(LUMIBOT_DEFAULT_PYTZ)
    df = df[~df.index.isna()].sort_index()
    if df.empty:
        out = pd.DataFrame(index=idx, columns=["open", "high", "low", "close", "volume", "missing"])
        out["missing"] = True
        return out

    day_key = df.index.normalize()
    grouped = df.groupby(day_key)
    daily = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(min_count=1) if "volume" in df.columns else pd.NA,
        }
    )
    daily_idx = pd.DatetimeIndex(daily.index)
    if daily_idx.tz is None:
        daily.index = daily_idx.tz_localize(LUMIBOT_DEFAULT_PYTZ)
    else:
        daily.index = daily_idx.tz_convert(LUMIBOT_DEFAULT_PYTZ)
    daily = daily.sort_index()
    daily["missing"] = False

    daily = daily.reindex(idx)
    close = pd.to_numeric(daily.get("close"), errors="coerce")
    daily["missing"] = daily["missing"].fillna(True) | close.isna()

    # IBKR crypto history is often effectively 24/5: weekend days may be absent even though
    # strategies are frequently configured as 24/7. To keep daily-cadence backtests stable
    # (no refresh loops / "missing BTC day"), forward-fill short gaps (<= 3 days) using the
    # prior close. This mirrors the existing Data.checker() tolerance window.
    if close is not None and not close.empty:
        filled_close = close.ffill(limit=3)
        filled_mask = close.isna() & filled_close.notna()
        if filled_mask.any():
            daily.loc[filled_mask, "close"] = filled_close[filled_mask]
            for col in ("open", "high", "low"):
                if col in daily.columns:
                    daily.loc[filled_mask, col] = pd.to_numeric(daily.loc[filled_mask, col], errors="coerce").fillna(
                        daily.loc[filled_mask, "close"]
                    )
            if "volume" in daily.columns:
                daily.loc[filled_mask, "volume"] = pd.to_numeric(daily.loc[filled_mask, "volume"], errors="coerce").fillna(0)
            daily.loc[filled_mask, "missing"] = False
    return daily


def _get_crypto_daily_bars(
    *,
    asset: Asset,
    quote: Optional[Asset],
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
    source: str,
) -> pd.DataFrame:
    """Return crypto daily bars aligned to midnight days in `LUMIBOT_DEFAULT_PYTZ`."""
    start_local = _to_utc(start_dt).astimezone(LUMIBOT_DEFAULT_PYTZ)
    end_local = _to_utc(end_dt).astimezone(LUMIBOT_DEFAULT_PYTZ)
    start_day, end_day = _crypto_day_bounds(start_local, end_local)

    exch = (exchange or os.environ.get("IBKR_CRYPTO_VENUE") or IBKR_DEFAULT_CRYPTO_VENUE).strip().upper()
    # IMPORTANT: keep derived daily bars in a separate cache namespace so we don't mix them with
    # legacy `bar=1d` results (which have different semantics and timestamps).
    derived_source = f"{source}_DERIVED_DAILY"
    cache_file = _cache_file_for(asset=asset, quote=quote, timestep="day", exchange=exch, source=derived_source)
    cache = ParquetSeriesCache(cache_file, remote_payload=_remote_payload(asset, quote, "day", exch, derived_source))
    cache.hydrate_remote()
    df_cache = cache.read()

    if not df_cache.empty:
        coverage_start = df_cache.index.min()
        coverage_end = df_cache.index.max()
    else:
        coverage_start = None
        coverage_end = None

    needs_fetch = (
        coverage_start is None
        or coverage_end is None
        or start_day < coverage_start
        or end_day > coverage_end
    )

    if needs_fetch:
        fetch_start = start_day if coverage_start is None else min(start_day, coverage_start)
        fetch_end = (end_day + pd.Timedelta(days=1)) if coverage_end is None else max(end_day + pd.Timedelta(days=1), coverage_end)

        hourly = _get_cached_bars_for_source(
            asset=asset,
            quote=quote,
            timestep="hour",
            start_dt=fetch_start,
            end_dt=fetch_end,
            exchange=exch,
            include_after_hours=include_after_hours,
            source=source,
        )
        daily = _derive_daily_from_intraday(hourly, start_day=fetch_start, end_day=(fetch_end - pd.Timedelta(days=1)))

        missing_days = daily.index[daily["missing"].fillna(True)]
        for day in missing_days:
            day_start = day
            day_end = day + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
            minute = _get_cached_bars_for_source(
                asset=asset,
                quote=quote,
                timestep="minute",
                start_dt=day_start,
                end_dt=day_end,
                exchange=exch,
                include_after_hours=True,
                source=source,
            )
            if minute is None or minute.empty:
                continue
            filled = _derive_daily_from_intraday(minute, start_day=day_start, end_day=day_start)
            if not filled.empty and not bool(filled["missing"].iloc[0]):
                daily.loc[day_start, ["open", "high", "low", "close", "volume"]] = filled.iloc[0][
                    ["open", "high", "low", "close", "volume"]
                ]
                daily.loc[day_start, "missing"] = False

        merged = ParquetSeriesCache.merge(df_cache, daily)
        cache.write(merged, remote_payload=_remote_payload(asset, quote, "day", exch, derived_source))
        df_cache = merged

    if df_cache.empty:
        return df_cache

    frame = df_cache.loc[(df_cache.index >= start_day) & (df_cache.index <= end_day)].copy()
    if "missing" in frame.columns:
        frame = frame[~frame["missing"].fillna(False)]
        frame = frame.drop(columns=["missing"], errors="ignore")
    if "close" in frame.columns:
        frame["bid"] = pd.to_numeric(frame.get("bid", frame["close"]), errors="coerce").fillna(frame["close"])
        frame["ask"] = pd.to_numeric(frame.get("ask", frame["close"]), errors="coerce").fillna(frame["close"])
    return frame


def _get_futures_daily_bars(
    *,
    asset: Asset,
    quote: Optional[Asset],
    start_dt: datetime,
    end_dt: datetime,
    exchange: Optional[str],
    include_after_hours: bool,
    source: str,
) -> pd.DataFrame:
    """Derive `day` bars aligned to the `us_futures` session (not midnight).

    This is intentionally session-based because futures strategies commonly use
    `self.set_market("us_futures")` and LumiBot's backtesting clock advances based on that calendar.
    """

    try:
        import pandas_market_calendars as mcal
    except Exception:
        return pd.DataFrame()

    start_utc = _to_utc(start_dt)
    end_utc = _to_utc(end_dt)
    if start_utc > end_utc:
        start_utc, end_utc = end_utc, start_utc
    start_local = start_utc.astimezone(LUMIBOT_DEFAULT_PYTZ)
    end_local = end_utc.astimezone(LUMIBOT_DEFAULT_PYTZ)

    cal = mcal.get_calendar("us_futures")
    schedule = cal.schedule(
        start_date=pd.Timestamp(start_utc.date()) - pd.Timedelta(days=2),
        end_date=pd.Timestamp(end_utc.date()) + pd.Timedelta(days=2),
    )
    if schedule is None or schedule.empty:
        return pd.DataFrame()

    session_start = pd.Timestamp(schedule["market_open"].min()).tz_convert("UTC").to_pydatetime()
    session_end = pd.Timestamp(schedule["market_close"].max()).tz_convert("UTC").to_pydatetime()
    if session_start >= session_end:
        return pd.DataFrame()

    # Prefer hourly bars for speed (deriving daily from minute across long windows is too slow).
    intraday = _get_cached_bars_for_source(
        asset=asset,
        quote=quote,
        timestep="hour",
        start_dt=session_start,
        end_dt=session_end,
        exchange=exchange,
        include_after_hours=include_after_hours,
        source=source,
    )
    intraday_timestep = "hour"
    if intraday is None or intraday.empty:
        intraday = _get_cached_bars_for_source(
            asset=asset,
            quote=quote,
            timestep="minute",
            start_dt=session_start,
            end_dt=session_end,
            exchange=exchange,
            include_after_hours=include_after_hours,
            source=source,
        )
        intraday_timestep = "minute"
        if intraday is None or intraday.empty:
            return pd.DataFrame()

    if _enable_futures_bid_ask_derivation():
        intraday, _ = _maybe_augment_futures_bid_ask(
            df_cache=intraday,
            asset=asset,
            quote=quote,
            timestep=intraday_timestep,
            start_dt=session_start,
            end_dt=session_end,
            exchange=exchange,
            include_after_hours=include_after_hours,
        )

    rows: list[dict[str, float]] = []
    idx: list[pd.Timestamp] = []
    minute_fallback: Optional[pd.DataFrame] = None
    for _, sess in schedule.iterrows():
        open_local = pd.Timestamp(sess["market_open"]).tz_convert("UTC").tz_convert(LUMIBOT_DEFAULT_PYTZ)
        close_local = pd.Timestamp(sess["market_close"]).tz_convert("UTC").tz_convert(LUMIBOT_DEFAULT_PYTZ)
        if close_local < start_local or open_local > end_local:
            continue
        window = intraday.loc[(intraday.index >= open_local) & (intraday.index <= close_local)]
        if window.empty and intraday_timestep != "minute":
            if minute_fallback is None:
                minute_fallback = _get_cached_bars_for_source(
                    asset=asset,
                    quote=quote,
                    timestep="minute",
                    start_dt=session_start,
                    end_dt=session_end,
                    exchange=exchange,
                    include_after_hours=include_after_hours,
                    source=source,
                )
                if _enable_futures_bid_ask_derivation():
                    minute_fallback, _ = _maybe_augment_futures_bid_ask(
                        df_cache=minute_fallback,
                        asset=asset,
                        quote=quote,
                        timestep="minute",
                        start_dt=session_start,
                        end_dt=session_end,
                        exchange=exchange,
                        include_after_hours=include_after_hours,
                    )
            if minute_fallback is not None and not minute_fallback.empty:
                window = minute_fallback.loc[(minute_fallback.index >= open_local) & (minute_fallback.index <= close_local)]
        if window.empty:
            continue

        open_px = float(window["open"].iloc[0]) if "open" in window.columns else float(window["close"].iloc[0])
        high_px = float(pd.to_numeric(window.get("high"), errors="coerce").max()) if "high" in window.columns else float(window["close"].max())
        low_px = float(pd.to_numeric(window.get("low"), errors="coerce").min()) if "low" in window.columns else float(window["close"].min())
        close_px = float(pd.to_numeric(window.get("close"), errors="coerce").iloc[-1])
        vol = float(pd.to_numeric(window.get("volume"), errors="coerce").fillna(0).sum()) if "volume" in window.columns else 0.0

        payload: dict[str, float] = {"open": open_px, "high": high_px, "low": low_px, "close": close_px, "volume": vol}
        if "bid" in window.columns:
            payload["bid"] = float(pd.to_numeric(window.get("bid"), errors="coerce").iloc[-1])
        if "ask" in window.columns:
            payload["ask"] = float(pd.to_numeric(window.get("ask"), errors="coerce").iloc[-1])

        rows.append(payload)
        idx.append(close_local)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    df = df.sort_index()
    df.index = df.index.tz_convert(LUMIBOT_DEFAULT_PYTZ)
    return df.loc[(df.index >= start_local) & (df.index <= end_local)]


def _resolve_conid(*, asset: Asset, quote: Optional[Asset], exchange: Optional[str]) -> int:
    cache_file = Path(LUMIBOT_CACHE_FOLDER) / CACHE_SUBFOLDER / "conids.json"
    cache_manager = get_backtest_cache()
    try:
        cache_manager.ensure_local_file(cache_file, payload={"provider": "ibkr", "type": "conids"})
    except Exception:
        pass

    mapping: Dict[str, int] = {}
    if cache_file.exists():
        try:
            mapping = json.loads(cache_file.read_text(encoding="utf-8")) or {}
        except Exception:
            mapping = {}

    # Seed conids.json across cache namespaces.
    #
    # Production backtests often run with a fresh S3 cache version/prefix to simulate cold-cache
    # behavior. IBKR Client Portal cannot resolve conids for *expired* futures contracts, so
    # historical futures backtests depend on the shared conid registry (`ibkr/conids.json`).
    #
    # If the current cache namespace does not contain `conids.json`, fall back to the default
    # `v1` namespace and materialize it locally (and, when possible, upload it into the current
    # namespace) so we do not thrash the downloader in a hot loop.
    if (not mapping) and (not cache_file.exists()) and getattr(cache_manager, "enabled", False):
        settings = getattr(cache_manager, "_settings", None)
        try:
            backend = getattr(settings, "backend", None)
            bucket = str(getattr(settings, "bucket", "") or "")
            prefix = str(getattr(settings, "prefix", "") or "").strip("/")
            version = str(getattr(settings, "version", "") or "").strip("/")
        except Exception:
            backend = None
            bucket = ""
            prefix = ""
            version = ""

        if backend == "s3" and bucket and version and version != "v1":
            try:
                relative_path = cache_file.resolve().relative_to(Path(LUMIBOT_CACHE_FOLDER).resolve()).as_posix()
            except Exception:
                relative_path = f"{CACHE_SUBFOLDER}/conids.json"

            seed_components = [prefix, "v1", relative_path]
            seed_key = "/".join([c for c in seed_components if c])
            seed_mapping: Dict[str, int] = {}
            try:
                seed_mapping = _download_remote_conids_json(cache_manager, bucket=bucket, key=seed_key)
            except Exception as exc:
                if _is_not_found_error(cache_manager, exc):
                    seed_mapping = {}
                else:
                    seed_mapping = {}

            if seed_mapping:
                mapping = dict(seed_mapping)
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
                try:
                    # Best-effort: upload into the current cache namespace so subsequent runs can
                    # reuse without re-downloading from the seed namespace.
                    _merge_upload_conids_json(cache_manager, cache_file, mapping=mapping, required_keys=set())
                except Exception:
                    pass

    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    effective_exchange = exchange
    if asset_type in {"future", "cont_future"} and not effective_exchange:
        effective_exchange = _resolve_futures_exchange(getattr(asset, "symbol", ""))

    # Conid keying is not fully uniform across historical caches (some runs key futures with
    # quote_symbol="USD", others omit it). For robustness (and to avoid unnecessary remote
    # lookups), try a small set of equivalent keys before falling back to the downloader.
    primary = _conid_key(asset=asset, quote=quote, exchange=effective_exchange)
    candidates = [primary.to_key()]
    if asset_type in {"future", "cont_future"}:
        if primary.quote_symbol:
            candidates.append(IbkrConidKey(primary.asset_type, primary.symbol, "", primary.exchange, primary.expiration).to_key())
        else:
            candidates.append(IbkrConidKey(primary.asset_type, primary.symbol, "USD", primary.exchange, primary.expiration).to_key())

    for key in candidates:
        cached = mapping.get(key)
        if isinstance(cached, int) and cached > 0:
            return cached

    keys_added: set[str] = set()
    conid = _lookup_conid_remote(asset=asset, quote=quote, exchange=effective_exchange, mapping=mapping, keys_added=keys_added)
    # Always persist under the primary key for forward consistency.
    primary_key = primary.to_key()
    conid_int = int(conid)
    prior_primary = mapping.get(primary_key)
    mapping[primary_key] = conid_int
    if prior_primary != conid_int:
        keys_added.add(primary_key)
    if asset_type in {"future", "cont_future"}:
        # Mirror the primary conid under both quote_symbol variants for compatibility with
        # historical caches and older in-flight backtests.
        if primary.quote_symbol:
            alt_key = IbkrConidKey(primary.asset_type, primary.symbol, "", primary.exchange, primary.expiration).to_key()
        else:
            alt_key = IbkrConidKey(primary.asset_type, primary.symbol, "USD", primary.exchange, primary.expiration).to_key()
        prior_alt = mapping.get(alt_key)
        mapping[alt_key] = conid_int
        if prior_alt != conid_int:
            keys_added.add(alt_key)

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
    try:
        _merge_upload_conids_json(cache_manager, cache_file, mapping=mapping, required_keys=keys_added)
    except Exception:
        pass
    return int(conid)


def _is_not_found_error(cache_manager, exc: Exception) -> bool:
    try:
        fn = getattr(cache_manager, "_is_not_found_error", None)
        if callable(fn):
            return bool(fn(exc))
    except Exception:
        pass
    msg = str(exc).lower()
    return any(token in msg for token in ("nosuchkey", "not found", "404", "no such key"))


def _download_remote_conids_json(cache_manager, *, bucket: str, key: str) -> Dict[str, int]:
    client = getattr(cache_manager, "_get_client", None)
    if not callable(client):
        return {}
    s3 = client()
    if not hasattr(s3, "get_object"):
        return {}
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response.get("Body")
    raw = b""
    if body is not None:
        raw = body.read()
        try:
            body.close()
        except Exception:
            pass
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}
    out: Dict[str, int] = {}
    for k, v in parsed.items():
        try:
            iv = int(v)
        except Exception:
            continue
        if iv > 0:
            out[str(k)] = iv
    return out


def _persist_s3_marker(*, local_path: Path, remote_key: str) -> None:
    try:
        marker_path = local_path.with_suffix(local_path.suffix + ".s3key")
        marker_tmp = marker_path.with_suffix(marker_path.suffix + ".tmp")
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_tmp.write_text(remote_key, encoding="utf-8")
        os.replace(marker_tmp, marker_path)
    except Exception:
        pass


def _merge_upload_conids_json(
    cache_manager,
    local_path: Path,
    *,
    mapping: Dict[str, int],
    required_keys: set[str],
    max_attempts: int = 3,
) -> None:
    """Upload `ibkr/conids.json` with a merge-before-upload retry to reduce lost updates."""
    if not cache_manager.enabled or cache_manager.mode != CacheMode.S3_READWRITE:
        cache_manager.on_local_update(local_path, payload={"provider": "ibkr", "type": "conids"})
        return

    settings = getattr(cache_manager, "_settings", None)
    if settings is None or getattr(settings, "backend", None) != "s3":
        cache_manager.on_local_update(local_path, payload={"provider": "ibkr", "type": "conids"})
        return

    remote_key = cache_manager.remote_key_for(local_path, payload={"provider": "ibkr", "type": "conids"})
    if not remote_key:
        cache_manager.on_local_update(local_path, payload={"provider": "ibkr", "type": "conids"})
        return

    bucket = str(getattr(settings, "bucket", "") or "")
    if not bucket:
        cache_manager.on_local_update(local_path, payload={"provider": "ibkr", "type": "conids"})
        return

    client_fn = getattr(cache_manager, "_get_client", None)
    if not callable(client_fn):
        cache_manager.on_local_update(local_path, payload={"provider": "ibkr", "type": "conids"})
        return
    s3 = client_fn()
    if not hasattr(s3, "upload_file") or not hasattr(s3, "get_object"):
        cache_manager.on_local_update(local_path, payload={"provider": "ibkr", "type": "conids"})
        return

    # If this update didn't add anything new, a plain upload is fine.
    if not required_keys:
        s3.upload_file(str(local_path), bucket, remote_key)
        _persist_s3_marker(local_path=local_path, remote_key=remote_key)
        return

    last_exc: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            # Pull the freshest remote, union, then upload.
            try:
                remote = _download_remote_conids_json(cache_manager, bucket=bucket, key=remote_key)
            except Exception as exc:
                if _is_not_found_error(cache_manager, exc):
                    remote = {}
                else:
                    raise
            merged = dict(remote)
            merged.update(mapping)
            if merged != mapping:
                mapping.clear()
                mapping.update(merged)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")

            s3.upload_file(str(local_path), bucket, remote_key)

            # Verify: ensure the keys we just added are present remotely.
            verified = _download_remote_conids_json(cache_manager, bucket=bucket, key=remote_key)
            if required_keys.issubset(set(verified.keys())):
                _persist_s3_marker(local_path=local_path, remote_key=remote_key)
                return

            # Lost update: retry after a short backoff.
            time.sleep(0.15 * (attempt + 1))
        except Exception as exc:
            last_exc = exc
            time.sleep(0.15 * (attempt + 1))

    if last_exc is not None:
        logger.warning("IBKR conids.json merge-upload failed after retries: %s", last_exc)


def _lookup_conid_remote(
    *,
    asset: Asset,
    quote: Optional[Asset],
    exchange: Optional[str],
    mapping: Optional[Dict[str, int]] = None,
    keys_added: Optional[set[str]] = None,
) -> int:
    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    if asset_type in {"future", "cont_future"}:
        if getattr(asset, "expiration", None) is None and asset_type != "cont_future" and not getattr(asset, "auto_expiry", None):
            raise ValueError(
                "IBKR futures require an explicit expiration on Asset(asset_type='future'). "
                "Use asset_type='cont_future' for continuous futures."
            )
        return _lookup_conid_future(asset=asset, exchange=exchange, mapping=mapping, keys_added=keys_added)
    if asset_type in {"crypto"}:
        return _lookup_conid_crypto(asset=asset, quote=quote)

    # Default: fall back to secdef search and use the first conid.
    base_url = _downloader_base_url()
    url = f"{base_url}/ibkr/iserver/secdef/search"
    payload = queue_request(url=url, querystring={"symbol": asset.symbol}, headers=None, timeout=None)
    if isinstance(payload, list) and payload:
        conid = payload[0].get("conid")
        if conid is not None:
            return int(conid)
    raise RuntimeError(f"Unable to resolve IBKR conid for {asset.symbol} (type={asset_type})")


def _lookup_conid_crypto(*, asset: Asset, quote: Optional[Asset]) -> int:
    # Best-effort: IBKR crypto availability depends on region; conid mappings differ by venue.
    base_url = _downloader_base_url()
    url = f"{base_url}/ibkr/iserver/secdef/search"
    venue = (os.environ.get("IBKR_CRYPTO_VENUE") or IBKR_DEFAULT_CRYPTO_VENUE).strip().upper()
    desired_quote = str(getattr(quote, "symbol", "") or "").strip().upper() if quote is not None else ""
    payload = queue_request(
        url=url,
        querystring={"symbol": asset.symbol, "secType": "CRYPTO"},
        headers=None,
        timeout=None,
    )
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected IBKR secdef/search response for crypto: {payload}")
    fallback_any: Optional[int] = None
    fallback_quote: Optional[int] = None
    for entry in payload:
        entry_currency = str(entry.get("currency") or "").strip().upper()
        conid = entry.get("conid")
        if conid is not None and fallback_any is None:
            try:
                fallback_any = int(conid)
            except Exception:
                fallback_any = None
        sections = entry.get("sections") or []
        for section in sections:
            if str(section.get("secType") or "").upper() == "CRYPTO":
                if venue:
                    exch = str(section.get("exchange") or "").upper()
                    if venue not in exch:
                        # Keep a best-effort fallback if the quote matches but the venue doesn't.
                        if fallback_quote is None:
                            try:
                                fallback_quote = int(conid) if conid is not None else None
                            except Exception:
                                fallback_quote = None
                        continue
                section_currency = str(section.get("currency") or "").strip().upper()
                resolved_currency = section_currency or entry_currency
                if desired_quote and resolved_currency and desired_quote != resolved_currency:
                    continue
                if conid is not None:
                    return int(conid)
    # Fallback: prefer any quote-matching crypto conid even if the venue metadata doesn't match.
    if fallback_quote is not None:
        return int(fallback_quote)
    # Final fallback: accept the first conid only when the caller didn't request a specific quote.
    if fallback_any is not None and not desired_quote:
        return int(fallback_any)
    raise RuntimeError(
        f"Unable to resolve IBKR crypto conid for {asset.symbol}/{getattr(quote,'symbol',None)} "
        f"(venue={venue or 'AUTO'})."
    )


def _lookup_conid_future(
    *,
    asset: Asset,
    exchange: Optional[str],
    mapping: Optional[Dict[str, int]] = None,
    keys_added: Optional[set[str]] = None,
) -> int:
    base_url = _downloader_base_url()
    url = f"{base_url}/ibkr/trsrv/futures"
    desired_exchange = (exchange or "").strip().upper()
    if not desired_exchange:
        try:
            desired_exchange = _resolve_futures_exchange(getattr(asset, "symbol", ""))
        except IbkrFuturesExchangeAmbiguousError:
            raise
        except Exception:
            desired_exchange = (os.environ.get("IBKR_FUTURES_EXCHANGE") or IBKR_DEFAULT_FUTURES_EXCHANGE_FALLBACK).strip().upper()

    symbol_upper = str(getattr(asset, "symbol", "") or "").strip().upper()
    expiration = getattr(asset, "expiration", None)
    target = expiration.strftime("%Y%m%d") if expiration is not None else ""

    # Negative cache: stop hammering IBKR for invalid roots/expirations.
    _load_negative_conid_cache()
    neg_root_key = IbkrConidKey("future", symbol_upper, "", desired_exchange, "").to_key()
    neg_target_key = IbkrConidKey("future", symbol_upper, "", desired_exchange, target).to_key() if target else ""
    neg_hit = _NEGATIVE_CONID_CACHE.get(neg_root_key) or (_NEGATIVE_CONID_CACHE.get(neg_target_key) if neg_target_key else None)
    if isinstance(neg_hit, dict):
        cached_msg = str(neg_hit.get("message") or "").strip() or (
            f"IBKR futures conid lookup is negatively cached for {symbol_upper} on {desired_exchange} (target={target or 'front_month'})."
        )
        logger.error("IBKR negative conid cache hit: %s", cached_msg)
        raise IbkrFuturesConidLookupError(cached_msg)

    query = {"symbols": asset.symbol, "exchange": desired_exchange, "secType": "FUT"}
    payload = queue_request(url=url, querystring=query, headers=None, timeout=None)
    # Response shape: { "<symbol>": [ {conid, expirationDate, ...}, ... ] }
    if not isinstance(payload, dict):
        # Some gateways require secType=CONTFUT to list contracts.
        payload = queue_request(
            url=url,
            querystring={"symbols": asset.symbol, "exchange": desired_exchange, "secType": "CONTFUT"},
            headers=None,
            timeout=None,
        )
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected IBKR trsrv/futures response: {payload}")
    contracts = payload.get(asset.symbol) or payload.get(asset.symbol.upper()) or []
    if not isinstance(contracts, list) or not contracts:
        msg = f"No futures contracts returned for {symbol_upper} on {desired_exchange}"
        _record_negative_conid(key=neg_root_key, reason="no_contracts", message=msg)
        raise IbkrFuturesConidLookupError(msg)

    # Bulk-refresh: update conids.json with *all* returned contract months for this root+exchange.
    # This keeps the registry current via REST so we rarely/never need a new TWS backfill.
    if mapping is not None:
        for contract in contracts:
            if not isinstance(contract, dict):
                continue
            conid = contract.get("conid")
            if conid is None:
                continue
            date_candidates = []
            exp = contract.get("expirationDate")
            if exp is not None:
                date_candidates.append(exp)
            ltd = contract.get("ltd") or contract.get("lastTradeDate") or contract.get("lastTradeDay") or contract.get("lastTrade")
            if ltd is not None:
                date_candidates.append(ltd)
            try:
                conid_int = int(conid)
            except Exception:
                continue
            if conid_int <= 0:
                continue

            for raw in date_candidates:
                exp_str = str(raw).strip()
                if not (exp_str.isdigit() and len(exp_str) == 8):
                    continue
                key_blank = IbkrConidKey("future", symbol_upper, "", desired_exchange, exp_str).to_key()
                key_usd = IbkrConidKey("future", symbol_upper, "USD", desired_exchange, exp_str).to_key()
                for k in (key_blank, key_usd):
                    prior = mapping.get(k)
                    if prior != conid_int:
                        mapping[k] = conid_int
                        if keys_added is not None:
                            keys_added.add(k)

    if expiration is not None:
        for contract in contracts:
            exp_str = str(contract.get("expirationDate") or "").strip()
            ltd_str = str(contract.get("ltd") or contract.get("lastTradeDate") or contract.get("lastTradeDay") or "").strip()
            if exp_str == target or ltd_str == target:
                return int(contract["conid"])
        msg = (
            f"IBKR did not return a conid for {symbol_upper} expiring {target} on {desired_exchange}. "
            "If this is an expired contract, IBKR Client Portal cannot reliably discover it. "
            "Ensure the IBKR conid registry (`<cache>/ibkr/conids.json`, S3-mirrored) contains the "
            "missing expiration. New contracts are expected to auto-populate via REST; only older "
            "historical gaps require a one-time TWS backfill."
        )
        if neg_target_key:
            _record_negative_conid(key=neg_target_key, reason="no_conid", message=msg)
        raise IbkrFuturesConidLookupError(msg)

    # Default: earliest expiration (front month) – used for smoke tests like MES.
    def _exp_key(item: Dict[str, Any]) -> int:
        try:
            return int(item.get("expirationDate") or 0)
        except Exception:
            return 0

    chosen = min(contracts, key=_exp_key)
    return int(chosen["conid"])


def _cache_file_for(
    *,
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    exchange: Optional[str],
    source: str,
) -> Path:
    provider_root = Path(LUMIBOT_CACHE_FOLDER) / CACHE_SUBFOLDER
    asset_folder = _asset_folder(asset)
    _bar, _bar_seconds, timestep_component = _timestep_to_ibkr_bar(timestep)
    exch = (exchange or "").strip().upper() or "AUTO"
    symbol = _safe_component(getattr(asset, "symbol", "") or "symbol")
    quote_symbol = _safe_component(getattr(quote, "symbol", "") or "USD") if quote else "USD"
    expiration = getattr(asset, "expiration", None)
    exp_component = expiration.strftime("%Y%m%d") if expiration else ""
    source_component = _safe_component(source)
    suffix = f"_{exp_component}" if exp_component else ""
    filename = f"{asset_folder}_{symbol}_{quote_symbol}_{timestep_component}_{exch}_{source_component}{suffix}.parquet"
    return provider_root / asset_folder / timestep_component / "bars" / filename


def _asset_folder(asset: Asset) -> str:
    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    if asset_type in {"crypto"}:
        return "crypto"
    if asset_type in {"future", "cont_future"}:
        return "future"
    return asset_type or "asset"


def _timestep_component(timestep: str) -> str:
    cleaned = str(timestep or "minute").strip().lower()
    return cleaned.replace(" ", "")


def _safe_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.upper())


def _timestep_to_ibkr_bar(timestep: str) -> Tuple[str, int, str]:
    raw = (timestep or "minute").strip().lower()
    raw = raw.replace(" ", "")
    raw = raw.replace("seconds", "second").replace("minutes", "minute").replace("hours", "hour").replace("days", "day")

    if raw in {"second", "1second", "s", "1s", "sec", "1sec"}:
        return "1sec", 1, "second"
    if raw.endswith("second"):
        qty = raw.removesuffix("second") or "1"
        seconds = int(qty)
        return f"{seconds}sec", seconds, f"{seconds}second"
    if raw.endswith("sec"):
        seconds = int(raw.removesuffix("sec") or "1")
        return f"{seconds}sec", seconds, f"{seconds}second"
    if raw.endswith("s") and raw[:-1].isdigit():
        seconds = int(raw[:-1] or "1")
        return f"{seconds}sec", seconds, f"{seconds}second"

    if raw in {"minute", "1minute", "m", "1m"}:
        return "1min", 60, "minute"
    if raw.endswith("minute"):
        qty = raw.removesuffix("minute") or "1"
        minutes = int(qty)
        return f"{minutes}min", minutes * 60, f"{minutes}minute"

    if raw in {"hour", "1hour", "h", "1h"}:
        return "1h", 60 * 60, "hour"
    if raw.endswith("hour"):
        qty = raw.removesuffix("hour") or "1"
        hours = int(qty)
        return f"{hours}h", hours * 60 * 60, f"{hours}hour"

    if raw in {"day", "1day", "d", "1d"}:
        return "1d", 24 * 60 * 60, "day"
    if raw.endswith("day"):
        qty = raw.removesuffix("day") or "1"
        days = int(qty)
        return f"{days}d", days * 24 * 60 * 60, f"{days}day"

    if raw.endswith("min"):
        minutes = int(raw.removesuffix("min") or "1")
        return f"{minutes}min", minutes * 60, f"{minutes}minute"

    raise ValueError(f"Unsupported IBKR timestep: {timestep}")


def _normalize_history_source(source: Optional[str]) -> str:
    raw = (source or os.environ.get("IBKR_HISTORY_SOURCE") or IBKR_DEFAULT_HISTORY_SOURCE).strip()
    if not raw:
        return IBKR_DEFAULT_HISTORY_SOURCE
    normalized = raw.strip().lower().replace("-", "_")
    if normalized in {"trades", "trade"}:
        return "Trades"
    if normalized in {"midpoint", "mid"}:
        return "Midpoint"
    if normalized in {"bid_ask", "bidask"}:
        return "Bid_Ask"
    raise ValueError(f"Unsupported IBKR history source '{source}'. Expected Trades, Midpoint, or Bid_Ask.")


def _max_period_for_bar(bar: str) -> str:
    """Return an IBKR `period` that requests at most ~1000 datapoints for the bar size."""
    normalized = (bar or "").strip().lower()
    if normalized.endswith("sec"):
        multiplier = int(normalized.removesuffix("sec") or "1")
        return f"{IBKR_HISTORY_MAX_POINTS * multiplier}sec"
    if normalized.endswith("min"):
        multiplier = int(normalized.removesuffix("min") or "1")
        return f"{IBKR_HISTORY_MAX_POINTS * multiplier}min"
    if normalized.endswith("h"):
        multiplier = int(normalized.removesuffix("h") or "1")
        return f"{IBKR_HISTORY_MAX_POINTS * multiplier}h"
    if normalized.endswith("d"):
        multiplier = int(normalized.removesuffix("d") or "1")
        return f"{IBKR_HISTORY_MAX_POINTS * multiplier}d"
    return f"{IBKR_HISTORY_MAX_POINTS}min"


def _read_cache_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    if not isinstance(df.index, pd.DatetimeIndex):
        # Defensive: older caches might have a column index.
        if "datetime" in df.columns:
            df = df.set_index(pd.to_datetime(df["datetime"], utc=True, errors="coerce"))
            df = df.drop(columns=["datetime"], errors="ignore")
    df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
    df = df[~df.index.isna()]
    df = df.sort_index()
    df.index = df.index.tz_convert(LUMIBOT_DEFAULT_PYTZ)
    if "missing" in df.columns:
        df["missing"] = df["missing"].fillna(False)
    else:
        df["missing"] = False
    return df


def _write_cache_frame(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df_to_save = df.copy()
    if not isinstance(df_to_save.index, pd.DatetimeIndex):
        raise ValueError("IBKR cache frames must be indexed by datetime")
    df_to_save.to_parquet(path)
    try:
        get_backtest_cache().on_local_update(path, payload=_remote_payload_from_path(path))
    except Exception:
        pass


def _contract_info_cache_file(conid: int) -> Path:
    provider_root = Path(LUMIBOT_CACHE_FOLDER) / CACHE_SUBFOLDER
    return provider_root / "future" / "contracts" / f"CONID_{int(conid)}.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, default=str, indent=2), encoding="utf-8")
    try:
        get_backtest_cache().on_local_update(path, payload=_remote_payload_from_path(path))
    except Exception:
        pass


def _fetch_contract_info(conid: int) -> Dict[str, Any]:
    base_url = _downloader_base_url()
    url = f"{base_url}/ibkr/iserver/contract/{int(conid)}/info"
    payload = queue_request(url=url, querystring=None, headers=None, timeout=None)
    if payload is None:
        return {}
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(f"IBKR contract info error: {payload.get('error')}")
    if not isinstance(payload, dict):
        return {}
    return payload


def _maybe_apply_future_contract_metadata(*, asset: Asset, exchange: Optional[str]) -> None:
    """Best-effort: populate futures multiplier + min_tick for accurate PnL and tick rounding."""
    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    if asset_type not in {"future", "cont_future"}:
        return

    try:
        conid = _resolve_conid(asset=asset, quote=None, exchange=exchange)
    except Exception:
        return

    cache_file = _contract_info_cache_file(int(conid))
    cache = get_backtest_cache()
    try:
        cache.ensure_local_file(cache_file, payload={"provider": "ibkr", "type": "contract_info", "conid": int(conid)})
    except Exception:
        pass

    info = _read_json(cache_file)
    if not info:
        try:
            info = _fetch_contract_info(int(conid))
        except Exception:
            info = {}
        if info:
            _write_json(cache_file, info)

    if not info:
        return

    raw_mult = info.get("multiplier")
    try:
        mult_val = float(raw_mult) if raw_mult is not None else None
    except Exception:
        mult_val = None
    if mult_val and mult_val > 0:
        try:
            asset.multiplier = int(mult_val) if float(mult_val).is_integer() else mult_val  # type: ignore[assignment]
        except Exception:
            pass

    raw_tick = info.get("minTick") if "minTick" in info else info.get("min_tick")
    try:
        tick_val = float(raw_tick) if raw_tick is not None else None
    except Exception:
        tick_val = None
    if tick_val and tick_val > 0:
        try:
            setattr(asset, "min_tick", tick_val)
        except Exception:
            pass


def _remote_payload(
    asset: Asset,
    quote: Optional[Asset],
    timestep: str,
    exchange: Optional[str],
    source: str,
) -> Dict[str, object]:
    return {
        "provider": "ibkr",
        "symbol": getattr(asset, "symbol", None),
        "asset_type": str(getattr(asset, "asset_type", "") or ""),
        "quote": getattr(quote, "symbol", None) if quote else None,
        "timestep": timestep,
        "exchange": exchange,
        "source": source,
        "expiration": getattr(asset, "expiration", None).isoformat() if getattr(asset, "expiration", None) else None,
    }


def _remote_payload_from_path(path: Path) -> Dict[str, object]:
    return {"provider": "ibkr", "path": path.as_posix()}


def _conid_key(asset: Asset, quote: Optional[Asset], exchange: Optional[str]) -> IbkrConidKey:
    asset_type = str(getattr(asset, "asset_type", "") or "").lower()
    symbol = str(getattr(asset, "symbol", "") or "")
    quote_symbol = str(getattr(quote, "symbol", "") or "") if quote else ""
    exch = (exchange or "").strip().upper()
    if asset_type == "crypto" and not exch:
        exch = (os.environ.get("IBKR_CRYPTO_VENUE") or IBKR_DEFAULT_CRYPTO_VENUE).strip().upper()
    expiration = ""
    if getattr(asset, "expiration", None) is not None:
        try:
            expiration = asset.expiration.strftime("%Y%m%d")  # type: ignore[union-attr]
        except Exception:
            expiration = str(asset.expiration)
    return IbkrConidKey(
        asset_type=asset_type,
        symbol=symbol,
        quote_symbol=quote_symbol,
        exchange=exch,
        expiration=expiration,
    )


def _downloader_base_url() -> str:
    return os.environ.get("DATADOWNLOADER_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


def _to_utc(dt_value: datetime) -> datetime:
    """Convert a datetime to UTC, treating naive datetimes as LumiBot local time.

    IMPORTANT: LumiBot uses `pytz` timezones. For pytz, you must NOT attach tzinfo via
    `datetime.replace(tzinfo=...)` because it can yield historical "LMT" offsets (e.g. -04:56
    for America/New_York) and create multi-hour gaps/misalignment in paginated history fetches.
    Use `tz.localize()` so DST rules apply correctly.
    """
    if isinstance(dt_value, pd.Timestamp):
        dt_value = dt_value.to_pydatetime()
    if dt_value.tzinfo is None:
        try:
            dt_value = LUMIBOT_DEFAULT_PYTZ.localize(dt_value)  # type: ignore[attr-defined]
        except Exception:
            # Fallback for non-pytz tzinfo implementations.
            dt_value = dt_value.replace(tzinfo=LUMIBOT_DEFAULT_PYTZ)
    return dt_value.astimezone(timezone.utc)
