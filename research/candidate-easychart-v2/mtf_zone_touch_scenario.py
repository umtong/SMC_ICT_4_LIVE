"""Case-derived multi-timeframe order-block first-touch family.

Case 28 explicitly planned a second long because closed 15m and 1h bullish
engulfing order blocks formed the same support area.  The order was planned at
that area before the return; no separate local swing sweep was stated as a
universal prerequisite.

This engine therefore emits a *limit-entry plan* when a fresh 1h/15m OB overlap
and a fresh pre-existing opposite objective are simultaneously observable.
It does not submit orders; NautilusTrader owns the pending limit and bracket.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain import Candle, Side
from easychart_zones import (
    EasyChartZoneDetector,
    PriceZone,
    ZoneKind,
    ZoneOverlap,
    ZoneSide,
    overlap_zones,
)


class MTFZoneTouchState(str, Enum):
    PLANNED = "PLANNED"
    NO_OBJECTIVE = "NO_OBJECTIVE"
    BAD_ENTRY_GEOMETRY = "BAD_ENTRY_GEOMETRY"
    RR_BELOW_MINIMUM = "RR_BELOW_MINIMUM"


@dataclass(slots=True)
class MTFZoneTouchSetup:
    setup_id: str
    overlap: ZoneOverlap
    higher_zone: PriceZone
    decision_zone: PriceZone
    observed_time_ns: int
    state: MTFZoneTouchState


@dataclass(frozen=True, slots=True)
class MTFZoneTouchTradePlan:
    plan_id: str
    causal_event_id: str
    symbol: str
    family: str
    side: Side
    observed_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    entry_order_kind: str
    setup_id: str
    higher_zone_id: str
    higher_strength_ratio: float
    decision_zone_id: str
    decision_strength_ratio: float
    overlap_lower: float
    overlap_upper: float
    target_zone_id: str
    target_zone_kind: ZoneKind
    target_observed_time_ns: int


class MTFZoneFirstTouchScenarioEngine:
    """Fresh 1h/15m same-side OB overlap with one distal-edge limit entry."""

    FAMILY = "MTF_OB_OVERLAP_FIRST_TOUCH"

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        higher_minutes: int = 60,
        decision_minutes: int = 15,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        if not higher_minutes > decision_minutes > 0:
            raise ValueError("timeframes must satisfy higher > decision > 0")
        if minimum_gross_rr <= 0.0:
            raise ValueError("minimum_gross_rr must be positive")
        self.symbol = symbol
        self.tick_size = tick_size
        self.higher_minutes = higher_minutes
        self.decision_minutes = decision_minutes
        self.minimum_gross_rr = minimum_gross_rr
        self.detectors = {
            higher_minutes: EasyChartZoneDetector(symbol, higher_minutes, tick_size),
            decision_minutes: EasyChartZoneDetector(symbol, decision_minutes, tick_size),
        }
        self.setups: list[MTFZoneTouchSetup] = []
        self.plans: list[MTFZoneTouchTradePlan] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _side(zone_side: ZoneSide) -> Side:
        return Side.LONG if zone_side is ZoneSide.SUPPORT else Side.SHORT

    @staticmethod
    def _entry(overlap: ZoneOverlap) -> float:
        # Case 28 placed the planned long at the lower/distal edge of the shared
        # support. Mirror the geometry for resistance.
        return overlap.lower if overlap.side is ZoneSide.SUPPORT else overlap.upper

    @staticmethod
    def _stop(higher: PriceZone, decision: PriceZone) -> float:
        # Both structures support the thesis; invalidation sits beyond the
        # deepest formation wick rather than choosing the tighter stop for RR.
        if higher.side is ZoneSide.SUPPORT:
            return min(higher.invalidation, decision.invalidation)
        return max(higher.invalidation, decision.invalidation)

    @staticmethod
    def _entry_is_pending_from(current: Candle, side: Side, entry: float) -> bool:
        return current.close > entry if side is Side.LONG else current.close < entry

    def _target(
        self,
        side: Side,
        entry: float,
        current: Candle,
        observed_time_ns: int,
    ) -> PriceZone | None:
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates: list[tuple[float, PriceZone]] = []
        for timeframe in (self.higher_minutes, self.decision_minutes):
            for zone in self.detectors[timeframe].active_zones(side=wanted):
                if zone.observed_time_ns >= observed_time_ns:
                    continue
                if zone.first_touch_index is not None:
                    continue
                price = zone.lower if side is Side.LONG else zone.upper
                # The complete current candle is already known. A target printed
                # inside it is not future reward space for a later limit fill.
                if side is Side.LONG and price > max(entry, current.high):
                    candidates.append((price, zone))
                elif side is Side.SHORT and price < min(entry, current.low):
                    candidates.append((price, zone))
        if not candidates:
            return None
        if side is Side.LONG:
            return min(candidates, key=lambda item: (item[0], item[1].observed_time_ns, item[1].zone_id))[1]
        return max(candidates, key=lambda item: (item[0], -item[1].observed_time_ns, item[1].zone_id))[1]

    def _setup_id(self, overlap: ZoneOverlap) -> str:
        return f"MTF-TOUCH:{overlap.overlap_id}"

    def _refresh(self, current: Candle) -> list[MTFZoneTouchTradePlan]:
        higher_detector = self.detectors[self.higher_minutes]
        decision_detector = self.detectors[self.decision_minutes]
        existing = {setup.setup_id for setup in self.setups}
        emitted: list[MTFZoneTouchTradePlan] = []

        for higher in higher_detector.active_zones(kind=ZoneKind.ORDER_BLOCK):
            for decision in decision_detector.active_zones(kind=ZoneKind.ORDER_BLOCK):
                # This family is deliberately the source-explicit OB/OB case,
                # not a generic "two things overlap" score.
                if higher.side is not decision.side:
                    continue
                if higher.first_touch_index is not None or decision.first_touch_index is not None:
                    continue
                overlap = overlap_zones(higher, decision)
                if overlap is None:
                    continue
                setup_id = self._setup_id(overlap)
                if setup_id in existing:
                    continue
                existing.add(setup_id)
                side = self._side(overlap.side)
                entry = self._entry(overlap)
                stop = self._stop(higher, decision)
                observed = max(overlap.observed_time_ns, current.ts_close_ns)

                if not self._entry_is_pending_from(current, side, entry):
                    self.setups.append(
                        MTFZoneTouchSetup(
                            setup_id,
                            overlap,
                            higher,
                            decision,
                            observed,
                            MTFZoneTouchState.BAD_ENTRY_GEOMETRY,
                        ),
                    )
                    self._inc("setup_not_above_or_below_planned_limit")
                    continue
                if side is Side.LONG and not stop < entry:
                    state = MTFZoneTouchState.BAD_ENTRY_GEOMETRY
                elif side is Side.SHORT and not entry < stop:
                    state = MTFZoneTouchState.BAD_ENTRY_GEOMETRY
                else:
                    state = MTFZoneTouchState.PLANNED
                if state is MTFZoneTouchState.BAD_ENTRY_GEOMETRY:
                    self.setups.append(
                        MTFZoneTouchSetup(setup_id, overlap, higher, decision, observed, state),
                    )
                    self._inc("setup_bad_stop_geometry")
                    continue

                target_zone = self._target(side, entry, current, observed)
                if target_zone is None:
                    self.setups.append(
                        MTFZoneTouchSetup(
                            setup_id,
                            overlap,
                            higher,
                            decision,
                            observed,
                            MTFZoneTouchState.NO_OBJECTIVE,
                        ),
                    )
                    self._inc("setup_without_fresh_preexisting_target")
                    continue
                target = target_zone.lower if side is Side.LONG else target_zone.upper
                risk = abs(entry - stop)
                reward = abs(target - entry)
                gross_rr = reward / risk if risk > 0.0 else 0.0
                if gross_rr + 1e-12 < self.minimum_gross_rr:
                    self.setups.append(
                        MTFZoneTouchSetup(
                            setup_id,
                            overlap,
                            higher,
                            decision,
                            observed,
                            MTFZoneTouchState.RR_BELOW_MINIMUM,
                        ),
                    )
                    self._inc("setup_rr_below_minimum")
                    continue

                self.sequence += 1
                causal_event_id = f"{self.FAMILY}:{overlap.overlap_id}"
                plan = MTFZoneTouchTradePlan(
                    plan_id=f"ecv2-mtf-touch-{self.symbol}-{self.sequence:08d}",
                    causal_event_id=causal_event_id,
                    symbol=self.symbol,
                    family=self.FAMILY,
                    side=side,
                    observed_time_ns=observed,
                    entry=entry,
                    stop=stop,
                    target=target,
                    gross_rr=gross_rr,
                    entry_order_kind="LIMIT",
                    setup_id=setup_id,
                    higher_zone_id=higher.zone_id,
                    higher_strength_ratio=higher.strength_ratio,
                    decision_zone_id=decision.zone_id,
                    decision_strength_ratio=decision.strength_ratio,
                    overlap_lower=overlap.lower,
                    overlap_upper=overlap.upper,
                    target_zone_id=target_zone.zone_id,
                    target_zone_kind=target_zone.kind,
                    target_observed_time_ns=target_zone.observed_time_ns,
                )
                higher.consumed = True
                decision.consumed = True
                self.setups.append(
                    MTFZoneTouchSetup(
                        setup_id,
                        overlap,
                        higher,
                        decision,
                        observed,
                        MTFZoneTouchState.PLANNED,
                    ),
                )
                self.plans.append(plan)
                emitted.append(plan)
                self._inc("plan_created")
        return emitted

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFZoneTouchTradePlan]:
        if timeframe_minutes not in self.detectors:
            raise ValueError(f"unsupported timeframe: {timeframe_minutes}")
        self.detectors[timeframe_minutes].on_bar(bar)
        return self._refresh(bar)
