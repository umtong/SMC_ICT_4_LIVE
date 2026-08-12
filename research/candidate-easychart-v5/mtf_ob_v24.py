"""Source-case 60m/15m/5m order-block confluence scenario.

The supplied EasyChart walkthrough assigns explicit timeframe roles and then
demonstrates a trade from an overlapping one-hour and 15-minute order block,
with a 5-minute order block refining the entry.  This module reuses the causal
v3 OB/FVG engine but narrows it to that demonstrated mechanism rather than
letting arbitrary mixed OB/FVG overlaps define context.

Scenario:
    fresh 60m OB + fresh 15m OB of the same side with a real price intersection
    -> first touch or sweep/reclaim of that intersection
    -> later event-local 5m OB formed through the context
    -> first later retest and reaction
    -> one predeclared full-position entry/stop/target plan.

At least one of the two contextual OBs and the 5m execution OB must meet the
source's 2x body-size quality cue.  No execution, risk, management, daily, time,
or trade-count rule changes.
"""
from __future__ import annotations

from typing import Any, Iterable

from domain import Candle
from easychart_mtf_scenario import MTFTradePlan, ScaleScenarioEngine, ScaleSetup
from easychart_zones import PriceZone, ZoneKind, overlap_zones


MTF_OB_SOURCE_RULE = (
    "SOURCE_EXPLICIT:OVERLAPPING_ONE_HOUR_AND_FIFTEEN_MINUTE_ORDER_BLOCKS_WITH_FIVE_MINUTE_ORDER_BLOCK_ENTRY"
)


class SourceMTFOrderBlockEngine(ScaleScenarioEngine):
    """Only the same-kind OB hierarchy demonstrated in the source case."""

    def _refresh_setups(self, event_time_ns: int) -> None:
        existing = {setup.setup_id for setup in self.setups}
        higher_detector = self.detectors[self.higher_minutes]
        decision_detector = self.detectors[self.decision_minutes]
        for higher in higher_detector.active_zones():
            if higher.kind is not ZoneKind.ORDER_BLOCK:
                continue
            for decision in decision_detector.active_zones():
                if decision.kind is not ZoneKind.ORDER_BLOCK:
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
                self._inc("source_mtf_ob_setup_created")
                self._trace(
                    "source_mtf_ob_setup_created",
                    event_time_ns,
                    setup,
                    provenance=MTF_OB_SOURCE_RULE,
                )

    def _event_local_trigger(
        self,
        setup: ScaleSetup,
        created: Iterable[PriceZone],
    ) -> PriceZone | None:
        confirmation_time = setup.confirmation_time_ns or 0
        candidates = [
            zone
            for zone in created
            if zone.kind is ZoneKind.ORDER_BLOCK
            and zone.side is setup.overlap.side
            and zone.observed_time_ns > confirmation_time
            and zone.high_quality_by_size
            and self._trigger_formation_touched_context(zone, setup)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda zone: (zone.observed_time_ns, zone.formed_index, zone.zone_id))
        return candidates[0]


class SourceMTFOrderBlockBundleV24:
    """One 60m/15m/5m OB scenario stream for one symbol."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        self.symbol = symbol
        self.macro = SourceMTFOrderBlockEngine(
            symbol,
            tick_size,
            scale_name="SOURCE_MTF_OB",
            higher_minutes=60,
            decision_minutes=15,
            trigger_minutes=5,
            minimum_gross_rr=minimum_gross_rr,
        )
        self.detectors = dict(self.macro.detectors)

    @property
    def setups(self):  # type: ignore[no-untyped-def]
        return self.macro.setups

    @property
    def plans(self):  # type: ignore[no-untyped-def]
        return self.macro.plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "source_mtf_ob": self.macro.diagnostics,
            "scenario_policy": {
                "name": "60M_OB_15M_OB_OVERLAP_5M_OB_FIRST_RETEST",
                "rule_provenance": MTF_OB_SOURCE_RULE,
            },
        }

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[MTFTradePlan]:
        if timeframe_minutes not in self.macro.detectors:
            return []
        return self.macro.on_bar(timeframe_minutes, bar)

    def drain_trace(self) -> list[dict[str, Any]]:
        return self.macro.drain_trace()

    def find_zone(self, zone_id: str):  # type: ignore[no-untyped-def]
        return self.macro.find_zone(zone_id)
