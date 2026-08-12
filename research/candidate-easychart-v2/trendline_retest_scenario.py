"""Case-derived trendline break, first retest and OB confirmation family.

The family is based on repeated source episodes (notably cases 02 and 14):

    causal wick trendline
    -> directional close break
    -> first later retest
    -> same-direction engulfing OB formed at that retest
    -> one entry / formation invalidation / nearest pre-existing objective

A local liquidity sweep is intentionally not required.  It may define another
family, but the reviewed source episodes did not make it a universal condition.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from causal_swings import SwingPoint, SwingSide
from causal_trendlines import (
    CausalTrendLine,
    CausalTrendLineTracker,
    TrendLineEvent,
    TrendLineEventKind,
    TrendLineSide,
)
from domain import Candle, Side
from easychart_zones import EasyChartZoneDetector, PriceZone, ZoneKind, ZoneSide


class TrendlineRetestState(str, Enum):
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    MISSED_WITHOUT_CONFIRMATION = "MISSED_WITHOUT_CONFIRMATION"
    NO_OBJECTIVE = "NO_OBJECTIVE"
    RR_BELOW_MINIMUM = "RR_BELOW_MINIMUM"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"


@dataclass(slots=True)
class TrendlineRetestSetup:
    setup_id: str
    line_id: str
    line_side: TrendLineSide
    retest_index: int
    retest_time_ns: int
    retest_level: float
    retest_high: float
    retest_low: float
    state: TrendlineRetestState = TrendlineRetestState.WAITING_CONFIRMATION
    trigger_zone_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrendlineRetestTradePlan:
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
    setup_id: str
    line_id: str
    line_side: TrendLineSide
    line_first_swing_id: str
    line_second_swing_id: str
    line_anchor_span_bars: int
    line_touch_count: int
    line_tolerance: float
    break_time_ns: int
    break_level: float
    retest_time_ns: int
    retest_level: float
    trigger_zone_id: str
    trigger_strength_ratio: float
    target_id: str
    target_kind: str
    target_observed_time_ns: int


@dataclass(frozen=True, slots=True)
class Objective:
    objective_id: str
    kind: str
    price: float
    observed_time_ns: int


class TrendlineFirstRetestScenarioEngine:
    """One-timeframe causal trendline breakout/retest family.

    The initial implementation keeps the reviewed episode small and auditable.
    Multi-timeframe context can route this family later; it is not silently
    invented inside the entry detector.
    """

    FAMILY = "TRENDLINE_BREAK_FIRST_RETEST_OB"

    def __init__(
        self,
        symbol: str,
        timeframe_minutes: int,
        tick_size: float,
        *,
        minimum_gross_rr: float = 1.0,
        swing_span: int = 2,
        min_anchor_bars: int = 3,
        tolerance_range_fraction: float = 0.10,
    ) -> None:
        if minimum_gross_rr <= 0.0:
            raise ValueError("minimum_gross_rr must be positive")
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.tick_size = tick_size
        self.minimum_gross_rr = minimum_gross_rr
        self.line_tracker = CausalTrendLineTracker(
            symbol,
            timeframe_minutes,
            tick_size,
            swing_span=swing_span,
            min_anchor_bars=min_anchor_bars,
            tolerance_range_fraction=tolerance_range_fraction,
        )
        self.zone_detector = EasyChartZoneDetector(symbol, timeframe_minutes, tick_size)
        self.setups: list[TrendlineRetestSetup] = []
        self.plans: list[TrendlineRetestTradePlan] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _line(self, line_id: str) -> CausalTrendLine:
        line = self.line_tracker.line(line_id)
        if line is None:
            raise RuntimeError(f"trendline disappeared: {line_id}")
        return line

    @staticmethod
    def _trade_side(line_side: TrendLineSide) -> Side:
        # Breaking resistance upward produces a long retest; breaking support
        # downward produces a short retest.
        return Side.LONG if line_side is TrendLineSide.RESISTANCE else Side.SHORT

    @staticmethod
    def _zone_side(side: Side) -> ZoneSide:
        return ZoneSide.SUPPORT if side is Side.LONG else ZoneSide.RESISTANCE

    @staticmethod
    def _formation_touches_line(
        zone: PriceZone,
        line: CausalTrendLine,
        bars: list[Candle],
    ) -> bool:
        for index in zone.formation_indices:
            level = line.price_at(index)
            bar = bars[index]
            if bar.low <= level + line.tolerance and bar.high >= level - line.tolerance:
                return True
        return False

    def _create_setups(self, events: list[TrendLineEvent]) -> None:
        existing = {setup.setup_id for setup in self.setups}
        for event in events:
            if event.kind is not TrendLineEventKind.FIRST_RETEST:
                continue
            setup_id = f"TL-RETEST:{event.line_id}:{event.index}"
            if setup_id in existing:
                continue
            self.setups.append(
                TrendlineRetestSetup(
                    setup_id=setup_id,
                    line_id=event.line_id,
                    line_side=event.side,
                    retest_index=event.index,
                    retest_time_ns=event.time_ns,
                    retest_level=event.line_level,
                    retest_high=event.bar_high,
                    retest_low=event.bar_low,
                ),
            )
            existing.add(setup_id)
            self._inc("setup_created")

    def _swing_unspent(self, swing: SwingPoint, current_index: int) -> bool:
        bars = self.line_tracker.bars
        for index in range(swing.event_index + 1, current_index):
            bar = bars[index]
            if swing.side is SwingSide.HIGH and bar.high >= swing.level:
                return False
            if swing.side is SwingSide.LOW and bar.low <= swing.level:
                return False
        return True

    def _nearest_objective(
        self,
        side: Side,
        entry: float,
        current: Candle,
        current_index: int,
        observed_time_ns: int,
    ) -> Objective | None:
        candidates: list[Objective] = []
        wanted_swing = SwingSide.HIGH if side is Side.LONG else SwingSide.LOW
        for swing in self.line_tracker.swing_tracker.swings:
            if swing.side is not wanted_swing:
                continue
            if swing.observed_time_ns >= observed_time_ns:
                continue
            if not self._swing_unspent(swing, current_index):
                continue
            if side is Side.LONG and swing.level > max(entry, current.high):
                candidates.append(
                    Objective(swing.swing_id, "SWING_HIGH", swing.level, swing.observed_time_ns),
                )
            elif side is Side.SHORT and swing.level < min(entry, current.low):
                candidates.append(
                    Objective(swing.swing_id, "SWING_LOW", swing.level, swing.observed_time_ns),
                )

        wanted_zone = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        for zone in self.zone_detector.active_zones(side=wanted_zone):
            if zone.observed_time_ns >= observed_time_ns:
                continue
            if zone.first_touch_index is not None:
                continue
            price = zone.lower if side is Side.LONG else zone.upper
            if side is Side.LONG and price > max(entry, current.high):
                candidates.append(Objective(zone.zone_id, zone.kind.value, price, zone.observed_time_ns))
            elif side is Side.SHORT and price < min(entry, current.low):
                candidates.append(Objective(zone.zone_id, zone.kind.value, price, zone.observed_time_ns))

        if not candidates:
            return None
        if side is Side.LONG:
            return min(candidates, key=lambda item: (item.price, item.observed_time_ns, item.objective_id))
        return max(candidates, key=lambda item: (item.price, -item.observed_time_ns, item.objective_id))

    @staticmethod
    def _setup_rank(setup: TrendlineRetestSetup, line: CausalTrendLine) -> tuple[int, int, str]:
        # No learned score: prefer the line which the market touched more often,
        # then the longer-lived geometry, then a stable identifier.
        return (-line.touch_count, -line.anchor_span_bars, line.line_id)

    def _invalidated_before_confirmation(
        self,
        setup: TrendlineRetestSetup,
        line: CausalTrendLine,
        index: int,
        bar: Candle,
    ) -> bool:
        level = line.price_at(index)
        if setup.line_side is TrendLineSide.RESISTANCE:
            return bar.close < level - line.tolerance
        return bar.close > level + line.tolerance

    @staticmethod
    def _departed_without_confirmation(
        setup: TrendlineRetestSetup,
        side: Side,
        bar: Candle,
    ) -> bool:
        if side is Side.LONG:
            return bar.close > setup.retest_high
        return bar.close < setup.retest_low

    def _advance(
        self,
        bar: Candle,
        index: int,
        created_zones: list[PriceZone],
    ) -> list[TrendlineRetestTradePlan]:
        plans: list[TrendlineRetestTradePlan] = []
        waiting = [
            setup
            for setup in self.setups
            if setup.state is TrendlineRetestState.WAITING_CONFIRMATION
        ]
        waiting.sort(key=lambda setup: self._setup_rank(setup, self._line(setup.line_id)))

        for setup in waiting:
            line = self._line(setup.line_id)
            side = self._trade_side(setup.line_side)
            if index < setup.retest_index:
                continue
            if index > setup.retest_index and self._invalidated_before_confirmation(
                setup,
                line,
                index,
                bar,
            ):
                setup.state = TrendlineRetestState.INVALIDATED
                self._inc("setup_invalidated_before_confirmation")
                continue

            trigger: PriceZone | None = None
            for zone in created_zones:
                if zone.consumed:
                    continue
                if zone.kind is not ZoneKind.ORDER_BLOCK:
                    continue
                if zone.side is not self._zone_side(side):
                    continue
                if zone.observed_time_ns < setup.retest_time_ns:
                    continue
                if not zone.high_quality_by_size:
                    self._inc("retest_order_block_below_two_x")
                    continue
                if not self._formation_touches_line(zone, line, self.zone_detector.bars):
                    continue
                trigger = zone
                break

            if trigger is None:
                if index > setup.retest_index and self._departed_without_confirmation(
                    setup,
                    side,
                    bar,
                ):
                    setup.state = TrendlineRetestState.MISSED_WITHOUT_CONFIRMATION
                    self._inc("setup_missed_without_confirmation")
                continue

            entry = bar.close
            stop = trigger.invalidation
            if side is Side.LONG and not stop < entry:
                setup.state = TrendlineRetestState.INVALIDATED
                self._inc("invalid_long_geometry")
                continue
            if side is Side.SHORT and not entry < stop:
                setup.state = TrendlineRetestState.INVALIDATED
                self._inc("invalid_short_geometry")
                continue

            objective = self._nearest_objective(
                side,
                entry,
                bar,
                index,
                bar.ts_close_ns,
            )
            if objective is None:
                setup.state = TrendlineRetestState.NO_OBJECTIVE
                self._inc("trigger_without_preexisting_objective")
                continue
            risk = abs(entry - stop)
            reward = abs(objective.price - entry)
            if risk <= 0.0 or reward <= 0.0:
                setup.state = TrendlineRetestState.INVALIDATED
                self._inc("nonpositive_geometry")
                continue
            gross_rr = reward / risk
            if gross_rr + 1e-12 < self.minimum_gross_rr:
                setup.state = TrendlineRetestState.RR_BELOW_MINIMUM
                self._inc("trigger_rr_below_minimum")
                continue
            if line.break_time_ns is None or line.break_level is None:
                raise RuntimeError("retested line lost break lineage")

            self.sequence += 1
            causal_event_id = f"{self.FAMILY}:{line.line_id}:{setup.retest_index}:{trigger.zone_id}"
            plan = TrendlineRetestTradePlan(
                plan_id=f"ecv2-tl-{self.symbol}-{self.sequence:08d}",
                causal_event_id=causal_event_id,
                symbol=self.symbol,
                family=self.FAMILY,
                side=side,
                observed_time_ns=bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=objective.price,
                gross_rr=gross_rr,
                setup_id=setup.setup_id,
                line_id=line.line_id,
                line_side=line.side,
                line_first_swing_id=line.first_swing_id,
                line_second_swing_id=line.second_swing_id,
                line_anchor_span_bars=line.anchor_span_bars,
                line_touch_count=line.touch_count,
                line_tolerance=line.tolerance,
                break_time_ns=line.break_time_ns,
                break_level=line.break_level,
                retest_time_ns=setup.retest_time_ns,
                retest_level=setup.retest_level,
                trigger_zone_id=trigger.zone_id,
                trigger_strength_ratio=trigger.strength_ratio,
                target_id=objective.objective_id,
                target_kind=objective.kind,
                target_observed_time_ns=objective.observed_time_ns,
            )
            setup.state = TrendlineRetestState.PLANNED
            setup.trigger_zone_id = trigger.zone_id
            trigger.consumed = True
            self.plans.append(plan)
            plans.append(plan)
            self._inc("plan_created")

            # The same OB/retest episode must not become several trades merely
            # because several near-identical pivot pairs describe the line.
            for other in waiting:
                if other is setup or other.state is not TrendlineRetestState.WAITING_CONFIRMATION:
                    continue
                if other.retest_index == setup.retest_index:
                    other.state = TrendlineRetestState.DUPLICATE_EPISODE
                    self._inc("duplicate_retest_setup_suppressed")
            break
        return plans

    def on_bar(self, bar: Candle) -> list[TrendlineRetestTradePlan]:
        created_zones = self.zone_detector.on_bar(bar)
        line_events = self.line_tracker.on_bar(bar)
        self._create_setups(line_events)
        index = len(self.line_tracker.bars) - 1
        return self._advance(bar, index, created_zones)
