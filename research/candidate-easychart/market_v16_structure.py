"""Causal horizontal-structure accepted-break engine for EasyChart v16.

This module operationalizes the source's "clear/meaningful support or resistance"
without turning every fixed-span pivot into a structure and without adding a
confluence score.  A directional-change pivot contributes its wick-to-body
reaction interval.  Two causally confirmed same-side reaction intervals must
overlap before a horizontal shelf exists.  The interval overlap is a
computational-geometry translation of a repeatedly defended visible price area,
not a fitted alpha threshold.

The only traded option is source-supported accepted break -> first retest:

1. a pre-existing reaction shelf supplies location;
2. price closes outside;
3. a distinct candle opens and closes outside, establishing acceptance;
4. the broken shelf or a same-leg fresh OB/FVG supplies fixed entry geometry;
5. the breakout wave origin supplies full-position invalidation;
6. the nearest still-active opposing pivot supplies the single objective.

A first objective below 1R is not skipped.  Missing origin, objective or causal
ordering leaves the episode unresolved.  Execution remains a cheap diagnostic;
NautilusTrader and finer event data are required for performance evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable, Sequence

from domain_v3 import Candle, Side, TargetMode
from market_v4 import StructuralPivot
from market_v15 import FootprintRef, footprint_ref
from market_v7 import ExpiringArmedSetup
from source_footprints import SourceFVG, SourceOrderBlock


class ShelfPhase(str, Enum):
    WAIT_BREAK = "WAIT_BREAK"
    OUTSIDE = "OUTSIDE"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class ReactionInterval:
    pivot: StructuralPivot
    low: float
    high: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.low, self.high)):
            raise ValueError("reaction interval must be finite")
        if self.high < self.low:
            raise ValueError("invalid reaction interval")


@dataclass(frozen=True, slots=True)
class HorizontalReactionShelf:
    shelf_id: str
    symbol: str
    side: Side
    observed_time_ns: int
    timeframe_minutes: int
    zone_low: float
    zone_high: float
    first: StructuralPivot
    second: StructuralPivot

    def __post_init__(self) -> None:
        if not self.shelf_id or not self.symbol:
            raise ValueError("shelf identifiers must be non-empty")
        if self.zone_high < self.zone_low:
            raise ValueError("invalid shelf zone")
        if self.observed_time_ns < max(
            self.first.observed_time_ns,
            self.second.observed_time_ns,
        ):
            raise ValueError("shelf observed before an anchor")
        if self.first.side != self.second.side:
            raise ValueError("shelf anchors must have the same side")
        if self.side is Side.LONG and self.first.side != "HIGH":
            raise ValueError("long continuation shelf must be resistance")
        if self.side is Side.SHORT and self.first.side != "LOW":
            raise ValueError("short continuation shelf must be support")

    @property
    def entry_boundary(self) -> float:
        return self.zone_high if self.side is Side.LONG else self.zone_low


@dataclass(slots=True)
class ShelfState:
    shelf: HorizontalReactionShelf
    phase: ShelfPhase = ShelfPhase.WAIT_BREAK
    break_index: int | None = None
    break_time_ns: int | None = None
    setup_id: str | None = None


@dataclass(frozen=True, slots=True)
class StructuralAcceptedBreakConfig:
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
class StructuralEngineUpdate:
    setups: tuple[ExpiringArmedSetup, ...] = ()
    events: tuple[dict[str, object], ...] = ()


def reaction_interval(
    pivot: StructuralPivot,
    candle: Candle,
) -> ReactionInterval:
    """Map one pivot candle to its wick-to-body reaction interval."""
    if pivot.side == "HIGH":
        low = max(candle.open, candle.close)
        high = candle.high
    elif pivot.side == "LOW":
        low = candle.low
        high = min(candle.open, candle.close)
    else:
        raise ValueError("pivot side must be HIGH or LOW")
    return ReactionInterval(pivot=pivot, low=float(low), high=float(high))


def build_horizontal_reaction_shelves(
    *,
    symbol: str,
    candles: Sequence[Candle],
    pivots: Iterable[StructuralPivot],
    timeframe_minutes: int,
) -> list[HorizontalReactionShelf]:
    """Create one shelf from each pair of consecutive same-side DC pivots.

    Directional-change pivots alternate, so consecutive same-side pivots are
    separated by a completed opposite auction leg.  No fixed bar-spacing or
    price-distance tolerance is introduced.  A shelf exists only when the two
    wick-to-body intervals have literal price overlap.
    """
    latest: dict[str, StructuralPivot] = {}
    shelves: list[HorizontalReactionShelf] = []
    ordered = sorted(
        pivots,
        key=lambda item: (item.observed_time_ns, item.event_time_ns, item.side),
    )
    for pivot in ordered:
        if pivot.center_index < 0 or pivot.center_index >= len(candles):
            raise IndexError("pivot center outside candle sequence")
        prior = latest.get(pivot.side)
        if prior is not None:
            first = reaction_interval(prior, candles[prior.center_index])
            second = reaction_interval(pivot, candles[pivot.center_index])
            overlap_low = max(first.low, second.low)
            overlap_high = min(first.high, second.high)
            if overlap_low <= overlap_high:
                side = Side.LONG if pivot.side == "HIGH" else Side.SHORT
                shelves.append(
                    HorizontalReactionShelf(
                        shelf_id=(
                            f"SHELF:{symbol}:{timeframe_minutes}:"
                            f"{pivot.side}:{prior.event_time_ns}:{pivot.event_time_ns}"
                        ),
                        symbol=symbol,
                        side=side,
                        observed_time_ns=max(
                            prior.observed_time_ns,
                            pivot.observed_time_ns,
                        ),
                        timeframe_minutes=timeframe_minutes,
                        zone_low=float(overlap_low),
                        zone_high=float(overlap_high),
                        first=prior,
                        second=pivot,
                    ),
                )
        latest[pivot.side] = pivot
    return shelves


class HorizontalAcceptedBreakEngine:
    """One-symbol horizontal shelf -> accepted break -> first-retest options."""

    def __init__(
        self,
        symbol: str,
        shelves: Iterable[HorizontalReactionShelf],
        pivots: Iterable[StructuralPivot],
        config: StructuralAcceptedBreakConfig,
    ) -> None:
        self.symbol = symbol
        self.config = config
        self.pending_shelves = sorted(
            shelves,
            key=lambda item: (item.observed_time_ns, item.shelf_id),
        )
        self.pivots = sorted(
            pivots,
            key=lambda item: (item.observed_time_ns, item.event_time_ns),
        )
        self.shelf_cursor = 0
        self.active: dict[str, ShelfState] = {}
        self.candles: list[Candle] = []
        self.footprints: dict[str, FootprintRef] = {}
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}
        self.audit_rows: list[dict[str, object]] = []

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self) -> str:
        self.sequence += 1
        return f"ec16-structure-{self.symbol}-{self.sequence:08d}"

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

    def _activate(self, current: Candle) -> None:
        while (
            self.shelf_cursor < len(self.pending_shelves)
            and self.pending_shelves[self.shelf_cursor].observed_time_ns
            <= current.ts_open_ns
        ):
            shelf = self.pending_shelves[self.shelf_cursor]
            self.shelf_cursor += 1
            self.active[shelf.shelf_id] = ShelfState(shelf)
            self._count("shelves_activated")

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
        state: ShelfState,
        current: Candle,
    ) -> list[FootprintRef]:
        assert state.break_time_ns is not None
        side = state.shelf.side
        boundary = state.shelf.entry_boundary
        output = []
        for item in self.footprints.values():
            if (
                item.side is not side
                or not state.break_time_ns <= item.observed_time_ns <= current.ts_close_ns
                or not self._fresh(item, current)
            ):
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
        state: ShelfState,
        current: Candle,
    ) -> tuple[float, FootprintRef | None, str]:
        boundary = state.shelf.entry_boundary
        footprints = self._eligible_footprints(state=state, current=current)
        candidates: list[tuple[float, FootprintRef | None, str]] = [
            (boundary, None, "REACTION_SHELF"),
        ]
        candidates.extend((item.proximal, item, item.kind) for item in footprints)
        if state.shelf.side is Side.LONG:
            return max(candidates, key=lambda value: (value[0], value[2]))
        return min(candidates, key=lambda value: (value[0], value[2]))

    def _origin(
        self,
        *,
        state: ShelfState,
        current: Candle,
    ) -> StructuralPivot | None:
        assert state.break_time_ns is not None
        wanted = "LOW" if state.shelf.side is Side.LONG else "HIGH"
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
        state: ShelfState,
        current: Candle,
    ) -> ExpiringArmedSetup | None:
        shelf = state.shelf
        side = shelf.side
        origin = self._origin(state=state, current=current)
        entry, item, entry_kind = self._entry_surface(state=state, current=current)
        audit: dict[str, object] = {
            "shelf_id": shelf.shelf_id,
            "symbol": self.symbol,
            "side": side.name,
            "shelf_zone_low": shelf.zone_low,
            "shelf_zone_high": shelf.zone_high,
            "break_time_ns": state.break_time_ns,
            "acceptance_time_ns": current.ts_close_ns,
            "entry": entry,
            "entry_kind": entry_kind,
            "footprint_id": None if item is None else item.footprint_id,
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
        objective = self._first_objective(
            side=side,
            entry=entry,
            current=current,
        )
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
                f"STRUCTURAL_ACCEPTED_BREAK:{self.symbol}:{shelf.shelf_id}:"
                f"{state.break_time_ns}:{current.ts_close_ns}:{side.name}"
            ),
            symbol=self.symbol,
            family=(
                "STRUCTURAL_ACCEPTED_BREAK_CONTINUATION_FIRST_RETEST_"
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
            source_pool_id=shelf.shelf_id,
            zone_low=float(item.zone_low if item is not None else shelf.zone_low),
            zone_high=float(item.zone_high if item is not None else shelf.zone_high),
            formation_extreme=float(invalidation),
            body_ratio=(
                2.0 if item is not None and item.source_two_x_quality else 0.0
            ),
            previous_body=0.0,
            current_body=0.0,
            context_bias=(
                "ROLE_GRAPH_V16|OPTION=STRUCTURAL_ACCEPTED_BREAK_FIRST_RETEST"
                "|LOCATION=OVERLAPPING_WICK_BODY_REACTION_SHELF"
                "|ACCEPTANCE=SEPARATE_OUTSIDE_OPEN_AND_CLOSE"
                f"|ENTRY_KIND={entry_kind}"
                f"|ORIGIN_EVENT={origin.event_time_ns}"
                f"|ORIGIN_OBSERVED={origin.observed_time_ns}"
                f"|BREAK={state.break_time_ns}"
                f"|ACCEPT={current.ts_close_ns}"
                f"|FOOTPRINT={item.footprint_id if item is not None else 'NONE'}"
                "|SOURCE_STATUS=SOURCE_EXPLICIT_PLUS_NAMED_CASE_INFERENCE"
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
        return setup

    def _observe_state(
        self,
        state: ShelfState,
        current: Candle,
        index: int,
    ) -> StructuralEngineUpdate:
        shelf = state.shelf
        if state.phase is ShelfPhase.COMPLETED:
            return StructuralEngineUpdate()
        boundary = shelf.entry_boundary
        outside_close = (
            current.close > boundary
            if shelf.side is Side.LONG
            else current.close < boundary
        )
        inside_close = not outside_close

        if state.phase is ShelfPhase.WAIT_BREAK:
            if outside_close:
                state.phase = ShelfPhase.OUTSIDE
                state.break_index = index
                state.break_time_ns = current.ts_close_ns
                self._count("first_outside_closes")
                return StructuralEngineUpdate(
                    events=(
                        {
                            "shelf_id": shelf.shelf_id,
                            "event": "FIRST_OUTSIDE_CLOSE",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            return StructuralEngineUpdate()

        assert state.phase is ShelfPhase.OUTSIDE
        assert state.break_index is not None
        assert state.break_time_ns is not None
        if inside_close:
            state.phase = ShelfPhase.COMPLETED
            self._count("break_failed_before_acceptance")
            return StructuralEngineUpdate(
                events=(
                    {
                        "shelf_id": shelf.shelf_id,
                        "event": "FAILED_BREAK_BEFORE_ACCEPTANCE",
                        "time_ns": current.ts_close_ns,
                    },
                )
            )

        opened_outside = (
            current.open > boundary
            if shelf.side is Side.LONG
            else current.open < boundary
        )
        if index > state.break_index and opened_outside and outside_close:
            setup = self._build_setup(state=state, current=current)
            state.phase = ShelfPhase.COMPLETED
            self._count("accepted_breaks")
            return StructuralEngineUpdate(
                setups=(() if setup is None else (setup,)),
                events=(
                    {
                        "shelf_id": shelf.shelf_id,
                        "event": "ACCEPTED_BREAK",
                        "time_ns": current.ts_close_ns,
                        "setup_id": None if setup is None else setup.setup_id,
                    },
                ),
            )
        return StructuralEngineUpdate()

    def on_close(self, current: Candle, index: int) -> StructuralEngineUpdate:
        self._activate(current)
        setups: list[ExpiringArmedSetup] = []
        events: list[dict[str, object]] = []
        for shelf_id, state in list(self.active.items()):
            update = self._observe_state(state, current, index)
            setups.extend(update.setups)
            events.extend(update.events)
            if state.phase is ShelfPhase.COMPLETED:
                self.active.pop(shelf_id, None)
        self.candles.append(current)
        setups.sort(key=lambda item: (item.observed_time_ns, item.setup_id))
        return StructuralEngineUpdate(tuple(setups), tuple(events))


__all__ = [
    "HorizontalAcceptedBreakEngine",
    "HorizontalReactionShelf",
    "ReactionInterval",
    "ShelfPhase",
    "ShelfState",
    "StructuralAcceptedBreakConfig",
    "StructuralEngineUpdate",
    "build_horizontal_reaction_shelves",
    "reaction_interval",
]
