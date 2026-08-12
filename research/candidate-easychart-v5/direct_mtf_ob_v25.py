"""Direct 15m/5m order-block overlap entry from the supplied walkthrough.

The actual EasyChart example pre-marks the intersection of a 15-minute bullish
order block and a 5-minute bullish order block, waits for price to reach it,
and adds at the touch.  A one-minute trend-line break is described as an
alternative entry for a trader who missed that area, not as a mandatory extra
confirmation.

Because the project forbids scale-in, this module selects the stronger planned
15m/5m overlap as the single entry opportunity:

    fresh same-side 15m OB and 5m OB with a real intersection
    -> at least one OB satisfies the source 2x body-size cue
    -> first later 1m touch which has not already broken the OB invalidation
    -> one full-position market entry after the completed 1m touch bar
    -> causal OB-wick stop and nearest pre-existing opposing objective.

No FVG/OB kind-count scoring, direction filter, partial management, daily gate,
time exit, trade-count limit, or risk change is introduced.
"""
from __future__ import annotations

from typing import Any

from domain import Candle, Side
from easychart_mtf_scenario import (
    MTFTradePlan,
    ScaleScenarioEngine,
    ScaleSetup,
    ScenarioPath,
    SetupState,
)
from easychart_zones import ZoneKind, ZoneSide, overlap_zones


DIRECT_MTF_OB_RULE = (
    "SOURCE_EXPLICIT:FIFTEEN_MINUTE_AND_FIVE_MINUTE_ORDER_BLOCK_INTERSECTION_IS_PLANNED_ENTRY_AREA"
)
DIRECT_TOUCH_RULE = (
    "SOURCE_EXPLICIT:IF_THE_PREPLANNED_ORDER_BLOCK_AREA_IS_REACHED_ENTER_OTHERWISE_DO_NOT_CHASE"
)


class DirectMTFOrderBlockEngine(ScaleScenarioEngine):
    """One 15m/5m OB context, resolved on the first later 1m touch."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(
            symbol,
            tick_size,
            scale_name="DIRECT_MTF_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )

    def _refresh_setups(self, event_time_ns: int) -> None:
        existing = {setup.setup_id for setup in self.setups}
        for higher in self.detectors[self.higher_minutes].active_zones():
            if higher.kind is not ZoneKind.ORDER_BLOCK:
                continue
            for decision in self.detectors[self.decision_minutes].active_zones():
                if decision.kind is not ZoneKind.ORDER_BLOCK:
                    continue
                if higher.side is not decision.side:
                    continue
                if not (higher.high_quality_by_size or decision.high_quality_by_size):
                    continue
                if higher.first_touch_index is not None or decision.first_touch_index is not None:
                    continue
                overlap = overlap_zones(higher, decision)
                if overlap is None:
                    continue
                setup_id = self._setup_id(overlap)
                if setup_id in existing:
                    continue
                setup = ScaleSetup(
                    setup_id=setup_id,
                    scale_name=self.scale_name,
                    overlap=overlap,
                    higher_zone=higher,
                    lower_zone=decision,
                    observed_time_ns=overlap.observed_time_ns,
                )
                self.setups.append(setup)
                existing.add(setup_id)
                self._inc("direct_mtf_ob_setup_created")
                self._trace(
                    "direct_mtf_ob_setup_created",
                    event_time_ns,
                    setup,
                    provenance=(DIRECT_MTF_OB_RULE, DIRECT_TOUCH_RULE),
                )

    def _candidate_touch(self, setup: ScaleSetup, previous: Candle, bar: Candle) -> bool:
        if setup.overlap.side is ZoneSide.SUPPORT:
            approached = previous.close > setup.overlap.upper
            touched = bar.low <= setup.overlap.upper
            not_accepted_through = bar.close >= setup.overlap.lower
        else:
            approached = previous.close < setup.overlap.lower
            touched = bar.high >= setup.overlap.lower
            not_accepted_through = bar.close <= setup.overlap.upper
        return approached and touched and not_accepted_through

    def _direct_stop(self, setup: ScaleSetup) -> float:
        if setup.overlap.side is ZoneSide.SUPPORT:
            return min(setup.higher_zone.invalidation, setup.lower_zone.invalidation)
        return max(setup.higher_zone.invalidation, setup.lower_zone.invalidation)

    def _plan_first_touches(self, bar: Candle, index: int) -> list[MTFTradePlan]:
        if index <= 0:
            return []
        previous = self.detectors[self.trigger_minutes].bars[index - 1]
        candidates: list[ScaleSetup] = []
        for setup in self.setups:
            if setup.state is not SetupState.WAITING_INTERACTION:
                continue
            if bar.ts_close_ns <= setup.observed_time_ns:
                continue
            if not self._context_still_available_before_interaction(setup):
                self._finish(setup, SetupState.INVALIDATED, bar, "context_spent_before_direct_touch")
                continue
            if self._candidate_touch(setup, previous, bar):
                candidates.append(setup)

        # Nested OB intersections at the same event are one causal opportunity.
        selected_by_side: dict[ZoneSide, ScaleSetup] = {}
        for setup in sorted(candidates, key=self._interaction_key):
            selected_by_side.setdefault(setup.overlap.side, setup)
        selected_ids = {setup.setup_id for setup in selected_by_side.values()}
        for setup in candidates:
            if setup.setup_id not in selected_ids:
                self._finish(
                    setup,
                    SetupState.DUPLICATE_EPISODE,
                    bar,
                    "nested_direct_mtf_ob_context_collapsed",
                )

        plans: list[MTFTradePlan] = []
        for setup in selected_by_side.values():
            side = self._side_for_context(setup.overlap.side)
            stop = self._direct_stop(setup)
            # A plan cannot use a stop already traded inside the completed touch bar.
            stop_already_breached = (
                bar.low <= stop if side is Side.LONG else bar.high >= stop
            )
            if stop_already_breached:
                self._finish(
                    setup,
                    SetupState.INVALIDATED,
                    bar,
                    "ob_invalidation_traded_on_first_touch",
                    stop=stop,
                    touch_low=bar.low,
                    touch_high=bar.high,
                )
                continue

            setup.path = (
                ScenarioPath.REJECTION
                if (
                    bar.low < setup.overlap.lower
                    if side is Side.LONG
                    else bar.high > setup.overlap.upper
                )
                else ScenarioPath.TOUCH
            )
            setup.interaction_time_ns = bar.ts_close_ns
            setup.interaction_trigger_index = index
            setup.interaction_extreme = bar.low if side is Side.LONG else bar.high
            setup.confirmation_time_ns = bar.ts_close_ns
            setup.trigger_zone = setup.lower_zone
            setup.trigger_zone_id = setup.lower_zone.zone_id
            setup.trigger_time_ns = setup.lower_zone.observed_time_ns
            setup.trigger_index = index

            plan = self._make_plan(setup, bar, bar.close, stop)
            if plan is not None:
                plans.append(plan)
                self._inc("direct_mtf_ob_first_touch_plan")
                self._trace(
                    "direct_mtf_ob_first_touch_plan",
                    bar.ts_close_ns,
                    setup,
                    plan_id=plan.plan_id,
                    provenance=(DIRECT_MTF_OB_RULE, DIRECT_TOUCH_RULE),
                )
        return plans

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes not in self.detectors:
            raise ValueError(f"unsupported timeframe: {timeframe_minutes}")
        for objective_timeframe, objective in self.objectives.items():
            if timeframe_minutes == objective_timeframe:
                objective.on_bar(bar)
            else:
                objective.observe_price(bar)
        detector = self.detectors[timeframe_minutes]
        detector.on_bar(bar)
        if timeframe_minutes in (self.higher_minutes, self.decision_minutes):
            self._refresh_setups(bar.ts_close_ns)
            return []
        index = len(detector.bars) - 1
        return self._plan_first_touches(bar, index)


class DirectMTFOrderBlockBundleV25:
    """One direct source-case family for one symbol."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.engine = DirectMTFOrderBlockEngine(symbol, tick_size, minimum_gross_rr)
        self.detectors = dict(self.engine.detectors)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.engine.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.engine.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "direct_mtf_ob": self.engine.diagnostics,
            "scenario_policy": {
                "name": "15M_OB_5M_OB_OVERLAP_FIRST_1M_TOUCH",
                "rule_provenance": (DIRECT_MTF_OB_RULE, DIRECT_TOUCH_RULE),
            },
        }

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes not in self.engine.detectors:
            return []
        return self.engine.on_bar(timeframe_minutes, bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        return self.engine.drain_trace()

    def find_zone(self, zone_id: str):  # type: ignore[no-untyped-def]
        return self.engine.find_zone(zone_id)
