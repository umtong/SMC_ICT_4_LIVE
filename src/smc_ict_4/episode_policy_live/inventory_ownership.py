"""Causal derivatives-inventory interpretation for liquidity episodes.

This is a provenance-preserving synthesis of mechanisms which already existed
in other research branches; it is not presented as a newly discovered alpha:

* candidate-02 V158 ``v158_oi_router.py`` (introduced at ``921266e01``),
  whose frozen Candidate-13 strategy was ``6e8e7a1461cb...`` and whose result
  parent was ``1b150d641740...``;
* candidate-06 OIDB/CIRB ``futures_metrics_data.py`` and
  ``crowding_inventory_response_engine.py`` (evidence lineage
  ``ea926a2d08bb...`` and ``492cda543753...``; source blobs
  ``264a2dae328f...`` and ``eaa6333afedc...``).

The reusable part is the causal observation contract and the economic
decomposition: an aligned price/flow shock accompanied by falling open
interest is position removal, while rising open interest is fresh sponsorship.
The all-account long/short composition sign then distinguishes discharge from
counter-inventory.  Candidate-02's fixed ``+0.10%`` V158 cutoff is deliberately
not copied: its final evidence was a sparse precision component (15 trades in
20 weeks), not a general alpha gate.  Magnitudes are returned as evidence and
the caller may combine them with the episode's other structural facts.

Missing or non-contiguous observations remain ``UNKNOWN``.  They are never
forward-filled, inferred from price, or converted into an approval.
"""

from __future__ import annotations

from bisect import bisect_right
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from hashlib import sha256
import io
import math
from pathlib import Path
import re
import time
from typing import Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


MINUTE_NS = 60_000_000_000
FIVE_MINUTE_NS = 5 * MINUTE_NS
MAX_NOMINAL_OFFSET_NS = FIVE_MINUTE_NS // 2
OFFICIAL_METRICS_BASE_URL = (
    "https://data.binance.vision/data/futures/um/daily/metrics"
)
SUPPORTED_METRICS_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
MAX_DOWNLOAD_WORKERS = 4


class InventoryDataError(ValueError):
    """An official metrics source violates its causal/data contract."""


CONFLICTING_OFFICIAL_DUPLICATE = "CONFLICTING_OFFICIAL_DUPLICATE"


@dataclass(frozen=True, slots=True)
class MetricsArchiveEvidence:
    """Outcome for one requested official daily archive."""

    symbol: str
    day: date
    status: str
    url: str
    archive_path: Path | None
    checksum_path: Path | None
    bytes: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class MetricsDownloadResult:
    """Complete evidence for a half-open multi-symbol download request."""

    start: date
    end_exclusive: date
    evidence: tuple[MetricsArchiveEvidence, ...]

    @property
    def verified(self) -> int:
        return sum(item.status.startswith("VERIFIED_") for item in self.evidence)

    @property
    def downloaded(self) -> int:
        return sum(item.status == "VERIFIED_DOWNLOADED" for item in self.evidence)

    @property
    def reused(self) -> int:
        return sum(item.status == "VERIFIED_REUSED" for item in self.evidence)

    @property
    def unavailable(self) -> int:
        return sum(
            item.status == "OFFICIAL_ARCHIVE_UNAVAILABLE" for item in self.evidence
        )

    @property
    def verified_bytes(self) -> int:
        return sum(item.bytes or 0 for item in self.evidence if item.status.startswith("VERIFIED_"))


class InventoryRegime(str, Enum):
    UNKNOWN = "UNKNOWN"
    POSITION_RESET = "POSITION_RESET"
    FRESH_SPONSORSHIP = "FRESH_SPONSORSHIP"
    UNCHANGED = "UNCHANGED"


class OwnershipBranch(str, Enum):
    UNKNOWN = "UNKNOWN"
    ALIGNED_COMPOSITION = "ALIGNED_COMPOSITION"
    COUNTER_INVENTORY = "COUNTER_INVENTORY"
    AMBIGUOUS = "AMBIGUOUS"


class InventoryInterpretation(str, Enum):
    UNKNOWN = "UNKNOWN"
    FORCED_DELEVERAGING_DISCHARGE = "FORCED_DELEVERAGING_DISCHARGE"
    FORCED_DELEVERAGING_COUNTER_INVENTORY = (
        "FORCED_DELEVERAGING_COUNTER_INVENTORY"
    )
    FRESH_SPONSORSHIP_CROWDING = "FRESH_SPONSORSHIP_CROWDING"
    FRESH_SPONSORSHIP_COUNTER_INVENTORY = (
        "FRESH_SPONSORSHIP_COUNTER_INVENTORY"
    )
    UNCHANGED_INVENTORY = "UNCHANGED_INVENTORY"


@dataclass(frozen=True, slots=True)
class InventoryMetric:
    """One official Binance USD-M metrics row on its causal clock."""

    symbol: str
    source_ts_ns: int
    nominal_ts_ns: int
    observed_ts_ns: int
    open_interest: float | None
    open_interest_value: float | None
    all_account_long_short: float | None
    top_account_long_short: float | None
    top_position_long_short: float | None
    taker_buy_sell_ratio: float | None
    invalid_fields: tuple[str, ...] = ()
    source_fingerprint: str | bytes | None = None
    source_archive: str | None = None
    source_archive_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.open_interest is not None and (
            self.open_interest <= 0.0 or not math.isfinite(self.open_interest)
        ):
            raise InventoryDataError("open interest must be finite and positive when present")
        if self.observed_ts_ns < self.source_ts_ns:
            raise InventoryDataError("an observation cannot precede its source timestamp")
        if abs(self.source_ts_ns - self.nominal_ts_ns) >= MAX_NOMINAL_OFFSET_NS:
            raise InventoryDataError("metrics timestamp is ambiguous between nominal slots")
        for name in (
            "open_interest_value",
            "all_account_long_short",
            "top_account_long_short",
            "top_position_long_short",
            "taker_buy_sell_ratio",
        ):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0.0):
                raise InventoryDataError(f"{name} must be finite and positive when present")

    @property
    def signed_taker_ratio(self) -> float | None:
        ratio = self.taker_buy_sell_ratio
        if ratio is None:
            return None
        return (ratio - 1.0) / (ratio + 1.0)


@dataclass(frozen=True, slots=True)
class InventoryConflictEvidence:
    """Auditable provenance for an official same-timestamp disagreement."""

    symbol: str
    source_ts_ns: int
    source_timestamps_ns: tuple[int, ...]
    nominal_ts_ns: int
    observed_ts_ns: int
    conflicting_fields: tuple[str, ...]
    source_archives: tuple[str, ...]
    source_archive_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryDecision:
    symbol: str
    regime: InventoryRegime
    ownership: OwnershipBranch
    interpretation: InventoryInterpretation
    reason: str
    shock_side: str
    episode_start_ns: int
    decision_ts_ns: int
    prior_observed_ts_ns: int | None
    current_observed_ts_ns: int | None
    oi_change_fraction: float | None
    all_account_change_log: float | None
    price_flow_aligned: bool | None

    @property
    def known(self) -> bool:
        return self.interpretation is not InventoryInterpretation.UNKNOWN


def causal_metric_clock(source_ts_ns: int) -> tuple[int, int]:
    """Return nearest nominal 5m slot and first completed observable minute.

    This ports candidate-06's repaired timestamp contract.  A source stamp a
    few seconds after a nominal slot is not shifted backward; it becomes usable
    only on the next completed minute.
    """

    lower = (source_ts_ns // FIVE_MINUTE_NS) * FIVE_MINUTE_NS
    upper = lower + FIVE_MINUTE_NS
    nominal = lower if source_ts_ns - lower <= upper - source_ts_ns else upper
    if abs(source_ts_ns - nominal) >= MAX_NOMINAL_OFFSET_NS:
        raise InventoryDataError("metrics timestamp is ambiguous between nominal slots")
    observed = ((source_ts_ns + MINUTE_NS - 1) // MINUTE_NS) * MINUTE_NS
    return nominal, observed


def _unknown(
    *,
    symbol: str,
    reason: str,
    shock_side: str,
    episode_start_ns: int,
    decision_ts_ns: int,
    prior: InventoryMetric | None = None,
    current: InventoryMetric | None = None,
) -> InventoryDecision:
    return InventoryDecision(
        symbol=symbol,
        regime=InventoryRegime.UNKNOWN,
        ownership=OwnershipBranch.UNKNOWN,
        interpretation=InventoryInterpretation.UNKNOWN,
        reason=reason,
        shock_side=shock_side,
        episode_start_ns=episode_start_ns,
        decision_ts_ns=decision_ts_ns,
        prior_observed_ts_ns=None if prior is None else prior.observed_ts_ns,
        current_observed_ts_ns=None if current is None else current.observed_ts_ns,
        oi_change_fraction=None,
        all_account_change_log=None,
        price_flow_aligned=None,
    )


def classify_inventory_change(
    prior: InventoryMetric,
    current: InventoryMetric,
    *,
    shock_side: str,
    episode_start_ns: int,
    decision_ts_ns: int,
    price_move: float,
    signed_taker_flow: float,
) -> InventoryDecision:
    """Classify a visible inventory change without a fitted magnitude gate."""

    side = shock_side.upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("shock_side must be BUY or SELL")
    if prior.symbol != current.symbol:
        raise ValueError("prior/current symbols differ")
    if any(
        CONFLICTING_OFFICIAL_DUPLICATE in item.invalid_fields
        for item in (prior, current)
    ):
        return _unknown(
            symbol=current.symbol,
            reason="CONFLICTING_OFFICIAL_DUPLICATE_IN_COMPARISON",
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            prior=prior,
            current=current,
        )
    if prior.open_interest is None or current.open_interest is None:
        return _unknown(
            symbol=current.symbol,
            reason="MISSING_OPEN_INTEREST",
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            prior=prior,
            current=current,
        )
    if current.observed_ts_ns > decision_ts_ns:
        return _unknown(
            symbol=current.symbol,
            reason="CURRENT_METRIC_NOT_YET_VISIBLE",
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            prior=prior,
            current=current,
        )
    if current.source_ts_ns < episode_start_ns:
        return _unknown(
            symbol=current.symbol,
            reason="NO_POST_EPISODE_METRIC",
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            prior=prior,
            current=current,
        )
    if not (math.isfinite(price_move) and math.isfinite(signed_taker_flow)):
        return _unknown(
            symbol=current.symbol,
            reason="PRICE_OR_FLOW_MISSING",
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            prior=prior,
            current=current,
        )

    shock_sign = 1.0 if side == "BUY" else -1.0
    aligned = shock_sign * price_move > 0.0 and shock_sign * signed_taker_flow > 0.0
    oi_change = (current.open_interest - prior.open_interest) / prior.open_interest
    if not aligned:
        return InventoryDecision(
            symbol=current.symbol,
            regime=InventoryRegime.UNKNOWN,
            ownership=OwnershipBranch.UNKNOWN,
            interpretation=InventoryInterpretation.UNKNOWN,
            reason="PRICE_TAKER_FLOW_NOT_ALIGNED_WITH_SHOCK",
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            prior_observed_ts_ns=prior.observed_ts_ns,
            current_observed_ts_ns=current.observed_ts_ns,
            oi_change_fraction=oi_change,
            all_account_change_log=None,
            price_flow_aligned=False,
        )

    all_change: float | None = None
    ownership = OwnershipBranch.UNKNOWN
    if (
        prior.all_account_long_short is not None
        and current.all_account_long_short is not None
    ):
        all_change = math.log(
            current.all_account_long_short / prior.all_account_long_short,
        )
        composition = shock_sign * all_change
        if composition > 0.0:
            ownership = OwnershipBranch.ALIGNED_COMPOSITION
        elif composition < 0.0:
            ownership = OwnershipBranch.COUNTER_INVENTORY
        else:
            ownership = OwnershipBranch.AMBIGUOUS

    if oi_change < 0.0:
        regime = InventoryRegime.POSITION_RESET
        if ownership is OwnershipBranch.ALIGNED_COMPOSITION:
            interpretation = InventoryInterpretation.FORCED_DELEVERAGING_DISCHARGE
            reason = "OI_CONTRACTION_WITH_ALIGNED_SHOCK_AND_CROWD_DISCHARGE"
        elif ownership is OwnershipBranch.COUNTER_INVENTORY:
            interpretation = (
                InventoryInterpretation.FORCED_DELEVERAGING_COUNTER_INVENTORY
            )
            reason = "OI_CONTRACTION_WITH_ALIGNED_SHOCK_AND_COUNTER_INVENTORY"
        else:
            interpretation = InventoryInterpretation.UNKNOWN
            reason = "POSITION_RESET_WITH_UNKNOWN_ACCOUNT_COMPOSITION"
    elif oi_change > 0.0:
        regime = InventoryRegime.FRESH_SPONSORSHIP
        if ownership is OwnershipBranch.ALIGNED_COMPOSITION:
            interpretation = InventoryInterpretation.FRESH_SPONSORSHIP_CROWDING
            reason = "OI_EXPANSION_WITH_ALIGNED_SHOCK_AND_ALIGNED_CROWDING"
        elif ownership is OwnershipBranch.COUNTER_INVENTORY:
            interpretation = (
                InventoryInterpretation.FRESH_SPONSORSHIP_COUNTER_INVENTORY
            )
            reason = "OI_EXPANSION_WITH_ALIGNED_SHOCK_AND_COUNTER_INVENTORY"
        else:
            interpretation = InventoryInterpretation.UNKNOWN
            reason = "FRESH_SPONSORSHIP_WITH_UNKNOWN_ACCOUNT_COMPOSITION"
    else:
        regime = InventoryRegime.UNCHANGED
        interpretation = InventoryInterpretation.UNCHANGED_INVENTORY
        reason = "OPEN_INTEREST_UNCHANGED"

    return InventoryDecision(
        symbol=current.symbol,
        regime=regime,
        ownership=ownership,
        interpretation=interpretation,
        reason=reason,
        shock_side=side,
        episode_start_ns=episode_start_ns,
        decision_ts_ns=decision_ts_ns,
        prior_observed_ts_ns=prior.observed_ts_ns,
        current_observed_ts_ns=current.observed_ts_ns,
        oi_change_fraction=oi_change,
        all_account_change_log=all_change,
        price_flow_aligned=True,
    )


_INVENTORY_VALUE_FIELDS = (
    "open_interest",
    "open_interest_value",
    "all_account_long_short",
    "top_account_long_short",
    "top_position_long_short",
    "taker_buy_sell_ratio",
)


def _same_metric_payload(left: InventoryMetric, right: InventoryMetric) -> bool:
    return (
        left.symbol == right.symbol
        and left.source_ts_ns == right.source_ts_ns
        and left.nominal_ts_ns == right.nominal_ts_ns
        and left.observed_ts_ns == right.observed_ts_ns
        and all(getattr(left, name) == getattr(right, name) for name in _INVENTORY_VALUE_FIELDS)
        and left.invalid_fields == right.invalid_fields
        and left.source_fingerprint == right.source_fingerprint
    )


def _merge_conflicting_metrics(
    group: Sequence[InventoryMetric],
) -> tuple[InventoryMetric, InventoryConflictEvidence]:
    first = group[0]
    differing = tuple(
        name
        for name in _INVENTORY_VALUE_FIELDS
        if len({getattr(item, name) for item in group}) != 1
    )
    invalid = set().union(*(item.invalid_fields for item in group))
    invalid.update(differing)
    invalid.add(CONFLICTING_OFFICIAL_DUPLICATE)
    values = {
        name: None if name in differing else getattr(first, name)
        for name in _INVENTORY_VALUE_FIELDS
    }
    sources = tuple(
        (
            item.source_archive or "UNKNOWN_ARCHIVE",
            item.source_archive_sha256 or "UNKNOWN_SHA256",
        )
        for item in group
    )
    source_ts_ns = max(item.source_ts_ns for item in group)
    observed_ts_ns = max(item.observed_ts_ns for item in group)
    merged = InventoryMetric(
        symbol=first.symbol,
        source_ts_ns=source_ts_ns,
        nominal_ts_ns=first.nominal_ts_ns,
        observed_ts_ns=observed_ts_ns,
        invalid_fields=tuple(sorted(invalid)),
        source_fingerprint=None,
        source_archive=None,
        source_archive_sha256=None,
        **values,
    )
    evidence = InventoryConflictEvidence(
        symbol=first.symbol,
        source_ts_ns=source_ts_ns,
        source_timestamps_ns=tuple(item.source_ts_ns for item in group),
        nominal_ts_ns=first.nominal_ts_ns,
        observed_ts_ns=observed_ts_ns,
        conflicting_fields=differing,
        source_archives=tuple(item[0] for item in sources),
        source_archive_sha256=tuple(item[1] for item in sources),
    )
    return merged, evidence


class InventoryTimeline:
    """Immutable causal lookup over official metrics observations."""

    def __init__(self, points: Iterable[InventoryMetric]) -> None:
        ordered = sorted(
            points,
            key=lambda item: (item.nominal_ts_ns, item.source_ts_ns),
        )
        if not ordered:
            self.symbol = ""
            self.points: tuple[InventoryMetric, ...] = ()
            self._observed: tuple[int, ...] = ()
            self.duplicate_observed_ts_ns: tuple[int, ...] = ()
            self.conflicting_duplicates: tuple[InventoryConflictEvidence, ...] = ()
            return
        symbols = {item.symbol for item in ordered}
        if len(symbols) != 1:
            raise InventoryDataError("one timeline cannot mix symbols")
        deduplicated: list[InventoryMetric] = []
        duplicate_timestamps: list[int] = []
        conflicts: list[InventoryConflictEvidence] = []
        index = 0
        while index < len(ordered):
            end = index + 1
            while (
                end < len(ordered)
                and ordered[end].nominal_ts_ns == ordered[index].nominal_ts_ns
            ):
                end += 1
            group = ordered[index:end]
            if len(group) == 1:
                deduplicated.append(group[0])
            elif all(_same_metric_payload(group[0], item) for item in group[1:]):
                deduplicated.append(group[0])
                duplicate_timestamps.extend(
                    group[0].observed_ts_ns for _ in range(len(group) - 1)
                )
            else:
                merged, evidence = _merge_conflicting_metrics(group)
                deduplicated.append(merged)
                conflicts.append(evidence)
            index = end
        deduplicated.sort(key=lambda item: item.observed_ts_ns)
        for left, right in zip(deduplicated, deduplicated[1:]):
            if right.observed_ts_ns <= left.observed_ts_ns:
                raise InventoryDataError("causal metrics observations are not increasing")
            if right.nominal_ts_ns <= left.nominal_ts_ns:
                raise InventoryDataError("nominal metrics slots are not increasing")
        self.symbol = deduplicated[0].symbol
        self.points = tuple(deduplicated)
        self._observed = tuple(item.observed_ts_ns for item in deduplicated)
        self.duplicate_observed_ts_ns = tuple(duplicate_timestamps)
        self.conflicting_duplicates = tuple(conflicts)

    def bounded_nominal(
        self,
        start_inclusive_ns: int,
        end_exclusive_ns: int,
    ) -> InventoryTimeline:
        """Return the causal timeline inside an exact half-open nominal range.

        Binance daily archives occasionally carry one observation for the
        following UTC boundary.  Replay discovery still verifies the complete
        source archive; this view prevents that next-period observation from
        becoming visible inside the requested replay while retaining duplicate
        provenance for observations which remain in range.
        """

        if end_exclusive_ns <= start_inclusive_ns:
            raise ValueError("inventory timeline range must be positive")
        bounded = InventoryTimeline(
            item
            for item in self.points
            if start_inclusive_ns <= item.nominal_ts_ns < end_exclusive_ns
        )
        bounded.duplicate_observed_ts_ns = tuple(
            timestamp
            for timestamp in self.duplicate_observed_ts_ns
            if start_inclusive_ns <= timestamp < end_exclusive_ns
        )
        bounded.conflicting_duplicates = tuple(
            item
            for item in self.conflicting_duplicates
            if start_inclusive_ns <= item.nominal_ts_ns < end_exclusive_ns
        )
        return bounded

    def evaluate(
        self,
        *,
        shock_side: str,
        episode_start_ns: int,
        decision_ts_ns: int,
        price_move: float,
        signed_taker_flow: float,
        change_points: int = 3,
    ) -> InventoryDecision:
        """Evaluate the latest post-episode row and a contiguous prior window.

        ``change_points=3`` describes the 15-minute change used by V158, but it
        is a measurement horizon rather than an alpha threshold.
        """

        if change_points < 1:
            raise ValueError("change_points must be positive")
        symbol = self.symbol
        side = shock_side.upper()
        current_index = bisect_right(self._observed, decision_ts_ns) - 1
        if current_index < 0:
            return _unknown(
                symbol=symbol,
                reason="NO_VISIBLE_METRIC",
                shock_side=side,
                episode_start_ns=episode_start_ns,
                decision_ts_ns=decision_ts_ns,
            )
        current = self.points[current_index]
        if current.source_ts_ns < episode_start_ns:
            return _unknown(
                symbol=symbol,
                reason="NO_POST_EPISODE_METRIC",
                shock_side=side,
                episode_start_ns=episode_start_ns,
                decision_ts_ns=decision_ts_ns,
                current=current,
            )
        prior_index = current_index - change_points
        if prior_index < 0:
            return _unknown(
                symbol=symbol,
                reason="INSUFFICIENT_PRIOR_METRICS",
                shock_side=side,
                episode_start_ns=episode_start_ns,
                decision_ts_ns=decision_ts_ns,
                current=current,
            )
        window = self.points[prior_index : current_index + 1]
        if any(
            right.nominal_ts_ns - left.nominal_ts_ns != FIVE_MINUTE_NS
            for left, right in zip(window, window[1:])
        ):
            return _unknown(
                symbol=symbol,
                reason="METRICS_WINDOW_HAS_GAP",
                shock_side=side,
                episode_start_ns=episode_start_ns,
                decision_ts_ns=decision_ts_ns,
                prior=window[0],
                current=current,
            )
        if any(
            CONFLICTING_OFFICIAL_DUPLICATE in item.invalid_fields
            for item in window
        ):
            return _unknown(
                symbol=symbol,
                reason="METRICS_WINDOW_HAS_CONFLICTING_OFFICIAL_DUPLICATE",
                shock_side=side,
                episode_start_ns=episode_start_ns,
                decision_ts_ns=decision_ts_ns,
                prior=window[0],
                current=current,
            )
        if any(item.open_interest is None for item in window):
            return _unknown(
                symbol=symbol,
                reason="METRICS_WINDOW_HAS_INVALID_OPEN_INTEREST",
                shock_side=side,
                episode_start_ns=episode_start_ns,
                decision_ts_ns=decision_ts_ns,
                prior=window[0],
                current=current,
            )
        return classify_inventory_change(
            window[0],
            current,
            shock_side=side,
            episode_start_ns=episode_start_ns,
            decision_ts_ns=decision_ts_ns,
            price_move=price_move,
            signed_taker_flow=signed_taker_flow,
        )


_ALIASES = {
    "create_time": ("create_time", "timestamp", "time"),
    "symbol": ("symbol",),
    "open_interest": ("sum_open_interest", "open_interest"),
    "open_interest_value": ("sum_open_interest_value", "open_interest_value"),
    "top_account": ("count_toptrader_long_short_ratio",),
    "top_position": ("sum_toptrader_long_short_ratio",),
    "all_account": ("count_long_short_ratio",),
    "taker_ratio": ("sum_taker_long_short_vol_ratio",),
}

_METRIC_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)


def _header(value: str) -> str:
    value = value.strip().replace("-", "_").replace(" ", "_")
    value = re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
    return re.sub(r"_+", "_", value)


def _timestamp_ns(raw: str) -> int:
    text = raw.strip()
    try:
        value = int(float(text))
    except ValueError:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)
    if value >= 100_000_000_000_000_000:
        return value
    if value >= 100_000_000_000_000:
        return value * 1_000
    if value >= 100_000_000_000:
        return value * 1_000_000
    return value * 1_000_000_000


def _optional_float(row: dict[str, str], aliases: Sequence[str]) -> float | None:
    for name in aliases:
        value = row.get(name)
        if value not in {None, ""}:
            return float(value)
    return None


def _positive_metric_value(
    row: dict[str, str],
    aliases: Sequence[str],
    field: str,
    invalid_fields: list[str],
) -> float | None:
    value = _optional_float(row, aliases)
    if value is None or not math.isfinite(value) or value <= 0.0:
        invalid_fields.append(field)
        return None
    return value


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _verify_checksum(path: Path) -> str:
    checksum = Path(str(path) + ".CHECKSUM")
    if not checksum.exists():
        raise InventoryDataError(f"official checksum is missing for {path}")
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    if len(expected) != 64 or _sha256(path) != expected:
        raise InventoryDataError(f"checksum mismatch for {path}")
    return expected


def _request_bytes(url: str, *, attempts: int = 4) -> bytes:
    """Fetch one fixed-host artifact, preserving a terminal HTTP 404."""

    request = Request(
        url,
        headers={"User-Agent": "SMC-ICT-4-liquidity-synthesis/metrics-v1"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 fixed HTTPS host
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise
            last_error = exc
        except (URLError, TimeoutError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1.5 * (attempt + 1))
    raise InventoryDataError(f"download failed after {attempts} attempts: {url}: {last_error}")


def _expected_checksum(payload: bytes, filename: str) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryDataError(f"checksum for {filename} is not UTF-8") from exc
    for line in text.splitlines():
        fields = line.strip().replace("*", " ").split()
        if not fields or len(fields[0]) != 64:
            continue
        if len(fields) == 1 or fields[-1].endswith(filename):
            return fields[0].lower()
    raise InventoryDataError(f"could not parse official checksum for {filename}")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _valid_zip(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as bundle:
            return bundle.testzip() is None
    except (OSError, zipfile.BadZipFile):
        return False


def _already_verified(archive: Path, checksum: Path) -> tuple[bool, str | None]:
    if not archive.exists() or not checksum.exists():
        return False, None
    try:
        expected = _expected_checksum(checksum.read_bytes(), archive.name)
    except (OSError, InventoryDataError):
        return False, None
    return _sha256(archive) == expected and _valid_zip(archive), expected


def _download_metrics_archive(
    symbol: str,
    day: date,
    destination: Path,
) -> MetricsArchiveEvidence:
    filename = f"{symbol}-metrics-{day.isoformat()}.zip"
    url = f"{OFFICIAL_METRICS_BASE_URL}/{symbol}/{filename}"
    directory = destination / symbol
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    verified, expected = _already_verified(archive, checksum)
    if verified:
        return MetricsArchiveEvidence(
            symbol=symbol,
            day=day,
            status="VERIFIED_REUSED",
            url=url,
            archive_path=archive,
            checksum_path=checksum,
            bytes=archive.stat().st_size,
            sha256=expected,
        )

    try:
        checksum_payload = _request_bytes(f"{url}.CHECKSUM")
    except HTTPError as exc:
        if exc.code != 404:
            raise
        return MetricsArchiveEvidence(
            symbol=symbol,
            day=day,
            status="OFFICIAL_ARCHIVE_UNAVAILABLE",
            url=url,
            archive_path=None,
            checksum_path=None,
            bytes=None,
            sha256=None,
        )
    expected = _expected_checksum(checksum_payload, filename)

    if archive.exists() and _sha256(archive) == expected and _valid_zip(archive):
        _atomic_write(checksum, checksum_payload)
        return MetricsArchiveEvidence(
            symbol=symbol,
            day=day,
            status="VERIFIED_REUSED",
            url=url,
            archive_path=archive,
            checksum_path=checksum,
            bytes=archive.stat().st_size,
            sha256=expected,
        )

    try:
        archive_payload = _request_bytes(url)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        return MetricsArchiveEvidence(
            symbol=symbol,
            day=day,
            status="OFFICIAL_ARCHIVE_UNAVAILABLE",
            url=url,
            archive_path=None,
            checksum_path=None,
            bytes=None,
            sha256=None,
        )
    actual = sha256(archive_payload).hexdigest()
    if actual != expected:
        raise InventoryDataError(
            f"checksum mismatch for {filename}: expected={expected}, actual={actual}",
        )
    directory.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(archive.name + ".part")
    try:
        temporary.write_bytes(archive_payload)
        if not _valid_zip(temporary):
            raise InventoryDataError(f"official archive is not a valid ZIP: {filename}")
        temporary.replace(archive)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    _atomic_write(checksum, checksum_payload)
    return MetricsArchiveEvidence(
        symbol=symbol,
        day=day,
        status="VERIFIED_DOWNLOADED",
        url=url,
        archive_path=archive,
        checksum_path=checksum,
        bytes=archive.stat().st_size,
        sha256=actual,
    )


def download_official_metrics_range(
    *,
    symbols: Iterable[str],
    start: date,
    end_exclusive: date,
    destination: str | Path,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
) -> MetricsDownloadResult:
    """Download official daily metrics over a half-open date range.

    At most four requests are in flight.  Existing checksum-verified files are
    reused without network access, making an interrupted backfill resumable.
    A provider 404 is returned as explicit evidence rather than synthesized or
    silently omitted.
    """

    selected = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
    if not selected:
        raise ValueError("at least one metrics symbol is required")
    unsupported = set(selected) - set(SUPPORTED_METRICS_SYMBOLS)
    if unsupported:
        raise ValueError(f"unsupported metrics symbols: {sorted(unsupported)}")
    if end_exclusive <= start:
        raise ValueError("metrics date range must be positive and half-open")
    if not 1 <= max_workers <= MAX_DOWNLOAD_WORKERS:
        raise ValueError(f"max_workers must be between 1 and {MAX_DOWNLOAD_WORKERS}")

    root = Path(destination).resolve()
    jobs = (
        (symbol, day)
        for day_offset in range((end_exclusive - start).days)
        for symbol in selected
        for day in (start + timedelta(days=day_offset),)
    )
    evidence: list[MetricsArchiveEvidence] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending: dict[Future[MetricsArchiveEvidence], tuple[str, date]] = {}

        def submit_next() -> bool:
            try:
                symbol, day = next(jobs)
            except StopIteration:
                return False
            future = pool.submit(_download_metrics_archive, symbol, day, root)
            pending[future] = (symbol, day)
            return True

        for _ in range(max_workers):
            if not submit_next():
                break
        while pending:
            completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in completed:
                pending.pop(future)
                evidence.append(future.result())
                submit_next()
    evidence.sort(key=lambda item: (item.day, item.symbol))
    return MetricsDownloadResult(
        start=start,
        end_exclusive=end_exclusive,
        evidence=tuple(evidence),
    )


def load_official_metrics_archives(
    symbol: str,
    archives: Iterable[str | Path],
    *,
    verify_checksums: bool = True,
) -> InventoryTimeline:
    """Load checksum-verified official Binance USD-M daily metrics ZIPs."""

    points: list[InventoryMetric] = []
    for item in sorted(Path(value) for value in archives):
        archive_sha256 = _verify_checksum(item) if verify_checksums else _sha256(item)
        archive_name = item.name
        with zipfile.ZipFile(item) as bundle:
            members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise InventoryDataError(f"expected one CSV in {item}, found {members}")
            with bundle.open(members[0]) as raw:
                rows = list(csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig")))
                if not rows:
                    raise InventoryDataError(f"metrics archive has no rows: {item}")
                normalized = [_header(value) for value in rows[0]]
                has_header = "create_time" in normalized or "sum_open_interest" in normalized
                names = normalized if has_header else list(_METRIC_COLUMNS)
                data_rows = rows[1:] if has_header else rows
                for values in data_rows:
                    if not values:
                        continue
                    if len(values) < len(names):
                        raise InventoryDataError(f"short metrics row in {item}")
                    row = {
                        names[index]: values[index].strip()
                        for index in range(len(names))
                    }
                    source_raw = next(
                        (row[name] for name in _ALIASES["create_time"] if row.get(name)),
                        None,
                    )
                    if source_raw is None or not any(
                        name in row for name in _ALIASES["open_interest"]
                    ):
                        raise InventoryDataError(f"required metrics fields absent in {item}")
                    row_symbol = next(
                        (row[name] for name in _ALIASES["symbol"] if row.get(name)),
                        symbol,
                    )
                    if row_symbol != symbol:
                        raise InventoryDataError(
                            f"metrics symbol mismatch in {item}: {row_symbol} != {symbol}",
                        )
                    source_ts = _timestamp_ns(source_raw)
                    nominal, observed = causal_metric_clock(source_ts)
                    invalid_fields: list[str] = []
                    oi = _positive_metric_value(
                        row,
                        _ALIASES["open_interest"],
                        "open_interest",
                        invalid_fields,
                    )
                    points.append(
                        InventoryMetric(
                            symbol=symbol,
                            source_ts_ns=source_ts,
                            nominal_ts_ns=nominal,
                            observed_ts_ns=observed,
                            open_interest=oi,
                            open_interest_value=_positive_metric_value(
                                row,
                                _ALIASES["open_interest_value"],
                                "open_interest_value",
                                invalid_fields,
                            ),
                            all_account_long_short=_positive_metric_value(
                                row,
                                _ALIASES["all_account"],
                                "all_account_long_short",
                                invalid_fields,
                            ),
                            top_account_long_short=_positive_metric_value(
                                row,
                                _ALIASES["top_account"],
                                "top_account_long_short",
                                invalid_fields,
                            ),
                            top_position_long_short=_positive_metric_value(
                                row,
                                _ALIASES["top_position"],
                                "top_position_long_short",
                                invalid_fields,
                            ),
                            taker_buy_sell_ratio=_positive_metric_value(
                                row,
                                _ALIASES["taker_ratio"],
                                "taker_buy_sell_ratio",
                                invalid_fields,
                            ),
                            invalid_fields=tuple(invalid_fields),
                            source_fingerprint=sha256(
                                "\x1f".join(values).encode("utf-8"),
                            ).digest(),
                            source_archive=archive_name,
                            source_archive_sha256=archive_sha256,
                        ),
                    )
    return InventoryTimeline(points)


__all__ = [
    "FIVE_MINUTE_NS",
    "MINUTE_NS",
    "CONFLICTING_OFFICIAL_DUPLICATE",
    "InventoryDataError",
    "InventoryConflictEvidence",
    "InventoryDecision",
    "InventoryInterpretation",
    "InventoryMetric",
    "InventoryRegime",
    "InventoryTimeline",
    "MAX_DOWNLOAD_WORKERS",
    "MetricsArchiveEvidence",
    "MetricsDownloadResult",
    "OwnershipBranch",
    "SUPPORTED_METRICS_SYMBOLS",
    "causal_metric_clock",
    "classify_inventory_change",
    "download_official_metrics_range",
    "load_official_metrics_archives",
]
