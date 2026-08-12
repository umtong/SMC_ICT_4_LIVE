"""Set-membership wick trendlines and first role-flip retests for EasyChart v18.

The source is explicit that trendlines use meaningful wick pivots, provide
market direction, and become tradeable after a strong break and first retest.
It is also explicit that angle and spacing matter while giving no numeric angle
or bar-distance rule.  A fixed tolerance or fitted slope threshold would
therefore replace the source rather than automate it.

This module uses two reusable ideas instead:

* directional-change pivots represent completed auction legs in intrinsic time,
  replacing an arbitrary fixed bar-spacing rule;
* each wick pivot contributes a wick-to-body reaction interval.  The unknown
  human-drawn line is the set of all straight lines passing through every
  interval.  A descending resistance line is accepted only when every feasible
  slope is negative; an ascending support line only when every feasible slope
  is positive.  No angle cutoff or price tolerance is added.

The traded case is deliberately narrow and source-shaped:

1. a pre-existing feasible descending/ascending wick trendline;
2. a body close beyond every feasible line;
3. a distinct candle opening and closing beyond every feasible line;
4. the first subsequent retest of the projected line band;
5. a same-leg EasyChart order block overlapping that retest;
6. full invalidation beyond both breakout-wave origin and OB formation extreme;
7. the first still-active opposing directional-change pivot as one target.

A first retest without the required OB remains unresolved; it is not silently
relabelled as a later retest.  A pending order is cancelled once the moving line
band and fixed OB zone no longer overlap.  This module remains a signal/state
component; authoritative execution belongs to NautilusTrader.
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


class TrendlinePhase(str, Enum):
    WAIT_BREAK = "WAIT_BREAK"
    OUTSIDE = "OUTSIDE"
    ACCEPTED_WAIT_FIRST_RETEST = "ACCEPTED_WAIT_FIRST_RETEST"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class FeasibleTrendlineVersion:
    line_id: str
    version_id: str
    version: int
    supersedes_version_ids: tuple[str, ...]
    symbol: str
    anchor_side: str
    trade_side: Side
    observed_time_ns: int
    timeframe_minutes: int
    anchors: tuple[ReactionInterval, ...]
    slope_low_per_ns: float
    slope_high_per_ns: float

    def __post_init__(self) -> None:
        if not self.line_id or not self.version_id or not self.symbol:
            raise ValueError("trendline identifiers must be non-empty")
        if self.version < 1 or len(self.anchors) < 2:
            raise ValueError("trendline requires a positive version and two anchors")
        if self.anchor_side not in {"HIGH", "LOW"}:
            raise ValueError("anchor side must be HIGH or LOW")
        if any(anchor.pivot.side != self.anchor_side for anchor in self.anchors):
            raise ValueError("trendline anchors must share one side")
        if not all(
            math.isfinite(value)
            for value in (self.slope_low_per_ns, self.slope_high_per_ns)
        ):
            raise ValueError("trendline slope interval must be finite")
        if self.slope_high_per_ns < self.slope_low_per_ns:
            raise ValueError("invalid feasible slope interval")
        if self.anchor_side == "HIGH":
            if self.trade_side is not Side.LONG or not self.slope_high_per_ns < 0.0:
                raise ValueError("HIGH trendline must be unambiguously descending resistance")
        else:
            if self.trade_side is not Side.SHORT or not self.slope_low_per_ns > 0.0:
                raise ValueError("LOW trendline must be unambiguously ascending support")
        if self.observed_time_ns < max(
            anchor.pivot.observed_time_ns for anchor in self.anchors
        ):
            raise ValueError("trendline observed before an anchor")

    @property
    def base_time_ns(self) -> int:
        return self.anchors[0].pivot.event_time_ns

    @property
    def anchor_count(self) -> int:
        return len(self.anchors)

    def _intercept_bounds(self, slope: float) -> tuple[float, float] | None:
        base = self.base_time_ns
        low = max(
            anchor.low - slope * (anchor.pivot.event_time_ns - base)
            for anchor in self.anchors
        )
        high = min(
            anchor.high - slope * (anchor.pivot.event_time_ns - base)
            for anchor in self.anchors
        )
        if high + 1e-12 < low:
            return None
        return float(low), float(high)

    def price_band(self, time_ns: int) -> tuple[float, float]:
        """Return the exact min/max price over the feasible line set."""
        base = self.base_time_ns
        x = time_ns - base
        slopes = {self.slope_low_per_ns, self.slope_high_per_ns}
        anchors = self.anchors

        # The upper envelope is max_m min_i(high_i + m * (x-x_i));
        # the lower envelope is min_m max_i(low_i + m * (x-x_i)).
        # Both piecewise-linear optima occur at a bound or pair intersection.
        for field in ("high", "low"):
            for i, first in enumerate(anchors):
                first_value = getattr(first, field)
                first_dx = x - (first.pivot.event_time_ns - base)
                for second in anchors[i + 1 :]:
                    second_value = getattr(second, field)
                    second_dx = x - (second.pivot.event_time_ns - base)
                    denominator = first_dx - second_dx
                    if denominator == 0:
                        continue
                    candidate = (second_value - first_value) / denominator
                    if (
                        self.slope_low_per_ns - 1e-18
                        <= candidate
                        <= self.slope_high_per_ns + 1e-18
                    ):
                        slopes.add(float(candidate))

        lower_candidates: list[float] = []
        upper_candidates: list[float] = []
        for slope in slopes:
            bounds = self._intercept_bounds(slope)
            if bounds is None:
                continue
            lower_candidates.append(bounds[0] + slope * x)
            upper_candidates.append(bounds[1] + slope * x)
        if not lower_candidates or not upper_candidates:
            raise RuntimeError("feasible trendline unexpectedly has no price band")
        return float(min(lower_candidates)), float(max(upper_candidates))


@dataclass(slots=True)
class TrendlineState:
    line: FeasibleTrendlineVersion
    phase: TrendlinePhase = TrendlinePhase.WAIT_BREAK
    break_index: int | None = None
    break_time_ns: int | None = None
    acceptance_index: int | None = None
    acceptance_time_ns: int | None = None
    setup_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrendlineRoleFlipConfig:
    tick_size: float
    signal_timeframe_minutes: int = 5
    valid_until_ns: int = 2**63 - 1

    def __post_init__(self) -> None:
        if not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("tick size must be positive")
        if self.signal_timeframe_minutes <= 0 or self.valid_until_ns <= 0:
            raise ValueError("time and validity must be positive")


@dataclass(frozen=True, slots=True)
class TrendlineEngineUpdate:
    setups: tuple[ExpiringArmedSetup, ...] = ()
    cancel_setup_ids: tuple[str, ...] = ()
    events: tuple[dict[str, object], ...] = ()


@dataclass(slots=True)
class _LineTrack:
    line_id: str
    version: int
    version_id: str
    observed_time_ns: int
    anchor_side: str
    anchors: list[ReactionInterval]
    slope_low: float
    slope_high: float
    active: bool = True


@dataclass(frozen=True, slots=True)
class _ObservedReaction:
    interval: ReactionInterval
    observed_time_ns: int


def feasible_slope_interval(
    anchors: Sequence[ReactionInterval],
) -> tuple[float, float] | None:
    if len(anchors) < 2:
        return None
    ordered = sorted(anchors, key=lambda item: item.pivot.event_time_ns)
    low = -math.inf
    high = math.inf
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            elapsed = second.pivot.event_time_ns - first.pivot.event_time_ns
            if elapsed <= 0:
                return None
            pair_low = (second.low - first.high) / elapsed
            pair_high = (second.high - first.low) / elapsed
            low = max(low, pair_low)
            high = min(high, pair_high)
            if high + 1e-18 < low:
                return None
    return float(low), float(high)


def _line_direction(
    anchor_side: str,
    slopes: tuple[float, float],
) -> Side | None:
    low, high = slopes
    if anchor_side == "HIGH" and high < 0.0:
        return Side.LONG
    if anchor_side == "LOW" and low > 0.0:
        return Side.SHORT
    return None


def _make_line_version(
    *,
    line_id: str,
    version_id: str,
    version: int,
    supersedes: tuple[str, ...],
    symbol: str,
    anchor_side: str,
    observed_time_ns: int,
    timeframe_minutes: int,
    anchors: Sequence[ReactionInterval],
) -> FeasibleTrendlineVersion | None:
    slopes = feasible_slope_interval(anchors)
    if slopes is None:
        return None
    trade_side = _line_direction(anchor_side, slopes)
    if trade_side is None:
        return None
    return FeasibleTrendlineVersion(
        line_id=line_id,
        version_id=version_id,
        version=version,
        supersedes_version_ids=supersedes,
        symbol=symbol,
        anchor_side=anchor_side,
        trade_side=trade_side,
        observed_time_ns=observed_time_ns,
        timeframe_minutes=timeframe_minutes,
        anchors=tuple(sorted(anchors, key=lambda item: item.pivot.event_time_ns)),
        slope_low_per_ns=slopes[0],
        slope_high_per_ns=slopes[1],
    )


def _accepted_beyond(
    line: FeasibleTrendlineVersion,
    *,
    time_ns: int,
    price: float,
) -> bool:
    low, high = line.price_band(time_ns)
    return price > high if line.trade_side is Side.LONG else price < low


def build_feasible_trendlines(
    *,
    symbol: str,
    candles: Sequence[Candle],
    pivots: Iterable[StructuralPivot],
    timeframe_minutes: int,
) -> list[FeasibleTrendlineVersion]:
    """Build causal, versioned set-membership trendlines."""
    observed: dict[int, list[StructuralPivot]] = {}
    for pivot in sorted(
        pivots,
        key=lambda item: (item.observed_time_ns, item.event_time_ns, item.side),
    ):
        if pivot.center_index < 0 or pivot.center_index >= len(candles):
            raise IndexError("pivot center outside candle sequence")
        observed.setdefault(pivot.observed_time_ns, []).append(pivot)

    tracks: list[_LineTrack] = []
    last_reaction: dict[str, _ObservedReaction] = {}
    versions: list[FeasibleTrendlineVersion] = []
    sequence = 0

    reset_time_by_side: dict[str, int] = {"HIGH": -1, "LOW": -1}

    def pair_survived(
        line: FeasibleTrendlineVersion,
        *,
        formed_event_ns: int,
        observed_time_ns: int,
    ) -> bool:
        """Reject a line that is already broken when its second anchor confirms.

        The line does not exist before the second anchor event.  From that event
        until causal observation, however, every completed close is historical
        information.  A line born after price has already accepted beyond all
        feasible members would be a retrospective drawing, not a tradeable
        pre-existing structure.
        """
        for bar in candles:
            if not (
                formed_event_ns < bar.ts_close_ns <= observed_time_ns
            ):
                continue
            if _accepted_beyond(line, time_ns=bar.ts_close_ns, price=bar.close):
                return False
        return True

    for current in candles:
        for track in tracks:
            if not track.active or track.observed_time_ns > current.ts_open_ns:
                continue
            line = _make_line_version(
                line_id=track.line_id,
                version_id=track.version_id,
                version=track.version,
                supersedes=(),
                symbol=symbol,
                anchor_side=track.anchor_side,
                observed_time_ns=track.observed_time_ns,
                timeframe_minutes=timeframe_minutes,
                anchors=track.anchors,
            )
            assert line is not None
            if _accepted_beyond(line, time_ns=current.ts_close_ns, price=current.close):
                track.active = False
                reset_time_by_side[track.anchor_side] = max(
                    reset_time_by_side[track.anchor_side],
                    current.ts_close_ns,
                )

        for pivot in sorted(
            observed.get(current.ts_close_ns, ()),
            key=lambda item: (item.event_time_ns, item.side),
        ):
            interval = reaction_interval(pivot, candles[pivot.center_index])
            compatible: list[tuple[_LineTrack, FeasibleTrendlineVersion]] = []
            for track in tracks:
                if not track.active or track.anchor_side != pivot.side:
                    continue
                anchors = [*track.anchors, interval]
                candidate = _make_line_version(
                    line_id=track.line_id,
                    version_id="candidate",
                    version=track.version + 1,
                    supersedes=(track.version_id,),
                    symbol=symbol,
                    anchor_side=pivot.side,
                    observed_time_ns=pivot.observed_time_ns,
                    timeframe_minutes=timeframe_minutes,
                    anchors=anchors,
                )
                if candidate is not None:
                    compatible.append((track, candidate))

            if compatible:
                track, candidate = max(
                    compatible,
                    key=lambda item: (
                        item[0].observed_time_ns,
                        item[0].version,
                        item[0].line_id,
                    ),
                )
                track.active = False
                version = track.version + 1
                version_id = (
                    f"{track.line_id}:V{version}:"
                    f"{pivot.event_time_ns}:{pivot.observed_time_ns}"
                )
                item = _make_line_version(
                    line_id=track.line_id,
                    version_id=version_id,
                    version=version,
                    supersedes=(track.version_id,),
                    symbol=symbol,
                    anchor_side=pivot.side,
                    observed_time_ns=pivot.observed_time_ns,
                    timeframe_minutes=timeframe_minutes,
                    anchors=[*track.anchors, interval],
                )
                assert item is not None
                tracks.append(
                    _LineTrack(
                        line_id=item.line_id,
                        version=item.version,
                        version_id=item.version_id,
                        observed_time_ns=item.observed_time_ns,
                        anchor_side=item.anchor_side,
                        anchors=list(item.anchors),
                        slope_low=item.slope_low_per_ns,
                        slope_high=item.slope_high_per_ns,
                    )
                )
                versions.append(item)
            else:
                prior = last_reaction.get(pivot.side)
                if (
                    prior is not None
                    and prior.observed_time_ns > reset_time_by_side[pivot.side]
                ):
                    sequence += 1
                    line_id = (
                        f"FEASIBLE_TRENDLINE:{symbol}:{timeframe_minutes}:"
                        f"{pivot.side}:{prior.interval.pivot.event_time_ns}:"
                        f"{pivot.event_time_ns}:{sequence}"
                    )
                    version_id = f"{line_id}:V1:{pivot.observed_time_ns}"
                    item = _make_line_version(
                        line_id=line_id,
                        version_id=version_id,
                        version=1,
                        supersedes=(),
                        symbol=symbol,
                        anchor_side=pivot.side,
                        observed_time_ns=pivot.observed_time_ns,
                        timeframe_minutes=timeframe_minutes,
                        anchors=(prior.interval, interval),
                    )
                    if item is not None and pair_survived(
                        item,
                        formed_event_ns=max(
                            prior.interval.pivot.event_time_ns,
                            interval.pivot.event_time_ns,
                        ),
                        observed_time_ns=pivot.observed_time_ns,
                    ):
                        tracks.append(
                            _LineTrack(
                                line_id=item.line_id,
                                version=item.version,
                                version_id=item.version_id,
                                observed_time_ns=item.observed_time_ns,
                                anchor_side=item.anchor_side,
                                anchors=list(item.anchors),
                                slope_low=item.slope_low_per_ns,
                                slope_high=item.slope_high_per_ns,
                            )
                        )
                        versions.append(item)
            last_reaction[pivot.side] = _ObservedReaction(
                interval=interval,
                observed_time_ns=pivot.observed_time_ns,
            )

    return sorted(versions, key=lambda item: (item.observed_time_ns, item.version_id))


@dataclass(frozen=True, slots=True)
class _ArmedOverlap:
    setup_id: str
    version_id: str
    line: FeasibleTrendlineVersion
    zone_low: float
    zone_high: float


class TrendlineRoleFlipEngine:
    def __init__(
        self,
        symbol: str,
        lines: Iterable[FeasibleTrendlineVersion],
        pivots: Iterable[StructuralPivot],
        config: TrendlineRoleFlipConfig,
    ) -> None:
        self.symbol = symbol
        self.config = config
        self.pending_lines = sorted(
            lines,
            key=lambda item: (item.observed_time_ns, item.version_id),
        )
        self.pivots = sorted(
            pivots,
            key=lambda item: (item.observed_time_ns, item.event_time_ns),
        )
        self.line_cursor = 0
        self.active: dict[str, TrendlineState] = {}
        self.footprints: dict[str, FootprintRef] = {}
        self.candles: list[Candle] = []
        self.armed_overlaps: dict[str, _ArmedOverlap] = {}
        self.setup_by_version: dict[str, str] = {}
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}
        self.audit_rows: list[dict[str, object]] = []

    def _count(self, key: str, amount: int = 1) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + amount

    def _new_id(self) -> str:
        self.sequence += 1
        return f"ec18-trendline-{self.symbol}-{self.sequence:08d}"

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

    def _activate(self, current: Candle) -> tuple[list[str], list[dict[str, object]]]:
        cancellations: list[str] = []
        events: list[dict[str, object]] = []
        while (
            self.line_cursor < len(self.pending_lines)
            and self.pending_lines[self.line_cursor].observed_time_ns <= current.ts_open_ns
        ):
            line = self.pending_lines[self.line_cursor]
            self.line_cursor += 1
            for prior_id in line.supersedes_version_ids:
                self.active.pop(prior_id, None)
                setup_id = self.setup_by_version.get(prior_id)
                if setup_id is not None:
                    cancellations.append(setup_id)
                    self.armed_overlaps.pop(setup_id, None)
                events.append(
                    {
                        "line_id": line.line_id,
                        "version_id": line.version_id,
                        "superseded_version_id": prior_id,
                        "event": "TRENDLINE_VERSION_SUPERSEDED",
                        "time_ns": current.ts_open_ns,
                        "cancel_setup_id": setup_id,
                    }
                )
            self.active[line.version_id] = TrendlineState(line)
            self._count("trendline_versions_activated")
        return cancellations, events

    def _cancel_lost_overlaps(self, current: Candle) -> list[str]:
        cancellations = []
        for setup_id, armed in list(self.armed_overlaps.items()):
            band_low, band_high = armed.line.price_band(current.ts_open_ns)
            overlaps = max(band_low, armed.zone_low) <= min(band_high, armed.zone_high)
            if not overlaps:
                cancellations.append(setup_id)
                self.armed_overlaps.pop(setup_id, None)
                self._count("pending_cancelled_line_ob_overlap_ended")
        return cancellations

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

    def _eligible_order_blocks(
        self,
        *,
        state: TrendlineState,
        current: Candle,
    ) -> list[FootprintRef]:
        assert state.break_time_ns is not None
        line = state.line
        band_low, band_high = line.price_band(current.ts_close_ns)
        output = []
        for item in self.footprints.values():
            if (
                item.kind != "ORDER_BLOCK"
                or item.side is not line.trade_side
                or not state.break_time_ns <= item.observed_time_ns <= current.ts_close_ns
                or not self._fresh(item, current)
                or max(item.zone_low, band_low) > min(item.zone_high, band_high)
            ):
                continue
            if line.trade_side is Side.LONG and item.proximal <= current.close:
                output.append(item)
            elif line.trade_side is Side.SHORT and item.proximal >= current.close:
                output.append(item)
        output.sort(
            key=lambda item: (
                item.observed_time_ns,
                -item.timeframe_minutes,
                item.footprint_id,
            )
        )
        return output

    def _origin(
        self,
        *,
        state: TrendlineState,
        current: Candle,
    ) -> StructuralPivot | None:
        assert state.break_time_ns is not None
        wanted = "LOW" if state.line.trade_side is Side.LONG else "HIGH"
        eligible = [
            pivot
            for pivot in self.pivots
            if pivot.side == wanted
            and pivot.event_time_ns < state.break_time_ns
            and pivot.observed_time_ns <= current.ts_close_ns
        ]
        return max(eligible, default=None, key=lambda item: item.event_time_ns)

    def _pivot_consumed_before(self, pivot: StructuralPivot, current: Candle) -> bool:
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

    def _objective(
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
        state: TrendlineState,
        current: Candle,
        order_block: FootprintRef,
    ) -> ExpiringArmedSetup | None:
        line = state.line
        side = line.trade_side
        entry = order_block.proximal
        origin = self._origin(state=state, current=current)
        objective = self._objective(side=side, entry=entry, current=current)
        audit: dict[str, object] = {
            "line_id": line.line_id,
            "version_id": line.version_id,
            "symbol": self.symbol,
            "side": side.name,
            "anchor_count": line.anchor_count,
            "slope_low_per_ns": line.slope_low_per_ns,
            "slope_high_per_ns": line.slope_high_per_ns,
            "break_time_ns": state.break_time_ns,
            "acceptance_time_ns": state.acceptance_time_ns,
            "first_retest_time_ns": current.ts_close_ns,
            "order_block_id": order_block.footprint_id,
            "entry": entry,
        }
        if origin is None:
            audit["disposition"] = "UNRESOLVED_MISSING_PREBREAK_WAVE_ORIGIN"
            self.audit_rows.append(audit)
            self._count("missing_prebreak_wave_origin")
            return None
        invalidation = (
            min(origin.level, order_block.invalidation)
            if side is Side.LONG
            else max(origin.level, order_block.invalidation)
        )
        stop = (
            invalidation - self.config.tick_size
            if side is Side.LONG
            else invalidation + self.config.tick_size
        )
        if objective is None:
            audit["disposition"] = "UNRESOLVED_NO_ACTIVE_OBJECTIVE"
            self.audit_rows.append(audit)
            self._count("missing_active_objective")
            return None
        target = objective.level
        audit.update(
            {
                "origin": origin.level,
                "invalidation": invalidation,
                "stop": stop,
                "objective": target,
            }
        )
        if side is Side.LONG and not stop < entry < target:
            audit["disposition"] = "REJECT_INVALID_LONG_GEOMETRY"
            self.audit_rows.append(audit)
            return None
        if side is Side.SHORT and not target < entry < stop:
            audit["disposition"] = "REJECT_INVALID_SHORT_GEOMETRY"
            self.audit_rows.append(audit)
            return None

        setup_id = self._new_id()
        setup = ExpiringArmedSetup(
            setup_id=setup_id,
            causal_event_id=(
                f"TRENDLINE_ROLE_FLIP:{self.symbol}:{line.line_id}:"
                f"{line.version_id}:{state.break_time_ns}:"
                f"{state.acceptance_time_ns}:{current.ts_close_ns}:{side.name}"
            ),
            symbol=self.symbol,
            family="TRENDLINE_ACCEPTED_BREAK_FIRST_RETEST_OVERLAPPING_OB",
            side=side,
            observed_time_ns=current.ts_close_ns,
            entry=float(entry),
            stop=float(stop),
            target_mode=TargetMode.FIXED_STRUCTURE,
            initial_target=float(target),
            fixed_target_id=(
                f"FIRST_ACTIVE_DC_PIVOT:{objective.side}:{objective.event_time_ns}"
            ),
            source_pool_id=line.version_id,
            zone_low=float(order_block.zone_low),
            zone_high=float(order_block.zone_high),
            formation_extreme=float(invalidation),
            body_ratio=2.0 if order_block.source_two_x_quality else 0.0,
            previous_body=0.0,
            current_body=0.0,
            context_bias=(
                "ROLE_GRAPH_V18|OPTION=TRENDLINE_ACCEPTED_BREAK_FIRST_RETEST"
                "|TRENDLINE=SET_MEMBERSHIP_WICK_TO_BODY_INTERVALS"
                "|DIRECTION=UNAMBIGUOUS_FEASIBLE_SLOPE_SIGN"
                "|ENTRY=OVERLAPPING_EASYCHART_ORDER_BLOCK"
                f"|LINE_ID={line.line_id}|VERSION={line.version}"
                f"|ANCHORS={line.anchor_count}"
                f"|OB={order_block.footprint_id}"
                f"|BREAK={state.break_time_ns}"
                f"|ACCEPT={state.acceptance_time_ns}"
                f"|RETEST={current.ts_close_ns}"
                "|SOURCE_STATUS=CASE02_SOURCE_EXPLICIT_PLUS_SET_MEMBERSHIP_OPERATIONALIZATION"
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
            audit["disposition"] = "REJECT_FIRST_ACTIVE_OBJECTIVE_RR_LT_1"
            self.audit_rows.append(audit)
            self._count("first_objective_rr_lt_1")
            return None
        state.setup_id = setup_id
        self.setup_by_version[line.version_id] = setup_id
        self.armed_overlaps[setup_id] = _ArmedOverlap(
            setup_id=setup_id,
            version_id=line.version_id,
            line=line,
            zone_low=order_block.zone_low,
            zone_high=order_block.zone_high,
        )
        audit["disposition"] = "ARM_OB_FIRST_RETEST"
        audit["setup_id"] = setup_id
        audit["gross_rr"] = plan.gross_rr
        self.audit_rows.append(audit)
        self._count("setups_armed")
        return setup

    def _observe_state(
        self,
        state: TrendlineState,
        current: Candle,
        index: int,
    ) -> TrendlineEngineUpdate:
        line = state.line
        band_open = line.price_band(current.ts_open_ns)
        band_close = line.price_band(current.ts_close_ns)
        outside_open = (
            current.open > band_open[1]
            if line.trade_side is Side.LONG
            else current.open < band_open[0]
        )
        outside_close = (
            current.close > band_close[1]
            if line.trade_side is Side.LONG
            else current.close < band_close[0]
        )

        if state.phase is TrendlinePhase.WAIT_BREAK:
            if outside_close:
                state.phase = TrendlinePhase.OUTSIDE
                state.break_index = index
                state.break_time_ns = current.ts_close_ns
                self._count("first_outside_closes")
                return TrendlineEngineUpdate(
                    events=(
                        {
                            "line_id": line.line_id,
                            "version_id": line.version_id,
                            "event": "FIRST_OUTSIDE_CLOSE",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            return TrendlineEngineUpdate()

        if state.phase is TrendlinePhase.OUTSIDE:
            assert state.break_index is not None
            if not outside_close:
                state.phase = TrendlinePhase.COMPLETED
                self._count("break_failed_before_acceptance")
                return TrendlineEngineUpdate(
                    events=(
                        {
                            "line_id": line.line_id,
                            "version_id": line.version_id,
                            "event": "FAILED_BREAK_BEFORE_ACCEPTANCE",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            if index > state.break_index and outside_open and outside_close:
                state.phase = TrendlinePhase.ACCEPTED_WAIT_FIRST_RETEST
                state.acceptance_index = index
                state.acceptance_time_ns = current.ts_close_ns
                self._count("accepted_breaks")
                return TrendlineEngineUpdate(
                    events=(
                        {
                            "line_id": line.line_id,
                            "version_id": line.version_id,
                            "event": "ACCEPTED_BREAK_WAIT_FIRST_RETEST",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            return TrendlineEngineUpdate()

        if state.phase is TrendlinePhase.ACCEPTED_WAIT_FIRST_RETEST:
            assert state.acceptance_index is not None
            if index <= state.acceptance_index:
                return TrendlineEngineUpdate()
            # The projected feasible line set moves during the candle.  The
            # lower envelope is concave and the upper envelope is convex, so
            # the union over the candle is bounded exactly by the endpoint
            # extrema.  This avoids pretending the close-time line existed for
            # the entire bar while remaining conservative about intrabar order.
            band_low = min(band_open[0], band_close[0])
            band_high = max(band_open[1], band_close[1])
            touched = current.low <= band_high and current.high >= band_low
            failed_role_flip = (
                current.close < band_low
                if line.trade_side is Side.LONG
                else current.close > band_high
            )
            if not touched:
                if failed_role_flip:
                    state.phase = TrendlinePhase.COMPLETED
                    self._count("role_flip_failed_before_retest")
                return TrendlineEngineUpdate()

            order_blocks = self._eligible_order_blocks(state=state, current=current)
            state.phase = TrendlinePhase.COMPLETED
            if failed_role_flip:
                self._count("first_retest_closed_through_line")
                return TrendlineEngineUpdate(
                    events=(
                        {
                            "line_id": line.line_id,
                            "version_id": line.version_id,
                            "event": "FIRST_RETEST_INVALIDATED_ROLE_FLIP",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            if not order_blocks:
                self._count("first_retest_without_overlapping_ob")
                return TrendlineEngineUpdate(
                    events=(
                        {
                            "line_id": line.line_id,
                            "version_id": line.version_id,
                            "event": "FIRST_RETEST_UNRESOLVED_NO_OVERLAPPING_OB",
                            "time_ns": current.ts_close_ns,
                        },
                    )
                )
            if line.trade_side is Side.LONG:
                selected = max(
                    order_blocks,
                    key=lambda item: (item.proximal, item.observed_time_ns, item.footprint_id),
                )
            else:
                selected = min(
                    order_blocks,
                    key=lambda item: (item.proximal, -item.observed_time_ns, item.footprint_id),
                )
            setup = self._build_setup(state=state, current=current, order_block=selected)
            return TrendlineEngineUpdate(
                setups=(() if setup is None else (setup,)),
                events=(
                    {
                        "line_id": line.line_id,
                        "version_id": line.version_id,
                        "event": "FIRST_RETEST_OB_RESPONSE",
                        "time_ns": current.ts_close_ns,
                        "setup_id": None if setup is None else setup.setup_id,
                    },
                ),
            )

        return TrendlineEngineUpdate()

    def on_close(self, current: Candle, index: int) -> TrendlineEngineUpdate:
        cancellations, events = self._activate(current)
        cancellations.extend(self._cancel_lost_overlaps(current))
        setups: list[ExpiringArmedSetup] = []
        for version_id, state in list(self.active.items()):
            update = self._observe_state(state, current, index)
            setups.extend(update.setups)
            cancellations.extend(update.cancel_setup_ids)
            events.extend(update.events)
            if state.phase is TrendlinePhase.COMPLETED:
                self.active.pop(version_id, None)
        self.candles.append(current)
        return TrendlineEngineUpdate(
            setups=tuple(sorted(setups, key=lambda item: (item.observed_time_ns, item.setup_id))),
            cancel_setup_ids=tuple(dict.fromkeys(cancellations)),
            events=tuple(events),
        )


__all__ = [
    "FeasibleTrendlineVersion",
    "TrendlineEngineUpdate",
    "TrendlinePhase",
    "TrendlineRoleFlipConfig",
    "TrendlineRoleFlipEngine",
    "build_feasible_trendlines",
    "feasible_slope_interval",
]
