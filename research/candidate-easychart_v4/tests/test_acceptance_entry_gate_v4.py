from __future__ import annotations

import unittest

from domain import Candle, Side
from easychart_zones import PriceZone, ZoneKind, ZoneSide
from market_structure import (
    BoundaryRole,
    StructuralBoundary,
    StructureEvent,
    StructureKind,
    StructurePath,
)
from scenario_bundle_v4 import StructuralSetupState
from scenario_runtime_v4_acceptance_gate import (
    SourceFaithfulRetestEntryGatedEngine,
)

NS = 60_000_000_000


def horizontal(
    boundary_id: str,
    *,
    role: BoundaryRole,
    price: float,
    kind: StructureKind,
) -> StructuralBoundary:
    return StructuralBoundary(
        boundary_id=boundary_id,
        kind=kind,
        role=role,
        timeframe_minutes=60,
        observed_time_ns=NS,
        observed_index=0,
        anchor_1_time_ns=NS,
        anchor_1_price=price,
        anchor_2_time_ns=NS,
        anchor_2_price=price,
        strength_ratio=2.0,
        pivot_span=2,
        active=False,
    )


class AcceptanceEntryGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SourceFaithfulRetestEntryGatedEngine(
            "TEST",
            0.1,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=1.0,
        )
        broken = horizontal(
            "BROKEN_RESISTANCE",
            role=BoundaryRole.RESISTANCE,
            price=10.0,
            kind=StructureKind.SWING_HIGH,
        )
        target = horizontal(
            "TARGET_RESISTANCE",
            role=BoundaryRole.RESISTANCE,
            price=12.0,
            kind=StructureKind.SWING_HIGH,
        )
        self.engine.structure.boundaries[broken.boundary_id] = broken
        self.engine.structure.boundaries[target.boundary_id] = target
        self.event = StructureEvent(
            event_id="TEST:60m:ACCEPTANCE:1",
            path=StructurePath.ACCEPTANCE,
            side=Side.LONG,
            primary_boundary_id=broken.boundary_id,
            supporting_boundary_ids=(),
            interaction_index=1,
            interaction_time_ns=60 * NS,
            interaction_extreme=10.8,
            reference_close=10.5,
            stop_reference=9.0,
            target_boundary_id=target.boundary_id,
            target_price_at_interaction=12.0,
            origin_pivot_id=None,
            origin_price=9.1,
            structure_kind=StructureKind.SWING_HIGH,
            channel_id=None,
            rule_provenance=(),
        )

    @staticmethod
    def _bar(minute: int, o: float, h: float, l: float, c: float) -> Candle:
        return Candle(minute * NS, o, h, l, c, 1.0)

    @staticmethod
    def _zone(observed_time_ns: int) -> PriceZone:
        return PriceZone(
            zone_id=f"TEST:5m:FVG:SUPPORT:{observed_time_ns}",
            kind=ZoneKind.FVG,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=5,
            lower=10.1,
            upper=10.2,
            invalidation=9.8,
            impulse_extreme=10.9,
            formed_index=1,
            formed_time_ns=observed_time_ns,
            observed_time_ns=observed_time_ns,
            formation_indices=(),
            strength_ratio=3.0,
        )

    def test_acceptance_cannot_arm_displacement_before_structural_retest(self) -> None:
        self.engine._create_setups((self.event,))
        setup = self.engine._active[self.engine._setup_id_for_event(self.event)]
        bar = self._bar(62, 10.4, 10.9, 10.2, 10.7)
        self.engine._arm_displacement(setup, bar, 1, (self._zone(bar.ts_close_ns),))
        self.assertEqual(setup.state, StructuralSetupState.WAITING_DISPLACEMENT)
        self.assertEqual(setup.trigger_zones, ())
        self.assertNotIn(setup.setup_id, self.engine._acceptance_entry_ready)

    def test_holding_structural_retest_unlocks_same_bar_displacement(self) -> None:
        self.engine._create_setups((self.event,))
        setup = self.engine._active[self.engine._setup_id_for_event(self.event)]
        bar = self._bar(65, 10.4, 10.8, 9.98, 10.3)
        self.engine._resolve_pending_acceptance_retest(bar)
        self.assertIn(setup.setup_id, self.engine._acceptance_entry_ready)
        self.engine._arm_displacement(setup, bar, 2, (self._zone(bar.ts_close_ns),))
        self.assertEqual(setup.state, StructuralSetupState.WAITING_RETEST)
        self.assertEqual(len(setup.trigger_zones), 1)
        self.assertEqual(setup.trigger_armed_time_ns, bar.ts_close_ns)

    def test_failed_first_structural_retest_terminates_trade_setup(self) -> None:
        self.engine._create_setups((self.event,))
        setup_id = self.engine._setup_id_for_event(self.event)
        setup = self.engine._active[setup_id]
        self.engine._resolve_pending_acceptance_retest(
            self._bar(65, 10.3, 10.4, 9.7, 9.9),
        )
        self.assertNotIn(setup_id, self.engine._active)
        self.assertEqual(setup.state, StructuralSetupState.FIRST_RETEST_UNRESOLVED)
        self.assertNotIn(setup_id, self.engine._acceptance_entry_ready)

    def test_missed_boundary_stays_pending_and_entry_stays_blocked(self) -> None:
        self.engine._create_setups((self.event,))
        setup = self.engine._active[self.engine._setup_id_for_event(self.event)]
        bar = self._bar(65, 10.6, 10.9, 10.2, 10.8)
        self.engine._resolve_pending_acceptance_retest(bar)
        self.assertIs(self.engine._pending_acceptance_context, self.event)
        self.engine._arm_displacement(setup, bar, 2, (self._zone(bar.ts_close_ns),))
        self.assertEqual(setup.state, StructuralSetupState.WAITING_DISPLACEMENT)
        self.assertEqual(setup.trigger_zones, ())

    def test_target_spent_before_structural_retest_terminates_setup(self) -> None:
        self.engine._create_setups((self.event,))
        setup_id = self.engine._setup_id_for_event(self.event)
        setup = self.engine._active[setup_id]
        self.engine._resolve_pending_acceptance_retest(
            self._bar(65, 11.7, 12.1, 11.5, 11.9),
        )
        self.assertNotIn(setup_id, self.engine._active)
        self.assertEqual(setup.state, StructuralSetupState.TARGET_SPENT)


if __name__ == "__main__":
    unittest.main()
