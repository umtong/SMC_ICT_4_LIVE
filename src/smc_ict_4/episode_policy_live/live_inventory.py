"""Causal public Binance USD-M inventory metrics for paper/shadow trading.

This adapter intentionally has no account credentials and never calls a trade
or top-trader endpoint.  It joins the two public five-minute observations used
by :mod:`inventory_ownership`:

* ``GET /futures/data/openInterestHist``; and
* ``GET /futures/data/globalLongShortAccountRatio``.

The contract was checked against Binance's official USD-M Futures REST market
data documentation on 2026-08-24.  Both endpoints document ``timestamp`` as
the end time of the period.  ``period=5m`` and a bounded ``limit`` are used.
The now API-key-required top-account and top-position endpoints are excluded.
The public taker-volume endpoint is also excluded because its timestamp is the
*start* of the period, whereas the two required endpoints use period end; the
price/flow tape already supplies causal taker flow to the policy.

Only rows with exactly equal symbol and raw endpoint timestamp are joined.
No interpolation, forward fill, neutral value, or stale reuse is performed.
Operational failures return an explicit non-ready result.  Identical repeated
observations are idempotent; any conflicting repeat is a fatal data-integrity
error.  The resulting rows are the same ``InventoryMetric`` objects consumed
by historical ``InventoryTimeline`` evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
from threading import RLock
import time
from typing import Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .inventory_ownership import (
    FIVE_MINUTE_NS,
    InventoryDataError,
    InventoryMetric,
    InventoryTimeline,
    SUPPORTED_METRICS_SYMBOLS,
    causal_metric_clock,
)


OFFICIAL_FAPI_BASE_URL = "https://fapi.binance.com"
OPEN_INTEREST_PATH = "/futures/data/openInterestHist"
GLOBAL_ACCOUNT_RATIO_PATH = "/futures/data/globalLongShortAccountRatio"
MIN_SEED_LIMIT = 4  # current point plus the three-change comparison window
MAX_LIVE_LIMIT = 30


class InventoryJsonTransport(Protocol):
    """Injectable read-only JSON transport."""

    def __call__(self, url: str, *, timeout_seconds: float) -> object: ...


class InventoryMetricConflictError(InventoryDataError):
    """The provider changed a previously observed symbol/timestamp value."""


class LiveInventoryStatus(str, Enum):
    READY = "READY"
    ENDPOINT_UNAVAILABLE = "ENDPOINT_UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    NO_COMPLETED_SNAPSHOT = "NO_COMPLETED_SNAPSHOT"
    NO_EXACT_JOIN = "NO_EXACT_JOIN"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class InventoryFeedGap:
    """An explicit reason inventory must be treated as unknown."""

    symbol: str
    expected_nominal_ts_ns: int
    observed_at_ns: int
    reason: str
    endpoint_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveInventoryPollResult:
    """One symbol's atomic poll outcome.

    ``timeline`` is deliberately absent unless ``status`` is ``READY``.  This
    prevents a caller from accidentally treating cached observations as the
    current snapshot following an endpoint failure or a stale response.
    """

    symbol: str
    status: LiveInventoryStatus
    observed_at_ns: int
    expected_nominal_ts_ns: int
    latest_joined_nominal_ts_ns: int | None
    added_points: int
    timeline: InventoryTimeline | None
    gap: InventoryFeedGap | None

    @property
    def ready(self) -> bool:
        return self.status is LiveInventoryStatus.READY and self.timeline is not None


@dataclass(frozen=True, slots=True)
class _OpenInterestRow:
    symbol: str
    timestamp_ms: int
    open_interest: float
    open_interest_value: float


@dataclass(frozen=True, slots=True)
class _AccountRatioRow:
    symbol: str
    timestamp_ms: int
    long_short_ratio: float


def urllib_json_transport(url: str, *, timeout_seconds: float) -> object:
    """Fetch JSON from the configured public HTTPS URL without credentials."""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SMC-ICT-4-live-inventory/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            payload = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"public metrics request failed for {url}: {exc}") from exc
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"non-JSON response from {url}") from exc


def _positive_float(value: object, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ValueError(f"{field} must be finite and positive")
    return parsed


def _timestamp_ms(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("timestamp must be an integer millisecond value")
    try:
        timestamp = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be an integer millisecond value") from exc
    if str(timestamp) != str(value).strip() and not isinstance(value, int):
        raise ValueError("timestamp must not contain a fractional value")
    if timestamp <= 0:
        raise ValueError("timestamp must be positive")
    return timestamp


def _validated_payload(payload: object, endpoint: str) -> Sequence[object]:
    if not isinstance(payload, list):
        raise ValueError(f"{endpoint} response must be a JSON array")
    return payload


def _insert_exact(
    rows: dict[tuple[str, int], object],
    key: tuple[str, int],
    value: object,
    endpoint: str,
) -> None:
    prior = rows.get(key)
    if prior is None:
        rows[key] = value
    elif prior != value:
        raise InventoryMetricConflictError(
            f"conflicting repeated {endpoint} observation for {key[0]} at {key[1]}",
        )


def _parse_open_interest(payload: object, requested_symbol: str) -> dict[tuple[str, int], _OpenInterestRow]:
    rows: dict[tuple[str, int], _OpenInterestRow] = {}
    for raw in _validated_payload(payload, OPEN_INTEREST_PATH):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{OPEN_INTEREST_PATH} row must be an object")
        symbol = str(raw.get("symbol", "")).upper()
        if symbol != requested_symbol:
            raise ValueError(
                f"{OPEN_INTEREST_PATH} symbol mismatch: {symbol!r} != {requested_symbol}",
            )
        timestamp = _timestamp_ms(raw.get("timestamp"))
        row = _OpenInterestRow(
            symbol=symbol,
            timestamp_ms=timestamp,
            open_interest=_positive_float(raw.get("sumOpenInterest"), "sumOpenInterest"),
            open_interest_value=_positive_float(
                raw.get("sumOpenInterestValue"),
                "sumOpenInterestValue",
            ),
        )
        _insert_exact(rows, (symbol, timestamp), row, OPEN_INTEREST_PATH)
    return rows


def _parse_account_ratio(payload: object, requested_symbol: str) -> dict[tuple[str, int], _AccountRatioRow]:
    rows: dict[tuple[str, int], _AccountRatioRow] = {}
    for raw in _validated_payload(payload, GLOBAL_ACCOUNT_RATIO_PATH):
        if not isinstance(raw, Mapping):
            raise ValueError(f"{GLOBAL_ACCOUNT_RATIO_PATH} row must be an object")
        symbol = str(raw.get("symbol", "")).upper()
        if symbol != requested_symbol:
            raise ValueError(
                f"{GLOBAL_ACCOUNT_RATIO_PATH} symbol mismatch: {symbol!r} != {requested_symbol}",
            )
        timestamp = _timestamp_ms(raw.get("timestamp"))
        row = _AccountRatioRow(
            symbol=symbol,
            timestamp_ms=timestamp,
            long_short_ratio=_positive_float(
                raw.get("longShortRatio"),
                "longShortRatio",
            ),
        )
        _insert_exact(rows, (symbol, timestamp), row, GLOBAL_ACCOUNT_RATIO_PATH)
    return rows


def _semantic_fingerprint(
    oi: _OpenInterestRow,
    ratio: _AccountRatioRow,
) -> str:
    canonical = "|".join(
        (
            oi.symbol,
            str(oi.timestamp_ms),
            format(oi.open_interest, ".17g"),
            format(oi.open_interest_value, ".17g"),
            format(ratio.long_short_ratio, ".17g"),
        ),
    )
    return sha256(canonical.encode("ascii")).hexdigest()


class LiveBinanceInventoryCollector:
    """Poll and retain causal public inventory observations for four symbols."""

    def __init__(
        self,
        *,
        symbols: Sequence[str] = SUPPORTED_METRICS_SYMBOLS,
        transport: InventoryJsonTransport = urllib_json_transport,
        clock_ns: Callable[[], int] = time.time_ns,
        base_url: str = OFFICIAL_FAPI_BASE_URL,
        limit: int = 8,
        timeout_seconds: float = 10.0,
        completion_lag_ns: int = 1_000_000_000,
    ) -> None:
        selected = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
        if not selected:
            raise ValueError("at least one inventory symbol is required")
        unsupported = set(selected) - set(SUPPORTED_METRICS_SYMBOLS)
        if unsupported:
            raise ValueError(f"unsupported metrics symbols: {sorted(unsupported)}")
        if not MIN_SEED_LIMIT <= limit <= MAX_LIVE_LIMIT:
            raise ValueError(
                f"limit must be between {MIN_SEED_LIMIT} and {MAX_LIVE_LIMIT}",
            )
        if timeout_seconds <= 0.0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= completion_lag_ns < FIVE_MINUTE_NS:
            raise ValueError("completion_lag_ns must be within one five-minute period")
        normalized_base = base_url.rstrip("/")
        if not normalized_base.startswith("https://"):
            raise ValueError("public metrics base_url must use HTTPS")

        self.symbols = selected
        self.transport = transport
        self.clock_ns = clock_ns
        self.base_url = normalized_base
        self.limit = limit
        self.timeout_seconds = float(timeout_seconds)
        self.completion_lag_ns = completion_lag_ns
        self._points: dict[str, dict[int, InventoryMetric]] = {
            symbol: {} for symbol in selected
        }
        self._last_result: dict[str, LiveInventoryPollResult] = {}
        self._lock = RLock()

    def _url(self, path: str, symbol: str) -> str:
        query = urlencode({"symbol": symbol, "period": "5m", "limit": self.limit})
        return f"{self.base_url}{path}?{query}"

    def _fetch(self, path: str, symbol: str) -> object:
        return self.transport(
            self._url(path, symbol),
            timeout_seconds=self.timeout_seconds,
        )

    def _non_ready(
        self,
        *,
        symbol: str,
        status: LiveInventoryStatus,
        now_ns: int,
        expected_ns: int,
        latest_ns: int | None,
        reason: str,
        errors: tuple[str, ...] = (),
        added: int = 0,
    ) -> LiveInventoryPollResult:
        result = LiveInventoryPollResult(
            symbol=symbol,
            status=status,
            observed_at_ns=now_ns,
            expected_nominal_ts_ns=expected_ns,
            latest_joined_nominal_ts_ns=latest_ns,
            added_points=added,
            timeline=None,
            gap=InventoryFeedGap(
                symbol=symbol,
                expected_nominal_ts_ns=expected_ns,
                observed_at_ns=now_ns,
                reason=reason,
                endpoint_errors=errors,
            ),
        )
        self._last_result[symbol] = result
        return result

    def poll(self, symbol: str) -> LiveInventoryPollResult:
        """Poll both public endpoints and atomically publish a ready timeline."""

        selected = symbol.upper()
        if selected not in self._points:
            raise ValueError(f"collector does not own symbol {selected}")
        with self._lock:
            now_ns = int(self.clock_ns())
            cutoff_ns = now_ns - self.completion_lag_ns
            expected_ns = (cutoff_ns // FIVE_MINUTE_NS) * FIVE_MINUTE_NS
            payloads: dict[str, object] = {}
            errors: list[str] = []
            for path in (OPEN_INTEREST_PATH, GLOBAL_ACCOUNT_RATIO_PATH):
                try:
                    payloads[path] = self._fetch(path, selected)
                except Exception as exc:  # operational boundary: remain UNKNOWN
                    errors.append(f"{path}: {type(exc).__name__}: {exc}")
            if errors:
                latest = self._latest_nominal(selected)
                return self._non_ready(
                    symbol=selected,
                    status=LiveInventoryStatus.ENDPOINT_UNAVAILABLE,
                    now_ns=now_ns,
                    expected_ns=expected_ns,
                    latest_ns=latest,
                    reason="PUBLIC_METRICS_ENDPOINT_UNAVAILABLE",
                    errors=tuple(errors),
                )

            try:
                oi_rows = _parse_open_interest(payloads[OPEN_INTEREST_PATH], selected)
                ratio_rows = _parse_account_ratio(
                    payloads[GLOBAL_ACCOUNT_RATIO_PATH],
                    selected,
                )
            except InventoryMetricConflictError:
                raise
            except (TypeError, ValueError) as exc:
                latest = self._latest_nominal(selected)
                return self._non_ready(
                    symbol=selected,
                    status=LiveInventoryStatus.INVALID_RESPONSE,
                    now_ns=now_ns,
                    expected_ns=expected_ns,
                    latest_ns=latest,
                    reason="PUBLIC_METRICS_RESPONSE_INVALID",
                    errors=(f"{type(exc).__name__}: {exc}",),
                )

            completed_oi = {
                key: row
                for key, row in oi_rows.items()
                if row.timestamp_ms * 1_000_000 <= cutoff_ns
            }
            completed_ratio = {
                key: row
                for key, row in ratio_rows.items()
                if row.timestamp_ms * 1_000_000 <= cutoff_ns
            }
            if not completed_oi or not completed_ratio:
                return self._non_ready(
                    symbol=selected,
                    status=LiveInventoryStatus.NO_COMPLETED_SNAPSHOT,
                    now_ns=now_ns,
                    expected_ns=expected_ns,
                    latest_ns=self._latest_nominal(selected),
                    reason="NO_COMPLETED_PUBLIC_METRICS_SNAPSHOT",
                )

            joined_keys = sorted(set(completed_oi) & set(completed_ratio), key=lambda item: item[1])
            if not joined_keys:
                return self._non_ready(
                    symbol=selected,
                    status=LiveInventoryStatus.NO_EXACT_JOIN,
                    now_ns=now_ns,
                    expected_ns=expected_ns,
                    latest_ns=self._latest_nominal(selected),
                    reason="PUBLIC_METRICS_HAVE_NO_EXACT_TIMESTAMP_JOIN",
                )

            try:
                additions: dict[int, InventoryMetric] = {}
                for key in joined_keys:
                    oi = completed_oi[key]
                    ratio = completed_ratio[key]
                    source_ns = oi.timestamp_ms * 1_000_000
                    nominal_ns, observed_ns = causal_metric_clock(source_ns)
                    metric = InventoryMetric(
                        symbol=selected,
                        source_ts_ns=source_ns,
                        nominal_ts_ns=nominal_ns,
                        observed_ts_ns=observed_ns,
                        open_interest=oi.open_interest,
                        open_interest_value=oi.open_interest_value,
                        all_account_long_short=ratio.long_short_ratio,
                        top_account_long_short=None,
                        top_position_long_short=None,
                        taker_buy_sell_ratio=None,
                        source_fingerprint=_semantic_fingerprint(oi, ratio),
                    )
                    prior = self._points[selected].get(observed_ns)
                    pending = additions.get(observed_ns)
                    comparison = prior if prior is not None else pending
                    if comparison is not None and comparison != metric:
                        raise InventoryMetricConflictError(
                            "conflicting repeated joined inventory observation for "
                            f"{selected} at {observed_ns}",
                        )
                    if comparison is None:
                        additions[observed_ns] = metric

                candidate_points = dict(self._points[selected])
                candidate_points.update(additions)
                timeline = InventoryTimeline(candidate_points.values())
                unmatched_current = any(
                    causal_metric_clock(key[1] * 1_000_000)[0] == expected_ns
                    for key in set(completed_oi) ^ set(completed_ratio)
                )
            except InventoryMetricConflictError:
                raise
            except (InventoryDataError, TypeError, ValueError) as exc:
                return self._non_ready(
                    symbol=selected,
                    status=LiveInventoryStatus.INVALID_RESPONSE,
                    now_ns=now_ns,
                    expected_ns=expected_ns,
                    latest_ns=self._latest_nominal(selected),
                    reason="PUBLIC_METRICS_RESPONSE_INVALID",
                    errors=(f"{type(exc).__name__}: {exc}",),
                )

            # Publish atomically only after every joined row and the complete
            # candidate timeline have passed validation.
            self._points[selected] = candidate_points
            latest_ns = timeline.points[-1].nominal_ts_ns if timeline.points else None
            if latest_ns != expected_ns:
                return self._non_ready(
                    symbol=selected,
                    status=(
                        LiveInventoryStatus.NO_EXACT_JOIN
                        if unmatched_current
                        else LiveInventoryStatus.STALE
                    ),
                    now_ns=now_ns,
                    expected_ns=expected_ns,
                    latest_ns=latest_ns,
                    reason=(
                        "CURRENT_PUBLIC_METRICS_TIMESTAMP_NOT_EXACTLY_JOINED"
                        if unmatched_current
                        else "LATEST_PUBLIC_METRICS_SNAPSHOT_IS_STALE"
                    ),
                    added=len(additions),
                )

            result = LiveInventoryPollResult(
                symbol=selected,
                status=LiveInventoryStatus.READY,
                observed_at_ns=now_ns,
                expected_nominal_ts_ns=expected_ns,
                latest_joined_nominal_ts_ns=latest_ns,
                added_points=len(additions),
                timeline=timeline,
                gap=None,
            )
            self._last_result[selected] = result
            return result

    def poll_all(self) -> tuple[LiveInventoryPollResult, ...]:
        """Poll configured symbols; operational failures stay per-symbol."""

        return tuple(self.poll(symbol) for symbol in self.symbols)

    def history(self, symbol: str) -> InventoryTimeline:
        """Return retained history for diagnostics, never as a readiness signal."""

        selected = symbol.upper()
        with self._lock:
            if selected not in self._points:
                raise ValueError(f"collector does not own symbol {selected}")
            return InventoryTimeline(self._points[selected].values())

    def last_result(self, symbol: str) -> LiveInventoryPollResult | None:
        """Return the last poll evidence for diagnostics, not current readiness."""

        selected = symbol.upper()
        with self._lock:
            if selected not in self._points:
                raise ValueError(f"collector does not own symbol {selected}")
            return self._last_result.get(selected)

    def current(self, symbol: str) -> LiveInventoryPollResult:
        """Return a timeline only while its joined slot is still current.

        This does not perform network I/O.  A caller which holds the collector
        past the next five-minute boundary cannot accidentally reuse an earlier
        ready result as current; it receives an explicit ``STALE`` gap until a
        new successful :meth:`poll`.
        """

        selected = symbol.upper()
        with self._lock:
            if selected not in self._points:
                raise ValueError(f"collector does not own symbol {selected}")
            now_ns = int(self.clock_ns())
            expected_ns = (
                (now_ns - self.completion_lag_ns) // FIVE_MINUTE_NS
            ) * FIVE_MINUTE_NS
            prior = self._last_result.get(selected)
            latest = self._latest_nominal(selected)
            if (
                prior is not None
                and prior.ready
                and latest == expected_ns
            ):
                return prior
            if prior is not None and not prior.ready and prior.expected_nominal_ts_ns == expected_ns:
                return prior
            return self._non_ready(
                symbol=selected,
                status=LiveInventoryStatus.STALE,
                now_ns=now_ns,
                expected_ns=expected_ns,
                latest_ns=latest,
                reason="LATEST_PUBLIC_METRICS_SNAPSHOT_IS_STALE",
            )

    def _latest_nominal(self, symbol: str) -> int | None:
        points = self._points[symbol]
        if not points:
            return None
        return max(point.nominal_ts_ns for point in points.values())


# Concise production-facing name; the longer name remains available to make
# the venue/source explicit in research code and existing imports.
LiveInventoryCollector = LiveBinanceInventoryCollector


__all__ = [
    "GLOBAL_ACCOUNT_RATIO_PATH",
    "InventoryFeedGap",
    "InventoryJsonTransport",
    "InventoryMetricConflictError",
    "LiveBinanceInventoryCollector",
    "LiveInventoryCollector",
    "LiveInventoryPollResult",
    "LiveInventoryStatus",
    "MAX_LIVE_LIMIT",
    "MIN_SEED_LIMIT",
    "OFFICIAL_FAPI_BASE_URL",
    "OPEN_INTEREST_PATH",
    "urllib_json_transport",
]
