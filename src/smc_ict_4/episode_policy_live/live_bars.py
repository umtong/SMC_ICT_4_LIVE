"""Causal, paginated Binance USD-M one-minute warm-up bars.

The connected strategy needs substantially more than Binance's one-request
default in order to construct its five-minute, prior-day, higher-timeframe and
auction-journey state before it makes decisions.  This module is deliberately
separate from the live execution adapter: it performs public, credential-free
market-data reads only and returns the same :class:`~.domain.Bar` objects used
by replay.

``fetch_recent_binance_bars`` is the integration entry point.  ``limit`` means
the requested number of *unique completed minutes*, not an HTTP page size.  It
defaults to seven completed days.  Each request is capped at Binance's 1,500
row limit and walks backward with an inclusive ``endTime`` cursor.  The clock
is sampled once, so a long bootstrap cannot admit a bar which was incomplete
when bootstrap began.

Identical page overlaps are harmless.  A conflicting value for an already
seen symbol/minute, malformed clock, non-progressing cursor, insufficient
history, or any one-minute gap in the returned window fails closed.  No gap is
filled and no partially formed current bar is retained.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import math
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .domain import Bar as PolicyBar


NS_PER_MILLISECOND = 1_000_000
MS_PER_MINUTE = 60_000
NS_PER_MINUTE = MS_PER_MINUTE * NS_PER_MILLISECOND
DEFAULT_WARMUP_MINUTES = 7 * 24 * 60
MAX_BINANCE_KLINE_PAGE = 1_500
DEFAULT_ENDPOINTS = (
    "https://fapi.binance.com",
    "https://fapi1.binance.com",
    "https://fapi2.binance.com",
)
KLINES_PATH = "/fapi/v1/klines"


class BinanceBarDataError(RuntimeError):
    """The public warm-up stream cannot be used causally."""


class BinanceBarConflictError(BinanceBarDataError):
    """Binance returned different values for one symbol/minute."""


class BinanceBarGapError(BinanceBarDataError):
    """The requested completed-minute window is not contiguous."""


class BinanceBarJsonTransport(Protocol):
    """Injectable, read-only JSON transport used by tests and live startup."""

    def __call__(self, url: str, *, timeout_seconds: float) -> object: ...


def urllib_json_transport(url: str, *, timeout_seconds: float) -> object:
    """Read one public Binance JSON response without credentials."""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SMC-ICT-4-live-bar-warmup/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"public kline request failed for {url}: {exc}") from exc
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"non-JSON response from {url}") from exc


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinanceBarDataError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BinanceBarDataError(f"invalid {field}: {value!r}") from exc
    if isinstance(value, float) and value != parsed:
        raise BinanceBarDataError(f"non-integral {field}: {value!r}")
    return parsed


def _parse_kline(symbol: str, row: object) -> PolicyBar:
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) < 11:
        raise BinanceBarDataError("Binance kline row must contain at least 11 fields")

    open_ms = _integer(row[0], "open time")
    raw_close_ms = _integer(row[6], "close time")
    if open_ms % MS_PER_MINUTE:
        raise BinanceBarDataError(
            f"unaligned one-minute open for {symbol}: {open_ms}",
        )
    expected_raw_close_ms = open_ms + MS_PER_MINUTE - 1
    if raw_close_ms != expected_raw_close_ms:
        raise BinanceBarDataError(
            "non-canonical Binance one-minute close: "
            f"{symbol} open={open_ms} close={raw_close_ms} "
            f"expected={expected_raw_close_ms}",
        )

    try:
        return PolicyBar(
            symbol=symbol,
            interval_minutes=1,
            open_time_ns=open_ms * NS_PER_MILLISECOND,
            # Binance's inclusive final millisecond is normalized to the
            # exclusive right edge used by replay and Nautilus external bars.
            close_time_ns=(open_ms + MS_PER_MINUTE) * NS_PER_MILLISECOND,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            quote_volume=float(row[7]),
            trade_count=_integer(row[8], "trade count"),
            taker_buy_quote_volume=float(row[10]),
        )
    except BinanceBarDataError:
        raise
    except (TypeError, ValueError) as exc:
        raise BinanceBarDataError(f"invalid Binance kline for {symbol}: {row!r}") from exc


def _request_page(
    *,
    symbol: str,
    end_time_ms: int,
    limit: int,
    endpoints: Sequence[str],
    transport: BinanceBarJsonTransport,
    timeout_seconds: float,
) -> object:
    query = urlencode(
        {
            "symbol": symbol,
            "interval": "1m",
            "endTime": end_time_ms,
            "limit": limit,
        },
    )
    errors: list[str] = []
    for endpoint in endpoints:
        url = f"{endpoint.rstrip('/')}{KLINES_PATH}?{query}"
        try:
            return transport(url, timeout_seconds=timeout_seconds)
        except Exception as exc:  # fallback hosts expose the same public API
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise BinanceBarDataError(
        f"unable to fetch Binance public bars for {symbol}: " + " | ".join(errors),
    )


def fetch_recent_binance_bars(
    symbol: str,
    *,
    limit: int = DEFAULT_WARMUP_MINUTES,
    clock_ns: Callable[[], int] = time.time_ns,
    transport: BinanceBarJsonTransport = urllib_json_transport,
    endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    page_limit: int = MAX_BINANCE_KLINE_PAGE,
    timeout_seconds: float = 30.0,
) -> list[PolicyBar]:
    """Return exactly ``limit`` contiguous completed one-minute bars.

    Completion is evaluated against one immutable ``clock_ns`` sample and is
    strict (``close_time_ns < bootstrap_started_ns``), matching the prior live
    adapter.  At most ``ceil(limit / page_limit) + 2`` logical pages are
    accepted; each logical page has at most one attempt per configured host.
    The two-page allowance covers a provider-returned current row or identical
    boundary overlap without permitting an unbounded short-page loop.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer number of minutes")
    if isinstance(page_limit, bool) or not isinstance(page_limit, int):
        raise ValueError("page_limit must be an integer")
    if not 1 <= page_limit <= MAX_BINANCE_KLINE_PAGE:
        raise ValueError(f"page_limit must be between 1 and {MAX_BINANCE_KLINE_PAGE}")
    if not endpoints:
        raise ValueError("at least one Binance public endpoint is required")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive and finite")

    bootstrap_started_ns = _integer(clock_ns(), "bootstrap clock")
    if bootstrap_started_ns <= NS_PER_MINUTE:
        raise ValueError("bootstrap clock is too early to contain a completed minute")

    # `endTime` is inclusive.  Point it at the inclusive final millisecond of
    # the newest bar whose exclusive close is strictly before startup.
    completed_boundary_ns = (
        (bootstrap_started_ns - 1) // NS_PER_MINUTE * NS_PER_MINUTE
    )
    cursor_end_ms = completed_boundary_ns // NS_PER_MILLISECOND - 1
    max_pages = math.ceil(limit / page_limit) + 2
    bars_by_open: dict[int, PolicyBar] = {}

    for _page_number in range(max_pages):
        if len(bars_by_open) >= limit:
            break
        requested = min(page_limit, limit - len(bars_by_open))
        payload = _request_page(
            symbol=symbol,
            end_time_ms=cursor_end_ms,
            limit=requested,
            endpoints=endpoints,
            transport=transport,
            timeout_seconds=timeout_seconds,
        )
        if not isinstance(payload, list):
            raise BinanceBarDataError(
                f"Binance kline response for {symbol} is not an array",
            )
        if not payload:
            raise BinanceBarDataError(
                f"Binance history ended before {limit} completed bars for {symbol}",
            )

        page: list[PolicyBar] = [_parse_kline(symbol, row) for row in payload]
        oldest_open_ms = min(bar.open_time_ns for bar in page) // NS_PER_MILLISECOND
        next_cursor_end_ms = oldest_open_ms - 1
        if next_cursor_end_ms >= cursor_end_ms:
            raise BinanceBarDataError(
                f"non-progressing Binance pagination cursor for {symbol}",
            )

        for bar in page:
            if bar.close_time_ns >= bootstrap_started_ns:
                continue
            prior = bars_by_open.get(bar.open_time_ns)
            if prior is None:
                bars_by_open[bar.open_time_ns] = bar
            elif prior != bar:
                raise BinanceBarConflictError(
                    "conflicting Binance bars share one minute: "
                    f"{symbol} open={bar.open_time_ns}",
                )
        cursor_end_ms = next_cursor_end_ms

    if len(bars_by_open) < limit:
        raise BinanceBarDataError(
            f"received only {len(bars_by_open)} of {limit} completed bars for {symbol}",
        )

    ordered = sorted(bars_by_open.values(), key=lambda bar: bar.open_time_ns)[-limit:]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        expected_open_ns = previous.open_time_ns + NS_PER_MINUTE
        if current.open_time_ns != expected_open_ns:
            raise BinanceBarGapError(
                "non-contiguous Binance warm-up window: "
                f"{symbol} expected_open={expected_open_ns} "
                f"actual_open={current.open_time_ns}",
            )
    return ordered


__all__ = [
    "BinanceBarConflictError",
    "BinanceBarDataError",
    "BinanceBarGapError",
    "DEFAULT_ENDPOINTS",
    "DEFAULT_WARMUP_MINUTES",
    "KLINES_PATH",
    "MAX_BINANCE_KLINE_PAGE",
    "fetch_recent_binance_bars",
    "urllib_json_transport",
]
