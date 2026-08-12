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
from scenario_runtime_v4_preserved import (
    SameSidePreservingStructuralScenarioEngine,
)

NS = 60_000_000_000


def boundary(
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


def event(
    event_id: str,
    *,
    path: StructurePath,
    side: Side,
    primary_boundary_id: str,
    interaction_time_ns: int,
    reference_close: float,
    stop: float,
    target_boundary_id: str,
    structure_kind: StructureKind,
) -> StructureEvent:
    return StructureEvent(
        event_id=event_id,
        path=path,
        side=side,
        primary_boundary_id=primary_boundary_id,
        supporting_boundary_ids=(),
        interaction_index=1,
        interaction_time_ns=interaction_time_ns,
        interaction_extreme=reference_close,
        reference_close=reference_close,
        stop_reference=stop,
        target_boundary_id=target_boundary_id,
        target_price_at_interaction=None,
        origin_pivot_id=None,
        origin_price=None,
        structure_kind=structure_kind,
        channel_id=None,
        rule_provenance=(),
    )


class SameSideContextPreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SameSidePreservingStructuralScenarioEngine(
            "TEST",
            0.1,
            scale_name="MACRO",
            context_minutes=60,
            trigger_minutes=5,
            minimum_gross_rr=1.0,
        )
        items = (
            boundary(
                "OLD_SUPPORT",
                role=BoundaryRole.SUPPORT,
                price=9.5,
                kind=StructureKind.SWING_LOW,
            ),
            boundary(
                "BROKEN_RESISTANCE",
                role=BoundaryRole.RESISTANCE,
                price=10.0,
                kind=StructureKind.SWING_HIGH,
            ),
            boundary(
                "BROKEN_SUPPORT",
                role=BoundaryRole.SUPPORT,
                price=10.0,
                kind=StructureKind.SWING_LOW,
            ),
            boundary(
                "UP_TARGET",
                role=BoundaryRole.RESISTANCE,
                price=12.0,
                kind=StructureKind.SWING_HIGH,
            ),
            boundary(
                "DOWN_TARGET",
                role=BoundaryRole.SUPPORT,
                price=8.0,
                kind=StructureKind.SWING_LOW,
            ),
        )
        for item in items:
            self.engine.structure.boundaries[item.boundary_id] = item

        self.old_long = event(
            "OLD_LONG",
            path=StructurePath.BOUNCE,
            side=Side.LONG,
            primary_boundary_id="OLD_SUPPORT",
            interaction_time_ns=10 * NS,
            reference_close=10.0,
            stop=9.0,
            target_boundary_id="UP_TARGET",
            structure_kind=StructureKind.SWING_LOW,
        )
        self.new_long = event(
            "NEW_LONG_ACCEPTANCE",
            path=StructurePath.ACCEPTANCE,
            side=Side.LONG,
            primary_boundary_id="BROKEN_RESISTANCE",
            interaction_time_ns=20 * NS,
            reference_close=10.5,
            stop=9.0,
            target_boundary_id="UP_TARGET",
            structure_kind=StructureKind.SWING_HIGH,
        )
        self.new_short = event(
            "NEW_SHORT_ACCEPTANCE",
            path=StructurePath.ACCEPTANCE,
            side=Side.SHORT,
            primary_boundary_id="BROKEN_SUPPORT",
            interaction_time_ns=20 * NS,
            reference_close=9.5,
            stop=11.0,
            target_boundary_id="DOWN_TARGET",
            structure_kind=StructureKind.SWING_LOW,
        )
        self.engine._active_context_event = self.old_long
        self.engine._active_context_confirmed_time_ns = 10 * NS
        self.engine._active_context_basis = "LIVE_1H_EVENT:BOUNCE:SWING_LOW:OLD_LONG"

    @staticmethod
    def bar(minute: int, o: float, h: float, l: float, c: float) -> Candle:
        return Candle(minute * NS, o, h, l, c, 1.0)

    def test_same_side_pending_acceptance_preserves_confirmed_context(self) -> None:
        self.engine._arm_pending_acceptance(self.new_long)
        side, _basis, active, _confirmed = self.engine.context_state()
        self.assertIs(side, Side.LONG)
        self.assertIs(active, self.old_long)
        self.assertIs(self.engine._pending_acceptance_context, self.new_long)
        self.assertEqual(
            self.engine.diagnostics.get(
                "context_same_side_acceptance_pending_without_suspension",
            ),
            1,
        )

    def test_opposite_pending_acceptance_suspends_confirmed_context(self) -> None:
        self.engine._arm_pending_acceptance(self.new_short)
        side, basis, active, _confirmed = self.engine.context_state()
        self.assertIsNone(side)
        self.assertIsNone(active)
        self.assertEqual(basis, "UNRESOLVED_1H_EVENT_CONTEXT")
        self.assertIs(self.engine._pending_acceptance_context, self.new_short)

    def test_failed_same_side_retest_consumes_update_but_keeps_old_context(self) -> None:
        self.engine._arm_pending_acceptance(self.new_long)
        self.engine._resolve_pending_acceptance_retest(
            self.bar(25, 10.3, 10.4, 9.7, 9.9),
        )
        side, _basis, active, _confirmed = self.engine.context_state()
        self.assertIs(side, Side.LONG)
        self.assertIs(active, self.old_long)
        self.assertIsNone(self.engine._pending_acceptance_context)

    def test_confirmed_same_side_retest_replaces_old_context(self) -> None:
        self.engine._arm_pending_acceptance(self.new_long)
        self.engine._resolve_pending_acceptance_retest(
            self.bar(25, 10.4, 10.6, 9.98, 10.3),
        )
        side, basis, active, confirmed = self.engine.context_state()
        self.assertIs(side, Side.LONG)
        self.assertIs(active, self.new_long)
        self.assertEqual(confirmed, 25 * NS)
        self.assertTrue(basis.startswith("LIVE_1H_EVENT:ACCEPTANCE:"))


if __name__ == "__main__":
    unittest.main()
