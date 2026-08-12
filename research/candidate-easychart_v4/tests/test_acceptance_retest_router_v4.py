from __future__ import annotations

import unittest

from domain import Candle, Side
from market_structure import (
    BoundaryRole,
    StructuralBoundary,
    StructureEvent,
    StructureKind,
    StructurePath,
)
from scenario_runtime_v4_refined import RetestConfirmedStructuralScenarioEngine

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


class AcceptanceRetestRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RetestConfirmedStructuralScenarioEngine(
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

    def _bar(self, minute: int, o: float, h: float, l: float, c: float) -> Candle:
        return Candle(minute * NS, o, h, l, c, 1.0)

    def test_acceptance_is_not_directional_permission_before_retest(self) -> None:
        self.engine._create_setups((self.event,))
        side, basis, active, _time = self.engine.context_state()
        self.assertIsNone(side)
        self.assertIsNone(active)
        self.assertEqual(basis, "UNRESOLVED_1H_EVENT_CONTEXT")
        self.assertIs(self.engine._pending_acceptance_context, self.event)

    def test_first_later_retest_holding_new_support_activates_context(self) -> None:
        self.engine._create_setups((self.event,))
        self.engine._resolve_pending_acceptance_retest(
            self._bar(65, 10.4, 10.6, 9.98, 10.3),
        )
        side, basis, active, confirmed = self.engine.context_state()
        self.assertIs(side, Side.LONG)
        self.assertIs(active, self.event)
        self.assertEqual(confirmed, 65 * NS)
        self.assertTrue(basis.startswith("LIVE_1H_EVENT:ACCEPTANCE:"))

    def test_missed_level_remains_unresolved_and_does_not_chase(self) -> None:
        self.engine._create_setups((self.event,))
        self.engine._resolve_pending_acceptance_retest(
            self._bar(65, 10.6, 10.9, 10.2, 10.8),
        )
        self.assertIsNone(self.engine.context_state()[0])
        self.assertIs(self.engine._pending_acceptance_context, self.event)

    def test_failed_first_retest_consumes_context(self) -> None:
        self.engine._create_setups((self.event,))
        self.engine._resolve_pending_acceptance_retest(
            self._bar(65, 10.3, 10.4, 9.7, 9.9),
        )
        self.assertIsNone(self.engine.context_state()[0])
        self.assertIsNone(self.engine._pending_acceptance_context)
        self.engine._resolve_pending_acceptance_retest(
            self._bar(70, 10.2, 10.5, 9.98, 10.3),
        )
        self.assertIsNone(self.engine.context_state()[0])

    def test_target_spent_before_retest_consumes_context(self) -> None:
        self.engine._create_setups((self.event,))
        self.engine._resolve_pending_acceptance_retest(
            self._bar(65, 11.7, 12.1, 11.5, 11.9),
        )
        self.assertIsNone(self.engine.context_state()[0])
        self.assertIsNone(self.engine._pending_acceptance_context)


if __name__ == "__main__":
    unittest.main()
