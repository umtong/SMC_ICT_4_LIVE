"""Structure-zone target adapter for the rejection-only micro-footprint core.

The first implementation correctly discovered pre-existing one-minute OB/FVG
objectives, but stored the detector's PriceZone directly in ScenarioSetup.  The
shared execution geometry expects a StructureZone because moving channel targets
and static horizontal targets share one contract.  This module changes only that
representation: the chosen immutable PriceZone is snapshotted as a horizontal
StructureZone before plan creation.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import ScenarioSetup, StructureFamily, StructureZone, V5TradePlan
from domain import Candle
from easychart_re1_rejection_micro_target import (
    FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
    EasyChartRE1RejectionMicroTargetBundle,
    RejectionTargetDecisionOBEngine,
    RejectionTargetDirectSweepEngine,
    RejectionTargetMajorSwingEngine,
    RejectionTargetMicroEngine,
)
from easychart_zones import PriceZone


class StructureZoneMicroFootprintTargetMixin:
    """Snapshot the selected detector zone into the shared target contract."""

    @staticmethod
    def _target_snapshot(zone: PriceZone, time_ns: int) -> StructureZone:
        return StructureZone(
            zone_id=f"MICRO_OBJECTIVE:{zone.zone_id}:SNAP:{time_ns}",
            kind=zone.kind,
            family=StructureFamily.HORIZONTAL,
            side=zone.side,
            timeframe_minutes=1,
            lower=zone.lower,
            upper=zone.upper,
            invalidation=zone.invalidation,
            impulse_extreme=zone.impulse_extreme,
            formed_index=zone.formed_index,
            formed_time_ns=zone.formed_time_ns,
            observed_time_ns=zone.observed_time_ns,
            formation_indices=tuple(zone.formation_indices),
            strength_ratio=zone.strength_ratio,
            source_structure_id=f"MICRO_OBJECTIVE:{zone.zone_id}",
            source_pivot_span=1,
            first_touch_index=zone.first_touch_index,
            first_touch_time_ns=zone.first_touch_time_ns,
            invalidated_index=zone.invalidated_index,
            invalidated_time_ns=zone.invalidated_time_ns,
            consumed=zone.consumed,
        )

    def _refine_micro_footprint_target(
        self,
        setup: ScenarioSetup,
        bar: Candle,
    ) -> None:
        if setup.target_price is None:
            return
        candidates = self._eligible_micro_targets(setup, bar)
        if not candidates:
            self._mft_inc("no_preexisting_unspent_micro_footprint")
            return
        selected = (
            min(
                candidates,
                key=lambda item: (
                    item[1],
                    item[0].observed_time_ns,
                    item[0].zone_id,
                ),
            )
            if setup.side.name == "LONG"
            else max(
                candidates,
                key=lambda item: (
                    item[1],
                    -item[0].observed_time_ns,
                    item[0].zone_id,
                ),
            )
        )
        source_zone, price = selected
        if not self._closer(setup.side, price, setup.target_price):
            self._mft_inc("coarser_objective_already_nearer")
            return
        previous_id = None if setup.target_zone is None else setup.target_zone.zone_id
        previous_price = setup.target_price
        snapshot = self._target_snapshot(source_zone, bar.ts_close_ns)
        setup.target_zone = snapshot
        setup.target_price = price
        self._audit(snapshot)
        self._mft_inc("objective_replaced_by_micro_footprint")
        self._trace(
            "objective_replaced_by_micro_footprint",
            bar.ts_close_ns,
            setup,
            previous_target_zone_id=previous_id,
            previous_target_price=previous_price,
            target_zone_id=snapshot.zone_id,
            target_zone_kind=source_zone.kind.value,
            target_price=price,
            target_observed_time_ns=source_zone.observed_time_ns,
            target_strength_ratio=source_zone.strength_ratio,
            rule_provenance=FIRST_UNSPENT_MICRO_FOOTPRINT_TARGET_RULE,
        )


class FixedRejectionTargetMicroEngine(
    StructureZoneMicroFootprintTargetMixin,
    RejectionTargetMicroEngine,
):
    pass


class FixedRejectionTargetMajorSwingEngine(
    StructureZoneMicroFootprintTargetMixin,
    RejectionTargetMajorSwingEngine,
):
    pass


class FixedRejectionTargetDecisionOBEngine(
    StructureZoneMicroFootprintTargetMixin,
    RejectionTargetDecisionOBEngine,
):
    pass


class FixedRejectionTargetDirectSweepEngine(
    StructureZoneMicroFootprintTargetMixin,
    RejectionTargetDirectSweepEngine,
):
    pass


class EasyChartRE1RejectionMicroTargetV2Bundle(
    EasyChartRE1RejectionMicroTargetBundle,
):
    """Rejection-only target core with target type compatible with execution."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs: dict[str, Any] = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = FixedRejectionTargetMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = FixedRejectionTargetMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.flow_decision_ob = FixedRejectionTargetDecisionOBEngine(
            symbol,
            tick_size,
            scale_name="FLOW_DECISION_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.direct_sweep_ob = FixedRejectionTargetDirectSweepEngine(
            symbol,
            tick_size,
            scale_name="DIRECT_SWEEP_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "major_swing", "flow_decision_ob", "direct_sweep_ob"):
            self._audit_offsets[key] = 0


MultiScaleScenarioBundle = EasyChartRE1RejectionMicroTargetV2Bundle
