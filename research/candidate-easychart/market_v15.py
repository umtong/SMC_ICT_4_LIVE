"""Role-routed boundary options for candidate-easychart v15.

This engine keeps competing auction interpretations separate:

* an immediate fakeout or a source-shaped W/M trap can initiate a failed-break
  reversal;
* a sponsored opposite footprint may initiate the same reversal while price is
  still outside the boundary (the predictive mode seen in the case corpus);
* an outside body close followed by another outside open/close can initiate an
  accepted-break first-retest option.

The patterns are not scored.  Each emitted setup is a complete option with a
location, interaction, response, entry, invalidation and provisional structural
cap.  A later target router must replace the cap with the first still-active
opposing objective where one exists.

This remains a signal/state component.  NautilusTrader owns authoritative
orders, fills, accounting and portfolio state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Iterable, Sequence

from domain_v3 import ArmedSetup, Candle, Side, TargetMode
from market_v4 import StructuralPivot
from market_v5 import DirectionalChangePivotDetector
from market_v7 import ExpiringArmedSetup, SessionLiquidityRange
from role_graph_v15 import (
    Direction,
    EvidenceKind,
    EvidenceRole,
    PositioningContext,
    Scale,
    ScenarioFamily,
    evidence,
    resolve_option,
)
from source_footprints import SourceFVG, SourceOrderBlock


class BoundaryPhase(str, Enum):
    INSIDE = "INSIDE"
    OUTSIDE = "OUTSIDE"
    REBOUND_SEEN = "REBOUND_SEEN"
    SECOND_LEG_SEEN = "SECOND_LEG_SEEN"
    RECLAIMED = "RECLAIMED"
    PROVISIONAL_ACCEPTANCE = "PROVISIONAL_ACCEPTANCE"
    COMPLETED = "COMPLETED"


class AuctionState(str, Enum):
    ISOLATED_RAID = "ISOLATED_RAID"
    COORDINATED_REJECTION = "COORDINATED_REJECTION"
    COORDINATED_REPRICING = "COORDINATED_REPRICING"
    LOCAL_ONLY_OR_MIXED = "LOCAL_ONLY_OR_MIXED"
    INSUFFICIENT_PEERS = "INSUFFICIENT_PEERS"


@dataclass(frozen=True, slots=True)
class BoundaryEngineConfig:
    tick_size: float
    source_timeframe_minutes: int = 5
    response_timeframe_minutes: int = 15
    enable_immediate_fakeout: bool = True
    enable_wm_trap: bool = True
    enable_predictive_outside_footprint: bool = True
    enable_accepted_break_retest: bool = True
    continuation_cap_range_widths: float = 10.0
    breakout_origin_dc_atr_multiple: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.source_timeframe_minutes <= 0 or self.response_timeframe_minutes <= 0:
            raise ValueError("timeframes must be positive")
        if (
            not math.isfinite(self.continuation_cap_range_widths)
            or self.continuation_cap_range_widths <= 1.0
        ):
            raise ValueError("continuation cap must be a finite search cap > 1 range")
        if (
            not math.isfinite(self.breakout_origin_dc_atr_multiple)
            or self.breakout_origin_dc_atr_multiple <= 0.0
        ):
            raise ValueError("breakout origin DC multiple must be positive")


@dataclass(frozen=True, slots=True)
class FootprintRef:
    footprint_id: str
    kind: str
    side: Side
    observed_time_ns: int
    zone_low: float
    zone_high: float
    invalidation: float
    source_two_x_quality: bool
    timeframe_minutes: int

    @property
    def proximal(self) -> float:
        return self.zone_high if self.side is Side.LONG else self.zone_low


def footprint_ref(item: SourceOrderBlock | SourceFVG) -> FootprintRef:
    if isinstance(item, SourceOrderBlock):
        return FootprintRef(
            footprint_id=item.footprint_id,
            kind="ORDER_BLOCK",
            side=item.side,
            observed_time_ns=item.observed_time_ns,
            zone_low=item.zone_low,
            zone_high=item.zone_high,
            invalidation=item.invalidation,
            source_two_x_quality=item.source_two_x_quality,
            timeframe_minutes=item.timeframe_minutes,
        )
    if isinstance(item, SourceFVG):
        invalidation = (
            item.formation_low if item.side is Side.LONG else item.formation_high
        )
        return FootprintRef(
            footprint_id=item.footprint_id,
            kind="FVG",
            side=item.side,
            observed_time_ns=item.observed_time_ns,
            zone_low=item.zone_low,
            zone_high=item.zone_high,
            invalidation=invalidation,
            source_two_x_quality=item.source_two_x_quality,
            timeframe_minutes=item.timeframe_minutes,
        )
    raise TypeError(type(item))


@dataclass(slots=True)
class BoundaryState:
    liquidity_range: SessionLiquidityRange
    phase: BoundaryPhase = BoundaryPhase.INSIDE
    outside_side: Side | None = None
    outside_first_index: int | None = None
    outside_first_time_ns: int | None = None
    outside_first_open: float | None = None
    outside_first_close: float | None = None
    outside_first_low: float | None = None
    outside_first_high: float | None = None
    outside_extreme: float | None = None
    rebound_close: float | None = None
    rebound_index: int | None = None
    second_leg_index: int | None = None
    continuation_setup_id: str | None = None
    reversal_setup_id: str | None = None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class BoundaryEngineUpdate:
    setups: tuple[ExpiringArmedSetup, ...] = ()
    cancel_setup_ids: tuple[str, ...] = ()
    events: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class PeerBoundaryObservation:
    symbol: str
    side: Side
    range_low: float
    range_high: float
    excursion_low: float
    excursion_high: float
    close: float

    @property
    def width(self) -> float:
        return self.range_high - self.range_low

    @property
    def penetration(self) -> float:
        if self.side is Side.LONG:
            return max(0.0, (self.range_low - self.excursion_low) / self.width)
        return max(0.0, (self.excursion_high - self.range_high) / self.width)

    @property
    def swept(self) -> bool:
        return self.penetration > 0.0

    @property
    def reclaimed(self) -> bool:
        return self.close >= self.range_low if self.side is Side.LONG else self.close <= self.range_high

    @property
    def remains_outside(self) -> bool:
        return self.close < self.range_low if self.side is Side.LONG else self.close > self.range_high


@dataclass(frozen=True, slots=True)
class AuctionStateDecision:
    state: AuctionState
    candidate_symbol: str
    candidate_penetration: float
    swept_peers: tuple[str, ...]
    reclaimed_peers: tuple[str, ...]
    outside_peers: tuple[str, ...]
    non_swept_peers: tuple[str, ...]


def classify_auction_state(
    *,
    candidate: PeerBoundaryObservation,
    peers: Iterable[PeerBoundaryObservation],
    required_peer_count: int = 3,
    majority: int = 2,
) -> AuctionStateDecision:
    unique: dict[str, PeerBoundaryObservation] = {}
    for item in peers:
        if item.symbol == candidate.symbol or item.symbol in unique:
            continue
        if item.side is not candidate.side:
            raise ValueError("peer observations must use the candidate side")
        unique[item.symbol] = item
    if len(unique) != required_peer_count:
        return AuctionStateDecision(
            state=AuctionState.INSUFFICIENT_PEERS,
            candidate_symbol=candidate.symbol,
            candidate_penetration=candidate.penetration,
            swept_peers=(),
            reclaimed_peers=(),
            outside_peers=(),
            non_swept_peers=(),
        )
    swept = tuple(sorted(k for k, v in unique.items() if v.swept))
    reclaimed = tuple(sorted(k for k, v in unique.items() if v.swept and v.reclaimed))
    outside = tuple(sorted(k for k, v in unique.items() if v.swept and v.remains_outside))
    non_swept = tuple(sorted(k for k, v in unique.items() if not v.swept))
    all_items = [candidate, *unique.values()]
    deepest = max(item.penetration for item in all_items)
    candidate_deepest = math.isclose(
        candidate.penetration,
        deepest,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
    if len(non_swept) >= majority and candidate_deepest:
        state = AuctionState.ISOLATED_RAID
    elif len(reclaimed) >= majority:
        state = AuctionState.COORDINATED_REJECTION
    elif len(outside) >= majority:
        state = AuctionState.COORDINATED_REPRICING
    else:
        state = AuctionState.LOCAL_ONLY_OR_MIXED
    return AuctionStateDecision(
        state=state,
        candidate_symbol=candidate.symbol,
        candidate_penetration=candidate.penetration,
        swept_peers=swept,
        reclaimed_peers=reclaimed,
        outside_peers=outside,
        non_swept_peers=non_swept,
    )


class RoleRoutedBoundaryEngine:
    """Causal multi-range engine for one symbol."""

    def __init__(
        self,
        symbol: str,
        ranges: Iterable[SessionLiquidityRange],
        config: BoundaryEngineConfig,
    ) -> None:
        self.symbol = symbol
        self.config = config
        self.pending_ranges = sorted(
            ranges,
            key=lambda item: (item.trade_start_ns, item.trade_end_ns, item.range_id),
        )
        self.range_cursor = 0
        self.active: dict[str, BoundaryState] = {}
        self.candles: list[Candle] = []
        self.footprints: dict[str, FootprintRef] = {}
        self.breakout_origin_detector = DirectionalChangePivotDetector(
            timeframe_minutes=config.source_timeframe_minutes,
            atr_period=14,
            atr_multiple=config.breakout_origin_dc_atr_multiple,
        )
        self.latest_breakout_high: StructuralPivot | None = None
        self.latest_breakout_low: StructuralPivot | None = None
        # Preserve the causal pivot history.  A breakout/retest stop belongs to
        # the origin of the wave that produced the *first* outside close.  A
        # later micro pivot printed after the break cannot retroactively become
        # that origin merely because it is the latest pivot at acceptance.
        self.breakout_pivots: list[StructuralPivot] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"ec15-{self.symbol}-{prefix}-{self.sequence:08d}"

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
            if item.source_two_x_quality:
                self._count(f"footprints_{item.kind.lower()}_source_two_x")

    def _activate_and_expire(self, current: Candle) -> list[str]:
        cancellations: list[str] = []
        while (
            self.range_cursor < len(self.pending_ranges)
            and self.pending_ranges[self.range_cursor].trade_start_ns <= current.ts_open_ns
        ):
            item = self.pending_ranges[self.range_cursor]
            self.range_cursor += 1
            if item.trade_end_ns <= current.ts_open_ns:
                continue
            self.active[item.range_id] = BoundaryState(item)
            self._count("ranges_activated")
        for range_id, state in list(self.active.items()):
            if state.liquidity_range.trade_end_ns <= current.ts_open_ns:
                if state.continuation_setup_id is not None:
                    cancellations.append(state.continuation_setup_id)
                if state.reversal_setup_id is not None:
                    cancellations.append(state.reversal_setup_id)
                self.active.pop(range_id, None)
                self._count("ranges_expired")
        return cancellations

    def _bars_between(self, after_ns: int, before_open_ns: int) -> Sequence[Candle]:
        return [
            bar
            for bar in self.candles
            if bar.ts_open_ns >= after_ns and bar.ts_close_ns < before_open_ns
        ]

    def _fresh(self, item: FootprintRef, current: Candle) -> bool:
        for bar in self._bars_between(item.observed_time_ns, current.ts_open_ns):
            if bar.low <= item.zone_high and bar.high >= item.zone_low:
                return False
        return True

    def _eligible_footprints(
        self,
        *,
        state: BoundaryState,
        side: Side,
        current: Candle,
    ) -> list[FootprintRef]:
        start = state.outside_first_time_ns or current.ts_open_ns
        output = [
            item
            for item in self.footprints.values()
            if item.side is side
            and start <= item.observed_time_ns <= current.ts_close_ns
            and self._fresh(item, current)
        ]
        output.sort(
            key=lambda item: (
                item.observed_time_ns,
                -item.timeframe_minutes,
                item.footprint_id,
            )
        )
        return output

    @staticmethod
    def _boundary(liquidity_range: SessionLiquidityRange, side: Side) -> float:
        return liquidity_range.low if side is Side.LONG else liquidity_range.high

    @staticmethod
    def _opposite(liquidity_range: SessionLiquidityRange, side: Side) -> float:
        return liquidity_range.high if side is Side.LONG else liquidity_range.low

    @staticmethod
    def _continuation_boundary(
        liquidity_range: SessionLiquidityRange,
        side: Side,
    ) -> float:
        # A long continuation retests the broken upper boundary; a short
        # continuation retests the broken lower boundary.
        return liquidity_range.high if side is Side.LONG else liquidity_range.low

    def _choose_entry(
        self,
        *,
        state: BoundaryState,
        side: Side,
        current: Candle,
        predictive: bool,
    ) -> tuple[float, FootprintRef | None, str] | None:
        footprints = self._eligible_footprints(state=state, side=side, current=current)
        boundary = self._boundary(state.liquidity_range, side)
        if predictive:
            candidates = []
            for item in footprints:
                entry = item.proximal
                if side is Side.LONG and entry <= current.close:
                    candidates.append((entry, item, item.kind))
                elif side is Side.SHORT and entry >= current.close:
                    candidates.append((entry, item, item.kind))
            if not candidates:
                return None
            if side is Side.LONG:
                return max(candidates, key=lambda value: (value[0], value[1].observed_time_ns))
            return min(candidates, key=lambda value: (value[0], -value[1].observed_time_ns))

        candidates: list[tuple[float, FootprintRef | None, str]] = [
            (boundary, None, "RECLAIMED_BOUNDARY"),
        ]
        for item in footprints:
            entry = item.proximal
            if side is Side.LONG and entry <= current.close:
                candidates.append((entry, item, item.kind))
            elif side is Side.SHORT and entry >= current.close:
                candidates.append((entry, item, item.kind))
        # A retracement from the reclaim close reaches the nearest active
        # support/resistance first.  Overlapping observations are one cluster;
        # no count-based score is used.
        if side is Side.LONG:
            return max(candidates, key=lambda value: (value[0], value[2]))
        return min(candidates, key=lambda value: (value[0], value[2]))

    def _role_resolution(
        self,
        *,
        state: BoundaryState,
        side: Side,
        current: Candle,
        entry_kind: str,
        family: ScenarioFamily,
        predictive: bool,
    ):
        direction = Direction.LONG if side is Side.LONG else Direction.SHORT
        base = state.outside_first_time_ns or current.ts_open_ns
        role_items = [
            evidence(
                f"{state.liquidity_range.range_id}:location",
                kind=EvidenceKind.HORIZONTAL_RANGE,
                roles={EvidenceRole.LOCATION, EvidenceRole.OBJECTIVE},
                direction=direction,
                scale=Scale.CONTEXT,
                event_time_ns=state.liquidity_range.observed_time_ns,
                observed_time_ns=state.liquidity_range.observed_time_ns,
                causal_leg_id=state.liquidity_range.range_id,
            ),
            evidence(
                f"{state.liquidity_range.range_id}:interaction",
                kind=EvidenceKind.LIQUIDITY_SWEEP,
                roles={EvidenceRole.INTERACTION},
                direction=direction,
                scale=Scale.LOCAL,
                event_time_ns=base,
                observed_time_ns=base,
                causal_leg_id=state.liquidity_range.range_id,
            ),
            evidence(
                f"{state.liquidity_range.range_id}:response:{current.ts_close_ns}",
                kind=(
                    EvidenceKind.DISPLACEMENT
                    if predictive
                    else (
                        EvidenceKind.IMMEDIATE_FAKEOUT
                        if state.rebound_index is None
                        else EvidenceKind.WM_TRAP
                    )
                ),
                roles={EvidenceRole.RESPONSE},
                direction=direction,
                scale=Scale.LOCAL,
                event_time_ns=current.ts_close_ns,
                observed_time_ns=current.ts_close_ns,
                causal_leg_id=state.liquidity_range.range_id,
            ),
            evidence(
                f"{state.liquidity_range.range_id}:entry:{entry_kind}:{current.ts_close_ns}",
                kind=(
                    EvidenceKind.ORDER_BLOCK
                    if entry_kind == "ORDER_BLOCK"
                    else EvidenceKind.FVG
                    if entry_kind == "FVG"
                    else EvidenceKind.FIRST_RETEST
                ),
                roles={EvidenceRole.ENTRY, EvidenceRole.INVALIDATION},
                direction=direction,
                scale=Scale.EXECUTION,
                event_time_ns=current.ts_close_ns,
                observed_time_ns=current.ts_close_ns,
                causal_leg_id=state.liquidity_range.range_id,
            ),
        ]
        return resolve_option(
            family=family,
            direction=direction,
            evidence=role_items,
            context=PositioningContext(),
        )

    def _strict_immediate_response(
        self,
        *,
        state: BoundaryState,
        side: Side,
        current: Candle,
    ) -> tuple[bool, str]:
        """Source-shaped immediate response without a fitted threshold.

        The source describes an immediate Fake out primarily as a long sweep
        wick and also treats a sponsored OB/FVG at the interaction as response
        evidence.  A one-tick poke with an ordinary candle is therefore left
        unresolved; it is not silently promoted to a reversal.
        """
        body = abs(current.close - current.open)
        if side is Side.LONG:
            sweep_wick = min(current.open, current.close) - current.low
            opposite_wick = current.high - max(current.open, current.close)
        else:
            sweep_wick = current.high - max(current.open, current.close)
            opposite_wick = min(current.open, current.close) - current.low
        dominant_wick = sweep_wick > max(body, opposite_wick, self.config.tick_size)
        sponsored = bool(
            self._eligible_footprints(state=state, side=side, current=current)
        )
        if dominant_wick:
            return True, "DOMINANT_SWEEP_WICK"
        if sponsored:
            return True, "SPONSORED_FOOTPRINT_RESPONSE"
        return False, "NO_DOMINANT_WICK_OR_SPONSORED_FOOTPRINT"

    def _reversal_setup(
        self,
        *,
        state: BoundaryState,
        current: Candle,
        side: Side,
        interaction: str,
        predictive: bool,
    ) -> ExpiringArmedSetup | None:
        selected = self._choose_entry(
            state=state,
            side=side,
            current=current,
            predictive=predictive,
        )
        if selected is None:
            self._count("predictive_no_sponsored_footprint")
            return None
        entry, item, entry_kind = selected
        extreme = state.outside_extreme
        if extreme is None:
            extreme = current.low if side is Side.LONG else current.high
        if side is Side.LONG:
            invalidation = min(
                extreme,
                item.invalidation if item is not None else extreme,
            )
            stop = invalidation - self.config.tick_size
        else:
            invalidation = max(
                extreme,
                item.invalidation if item is not None else extreme,
            )
            stop = invalidation + self.config.tick_size
        target = self._opposite(state.liquidity_range, side)
        if side is Side.LONG:
            if not stop < entry < target or current.high >= target:
                self._count("reversal_invalid_or_target_consumed")
                return None
        else:
            if not target < entry < stop or current.low <= target:
                self._count("reversal_invalid_or_target_consumed")
                return None

        family = ScenarioFamily.FAILED_BREAK_REVERSAL
        resolution = self._role_resolution(
            state=state,
            side=side,
            current=current,
            entry_kind=entry_kind,
            family=family,
            predictive=predictive,
        )
        if not resolution.executable:
            self._count(f"role_rejected_{resolution.disposition}")
            return None

        setup_id = self._new_id("reversal")
        setup = ExpiringArmedSetup(
            setup_id=setup_id,
            causal_event_id=(
                f"ROLE_FAILED_BREAK:{self.symbol}:{state.liquidity_range.range_id}:"
                f"{state.outside_first_time_ns or current.ts_open_ns}:{interaction}:{side.name}"
            ),
            symbol=self.symbol,
            family=f"ROLE_FAILED_BREAK_{interaction}_{entry_kind}",
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=float(entry),
            stop=float(stop),
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=float(target),
            fixed_target_id=f"OPPOSITE_BOUNDARY:{state.liquidity_range.range_id}",
            source_pool_id=state.liquidity_range.range_id,
            zone_low=float(item.zone_low if item is not None else entry),
            zone_high=float(item.zone_high if item is not None else entry),
            formation_extreme=float(invalidation),
            body_ratio=2.0 if item is not None and item.source_two_x_quality else 0.0,
            previous_body=0.0,
            current_body=0.0,
            context_bias=(
                f"ROLE_GRAPH_V15|OPTION=FAILED_BREAK_REVERSAL"
                f"|MODE={'PREDICTIVE' if predictive else 'CONFIRMED'}"
                f"|ENTRY_KIND={entry_kind}"
                f"|FOOTPRINT={item.footprint_id if item is not None else 'NONE'}"
                f"|RANGE={state.liquidity_range.reference_family}"
                f"|WINDOW={state.liquidity_range.trade_window}"
            ),
            source_timeframe_minutes=self.config.source_timeframe_minutes,
            valid_until_ns=state.liquidity_range.trade_end_ns,
        )
        if setup.executable(
            target,
            target_id=setup.fixed_target_id,
            min_gross_rr=1.0,
        ) is None:
            self._count("reversal_gross_rr_lt_1")
            return None
        state.reversal_setup_id = setup_id
        self._count(f"setups_{interaction.lower()}")
        return setup

    def _prebreak_wave_origin(
        self,
        *,
        state: BoundaryState,
        side: Side,
        observed_time_ns: int,
    ) -> StructuralPivot | None:
        """Return the latest opposite pivot whose *event* predates the break.

        Directional-change confirmation may arrive after the first outside
        close, but the wick extreme itself must already belong to the wave that
        produced that break.  This separates event time from observed time and
        prevents a post-break pullback pivot from manufacturing a tight stop.
        """
        break_time_ns = state.outside_first_time_ns
        if break_time_ns is None:
            return None
        wanted = "LOW" if side is Side.LONG else "HIGH"
        eligible = [
            pivot
            for pivot in self.breakout_pivots
            if pivot.side == wanted
            and pivot.event_time_ns < break_time_ns
            and pivot.observed_time_ns <= observed_time_ns
        ]
        return max(eligible, default=None, key=lambda pivot: pivot.event_time_ns)

    def _continuation_setup(
        self,
        *,
        state: BoundaryState,
        current: Candle,
        side: Side,
    ) -> ExpiringArmedSetup | None:
        liquidity_range = state.liquidity_range
        entry = self._continuation_boundary(liquidity_range, side)
        # The source-defined invalidation for a break/retest is the origin of
        # the wave that produced the first outside close.  Never substitute a
        # later micro pivot that happened to be latest at acceptance.
        origin = self._prebreak_wave_origin(
            state=state,
            side=side,
            observed_time_ns=current.ts_close_ns,
        )
        if side is Side.LONG:
            if origin is None or origin.level >= entry:
                self._count("continuation_missing_prebreak_wave_origin")
                self._count("continuation_missing_breakout_wave_origin")
                return None
            stop = origin.level - self.config.tick_size
            target = entry + liquidity_range.width * self.config.continuation_cap_range_widths
            if not stop < entry < target:
                return None
        else:
            if origin is None or origin.level <= entry:
                self._count("continuation_missing_prebreak_wave_origin")
                self._count("continuation_missing_breakout_wave_origin")
                return None
            stop = origin.level + self.config.tick_size
            target = max(
                self.config.tick_size,
                entry - liquidity_range.width * self.config.continuation_cap_range_widths,
            )
            if not target < entry < stop:
                return None

        direction = Direction.LONG if side is Side.LONG else Direction.SHORT
        role_items = [
            evidence(
                f"{liquidity_range.range_id}:location",
                kind=EvidenceKind.HORIZONTAL_RANGE,
                roles={EvidenceRole.LOCATION},
                direction=direction,
                scale=Scale.CONTEXT,
                event_time_ns=liquidity_range.observed_time_ns,
            ),
            evidence(
                f"{liquidity_range.range_id}:acceptance",
                kind=EvidenceKind.ACCEPTED_BREAK,
                roles={EvidenceRole.INTERACTION, EvidenceRole.RESPONSE},
                direction=direction,
                scale=Scale.LOCAL,
                event_time_ns=current.ts_close_ns,
            ),
            evidence(
                f"{liquidity_range.range_id}:retest",
                kind=EvidenceKind.FIRST_RETEST,
                roles={EvidenceRole.ENTRY, EvidenceRole.INVALIDATION},
                direction=direction,
                scale=Scale.EXECUTION,
                event_time_ns=current.ts_close_ns,
            ),
            evidence(
                f"{liquidity_range.range_id}:external-objective-search",
                kind=EvidenceKind.OPPOSING_STRUCTURE,
                roles={EvidenceRole.OBJECTIVE},
                direction=direction,
                scale=Scale.CONTEXT,
                event_time_ns=liquidity_range.observed_time_ns,
            ),
        ]
        resolution = resolve_option(
            family=ScenarioFamily.ACCEPTED_BREAK_FIRST_RETEST,
            direction=direction,
            evidence=role_items,
            context=PositioningContext(),
        )
        if not resolution.executable:
            self._count(f"continuation_role_rejected_{resolution.disposition}")
            return None

        setup_id = self._new_id("continuation")
        setup = ExpiringArmedSetup(
            setup_id=setup_id,
            causal_event_id=(
                f"ROLE_ACCEPTED_BREAK:{self.symbol}:{liquidity_range.range_id}:"
                f"{state.outside_first_time_ns}:{side.name}"
            ),
            symbol=self.symbol,
            family="ROLE_ACCEPTED_BREAK_FIRST_RETEST_NEEDS_ACTIVE_OBJECTIVE",
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=float(entry),
            stop=float(stop),
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=float(target),
            fixed_target_id=f"SEARCH_CAP_ONLY:{liquidity_range.range_id}",
            source_pool_id=liquidity_range.range_id,
            zone_low=float(entry),
            zone_high=float(entry),
            formation_extreme=float(origin.level),
            body_ratio=0.0,
            previous_body=0.0,
            current_body=0.0,
            context_bias=(
                f"ROLE_GRAPH_V15|OPTION=ACCEPTED_BREAK_FIRST_RETEST"
                f"|TARGET_CAP_NOT_OBJECTIVE"
                f"|BREAKOUT_WAVE_ORIGIN={origin.level:.12g}"
                f"|BREAKOUT_WAVE_ORIGIN_EVENT={origin.event_time_ns}"
                f"|BREAKOUT_WAVE_ORIGIN_OBSERVED={origin.observed_time_ns}"
                f"|BREAK_TIME={state.outside_first_time_ns}"
                f"|RANGE={liquidity_range.reference_family}"
                f"|WINDOW={liquidity_range.trade_window}"
            ),
            source_timeframe_minutes=self.config.source_timeframe_minutes,
            valid_until_ns=liquidity_range.trade_end_ns,
        )
        state.continuation_setup_id = setup_id
        self._count("setups_accepted_break")
        return setup

    def _outside_values(
        self,
        state: BoundaryState,
        current: Candle,
        side: Side,
    ) -> tuple[bool, bool, bool]:
        boundary = self._boundary(state.liquidity_range, side)
        if side is Side.LONG:
            crossed = current.low < boundary
            closed_outside = current.close < boundary
            closed_inside = current.close >= boundary
        else:
            crossed = current.high > boundary
            closed_outside = current.close > boundary
            closed_inside = current.close <= boundary
        return crossed, closed_outside, closed_inside

    def _start_outside(
        self,
        state: BoundaryState,
        current: Candle,
        index: int,
        side: Side,
    ) -> None:
        state.phase = BoundaryPhase.OUTSIDE
        state.outside_side = side
        state.outside_first_index = index
        state.outside_first_time_ns = current.ts_close_ns
        state.outside_first_open = current.open
        state.outside_first_close = current.close
        state.outside_first_low = current.low
        state.outside_first_high = current.high
        state.outside_extreme = current.low if side is Side.LONG else current.high
        self._count("outside_closes")

    def _update_extreme(self, state: BoundaryState, current: Candle) -> None:
        assert state.outside_side is not None
        assert state.outside_extreme is not None
        if state.outside_side is Side.LONG:
            state.outside_extreme = min(state.outside_extreme, current.low)
        else:
            state.outside_extreme = max(state.outside_extreme, current.high)

    def _observe_state(
        self,
        state: BoundaryState,
        current: Candle,
        index: int,
    ) -> BoundaryEngineUpdate:
        if state.completed:
            return BoundaryEngineUpdate()
        liquidity_range = state.liquidity_range
        if not (
            liquidity_range.trade_start_ns <= current.ts_open_ns
            and current.ts_open_ns < liquidity_range.trade_end_ns
        ):
            return BoundaryEngineUpdate()

        lower_cross = current.low < liquidity_range.low
        upper_cross = current.high > liquidity_range.high
        if lower_cross and upper_cross and state.outside_side is None:
            state.completed = True
            self._count("same_bar_two_sided_unresolved")
            return BoundaryEngineUpdate(
                events=(
                    {
                        "range_id": liquidity_range.range_id,
                        "time_ns": current.ts_close_ns,
                        "event": "UNRESOLVED_TWO_SIDED_INTERACTION",
                    },
                ),
            )

        setups: list[ExpiringArmedSetup] = []
        cancellations: list[str] = []
        events: list[dict[str, object]] = []

        if state.outside_side is None:
            if lower_cross:
                if current.close >= liquidity_range.low and self.config.enable_immediate_fakeout:
                    state.outside_side = Side.LONG
                    # The wick sweep/reclaim is known only when this candle
                    # closes; do not backdate the interaction to its open.
                    state.outside_first_time_ns = current.ts_close_ns
                    state.outside_extreme = current.low
                    strict, response_kind = self._strict_immediate_response(
                        state=state, side=Side.LONG, current=current
                    )
                    if not strict:
                        state.completed = True
                        self._count("immediate_reclaim_unresolved_no_source_response")
                        events.append({
                            "range_id": liquidity_range.range_id,
                            "time_ns": current.ts_close_ns,
                            "event": "UNRESOLVED_IMMEDIATE_RECLAIM",
                            "reason": response_kind,
                        })
                        return BoundaryEngineUpdate((), (), tuple(events))
                    setup = self._reversal_setup(
                        state=state,
                        current=current,
                        side=Side.LONG,
                        interaction=f"IMMEDIATE_FAKEOUT_{response_kind}",
                        predictive=False,
                    )
                    if setup is not None:
                        setups.append(setup)
                    state.completed = True
                    return BoundaryEngineUpdate(tuple(setups), (), tuple(events))
                if current.close < liquidity_range.low:
                    self._start_outside(state, current, index, Side.LONG)
            elif upper_cross:
                if current.close <= liquidity_range.high and self.config.enable_immediate_fakeout:
                    state.outside_side = Side.SHORT
                    state.outside_first_time_ns = current.ts_close_ns
                    state.outside_extreme = current.high
                    strict, response_kind = self._strict_immediate_response(
                        state=state, side=Side.SHORT, current=current
                    )
                    if not strict:
                        state.completed = True
                        self._count("immediate_reclaim_unresolved_no_source_response")
                        events.append({
                            "range_id": liquidity_range.range_id,
                            "time_ns": current.ts_close_ns,
                            "event": "UNRESOLVED_IMMEDIATE_RECLAIM",
                            "reason": response_kind,
                        })
                        return BoundaryEngineUpdate((), (), tuple(events))
                    setup = self._reversal_setup(
                        state=state,
                        current=current,
                        side=Side.SHORT,
                        interaction=f"IMMEDIATE_FAKEOUT_{response_kind}",
                        predictive=False,
                    )
                    if setup is not None:
                        setups.append(setup)
                    state.completed = True
                    return BoundaryEngineUpdate(tuple(setups), (), tuple(events))
                if current.close > liquidity_range.high:
                    self._start_outside(state, current, index, Side.SHORT)
            return BoundaryEngineUpdate(tuple(setups), tuple(cancellations), tuple(events))

        side = state.outside_side
        prior_extreme = state.outside_extreme
        assert prior_extreme is not None
        self._update_extreme(state, current)
        boundary = self._boundary(liquidity_range, side)
        closed_inside = current.close >= boundary if side is Side.LONG else current.close <= boundary
        closed_outside = not closed_inside

        if (
            self.config.enable_predictive_outside_footprint
            and state.reversal_setup_id is None
            and closed_outside
        ):
            predictive = self._reversal_setup(
                state=state,
                current=current,
                side=side,
                interaction="PREDICTIVE_OUTSIDE_FOOTPRINT",
                predictive=True,
            )
            if predictive is not None:
                setups.append(predictive)
                if state.continuation_setup_id is not None:
                    cancellations.append(state.continuation_setup_id)
                # Do not complete the range.  A later reclaim confirms the
                # continuation, while the predictive order remains governed by
                # its structural stop and window.
                events.append(
                    {
                        "range_id": liquidity_range.range_id,
                        "time_ns": current.ts_close_ns,
                        "event": "PREDICTIVE_REVERSAL_ARMED",
                        "setup_id": predictive.setup_id,
                    },
                )

        # Source-shaped W/M state.  A rebound must be followed by a distinct
        # second leg before a later reclaim.  The reclaim bar cannot invent its
        # own second leg from unknown intrabar ordering.
        if state.phase in {BoundaryPhase.OUTSIDE, BoundaryPhase.PROVISIONAL_ACCEPTANCE}:
            if side is Side.LONG:
                rebound = (
                    index > int(state.outside_first_index if state.outside_first_index is not None else index)
                    and current.close > float(state.outside_first_close if state.outside_first_close is not None else current.close)
                    and current.low > float(prior_extreme)
                )
            else:
                rebound = (
                    index > int(state.outside_first_index if state.outside_first_index is not None else index)
                    and current.close < float(state.outside_first_close if state.outside_first_close is not None else current.close)
                    and current.high < float(prior_extreme)
                )
            if rebound and closed_outside:
                state.phase = BoundaryPhase.REBOUND_SEEN
                state.rebound_close = current.close
                state.rebound_index = index
                self._count("wm_rebound_seen")

        elif state.phase is BoundaryPhase.REBOUND_SEEN:
            assert state.rebound_close is not None
            if side is Side.LONG:
                second_leg = (
                    index > int(state.rebound_index if state.rebound_index is not None else index)
                    and current.low < float(prior_extreme)
                    and current.close < state.rebound_close
                    and closed_outside
                )
            else:
                second_leg = (
                    index > int(state.rebound_index if state.rebound_index is not None else index)
                    and current.high > float(prior_extreme)
                    and current.close > state.rebound_close
                    and closed_outside
                )
            if second_leg:
                state.phase = BoundaryPhase.SECOND_LEG_SEEN
                state.second_leg_index = index
                self._count("wm_second_leg_seen")

        if closed_inside:
            if (
                self.config.enable_wm_trap
                and state.phase is BoundaryPhase.SECOND_LEG_SEEN
                and index > int(state.second_leg_index if state.second_leg_index is not None else index)
                and state.reversal_setup_id is None
            ):
                setup = self._reversal_setup(
                    state=state,
                    current=current,
                    side=side,
                    interaction="WM_TRAP_RECLAIM",
                    predictive=False,
                )
                if setup is not None:
                    setups.append(setup)
                    if state.continuation_setup_id is not None:
                        cancellations.append(state.continuation_setup_id)
                    state.completed = True
                    self._count("wm_trap_confirmed")
            elif state.reversal_setup_id is not None:
                if state.continuation_setup_id is not None:
                    cancellations.append(state.continuation_setup_id)
                state.completed = True
                self._count("predictive_reversal_later_reclaimed")
            else:
                # Delayed reclaim without the source-shaped W/M path remains
                # unresolved rather than being relabelled a trap.
                state.completed = True
                self._count("delayed_reclaim_not_wm_unresolved")
            return BoundaryEngineUpdate(tuple(setups), tuple(cancellations), tuple(events))

        # Provisional accepted break: the first outside close is followed by a
        # distinct candle that opens and closes outside.  It may still later
        # become a trap; therefore both interpretations remain explicit.
        if (
            self.config.enable_accepted_break_retest
            and state.continuation_setup_id is None
            and state.reversal_setup_id is None
            and state.outside_first_index is not None
            and index > state.outside_first_index
        ):
            opened_outside = current.open < boundary if side is Side.LONG else current.open > boundary
            if opened_outside and closed_outside:
                continuation_side = Side.SHORT if side is Side.LONG else Side.LONG
                setup = self._continuation_setup(
                    state=state,
                    current=current,
                    side=continuation_side,
                )
                if setup is not None:
                    setups.append(setup)
                    # Acceptance is a competing option, not the parent state:
                    # preserve OUTSIDE/REBOUND/SECOND_LEG so a later source-
                    # shaped trap can still be recognized and cancel it.
                    self._count("provisional_acceptance")
                    events.append(
                        {
                            "range_id": liquidity_range.range_id,
                            "time_ns": current.ts_close_ns,
                            "event": "PROVISIONAL_ACCEPTED_BREAK_ARMED",
                            "setup_id": setup.setup_id,
                        },
                    )

        return BoundaryEngineUpdate(tuple(setups), tuple(cancellations), tuple(events))

    def on_close(self, current: Candle, index: int) -> BoundaryEngineUpdate:
        self.candles.append(current)
        pivot = self.breakout_origin_detector.on_candle(current, index)
        if pivot is not None:
            self.breakout_pivots.append(pivot)
            self.breakout_pivots = self.breakout_pivots[-256:]
            if pivot.side == "HIGH":
                self.latest_breakout_high = pivot
            else:
                self.latest_breakout_low = pivot
            self._count(f"breakout_origin_pivots_{pivot.side.lower()}")
        cancellations = self._activate_and_expire(current)
        setups: list[ExpiringArmedSetup] = []
        events: list[dict[str, object]] = []
        for range_id, state in list(self.active.items()):
            update = self._observe_state(state, current, index)
            setups.extend(update.setups)
            cancellations.extend(update.cancel_setup_ids)
            events.extend(update.events)
            if state.completed:
                self.active.pop(range_id, None)
        # deterministic ordering and duplicate cancellation suppression
        setups.sort(key=lambda item: (item.observed_time_ns, item.setup_id))
        cancellations = list(dict.fromkeys(cancellations))
        return BoundaryEngineUpdate(
            setups=tuple(setups),
            cancel_setup_ids=tuple(cancellations),
            events=tuple(events),
        )


__all__ = [
    "AuctionState",
    "AuctionStateDecision",
    "BoundaryEngineConfig",
    "BoundaryEngineUpdate",
    "BoundaryPhase",
    "BoundaryState",
    "FootprintRef",
    "PeerBoundaryObservation",
    "RoleRoutedBoundaryEngine",
    "classify_auction_state",
    "footprint_ref",
]
