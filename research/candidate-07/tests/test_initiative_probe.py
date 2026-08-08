from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from initiative_probe import (  # noqa: E402
    PersistentInitiativeAuctionGate,
    ProbeOutcome,
    ShadowReversalProbe,
)
from model import Direction, ScenarioKind, TradePlan  # noqa: E402


def plan(
    *,
    scenario_id: str = "probe",
    direction: Direction = Direction.SHORT,
    liquidity: float = 101.0,
) -> TradePlan:
    if direction is Direction.SHORT:
        entry, stop, target = 100.0, 102.0, 96.0
    else:
        entry, stop, target = 100.0, 98.0, 104.0
    return TradePlan(
        scenario_id=scenario_id,
        kind=ScenarioKind.ABSORPTION_RECLAIM,
        direction=direction,
        observed_time_ns=10,
        entry_reference=entry,
        stop_price=stop,
        target_price=target,
        liquidity_level=liquidity,
        expected_rr=2.0,
        details={"atr": 1.0},
    )


class PersistentInitiativeGateTests(unittest.TestCase):
    def test_source_reclaim_is_notice_not_release(self) -> None:
        gate = PersistentInitiativeAuctionGate()
        state = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            source_scenario_id="failed-short",
            accepted_source_level=101.0,
            accepted_at_ns=1,
        )
        self.assertEqual(gate.observe_close(100.9), ())
        self.assertTrue(gate.source_reclaimed)
        self.assertEqual(gate.actual_state(Direction.SHORT), state)
        self.assertEqual(gate.consume_source_reclaim_notice(), state)
        self.assertIsNone(gate.consume_source_reclaim_notice())

    def test_default_block_check_can_be_deferred_without_losing_state(self) -> None:
        gate = PersistentInitiativeAuctionGate()
        state = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.LONG,
            source_scenario_id="failed-long",
            accepted_source_level=99.0,
            accepted_at_ns=2,
        )
        gate.defer_blocking = True
        self.assertIsNone(gate.state(Direction.LONG))
        self.assertEqual(gate.actual_state(Direction.LONG), state)
        gate.defer_blocking = False
        self.assertEqual(gate.state(Direction.LONG), state)

    def test_opposite_failure_atomically_transfers_global_owner(self) -> None:
        gate = PersistentInitiativeAuctionGate()
        old = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            source_scenario_id="old",
            accepted_source_level=101.0,
            accepted_at_ns=1,
        )
        new, displaced = gate.transfer_on_failed_reversal(
            blocked_reversal_direction=Direction.LONG,
            source_scenario_id="new",
            accepted_source_level=99.0,
            accepted_at_ns=2,
        )
        self.assertEqual(displaced, old)
        self.assertEqual(gate.actual_state(Direction.LONG), new)
        self.assertIsNone(gate.actual_state(Direction.SHORT))

    def test_counter_acceptance_releases_only_blocked_direction(self) -> None:
        gate = PersistentInitiativeAuctionGate()
        state = gate.accept_failed_reversal(
            blocked_reversal_direction=Direction.SHORT,
            source_scenario_id="owner",
            accepted_source_level=101.0,
            accepted_at_ns=1,
        )
        self.assertIsNone(gate.observe_counter_acceptance(Direction.LONG))
        self.assertEqual(gate.observe_counter_acceptance(Direction.SHORT), state)
        self.assertIsNone(gate.actual_state())


class ShadowReversalProbeTests(unittest.TestCase):
    def test_short_probe_target_first_proves_counter_initiative(self) -> None:
        probe = ShadowReversalProbe(
            plan=plan(direction=Direction.SHORT),
            owner_scenario_id="owner",
            armed_at_ns=10,
        )
        waiting = probe.observe_close(99.0)
        terminal = probe.observe_close(96.0)
        self.assertEqual(waiting.outcome, ProbeOutcome.WAITING)
        self.assertEqual(terminal.outcome, ProbeOutcome.TARGET_DELIVERED)
        self.assertEqual(terminal.bars_seen, 2)

    def test_short_probe_stop_first_reconfirms_long_initiative(self) -> None:
        probe = ShadowReversalProbe(
            plan=plan(direction=Direction.SHORT),
            owner_scenario_id="owner",
            armed_at_ns=10,
        )
        terminal = probe.observe_close(102.1)
        self.assertEqual(terminal.outcome, ProbeOutcome.STOP_ACCEPTED)
        self.assertEqual(probe.continuation_direction, Direction.LONG)

    def test_long_probe_is_symmetric(self) -> None:
        target_probe = ShadowReversalProbe(
            plan=plan(direction=Direction.LONG, liquidity=99.0),
            owner_scenario_id="owner",
            armed_at_ns=10,
        )
        stop_probe = ShadowReversalProbe(
            plan=plan(
                scenario_id="stop",
                direction=Direction.LONG,
                liquidity=99.0,
            ),
            owner_scenario_id="owner",
            armed_at_ns=10,
        )
        self.assertEqual(
            target_probe.observe_close(104.1).outcome,
            ProbeOutcome.TARGET_DELIVERED,
        )
        self.assertEqual(
            stop_probe.observe_close(97.9).outcome,
            ProbeOutcome.STOP_ACCEPTED,
        )
        self.assertEqual(stop_probe.continuation_direction, Direction.SHORT)

    def test_only_more_extreme_same_direction_source_replaces_probe(self) -> None:
        short_probe = ShadowReversalProbe(
            plan=plan(direction=Direction.SHORT, liquidity=101.0),
            owner_scenario_id="owner",
            armed_at_ns=10,
        )
        self.assertTrue(
            short_probe.new_source_is_more_extreme(
                plan(scenario_id="higher", direction=Direction.SHORT, liquidity=102.0)
            )
        )
        self.assertFalse(
            short_probe.new_source_is_more_extreme(
                plan(scenario_id="lower", direction=Direction.SHORT, liquidity=100.5)
            )
        )
        long_probe = ShadowReversalProbe(
            plan=plan(direction=Direction.LONG, liquidity=99.0),
            owner_scenario_id="owner",
            armed_at_ns=10,
        )
        self.assertTrue(
            long_probe.new_source_is_more_extreme(
                plan(scenario_id="lower-long", direction=Direction.LONG, liquidity=98.0)
            )
        )


if __name__ == "__main__":
    unittest.main()
