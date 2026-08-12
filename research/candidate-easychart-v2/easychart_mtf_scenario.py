"""First source-explicit EasyChart scenario family.

The scenario separates roles rather than stacking synonymous filters:

    60m context OB/FVG with at least one OB in the context pair
    -> 15m overlapping decision zone
    -> first interaction
    -> later, size-confirmed 5m engulfing order block
    -> entry / trigger invalidation / fresh opposite-zone target

This is still a partial EasyChart family: trendline, channel, explicit external
liquidity sweep and 4h directional routing are intentionally not claimed here.
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


class SetupState(str, Enum):
    WAITING_INTERACTION = "WAITING_INTERACTION"
    WAITING_TRIGGER = "WAITING_TRIGGER"
    PLANNED = "PLANNED"
    INVALIDATED = "INVALIDATED"
    MISSED_WITHOUT_TRIGGER = "MISSED_WITHOUT_TRIGGER"
    TARGET_SPENT = "TARGET_SPENT"
    TRIGGER_FAILED_GEOMETRY = "TRIGGER_FAILED_GEOMETRY"


@dataclass(slots=True)
class MTFSetup:
    setup_id: str
    overlap: ZoneOverlap
    higher_zone: PriceZone
    lower_zone: PriceZone
    observed_time_ns: int
    state: SetupState = SetupState.WAITING_INTERACTION
    interaction_time_ns: int | None = None
    interaction_trigger_index: int | None = None
    trigger_zone_id: str | None = None


@dataclass(frozen=True, slots=True)
class MTFTradePlan:
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
    higher_zone_id: str
    higher_zone_kind: ZoneKind
    higher_strength_ratio: float
    lower_zone_id: str
    lower_zone_kind: ZoneKind
    lower_strength_ratio: float
    trigger_zone_id: str
    trigger_strength_ratio: float
    target_zone_id: str
    target_zone_kind: ZoneKind
    overlap_lower: float
    overlap_upper: float
    interaction_time_ns: int
    trigger_time_ns: int

    @property
    def kind_diversity(self) -> int:
        return len({self.higher_zone_kind, self.lower_zone_kind, self.target_zone_kind})


class MTFOverlapScenarioEngine:
    """Causal 60m/15m overlap with a distinct 5m entry confirmation."""

    FAMILY = "MTF_ZONE_OVERLAP_5M_OB"

    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        higher_minutes: int = 60,
        decision_minutes: int = 15,
        trigger_minutes: int = 5,
        minimum_gross_rr: float = 1.0,
    ) -> None:
        if not higher_minutes > decision_minutes > trigger_minutes > 0:
            raise ValueError("timeframes must satisfy higher > decision > trigger > 0")
        if minimum_gross_rr <= 0.0:
            raise ValueError("minimum_gross_rr must be positive")
        self.symbol = symbol
        self.tick_size = tick_size
        self.higher_minutes = higher_minutes
        self.decision_minutes = decision_minutes
        self.trigger_minutes = trigger_minutes
        self.minimum_gross_rr = minimum_gross_rr
        self.detectors = {
            higher_minutes: EasyChartZoneDetector(symbol, higher_minutes, tick_size),
            decision_minutes: EasyChartZoneDetector(symbol, decision_minutes, tick_size),
            trigger_minutes: EasyChartZoneDetector(symbol, trigger_minutes, tick_size),
        }
        self.setups: list[MTFSetup] = []
        self.plans: list[MTFTradePlan] = []
        self.sequence = 0
        self.diagnostics: dict[str, int] = {}

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    @staticmethod
    def _bar_touches_zone(bar: Candle, lower: float, upper: float) -> bool:
        return bar.low <= upper and bar.high >= lower

    @staticmethod
    def _zone_invalidated_by_bar(zone: PriceZone, bar: Candle) -> bool:
        if zone.side is ZoneSide.SUPPORT:
            return bar.low <= zone.invalidation
        return bar.high >= zone.invalidation

    @staticmethod
    def _departed_favorably_without_entry(setup: MTFSetup, bar: Candle) -> bool:
        # Once the first-return episode leaves the decision overlap without the
        # chosen 5m confirmation, entering on a later revisit would be chasing a
        # different causal episode. The source repeatedly says not to enter if
        # the planned place is missed.
        if setup.overlap.side is ZoneSide.SUPPORT:
            return bar.close > setup.overlap.upper
        return bar.close < setup.overlap.lower

    def _setup_id(self, overlap: ZoneOverlap) -> str:
        return f"SETUP:{overlap.overlap_id}"

    def _refresh_setups(self) -> None:
        higher_detector = self.detectors[self.higher_minutes]
        lower_detector = self.detectors[self.decision_minutes]
        existing = {setup.setup_id for setup in self.setups}
        for higher in higher_detector.active_zones():
            for lower in lower_detector.active_zones():
                # FVG is not treated as a standalone trade reason in the source.
                # At least one member of the context/decision pair must be an OB.
                if higher.kind is not ZoneKind.ORDER_BLOCK and lower.kind is not ZoneKind.ORDER_BLOCK:
                    self._inc("setup_rejected_fvg_only_context")
                    continue
                # A source-explicit 2x expansion on at least one member prevents
                # two marginal engulfing candles being called strong confluence.
                if not (higher.high_quality_by_size or lower.high_quality_by_size):
                    self._inc("setup_rejected_no_size_confirmed_member")
                    continue
                # The first return is the auditable opportunity. Already touched
                # zones are memory, not fresh pending orders.
                if higher.first_touch_index is not None or lower.first_touch_index is not None:
                    continue
                overlap = overlap_zones(higher, lower)
                if overlap is None:
                    continue
                setup_id = self._setup_id(overlap)
                if setup_id in existing:
                    continue
                self.setups.append(
                    MTFSetup(
                        setup_id=setup_id,
                        overlap=overlap,
                        higher_zone=higher,
                        lower_zone=lower,
                        observed_time_ns=overlap.observed_time_ns,
                    ),
                )
                existing.add(setup_id)
                self._inc("setup_created")
                self._inc(f"setup_{higher.kind.value.lower()}_{lower.kind.value.lower()}")

    def _opposite_target(
        self,
        side: ZoneSide,
        entry: float,
        current: Candle,
        observed_time_ns: int,
    ) -> tuple[PriceZone, float] | None:
        opposite = ZoneSide.RESISTANCE if side is ZoneSide.SUPPORT else ZoneSide.SUPPORT
        candidates: list[tuple[float, PriceZone]] = []
        for timeframe in (self.higher_minutes, self.decision_minutes):
            for zone in self.detectors[timeframe].active_zones(side=opposite):
                if zone.observed_time_ns >= observed_time_ns:
                    continue
                # A previously mitigated OB/FVG is not an unspent objective. It
                # may remain chart memory, but this family needs untouched future
                # space to define the trade before entry.
                if zone.first_touch_index is not None:
                    continue
                # Confirmation is observed only at the close. A target already
                # touched earlier inside that same candle is not available space.
                if side is ZoneSide.SUPPORT and zone.lower > max(entry, current.high):
                    candidates.append((zone.lower, zone))
                elif side is ZoneSide.RESISTANCE and zone.upper < min(entry, current.low):
                    candidates.append((zone.upper, zone))
        if not candidates:
            return None
        if side is ZoneSide.SUPPORT:
            price, zone = min(candidates, key=lambda item: item[0])
        else:
            price, zone = max(candidates, key=lambda item: item[0])
        return zone, price

    def _trigger_formation_touched_setup(self, trigger: PriceZone, setup: MTFSetup) -> bool:
        detector = self.detectors[self.trigger_minutes]
        return any(
            self._bar_touches_zone(detector.bars[index], setup.overlap.lower, setup.overlap.upper)
            for index in trigger.formation_indices
        )

    def _advance_setups(self, bar: Candle, trigger_index: int, created: list[PriceZone]) -> list[MTFTradePlan]:
        plans: list[MTFTradePlan] = []
        for setup in self.setups:
            if setup.state not in (SetupState.WAITING_INTERACTION, SetupState.WAITING_TRIGGER):
                continue
            if bar.ts_close_ns <= setup.observed_time_ns:
                continue
            if self._zone_invalidated_by_bar(setup.higher_zone, bar) or self._zone_invalidated_by_bar(
                setup.lower_zone,
                bar,
            ):
                setup.state = SetupState.INVALIDATED
                self._inc("setup_invalidated_before_entry")
                continue

            newly_interacted = False
            if setup.state is SetupState.WAITING_INTERACTION and self._bar_touches_zone(
                bar,
                setup.overlap.lower,
                setup.overlap.upper,
            ):
                setup.state = SetupState.WAITING_TRIGGER
                setup.interaction_time_ns = bar.ts_close_ns
                setup.interaction_trigger_index = trigger_index
                newly_interacted = True
                self._inc("setup_interaction")

            if setup.state is not SetupState.WAITING_TRIGGER or newly_interacted:
                # Zone contact and reversal confirmation must be two separately
                # observable closed-bar events, not one candle confirming itself.
                continue

            selected_trigger: PriceZone | None = None
            for trigger in created:
                if trigger.kind is not ZoneKind.ORDER_BLOCK or trigger.side is not setup.overlap.side:
                    continue
                if trigger.observed_time_ns <= (setup.interaction_time_ns or 0):
                    continue
                if not self._trigger_formation_touched_setup(trigger, setup):
                    continue
                # The OB chapter explicitly calls a >=2x body difference the
                # reliable form. The first diagnostic showed that accepting weak
                # engulfing candles doubled the trigger set, so this is restoring
                # the source definition rather than tuning a result threshold.
                if not trigger.high_quality_by_size:
                    self._inc("trigger_order_block_below_two_x")
                    continue
                selected_trigger = trigger
                break

            if selected_trigger is None:
                if self._departed_favorably_without_entry(setup, bar):
                    setup.state = SetupState.MISSED_WITHOUT_TRIGGER
                    self._inc("setup_missed_without_trigger")
                continue

            trigger = selected_trigger
            entry = bar.close
            stop = trigger.invalidation
            if setup.overlap.side is ZoneSide.SUPPORT and not stop < entry:
                setup.state = SetupState.TRIGGER_FAILED_GEOMETRY
                self._inc("trigger_invalid_long_geometry")
                continue
            if setup.overlap.side is ZoneSide.RESISTANCE and not entry < stop:
                setup.state = SetupState.TRIGGER_FAILED_GEOMETRY
                self._inc("trigger_invalid_short_geometry")
                continue
            target_result = self._opposite_target(setup.overlap.side, entry, bar, bar.ts_close_ns)
            if target_result is None:
                self._inc("trigger_without_fresh_preexisting_target")
                if self._departed_favorably_without_entry(setup, bar):
                    setup.state = SetupState.TARGET_SPENT
                continue
            target_zone, target = target_result
            risk = abs(entry - stop)
            reward = abs(target - entry)
            if risk <= 0.0 or reward <= 0.0:
                setup.state = SetupState.TRIGGER_FAILED_GEOMETRY
                self._inc("trigger_nonpositive_geometry")
                continue
            gross_rr = reward / risk
            if gross_rr + 1e-12 < self.minimum_gross_rr:
                self._inc("trigger_rr_below_minimum")
                if self._departed_favorably_without_entry(setup, bar):
                    setup.state = SetupState.MISSED_WITHOUT_TRIGGER
                continue
            self.sequence += 1
            side = Side.LONG if setup.overlap.side is ZoneSide.SUPPORT else Side.SHORT
            causal_event_id = f"{self.FAMILY}:{setup.setup_id}:{trigger.zone_id}"
            plan = MTFTradePlan(
                plan_id=f"ecv2-mtf-{self.symbol}-{self.sequence:08d}",
                causal_event_id=causal_event_id,
                symbol=self.symbol,
                family=self.FAMILY,
                side=side,
                observed_time_ns=bar.ts_close_ns,
                entry=entry,
                stop=stop,
                target=target,
                gross_rr=gross_rr,
                setup_id=setup.setup_id,
                higher_zone_id=setup.higher_zone.zone_id,
                higher_zone_kind=setup.higher_zone.kind,
                higher_strength_ratio=setup.higher_zone.strength_ratio,
                lower_zone_id=setup.lower_zone.zone_id,
                lower_zone_kind=setup.lower_zone.kind,
                lower_strength_ratio=setup.lower_zone.strength_ratio,
                trigger_zone_id=trigger.zone_id,
                trigger_strength_ratio=trigger.strength_ratio,
                target_zone_id=target_zone.zone_id,
                target_zone_kind=target_zone.kind,
                overlap_lower=setup.overlap.lower,
                overlap_upper=setup.overlap.upper,
                interaction_time_ns=setup.interaction_time_ns or bar.ts_close_ns,
                trigger_time_ns=bar.ts_close_ns,
            )
            setup.state = SetupState.PLANNED
            setup.trigger_zone_id = trigger.zone_id
            setup.higher_zone.consumed = True
            setup.lower_zone.consumed = True
            trigger.consumed = True
            self.plans.append(plan)
            plans.append(plan)
            self._inc("plan_created")
        return plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes not in self.detectors:
            raise ValueError(f"unsupported timeframe: {timeframe_minutes}")
        detector = self.detectors[timeframe_minutes]
        created = detector.on_bar(bar)
        if timeframe_minutes in (self.higher_minutes, self.decision_minutes):
            self._refresh_setups()
            return []
        trigger_index = len(detector.bars) - 1
        return self._advance_setups(bar, trigger_index, created)
