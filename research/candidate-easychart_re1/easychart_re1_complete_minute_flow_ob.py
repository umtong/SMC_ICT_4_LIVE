"""Flow-validated 15-minute OBs using every completed constituent minute.

Multi-timeframe bars with the same close timestamp are dispatched from higher to
lower timeframe.  The prior formation validator therefore saw only the first
fourteen one-minute observations of a fifteen-minute displacement when the
15-minute OB was created.  That was causal but incomplete.

This module queues a newly observed 15-minute OB, lets the one-minute bar with
the same close timestamp update the causal flow analyzer, then validates and
publishes the OB for future interactions.  It changes no entry, stop, target,
routing or threshold responsibility.
"""
from __future__ import annotations

from typing import Any

from domain import Candle
from easychart_re1_flow_ob import (
    FlowValidatedOrderBlockDecisionStructureBook,
)
from easychart_re1_flow_ob_responsibility import (
    EasyChartRE1ResponsibleFlowOBBundle,
    ResponsibleFlowValidatedDecisionAreaEngine,
)
from easychart_zones import PriceZone, ZoneKind


class CompleteMinuteFlowValidatedOBBook(FlowValidatedOrderBlockDecisionStructureBook):
    """Publish a formation only after its final constituent 1m close is observed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_flow_zones: list[PriceZone] = []
        self._pending_counts: dict[str, int] = {}

    def _pinc(self, key: str) -> None:
        self._pending_counts[key] = self._pending_counts.get(key, 0) + 1

    def _register(self, zone: PriceZone) -> None:
        if (
            zone.kind is not ZoneKind.ORDER_BLOCK
            or not zone.high_quality_by_size
            or zone.zone_id in self._source_ids
            or any(item.zone_id == zone.zone_id for item in self._pending_flow_zones)
        ):
            return
        self._pending_flow_zones.append(zone)
        self._pinc("formation_waiting_final_constituent_minute")

    def flush_pending(self, time_ns: int) -> None:
        ready = [
            zone for zone in self._pending_flow_zones if zone.observed_time_ns <= time_ns
        ]
        self._pending_flow_zones = [
            zone for zone in self._pending_flow_zones if zone.observed_time_ns > time_ns
        ]
        for zone in ready:
            before = len(self.flow_evidence)
            FlowValidatedOrderBlockDecisionStructureBook._register(self, zone)
            if len(self.flow_evidence) > before:
                self._pinc("formation_validated_after_complete_minute_set")
            else:
                self._pinc("formation_rejected_after_complete_minute_set")

    @property
    def flow_validation_diagnostics(self) -> dict[str, Any]:
        output = dict(super().flow_validation_diagnostics)
        output["complete_minute_set"] = {
            "counts": dict(sorted(self._pending_counts.items())),
            "pending_at_end": len(self._pending_flow_zones),
            "policy": "VALIDATE_AFTER_FINAL_CONSTITUENT_ONE_MINUTE_CLOSE",
        }
        return output


class CompleteMinuteFlowDecisionAreaEngine(ResponsibleFlowValidatedDecisionAreaEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.structure = CompleteMinuteFlowValidatedOBBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
            self.flow_analyzer,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle):  # type: ignore[no-untyped-def]
        plans = super().on_bar(timeframe_minutes, bar)
        if timeframe_minutes == self.trigger_minutes:
            # FlowEntryMixin has now observed this completed one-minute bar.
            self.structure.flush_pending(bar.ts_close_ns)
        return plans


class EasyChartRE1CompleteMinuteFlowOBBundle(EasyChartRE1ResponsibleFlowOBBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.flow_decision_ob = CompleteMinuteFlowDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["flow_decision_ob"] = 0


MultiScaleScenarioBundle = EasyChartRE1CompleteMinuteFlowOBBundle
