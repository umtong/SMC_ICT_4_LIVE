"""Common-core structural role-flip engine for EasyChart v17.

The v16 horizontal shelf implementation created one label for every consecutive
same-side pivot pair.  A human does not normally treat three reactions to one
visible price area as three independent structures.  This module gives the
structure a causal identity and a lifecycle:

* every directional-change pivot contributes its wick-to-body reaction interval;
* two reactions create a structure only when their literal intervals overlap;
* a later reaction updates the most recently confirmed compatible active
  structure only while the common intersection remains non-empty;
* the update is a new *version* of the same causal structure and supersedes the
  prior pending intent rather than adding another confirmation vote;
* an accepted body close through the common core ends that version's formation
  lifecycle, so old anchors cannot silently seed a later same-side structure;
* a loose three-candle wick gap is still recorded by the source census, but it
  cannot provide FVG entry geometry unless the source-stated large middle candle
  condition is satisfied.

This is an operationalization of the source's meaningful/clear structure and
first break-retest language.  It does not use a price-distance tolerance,
confluence score, outcome label, or fitted time decay.  The only traded policy
remains accepted break -> first retest with pre-break wave origin, full stop and
first still-active opposing objective.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable, Sequence

from domain_v3 import Candle, Side, TargetMode
from market_v4 import StructuralPivot
from market_v15 import FootprintRef, footprint_ref
from market_v16_structure import ReactionInterval, reaction_interval
from market_v7 import ExpiringArmedSetup
from source_footprints import SourceFVG, SourceOrderBlock


class StructurePhase(str, Enum):
    WAIT_BREAK = "WAIT_BREAK"
    OUTSIDE = "OUTSIDE"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class CommonCoreStructureVersion:
    """One causally observable version of a repeated reaction structure."""

    structure_id: str
    version_id: str
    version: int
    supersedes_version_ids: tuple[str, ...]
    symbol: str
    side: Side
    observed_time_ns: int
    timeframe_minutes: int
    zone_low: float
    zone_high: float
    anchors: tuple[StructuralPivot, ...]

    def __post_init__(self) -> None:
        if not self.structure_id or not self.version_id or not self.symbol:
            raise ValueError("structure identifiers must be non-empty")
        if self.version < 1:
            raise ValueError("structure version must be positive")
        if not all(math.isfinite(value) for value in (self.zone_low, self.zone_high)):
            raise ValueError("structure zone must be finite")
        if self.zone_high < self.zone_low:
            raise ValueError("invalid common-core zone")
        if len(self.anchors) < 2:
            raise ValueError("a structure requires at least two reactions")
        anchor_sides = {anchor.side for anchor in self.anchors}
        if len(anchor_sides) != 1:
            raise ValueError("all structure anchors must have the same side")
        anchor_side = self.anchors[0].side
        if self.side is Side.LONG and anchor_side != "HIGH":
            raise ValueError("long continuation structure must be resistance")
        if self.side is Side.SHORT and anchor_side != "LOW":
            raise ValueError("short continuation structure must be support")
        if self.observed_time_ns < max(anchor.observed_time_ns for anchor in self.anchors):
            raise ValueError("structure observed before an anchor")
        if self.version_id in self.supersedes_version_ids:
            raise ValueError("a structure version cannot supersede itself")

    @property
    def entry_boundary(self) -> float:
        return self.zone_high if self.side is Side.LONG else self.zone_low

    @property
    def anchor_count(self) -> int:
        return len(self.anchors)


@dataclass(slots=True)
class StructureState:
    structure: CommonCoreStructureVersion
    phase: StructurePhase = StructurePhase.WAIT_BREAK
    break_index: int | None = None
    break_time_ns: int | None = None
    setup_id: str | None = None


@dataclass(frozen=True, slots=True)
class CommonCoreAcceptedBreakConfig:
    tick_size: float
    signal_timeframe_minutes: int = 5
    valid_until_ns: int = 2**63 - 1

    def __post_init__(self) -> None:
        if not math.isfinite(self.tick_size) or self.tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if self.signal_timeframe_minutes <= 0:
            raise ValueError("signal timeframe must be positive")
        if self.valid_until_ns <= 0:
            raise ValueError("valid_until_ns must be positive")


@dataclass(frozen=True, slots=True)
class CommonCoreEngineUpdate:
    setups: tuple[ExpiringArmedSetup, ...] = ()
    cancel_setup_ids: tuple[str, ...] = ()
    events: tuple[dict[str, object], ...] = ()


@dataclass(slots=True)
class _Track:
    structure_id: str
    side: Side
    version: int
    version_id: str
    observed_time_ns: int
    zone_low: float
    zone_high: float
    anchors: list[StructuralPivot]
    active: bool = True

    @property
    def core(self) -> tuple[float, float]:
        return self.zone_low, self.zone_high


@dataclass(frozen=True, slots=True)
class _ObservedReaction:
    interval: ReactionInterval
    observed_time_ns: int


def _interval_overlap(
    first_low: float,
    first_high: float,
    second_low: float,
    second_high: float,
) -> tuple[float, float] | None:
    low = max(first_low, second_low)
    high = min(first_high, second_high)
    return None if high < low else (float(low), float(high))


def _accepted_through(
    *,
    side: Side,
    zone_low: float,
    zone_high: float,
    close: float,
) -> bool:
    return close > zone_high if side is Side.LONG else close < zone_low


def _unique_anchors(items: Iterable[StructuralPivot]) -> list[StructuralPivot]:
    keyed: dict[tuple[str, int, int, int], StructuralPivot] = {}
    for item in items:
        key = (item.side, item.event_time_ns, item.observed_time_ns, item.center_index)
        keyed[key] = item
    return sorted(
        keyed.values(),
        key=lambda item: (item.event_time_ns, item.observed_time_ns, item.center_index),
    )


def build_common_core_structures(
    *,
    symbol: str,
    candles: Sequence[Candle],
    pivots: Iterable[StructuralPivot],
    timeframe_minutes: int,
) -> list[CommonCoreStructureVersion]:
    """Build versioned repeated-reaction structures without pair inflation.

    Structure versions are emitted in the exact order they become observable.
    An update narrows the common core and supersedes prior compatible versions.
    The latest same-side reaction can seed a new structure only when the literal
    pair overlap survived all already-observed closes between the reactions.
    """
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be positive")
    indexed: dict[int, list[StructuralPivot]] = {}
    ordered = sorted(
        pivots,
        key=lambda item: (item.observed_time_ns, item.event_time_ns, item.side),
    )
    for pivot in ordered:
        if pivot.center_index < 0 or pivot.center_index >= len(candles):
            raise IndexError("pivot center outside candle sequence")
        indexed.setdefault(pivot.observed_time_ns, []).append(pivot)

    tracks: list[_Track] = []
    last_reaction: dict[Side, _ObservedReaction] = {}
    versions: list[CommonCoreStructureVersion] = []
    root_sequence = 0

    def emit(
        track: _Track,
        *,
        supersedes: tuple[str, ...],
    ) -> CommonCoreStructureVersion:
        item = CommonCoreStructureVersion(
            structure_id=track.structure_id,
            version_id=track.version_id,
            version=track.version,
            supersedes_version_ids=supersedes,
            symbol=symbol,
            side=track.side,
            observed_time_ns=track.observed_time_ns,
            timeframe_minutes=timeframe_minutes,
            zone_low=track.zone_low,
            zone_high=track.zone_high,
            anchors=tuple(track.anchors),
        )
        versions.append(item)
        return item

    def pair_survived(
        *,
        prior: _ObservedReaction,
        current: ReactionInterval,
        side: Side,
        core: tuple[float, float],
        current_observed_ns: int,
    ) -> bool:
        # The proposed structure was not observable until the current pivot was
        # confirmed, but a full body acceptance through the common area between
        # the two observations means the two reactions do not describe one
        # continuously defended price structure.
        for bar in candles:
            if not (
                prior.observed_time_ns < bar.ts_open_ns
                and bar.ts_close_ns < current_observed_ns
            ):
                continue
            if _accepted_through(
                side=side,
                zone_low=core[0],
                zone_high=core[1],
                close=bar.close,
            ):
                return False
        return True

    for current_candle in candles:
        # A structure version known before this candle opened is retired when a
        # body close accepts beyond its common core.  The current close cannot
        # retroactively invalidate a structure first observed at this same close.
        for track in tracks:
            if (
                track.active
                and track.observed_time_ns <= current_candle.ts_open_ns
                and _accepted_through(
                    side=track.side,
                    zone_low=track.zone_low,
                    zone_high=track.zone_high,
                    close=current_candle.close,
                )
            ):
                track.active = False

        newly_observed = sorted(
            indexed.get(current_candle.ts_close_ns, ()),
            key=lambda item: (item.event_time_ns, item.side),
        )
        for pivot in newly_observed:
            interval = reaction_interval(pivot, candles[pivot.center_index])
            side = Side.LONG if pivot.side == "HIGH" else Side.SHORT
            compatible = [
                track
                for track in tracks
                if track.active
                and track.side is side
                and _interval_overlap(
                    track.zone_low,
                    track.zone_high,
                    interval.low,
                    interval.high,
                )
                is not None
            ]

            if compatible:
                # A human normally updates the most recently confirmed visible
                # compatible structure, rather than counting the same reaction
                # as an independent vote for every old label.
                primary = max(
                    compatible,
                    key=lambda item: (item.observed_time_ns, item.version, item.structure_id),
                )
                core = _interval_overlap(
                    primary.zone_low,
                    primary.zone_high,
                    interval.low,
                    interval.high,
                )
                assert core is not None

                # Merge any other active structure whose common core still has
                # literal overlap with the updated core.  Interval geometry
                # supplies the identity; there is no distance tolerance.
                merged = [primary]
                for candidate in sorted(
                    (item for item in compatible if item is not primary),
                    key=lambda item: (item.observed_time_ns, item.structure_id),
                    reverse=True,
                ):
                    next_core = _interval_overlap(
                        core[0],
                        core[1],
                        candidate.zone_low,
                        candidate.zone_high,
                    )
                    if next_core is not None:
                        core = next_core
                        merged.append(candidate)

                root = min(
                    merged,
                    key=lambda item: (
                        item.anchors[0].event_time_ns,
                        item.structure_id,
                    ),
                )
                anchors = _unique_anchors(
                    [pivot, *(anchor for item in merged for anchor in item.anchors)]
                )
                supersedes = tuple(
                    sorted({item.version_id for item in merged})
                )
                for item in merged:
                    item.active = False
                next_version = max(item.version for item in merged) + 1
                version_id = (
                    f"{root.structure_id}:V{next_version}:"
                    f"{pivot.event_time_ns}:{pivot.observed_time_ns}"
                )
                track = _Track(
                    structure_id=root.structure_id,
                    side=side,
                    version=next_version,
                    version_id=version_id,
                    observed_time_ns=pivot.observed_time_ns,
                    zone_low=core[0],
                    zone_high=core[1],
                    anchors=anchors,
                )
                tracks.append(track)
                emit(track, supersedes=supersedes)
            else:
                prior = last_reaction.get(side)
                if prior is not None:
                    core = _interval_overlap(
                        prior.interval.low,
                        prior.interval.high,
                        interval.low,
                        interval.high,
                    )
                    if core is not None and pair_survived(
                        prior=prior,
                        current=interval,
                        side=side,
                        core=core,
                        current_observed_ns=pivot.observed_time_ns,
                    ):
                        root_sequence += 1
                        structure_id = (
                            f"COMMON_CORE:{symbol}:{timeframe_minutes}:"
                            f"{pivot.side}:{prior.interval.pivot.event_time_ns}:"
                            f"{pivot.event_time_ns}:{root_sequence}"
                        )
                        version_id = (
                            f"{structure_id}:V1:{pivot.observed_time_ns}"
                        )
                        track = _Track(
                            structure_id=structure_id,
                            side=side,
                            version=1,
                            version_id=version_id,
                            observed_time_ns=pivot.observed_time_ns,
                            zone_low=core[0],
                            zone_high=core[1],
                            anchors=_unique_anchors(
                                (prior.interval.pivot, pivot)
                            ),
                        )
                        tracks.append(track)
                        emit(track, supersedes=())

            last_reaction[side] = _ObservedReaction(
                interval=interval,
                observed_time_ns=pivot.observed_time_ns,
            )

    return sorted(
        versions,
        key=lambda item: (item.observed_time_ns, item.version_id),
    )


class CommonCoreAcceptedBreakEngine:
    """One-symbol versioned structure -> accepted break -> first retest."""

    def __init__(
        self,
        symbol: str,
        structures: Iterable[CommonCoreStructureVersion],
        pivots: Iterable[StructuralPivot],
        config: CommonCoreAcceptedBreakConfig,
    ) -> None:
        self.symbol = symbol
        self.config = config
        self.pending_structures = sorted(
            structures,
            key=lambda item: (item.observed_time_ns, item.version_id),
        )
        self.pivots = sorted(
            pivots,
            key=lambda item: (item.observed_time_ns, item.event_time_ns),
        )
        self.structure_cursor = 0
        self.active: dict[str, StructureState] = {}
        self.candles: list[Candle] = []
        self.footprints: dict[str, FootprintRef] = {}
        self.sequence = 0
        self.setup_by_version: dict[str, str] = {}
        self.superseded_versions: set[str] = set()
        self.diagnostics: dict[str, int] = {}
        self.audit_rows: list[dict[str, object]] = []

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self) -> str:
        self.sequence += 1
        return f"ec17-common-core-{self.symbol}-{self.sequence:08d}"

    def ingest_footprints(
        self,
        items: Iterable[SourceOrderBlock | SourceFVG | FootprintRef],
    ) -> None:
        for raw in items:
            item = raw if isinstance(raw, FootprintRef) else footprint_ref(raw)
            if item.footprint_id in self.footprints:
                continue
            self.footprints[item.footprint_id] = item
            self._count(f"footprints_{item.kind.lower()}")
            if item.kind == "FVG" and item.source_two_x_quality:
                self._count("footprints_fvg_source_valid")

    def _activate(self, current: Candle) -> tuple[list[str], list[dict[str, object]]]:
        cancellations: list[str] = []
        events: list[dict[str, object]] = []
        while (
            self.structure_cursor < len(self.pending_structures)
            and self.pending_structures[self.structure_cursor].observed_time_ns
            <= current.ts_open_ns
        ):
            structure = self.pending_structures[self.structure_cursor]
            self.structure_cursor += 1
            for prior_id in structure.supersedes_version_ids:
                prior = self.active.pop(prior_id, None)
                already_superseded = prior_id in self.superseded_versions
                self.superseded_versions.add(prior_id)
                if not already_superseded:
                    self._count("structure_versions_superseded")
                pending_id = self.setup_by_version.get(prior_id)
                if pending_id is None and prior is not None:
                    pending_id = prior.setup_id
                if pending_id is not None:
                    cancellations.append(pending_id)
                    self._count("pending_intents_cancelled_by_structure_update")
                events.append(
                    {
                        "structure_id": structure.structure_id,
                        "version_id": structure.version_id,
                        "superseded_version_id": prior_id,
                        "event": "STRUCTURE_VERSION_SUPERSEDED",
                        "time_ns": current.ts_open_ns,
                        "cancel_setup_id": pending_id,
                    }
                )
            self.active[structure.version_id] = StructureState(structure)
            self._count("structure_versions_activated")
            events.append(
                {
                    "structure_id": structure.structure_id,
                    "version_id": structure.version_id,
                    "event": "STRUCTURE_VERSION_ACTIVATED",
                    "time_ns": current.ts_open_ns,
                    "zone_low": structure.zone_low,
                    "zone_high": structure.zone_high,
                    "anchor_count": structure.anchor_count,
                }
            )
        return cancellations, events

    def _bars_between(self, after_ns: int, before_open_ns: int) -> Sequence[Candle]:
        return [
            bar
            for bar in self.candles
            if bar.ts_open_ns >= after_ns and bar.ts_close_ns < before_open_ns
        ]

    def _fresh(self, item: FootprintRef, current: Candle) -> bool:
        return not any(
            bar.low <= item.zone_high and bar.high >= item.zone_low
            for bar in self._bars_between(item.observed_time_ns, current.ts_open_ns)
        )

    def _eligible_footprints(
        self,
        *,
        state: StructureState,
        current: Candle,
    ) -> list[FootprintRef]:
        assert state.break_time_ns is not None
        side = state.structure.side
        boundary = state.structure.entry_boundary
        output: list[FootprintRef] = []
        for item in self.footprints.values():
            if (
                item.side is not side
                or not state.break_time_ns <= item.observed_time_ns <= current.ts_close_ns
                or not self._fresh(item, current)
            ):
                continue
            if item.kind == "FVG" and not item.source_two_x_quality:
                self._count("loose_fvg_rejected_source_definition")
                continue
            entry = item.proximal
            if side is Side.LONG and boundary <= entry <= current.close:
                output.append(item)
            elif side is Side.SHORT and current.close <= entry <= boundary:
                output.append(item)
        output.sort(
            key=lambda item: (
                item.observed_time_ns,
                -item.timeframe_minutes,
                item.footprint_id,
            )
        )
        return output

    def _entry_surface(
        self,
        *,
        state: StructureState,
        current: Candle,
    ) -> tuple[float, FootprintRef | None, str]:
        boundary = state.structure.entry_boundary
        footprints = self._eligible_footprints(state=state, current=current)
        candidates: list[tuple[float, FootprintRef | None, str]] = [
            (boundary, None, "COMMON_CORE_STRUCTURE"),
        ]
        for item in footprints:
            kind = "STRICT_FVG" if item.kind == "FVG" else item.kind
            candidates.append((item.proximal, item, kind))
        if state.structure.side is Side.LONG:
            return max(candidates, key=lambda value: (value[0], value[2]))
        return min(candidates, key=lambda value: (value[0], value[2]))

    def _origin(
        self,
        *,
        state: StructureState,
        current: Candle,
    ) -> StructuralPivot | None:
        assert state.break_time_ns is not None
        wanted = "LOW" if state.structure.side is Side.LONG else "HIGH"
        eligible = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.event_time_ns < state.break_time_ns
            and pivot.observed_time_ns <= current.ts_close_ns
        ]
        return max(eligible, default=None, key=lambda item: item.event_time_ns)

    def _pivot_consumed_before(
        self,
        pivot: StructuralPivot,
        current: Candle,
    ) -> bool:
        for bar in self._bars_between(pivot.observed_time_ns, current.ts_open_ns):
            if pivot.side == "HIGH" and bar.high >= pivot.level:
                return True
            if pivot.side == "LOW" and bar.low <= pivot.level:
                return True
        if pivot.side == "HIGH" and current.high >= pivot.level:
            return True
        if pivot.side == "LOW" and current.low <= pivot.level:
            return True
        return False

    def _first_objective(
        self,
        *,
        side: Side,
        entry: float,
        current: Candle,
    ) -> StructuralPivot | None:
        wanted = "HIGH" if side is Side.LONG else "LOW"
        candidates = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.observed_time_ns < current.ts_close_ns
            and (
                (side is Side.LONG and pivot.level > entry)
                or (side is Side.SHORT and pivot.level < entry)
            )
            and not self._pivot_consumed_before(pivot, current)
        ]
        if side is Side.LONG:
            return min(candidates, default=None, key=lambda item: item.level)
        return max(candidates, default=None, key=lambda item: item.level)

    def _build_setup(
        self,
        *,
        state: StructureState,
        current: Candle,
    ) -> ExpiringArmedSetup | None:
        structure = state.structure
        side = structure.side
        origin = self._origin(state=state, current=current)
        entry, item, entry_kind = self._entry_surface(state=state, current=current)
        audit: dict[str, object] = {
            "structure_id": structure.structure_id,
            "version_id": structure.version_id,
            "version": structure.version,
            "anchor_count": structure.anchor_count,
            "symbol": self.symbol,
            "side": side.name,
            "zone_low": structure.zone_low,
            "zone_high": structure.zone_high,
            "break_time_ns": state.break_time_ns,
            "acceptance_time_ns": current.ts_close_ns,
            "entry": entry,
            "entry_kind": entry_kind,
            "footprint_id": None if item is None else item.footprint_id,
            "footprint_source_two_x_quality": (
                None if item is None else item.source_two_x_quality
            ),
        }
        if origin is None:
            self._count("missing_prebreak_wave_origin")
            audit["disposition"] = "UNRESOLVED_MISSING_PREBREAK_WAVE_ORIGIN"
            self.audit_rows.append(audit)
            return None
        invalidation = origin.level
        if item is not None:
            if side is Side.LONG:
                invalidation = min(invalidation, item.invalidation)
            else:
                invalidation = max(invalidation, item.invalidation)
        stop = (
            invalidation - self.config.tick_size
            if side is Side.LONG
            else invalidation + self.config.tick_size
        )
        objective = self._first_objective(side=side, entry=entry, current=current)
        audit.update(
            {
                "origin_level": origin.level,
                "origin_event_time_ns": origin.event_time_ns,
                "origin_observed_time_ns": origin.observed_time_ns,
                "invalidation": invalidation,
                "stop": stop,
                "objective": None if objective is None else objective.level,
                "objective_event_time_ns": (
                    None if objective is None else objective.event_time_ns
                ),
                "objective_observed_time_ns": (
                    None if objective is None else objective.observed_time_ns
                ),
            }
        )
        if objective is None:
            self._count("missing_active_objective")
            audit["disposition"] = "UNRESOLVED_NO_ACTIVE_OBJECTIVE"
            self.audit_rows.append(audit)
            return None
        target = objective.level
        if side is Side.LONG and not stop < entry < target:
            self._count("invalid_long_geometry")
            audit["disposition"] = "REJECT_INVALID_LONG_GEOMETRY"
            self.audit_rows.append(audit)
            return None
        if side is Side.SHORT and not target < entry < stop:
            self._count("invalid_short_geometry")
            audit["disposition"] = "REJECT_INVALID_SHORT_GEOMETRY"
            self.audit_rows.append(audit)
            return None

        setup_id = self._new_id()
        setup = ExpiringArmedSetup(
            setup_id=setup_id,
            causal_event_id=(
                f"COMMON_CORE_ACCEPTED_BREAK:{self.symbol}:"
                f"{structure.structure_id}:{structure.version_id}:"
                f"{state.break_time_ns}:{current.ts_close_ns}:{side.name}"
            ),
            symbol=self.symbol,
            family=(
                "COMMON_CORE_ACCEPTED_BREAK_CONTINUATION_FIRST_RETEST_"
                f"{entry_kind}"
            ),
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=float(entry),
            stop=float(stop),
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=float(target),
            fixed_target_id=(
                f"FIRST_ACTIVE_DC_PIVOT:{objective.side}:{objective.event_time_ns}"
            ),
            source_pool_id=structure.version_id,
            zone_low=float(item.zone_low if item is not None else structure.zone_low),
            zone_high=float(item.zone_high if item is not None else structure.zone_high),
            formation_extreme=float(invalidation),
            body_ratio=(
                2.0 if item is not None and item.source_two_x_quality else 0.0
            ),
            previous_body=0.0,
            current_body=0.0,
            context_bias=(
                "ROLE_GRAPH_V17|OPTION=COMMON_CORE_ACCEPTED_BREAK_FIRST_RETEST"
                "|LOCATION=VERSIONED_COMMON_INTERSECTION_OF_REACTIONS"
                "|IDENTITY=MOST_RECENT_COMPATIBLE_ACTIVE_STRUCTURE"
                "|ACCEPTANCE=SEPARATE_OUTSIDE_OPEN_AND_CLOSE"
                f"|STRUCTURE_ID={structure.structure_id}"
                f"|VERSION={structure.version}"
                f"|ANCHORS={structure.anchor_count}"
                f"|ENTRY_KIND={entry_kind}"
                f"|ORIGIN_EVENT={origin.event_time_ns}"
                f"|BREAK={state.break_time_ns}"
                f"|ACCEPT={current.ts_close_ns}"
                f"|FOOTPRINT={item.footprint_id if item is not None else 'NONE'}"
                "|FVG_ELIGIBILITY=SOURCE_TWO_X_REQUIRED"
                "|SOURCE_STATUS=SOURCE_EXPLICIT_PLUS_NAMED_OPERATIONALIZATION"
            ),
            source_timeframe_minutes=self.config.signal_timeframe_minutes,
            valid_until_ns=self.config.valid_until_ns,
        )
        plan = setup.executable(
            target,
            target_id=setup.fixed_target_id,
            min_gross_rr=1.0,
        )
        if plan is None:
            self._count("first_objective_rr_lt_1")
            audit["disposition"] = "REJECT_FIRST_ACTIVE_OBJECTIVE_RR_LT_1"
            self.audit_rows.append(audit)
            return None
        audit["disposition"] = "ARM_FIRST_RETEST"
        audit["setup_id"] = setup_id
        audit["gross_rr"] = plan.gross_rr
        self.audit_rows.append(audit)
        self._count("setups_armed")
        self._count(f"setups_armed_{entry_kind.lower()}")
        state.setup_id = setup_id
        self.setup_by_version[structure.version_id] = setup_id
        return setup

    def _observe_state(
        self,
        state: StructureState,
        current: Candle,
        index: int,
    ) -> CommonCoreEngineUpdate:
        structure = state.structure
        if state.phase is StructurePhase.COMPLETED:
            return CommonCoreEngineUpdate()
        boundary = structure.entry_boundary
        outside_close = (
            current.close > boundary
            if structure.side is Side.LONG
            else current.close < boundary
        )
        inside_close = not outside_close

        if state.phase is StructurePhase.WAIT_BREAK:
            if outside_close:
                state.phase = StructurePhase.OUTSIDE
                state.break_index = index
                state.break_time_ns = current.ts_close_ns
                self._count("first_outside_closes")
                return CommonCoreEngineUpdate(
                    events=(
                        {
                            "structure_id": structure.structure_id,
                            "version_id": structure.version_id,
                            "event": "FIRST_OUTSIDE_CLOSE",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            return CommonCoreEngineUpdate()

        assert state.phase is StructurePhase.OUTSIDE
        assert state.break_index is not None
        assert state.break_time_ns is not None
        if inside_close:
            state.phase = StructurePhase.COMPLETED
            self._count("break_failed_before_acceptance")
            return CommonCoreEngineUpdate(
                events=(
                    {
                        "structure_id": structure.structure_id,
                        "version_id": structure.version_id,
                        "event": "FAILED_BREAK_BEFORE_ACCEPTANCE",
                        "time_ns": current.ts_close_ns,
                    },
                )
            )

        opened_outside = (
            current.open > boundary
            if structure.side is Side.LONG
            else current.open < boundary
        )
        if index > state.break_index and opened_outside and outside_close:
            setup = self._build_setup(state=state, current=current)
            state.phase = StructurePhase.COMPLETED
            self._count("accepted_breaks")
            return CommonCoreEngineUpdate(
                setups=(() if setup is None else (setup,)),
                events=(
                    {
                        "structure_id": structure.structure_id,
                        "version_id": structure.version_id,
                        "event": "ACCEPTED_BREAK",
                        "time_ns": current.ts_close_ns,
                        "setup_id": None if setup is None else setup.setup_id,
                    },
                ),
            )
        return CommonCoreEngineUpdate()

    def on_close(self, current: Candle, index: int) -> CommonCoreEngineUpdate:
        cancellations, events = self._activate(current)
        setups: list[ExpiringArmedSetup] = []
        for version_id, state in list(self.active.items()):
            update = self._observe_state(state, current, index)
            setups.extend(update.setups)
            cancellations.extend(update.cancel_setup_ids)
            events.extend(update.events)
            if state.phase is StructurePhase.COMPLETED:
                self.active.pop(version_id, None)
        self.candles.append(current)
        setups.sort(key=lambda item: (item.observed_time_ns, item.setup_id))
        return CommonCoreEngineUpdate(
            setups=tuple(setups),
            cancel_setup_ids=tuple(dict.fromkeys(cancellations)),
            events=tuple(events),
        )


__all__ = [
    "CommonCoreAcceptedBreakConfig",
    "CommonCoreAcceptedBreakEngine",
    "CommonCoreEngineUpdate",
    "CommonCoreStructureVersion",
    "StructurePhase",
    "StructureState",
    "build_common_core_structures",
]
