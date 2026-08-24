"""Causal prior-distribution auction scenarios.

This module ports the useful market logic from the Candidate 16 value-profile
studies without their research-stage time boxes, scores, exits, or account
simulation.  A completed UTC day is sealed as a *bar-derived approximation* of
volume at price.  The following UTC day may then produce either:

* an outside-auction reclaim targeting the sealed point of control; or
* acceptance through a fresh HVN--LVN--HVN lane targeting the near edge of the
  opposite HVN.

Both paths are event-time state machines.  First physical lane contact and the
first later retest are final, resumption must occur on a strictly later bar,
and no amount of elapsed clock time ends a valid hypothesis.  The module emits
pre-entry geometry only; portfolio, orders, fills, and exits remain the concern
of the shared NautilusTrader execution layer.

The profile is deliberately named an approximation.  OHLCV bars do not reveal
where volume traded inside a bar, so each completed bar's quote volume is
allocated uniformly across the profile rows intersected by its high/low range.
No tick-level precision is claimed.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import math
from statistics import median
from typing import Iterable, Mapping, Sequence

from .domain import Bar, EntryZone, PolicyError, stable_id


_PROFILE_ROWS = 100
_VALUE_AREA_FRACTION = 0.70
_PARTICIPATION_BASELINE_BARS = 20


def _utc_day(time_ns: int) -> date:
    return datetime.fromtimestamp(time_ns / 1_000_000_000, tz=timezone.utc).date()


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise PolicyError("cannot calculate a quantile of no values")
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True, slots=True)
class VolumeAtPriceRow:
    """One row of the explicitly approximate sealed distribution."""

    index: int
    lower: float
    upper: float
    allocated_quote_volume: float


@dataclass(frozen=True, slots=True)
class DistributionLane:
    """A low-participation path between two accepted high-volume nodes."""

    lane_id: str
    lower_hvn_lower: float
    lower_hvn_upper: float
    lower_entry_edge: float
    upper_entry_edge: float
    upper_hvn_lower: float
    upper_hvn_upper: float
    lower_objective_id: str
    upper_objective_id: str
    lower_target: float
    upper_target: float
    row_width: float

    def __post_init__(self) -> None:
        if not (
            self.lower_hvn_lower < self.lower_hvn_upper
            <= self.lower_entry_edge < self.upper_entry_edge
            <= self.upper_hvn_lower < self.upper_hvn_upper
        ):
            raise PolicyError("invalid HVN-LVN-HVN lane geometry")


@dataclass(frozen=True, slots=True)
class SealedValueDistribution:
    """Prior completed UTC-day distribution, immutable after sealing."""

    profile_id: str
    symbol: str
    profile_day: date
    source_start_time_ns: int
    source_end_time_ns: int
    sealed_time_ns: int
    low: float
    high: float
    row_width: float
    rows: tuple[VolumeAtPriceRow, ...]
    poc: float
    val: float
    vah: float
    poc_objective_id: str
    lanes: tuple[DistributionLane, ...]
    total_quote_volume: float
    approximation: str = "UNIFORM_QUOTE_VOLUME_ACROSS_BAR_HIGH_LOW_ROWS"

    def __post_init__(self) -> None:
        if self.sealed_time_ns < self.source_end_time_ns:
            raise PolicyError("a profile cannot be observed before its source closes")
        if not self.low <= self.val <= self.poc <= self.vah <= self.high or self.val >= self.vah:
            raise PolicyError("invalid sealed value-area geometry")
        if self.row_width <= 0.0 or self.total_quote_volume <= 0.0:
            raise PolicyError("sealed distribution must contain positive volume")


@dataclass(frozen=True, slots=True)
class ValueDistributionCandidate:
    """Complete pre-entry geometry for the unified strategy router."""

    episode_id: str
    candidate_id: str
    symbol: str
    family: str
    scenario: str
    side: str
    decision_time_ns: int
    entry: float
    stop: float
    target: float
    source_object_id: str
    objective_object_id: str
    entry_zone: EntryZone
    evidence: Mapping[str, float | str | int]

    def __post_init__(self) -> None:
        if self.family != "VALUE_DISTRIBUTION_AUCTION":
            raise PolicyError("unexpected value-distribution family")
        if self.scenario not in {"OUTSIDE_AUCTION_RECLAIM", "LVN_ACCEPTANCE"}:
            raise PolicyError("unknown value-distribution scenario")
        if self.side not in {"LONG", "SHORT"}:
            raise PolicyError("candidate side must be LONG or SHORT")
        if self.side == "LONG" and not self.stop < self.entry < self.target:
            raise PolicyError("LONG geometry must satisfy stop < entry < target")
        if self.side == "SHORT" and not self.target < self.entry < self.stop:
            raise PolicyError("SHORT geometry must satisfy target < entry < stop")
        if self.gross_rr < 1.0 - 1e-12:
            raise PolicyError("candidate gross reward/risk must be at least 1.0")

    @property
    def gross_rr(self) -> float:
        return abs(self.target - self.entry) / abs(self.entry - self.stop)


def _clusters(mask: Sequence[bool]) -> list[tuple[int, int]]:
    clusters: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate((*mask, False)):
        if active and start is None:
            start = index
        elif not active and start is not None:
            clusters.append((start, index - 1))
            start = None
    return clusters


def _is_complete_utc_day(bars: Sequence[Bar]) -> bool:
    if len(bars) != 1_440 or any(bar.interval_minutes != 1 for bar in bars):
        return False
    ordered = sorted(bars, key=lambda item: item.open_time_ns)
    day = _utc_day(ordered[0].open_time_ns)
    if any(_utc_day(bar.open_time_ns) != day for bar in ordered):
        return False
    minute_ns = 60_000_000_000
    return all(
        later.open_time_ns - earlier.open_time_ns == minute_ns
        for earlier, later in zip(ordered, ordered[1:])
    )


def seal_completed_distribution(
    bars: Sequence[Bar],
    *,
    sealed_time_ns: int,
) -> SealedValueDistribution | None:
    """Seal one complete UTC day into a causal value distribution.

    ``None`` means that the completed day has no usable positive-volume price
    range.  Incomplete or mixed-symbol input is rejected rather than silently
    manufacturing a prior distribution.
    """

    if not bars:
        return None
    ordered = sorted(bars, key=lambda item: item.open_time_ns)
    symbols = {bar.symbol for bar in ordered}
    if len(symbols) != 1:
        raise PolicyError("a sealed profile must belong to exactly one symbol")
    if not _is_complete_utc_day(ordered):
        raise PolicyError("a sealed profile requires every completed UTC-day minute")
    source_end = max(bar.close_time_ns for bar in ordered)
    if sealed_time_ns < source_end:
        raise PolicyError("sealed_time_ns precedes the final source bar")

    low = min(bar.low for bar in ordered)
    high = max(bar.high for bar in ordered)
    total_quote_volume = sum(bar.quote_volume for bar in ordered)
    if high <= low or total_quote_volume <= 0.0:
        return None
    row_width = (high - low) / _PROFILE_ROWS
    volumes = [0.0] * _PROFILE_ROWS
    for bar in ordered:
        first = max(0, min(_PROFILE_ROWS - 1, int((bar.low - low) / row_width)))
        last = max(0, min(_PROFILE_ROWS - 1, int((bar.high - low) / row_width)))
        count = last - first + 1
        allocation = bar.quote_volume / count
        for row_index in range(first, last + 1):
            volumes[row_index] += allocation

    rows = tuple(
        VolumeAtPriceRow(
            index=index,
            lower=low + index * row_width,
            upper=low + (index + 1) * row_width,
            allocated_quote_volume=volume,
        )
        for index, volume in enumerate(volumes)
    )
    total = sum(volumes)
    poc_index = max(range(_PROFILE_ROWS), key=lambda index: (volumes[index], -abs(index - 49.5)))
    target_volume = total * _VALUE_AREA_FRACTION
    accumulated = volumes[poc_index]
    value_low = poc_index
    value_high = poc_index
    while accumulated < target_volume:
        lower_volume = volumes[value_low - 1] if value_low > 0 else -1.0
        upper_volume = volumes[value_high + 1] if value_high + 1 < _PROFILE_ROWS else -1.0
        if lower_volume < 0.0 and upper_volume < 0.0:
            break
        if lower_volume > upper_volume:
            value_low -= 1
            accumulated += lower_volume
        else:
            value_high += 1
            accumulated += upper_volume

    symbol = ordered[0].symbol
    profile_day = _utc_day(ordered[0].open_time_ns)
    profile_id = stable_id(symbol, profile_day.isoformat(), source_end, prefix="vdp-")
    poc = low + (poc_index + 0.5) * row_width
    poc_objective_id = stable_id(profile_id, "POC", poc_index, prefix="vdo-")

    positive = [volume for volume in volumes if volume > 0.0]
    lanes: list[DistributionLane] = []
    if positive:
        hvn_threshold = _quantile(positive, 0.75)
        lvn_threshold = _quantile(positive, 0.25)
        profile_median = median(positive)
        hvns = [item for item in _clusters([value >= hvn_threshold for value in volumes]) if item[1] - item[0] + 1 >= 3]
        lvns = [item for item in _clusters([0.0 < value <= lvn_threshold for value in volumes]) if 1 <= item[1] - item[0] + 1 <= 3]
        unique: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        for lvn_start, lvn_end in lvns:
            lower_nodes = [node for node in hvns if node[1] < lvn_start]
            upper_nodes = [node for node in hvns if node[0] > lvn_end]
            if not lower_nodes or not upper_nodes:
                continue
            lower_node = max(lower_nodes, key=lambda item: item[1])
            upper_node = min(upper_nodes, key=lambda item: item[0])
            gap_start = lower_node[1] + 1
            gap_end = upper_node[0] - 1
            if not 1 <= gap_end - gap_start + 1 <= 8:
                continue
            if max(volumes[gap_start : gap_end + 1]) > profile_median:
                continue
            unique[(gap_start, gap_end)] = (*lower_node, *upper_node)
        for (gap_start, gap_end), (lower_start, lower_end, upper_start, upper_end) in sorted(unique.items()):
            lower_target = low + (lower_end + 1) * row_width
            upper_target = low + upper_start * row_width
            lane_id = stable_id(profile_id, "LANE", gap_start, gap_end, prefix="vdl-")
            lanes.append(
                DistributionLane(
                    lane_id=lane_id,
                    lower_hvn_lower=low + lower_start * row_width,
                    lower_hvn_upper=lower_target,
                    lower_entry_edge=low + gap_start * row_width,
                    upper_entry_edge=low + (gap_end + 1) * row_width,
                    upper_hvn_lower=upper_target,
                    upper_hvn_upper=low + (upper_end + 1) * row_width,
                    lower_objective_id=stable_id(profile_id, "LOWER_HVN", lower_end, prefix="vdo-"),
                    upper_objective_id=stable_id(profile_id, "UPPER_HVN", upper_start, prefix="vdo-"),
                    lower_target=lower_target,
                    upper_target=upper_target,
                    row_width=row_width,
                ),
            )

    return SealedValueDistribution(
        profile_id=profile_id,
        symbol=symbol,
        profile_day=profile_day,
        source_start_time_ns=ordered[0].open_time_ns,
        source_end_time_ns=source_end,
        sealed_time_ns=sealed_time_ns,
        low=low,
        high=high,
        row_width=row_width,
        rows=rows,
        poc=poc,
        val=low + value_low * row_width,
        vah=low + (value_high + 1) * row_width,
        poc_objective_id=poc_objective_id,
        lanes=tuple(lanes),
        total_quote_volume=total,
    )


@dataclass(slots=True)
class _Episode:
    episode_id: str
    scenario: str
    side: str
    source_object_id: str
    objective_object_id: str
    edge: float
    target: float
    row_width: float
    state: str
    contact_time_ns: int | None = None
    control_time_ns: int | None = None
    outside_retention_time_ns: int | None = None
    retest_time_ns: int | None = None
    retest_open_time_ns: int | None = None
    retest_extreme: float | None = None
    retest_break: float | None = None
    evidence: dict[str, float | str | int] = field(default_factory=dict)


class ValueDistributionAuctionBook:
    """Streaming source book and event-time FSM for one symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.active_profile: SealedValueDistribution | None = None
        self._building_day: date | None = None
        self._building_bars: list[Bar] = []
        self._previous_bar: Bar | None = None
        self._participation: deque[float] = deque(maxlen=_PARTICIPATION_BASELINE_BARS)
        self._episodes: dict[str, _Episode] = {}
        self._consumed_sources: dict[str, tuple[int, str]] = {}
        self._consumed_objectives: dict[str, tuple[int, str]] = {}
        self._active_outside_source_id: str | None = None
        self._last_value_region: str | None = None

    @property
    def consumed_sources(self) -> Mapping[str, tuple[int, str]]:
        return dict(self._consumed_sources)

    @property
    def consumed_objectives(self) -> Mapping[str, tuple[int, str]]:
        return dict(self._consumed_objectives)

    def activate(self, profile: SealedValueDistribution, *, activation_time_ns: int) -> None:
        """Supersede the prior profile and create fresh causal source objects."""

        if profile.symbol != self.symbol:
            raise PolicyError("profile symbol does not match its auction book")
        if activation_time_ns < profile.sealed_time_ns:
            raise PolicyError("profile activation precedes profile observation")
        self.active_profile = profile
        self._episodes.clear()
        self._participation.clear()
        self._active_outside_source_id = None
        self._last_value_region = None
        for lane in profile.lanes:
            self._episodes[lane.lane_id] = _Episode(
                episode_id=stable_id(profile.profile_id, lane.lane_id, prefix="vde-"),
                scenario="LVN_ACCEPTANCE",
                side="UNDECIDED",
                source_object_id=lane.lane_id,
                objective_object_id="",
                edge=math.nan,
                target=math.nan,
                row_width=lane.row_width,
                state="WAIT_FIRST_CONTACT",
                evidence={
                    "profile_id": profile.profile_id,
                    "profile_day": profile.profile_day.isoformat(),
                    "lower_entry_edge": lane.lower_entry_edge,
                    "upper_entry_edge": lane.upper_entry_edge,
                },
            )

    def consume_source(self, source_id: str, *, time_ns: int, reason: str) -> None:
        self._consumed_sources.setdefault(source_id, (time_ns, reason))
        episode = self._episodes.get(source_id)
        if episode is not None and episode.state != "CANDIDATE_EMITTED":
            episode.state = "TERMINATED"

    def consume_objective(self, objective_id: str, *, time_ns: int, reason: str) -> None:
        self._consumed_objectives.setdefault(objective_id, (time_ns, reason))
        for episode in self._episodes.values():
            if episode.objective_object_id == objective_id and episode.state != "CANDIDATE_EMITTED":
                episode.state = "TERMINATED"

    def _consume_touched_objectives(self, bar: Bar) -> None:
        profile = self.active_profile
        if profile is None:
            return
        objectives = {profile.poc_objective_id: profile.poc}
        for lane in profile.lanes:
            objectives[lane.lower_objective_id] = lane.lower_target
            objectives[lane.upper_objective_id] = lane.upper_target
        for objective_id, price in objectives.items():
            if objective_id not in self._consumed_objectives and bar.low <= price <= bar.high:
                self.consume_objective(
                    objective_id,
                    time_ns=bar.close_time_ns,
                    reason="PHYSICAL_OBJECTIVE_TOUCH",
                )

    def _roll_day(self, bar: Bar) -> None:
        bar_day = _utc_day(bar.open_time_ns)
        if self._building_day is None:
            self._building_day = bar_day
            return
        if bar_day == self._building_day:
            return
        if bar_day < self._building_day:
            raise PolicyError("value-distribution bars must arrive in event-time order")
        immediately_follows = (bar_day - self._building_day).days == 1
        if immediately_follows and self._building_bars and _is_complete_utc_day(self._building_bars):
            profile = seal_completed_distribution(
                self._building_bars,
                sealed_time_ns=bar.open_time_ns,
            )
            if profile is not None:
                self.activate(profile, activation_time_ns=bar.open_time_ns)
        else:
            self.active_profile = None
            self._episodes.clear()
            self._participation.clear()
            self._active_outside_source_id = None
            self._last_value_region = None
        self._building_day = bar_day
        self._building_bars = []

    @staticmethod
    def _control(bar: Bar, side: str, spot_return: float | None) -> bool:
        sign = 1.0 if side == "LONG" else -1.0
        if sign * bar.body <= 0.0 or sign * bar.signed_quote_flow <= 0.0:
            return False
        return spot_return is None or sign * spot_return > 0.0

    @staticmethod
    def _target_touched(bar: Bar, side: str, target: float) -> bool:
        return bar.high >= target if side == "LONG" else bar.low <= target

    def _outside_episode(self, bar: Bar) -> _Episode | None:
        profile = self.active_profile
        if profile is None:
            return None
        if self._active_outside_source_id is not None:
            existing = self._episodes[self._active_outside_source_id]
            if existing.state not in {"TERMINATED"}:
                return existing
            self._active_outside_source_id = None

        if profile.poc_objective_id in self._consumed_objectives:
            return None
        if bar.close < profile.val:
            region, side, edge = "BELOW_VALUE", "LONG", profile.val
        elif bar.close > profile.vah:
            region, side, edge = "ABOVE_VALUE", "SHORT", profile.vah
        else:
            region = "INSIDE_VALUE"

        # The first post-activation completed bar may already establish an
        # outside auction.  Thereafter only a fresh inside -> outside departure
        # owns another episode; remaining outside is the same causal event.
        first_post_activation = self._last_value_region is None
        fresh_departure = region != "INSIDE_VALUE" and (
            first_post_activation or self._last_value_region == "INSIDE_VALUE"
        )
        if not fresh_departure:
            return None
        source_id = stable_id(
            profile.profile_id,
            "OUTSIDE_DEPARTURE",
            bar.close_time_ns,
            side,
            edge,
            prefix="vds-",
        )
        episode = _Episode(
            episode_id=stable_id(profile.profile_id, source_id, bar.close_time_ns, prefix="vde-"),
            scenario="OUTSIDE_AUCTION_RECLAIM",
            side=side,
            source_object_id=source_id,
            objective_object_id=profile.poc_objective_id,
            edge=edge,
            target=profile.poc,
            row_width=profile.row_width,
            state="ESTABLISH_OUTSIDE_AUCTION",
            evidence={
                "profile_id": profile.profile_id,
                "profile_day": profile.profile_day.isoformat(),
                "value_edge": edge,
                "profile_poc": profile.poc,
                "departure_time_ns": bar.close_time_ns,
                "departure_close": bar.close,
                "departure_extreme": bar.low if side == "LONG" else bar.high,
            },
        )
        self._episodes[source_id] = episode
        self._active_outside_source_id = source_id
        return episode

    def _candidate(self, episode: _Episode, bar: Bar) -> ValueDistributionCandidate | None:
        assert episode.retest_extreme is not None and episode.retest_break is not None
        sign = 1.0 if episode.side == "LONG" else -1.0
        entry = bar.close
        if episode.side == "LONG":
            stop = min(episode.edge - episode.row_width, episode.retest_extreme - episode.row_width)
        else:
            stop = max(episode.edge + episode.row_width, episode.retest_extreme + episode.row_width)
        target = episode.target
        valid = stop < entry < target if episode.side == "LONG" else target < entry < stop
        if not valid:
            episode.state = "TERMINATED"
            return None
        gross_rr = abs(target - entry) / abs(entry - stop)
        if gross_rr < 1.0 - 1e-12:
            episode.state = "TERMINATED"
            return None
        zone_lower = min(episode.edge, episode.retest_extreme)
        zone_upper = max(episode.edge, episode.retest_extreme)
        if zone_upper <= zone_lower:
            zone_lower = episode.edge - 0.5 * episode.row_width
            zone_upper = episode.edge + 0.5 * episode.row_width
        evidence = {
            **episode.evidence,
            "control_time_ns": int(episode.control_time_ns or 0),
            "retest_time_ns": int(episode.retest_time_ns or 0),
            "retest_extreme": episode.retest_extreme,
            "retest_break": episode.retest_break,
            "gross_rr": gross_rr,
        }
        candidate = ValueDistributionCandidate(
            episode_id=episode.episode_id,
            candidate_id=stable_id(episode.episode_id, bar.close_time_ns, prefix="vdc-"),
            symbol=self.symbol,
            family="VALUE_DISTRIBUTION_AUCTION",
            scenario=episode.scenario,
            side=episode.side,
            decision_time_ns=bar.close_time_ns,
            entry=entry,
            stop=stop,
            target=target,
            source_object_id=episode.source_object_id,
            objective_object_id=episode.objective_object_id,
            entry_zone=EntryZone(
                kind="VALUE_DISTRIBUTION_FIRST_RETEST",
                lower=zone_lower,
                upper=zone_upper,
                observed_time_ns=int(episode.retest_time_ns or bar.close_time_ns),
                source_bar_open_time_ns=int(episode.retest_open_time_ns or bar.open_time_ns),
            ),
            evidence=evidence,
        )
        episode.state = "CANDIDATE_EMITTED"
        self.consume_source(
            episode.source_object_id,
            time_ns=bar.close_time_ns,
            reason="CANDIDATE_EMITTED",
        )
        if (
            episode.scenario == "OUTSIDE_AUCTION_RECLAIM"
            and self._active_outside_source_id == episode.source_object_id
        ):
            # The emitted episode no longer owns the outside-auction pointer.
            # A later inside -> outside departure may therefore form a new
            # causal episode if the shared POC objective is still fresh.
            self._active_outside_source_id = None
        return candidate

    def _advance_outside(
        self,
        episode: _Episode,
        bar: Bar,
        spot_return: float | None,
    ) -> ValueDistributionCandidate | None:
        inside = self.active_profile is not None and self.active_profile.val <= bar.close <= self.active_profile.vah
        outside = bar.close < episode.edge if episode.side == "LONG" else bar.close > episode.edge
        if episode.state == "ESTABLISH_OUTSIDE_AUCTION":
            if outside:
                extension = episode.edge - bar.low if episode.side == "LONG" else bar.high - episode.edge
                episode.evidence["outside_extension"] = max(float(episode.evidence.get("outside_extension", 0.0)), extension)
                departure_time_ns = int(episode.evidence["departure_time_ns"])
                if bar.close_time_ns <= departure_time_ns:
                    # The departure bar establishes location only.  Acceptance
                    # requires a distinct completed bar to retain the auction
                    # outside value before any reclaim is eligible.
                    return None
                episode.outside_retention_time_ns = bar.close_time_ns
                episode.evidence.update(
                    {
                        "outside_retention_time_ns": bar.close_time_ns,
                        "outside_retention_close": bar.close,
                        "outside_retention_extreme": bar.low if episode.side == "LONG" else bar.high,
                    },
                )
                episode.state = "WAIT_STRICTLY_LATER_RECLAIM"
                return None
            if inside:
                self.consume_source(
                    episode.source_object_id,
                    time_ns=bar.close_time_ns,
                    reason="FIRST_RECLAIM_BEFORE_OUTSIDE_RETENTION",
                )
                episode.state = "TERMINATED"
            return None
        if episode.state == "WAIT_STRICTLY_LATER_RECLAIM":
            if outside:
                extension = episode.edge - bar.low if episode.side == "LONG" else bar.high - episode.edge
                episode.evidence["outside_extension"] = max(float(episode.evidence.get("outside_extension", 0.0)), extension)
                return None
            if inside:
                self.consume_source(episode.source_object_id, time_ns=bar.close_time_ns, reason="FIRST_RECLAIM_FINAL")
                assert episode.outside_retention_time_ns is not None
                physically_reclaimed = bar.high >= episode.edge if episode.side == "LONG" else bar.low <= episode.edge
                if (
                    bar.close_time_ns <= episode.outside_retention_time_ns
                    or not physically_reclaimed
                    or not self._control(bar, episode.side, spot_return)
                ):
                    episode.state = "TERMINATED"
                    return None
                if self._target_touched(bar, episode.side, episode.target):
                    self.consume_objective(
                        episode.objective_object_id,
                        time_ns=bar.close_time_ns,
                        reason="POC_TOUCHED_ON_RECLAIM",
                    )
                    episode.state = "TERMINATED"
                    return None
                if not (bar.close < episode.target if episode.side == "LONG" else bar.close > episode.target):
                    episode.state = "TERMINATED"
                    return None
                episode.control_time_ns = bar.close_time_ns
                episode.evidence["reclaim_time_ns"] = bar.close_time_ns
                episode.evidence["reclaim_close"] = bar.close
                episode.state = "WAIT_FIRST_RETEST"
            return None
        return self._advance_after_control(episode, bar, spot_return)

    def _advance_lane(
        self,
        episode: _Episode,
        lane: DistributionLane,
        bar: Bar,
        prior_bar: Bar | None,
        participation_baseline: float | None,
        spot_return: float | None,
    ) -> ValueDistributionCandidate | None:
        if episode.state == "WAIT_FIRST_CONTACT":
            if prior_bar is None:
                return None
            physical_contact = bar.high >= lane.lower_entry_edge and bar.low <= lane.upper_entry_edge
            if not physical_contact:
                return None
            long_contact = prior_bar.close < lane.lower_entry_edge
            short_contact = prior_bar.close > lane.upper_entry_edge
            self.consume_source(lane.lane_id, time_ns=bar.close_time_ns, reason="FIRST_PHYSICAL_CONTACT")
            if long_contact == short_contact:
                episode.state = "TERMINATED"
                return None
            episode.side = "LONG" if long_contact else "SHORT"
            episode.edge = lane.lower_entry_edge if long_contact else lane.upper_entry_edge
            episode.target = lane.upper_target if long_contact else lane.lower_target
            episode.objective_object_id = lane.upper_objective_id if long_contact else lane.lower_objective_id
            episode.contact_time_ns = bar.close_time_ns
            if episode.objective_object_id in self._consumed_objectives:
                episode.state = "TERMINATED"
                return None
            if self._target_touched(bar, episode.side, episode.target):
                self.consume_objective(episode.objective_object_id, time_ns=bar.close_time_ns, reason="TARGET_TOUCHED_ON_CONTACT")
                episode.state = "TERMINATED"
                return None
            inside = lane.lower_entry_edge < bar.close < lane.upper_entry_edge
            participation = (
                participation_baseline is not None
                and bar.quote_volume > participation_baseline
            )
            if not inside or not participation or not self._control(bar, episode.side, spot_return):
                episode.state = "TERMINATED"
                return None
            episode.control_time_ns = bar.close_time_ns
            episode.evidence.update(
                {
                    "contact_time_ns": bar.close_time_ns,
                    "contact_quote_volume": bar.quote_volume,
                    "participation_baseline": float(participation_baseline),
                },
            )
            episode.state = "WAIT_FIRST_RETEST"
            return None
        return self._advance_after_control(episode, bar, spot_return)

    def _advance_after_control(
        self,
        episode: _Episode,
        bar: Bar,
        spot_return: float | None,
    ) -> ValueDistributionCandidate | None:
        if episode.state in {"TERMINATED", "CANDIDATE_EMITTED"}:
            return None
        if self._target_touched(bar, episode.side, episode.target):
            self.consume_objective(episode.objective_object_id, time_ns=bar.close_time_ns, reason="OBJECTIVE_CONSUMED_BEFORE_ENTRY")
            episode.state = "TERMINATED"
            return None
        invalid_close = bar.close <= episode.edge if episode.side == "LONG" else bar.close >= episode.edge
        if episode.state == "WAIT_FIRST_RETEST":
            touched = bar.low <= episode.edge if episode.side == "LONG" else bar.high >= episode.edge
            if not touched:
                return None
            # The first physical retest is immutable.  It either defends on its
            # completed close or ends the scenario; later bars cannot replace it.
            if invalid_close:
                episode.state = "TERMINATED"
                return None
            episode.retest_time_ns = bar.close_time_ns
            episode.retest_open_time_ns = bar.open_time_ns
            episode.retest_extreme = bar.low if episode.side == "LONG" else bar.high
            episode.retest_break = bar.high if episode.side == "LONG" else bar.low
            episode.state = "WAIT_STRICTLY_LATER_RESUMPTION"
            return None
        if invalid_close:
            episode.state = "TERMINATED"
            return None
        assert episode.retest_time_ns is not None and episode.retest_break is not None
        if bar.close_time_ns <= episode.retest_time_ns:
            return None
        resumed = bar.close > episode.retest_break if episode.side == "LONG" else bar.close < episode.retest_break
        if resumed and self._control(bar, episode.side, spot_return):
            return self._candidate(episode, bar)
        return None

    def on_bar(
        self,
        bar: Bar,
        *,
        spot_return: float | None = None,
    ) -> tuple[ValueDistributionCandidate, ...]:
        """Advance the book with one completed one-minute bar.

        The current bar never enters its own participation baseline.  Therefore
        a lane's first physical contact consumes the source even when it occurs
        before twenty prior observations make that baseline available.
        """

        if bar.symbol != self.symbol or bar.interval_minutes != 1:
            raise PolicyError("value-distribution books accept one-minute bars for their own symbol")
        if self._previous_bar is not None and bar.open_time_ns <= self._previous_bar.open_time_ns:
            raise PolicyError("value-distribution bars must be strictly ordered")
        self._roll_day(bar)
        baseline = median(self._participation) if len(self._participation) == _PARTICIPATION_BASELINE_BARS else None
        candidates: list[ValueDistributionCandidate] = []
        profile = self.active_profile
        if profile is not None and _utc_day(bar.open_time_ns) > profile.profile_day:
            outside = self._outside_episode(bar)
            self._consume_touched_objectives(bar)
            if outside is not None:
                result = self._advance_outside(outside, bar, spot_return)
                if result is not None:
                    candidates.append(result)
            if bar.close < profile.val:
                self._last_value_region = "BELOW_VALUE"
            elif bar.close > profile.vah:
                self._last_value_region = "ABOVE_VALUE"
            else:
                self._last_value_region = "INSIDE_VALUE"
            lanes_by_id = {lane.lane_id: lane for lane in profile.lanes}
            for lane_id, lane in lanes_by_id.items():
                episode = self._episodes[lane_id]
                result = self._advance_lane(
                    episode,
                    lane,
                    bar,
                    self._previous_bar,
                    baseline,
                    spot_return,
                )
                if result is not None:
                    candidates.append(result)
        self._participation.append(bar.quote_volume)
        self._building_bars.append(bar)
        self._previous_bar = bar
        return tuple(candidates)


def sealed_profiles(bars: Iterable[Bar]) -> tuple[SealedValueDistribution, ...]:
    """Convenience builder for already ordered historical one-minute bars."""

    groups: dict[tuple[str, date], list[Bar]] = {}
    for bar in bars:
        groups.setdefault((bar.symbol, _utc_day(bar.open_time_ns)), []).append(bar)
    profiles: list[SealedValueDistribution] = []
    for _, group in sorted(groups.items(), key=lambda item: item[0]):
        if not _is_complete_utc_day(group):
            continue
        sealed_at = max(bar.close_time_ns for bar in group)
        profile = seal_completed_distribution(group, sealed_time_ns=sealed_at)
        if profile is not None:
            profiles.append(profile)
    return tuple(profiles)
