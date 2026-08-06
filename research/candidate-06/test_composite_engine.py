from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from composite_engine import MultiTimescaleLiquidityRelayEngine
from lrb_types import BarObservation, PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


class _Fake:
    def __init__(self, signal: ScenarioSignal | None, name: str):
        self.signal = signal
        self.name = name
        self.aborted = False

    def observe(self, snapshot, *, allow_new):
        transition = ScenarioTransition(
            scenario_id=self.name,
            event_type="SCENARIO_TRANSITION",
            previous_state="OBSERVE",
            next_state="ENTRY_ARMED" if self.signal else "OBSERVE",
            reason_code=f"{self.name}_OBSERVE",
            reference_price=snapshot.observation.close,
            details={},
        )
        return ScenarioStep(transitions=(transition,), signal=self.signal)

    def abort_active(self, snapshot, reason):
        self.aborted = True
        return ScenarioStep(
            transitions=(
                ScenarioTransition(
                    scenario_id=self.name,
                    event_type="SCENARIO_TRANSITION",
                    previous_state="OBSERVE",
                    next_state="RESET",
                    reason_code=reason,
                    reference_price=snapshot.observation.close,
                    details={},
                ),
            )
        )


def signal(identifier: str) -> ScenarioSignal:
    return ScenarioSignal(
        scenario_id=identifier,
        family="SRR",
        direction="LONG",
        observed_ts_ns=60_000_000_000,
        reference_entry=100.0,
        stop_price=99.0,
        target_price=102.0,
        target_reason="TEST",
        atr=1.0,
        liquidity_level=99.5,
        details={},
    )


def snapshot() -> PrimitiveSnapshot:
    return PrimitiveSnapshot(
        index=1,
        observation=BarObservation(60_000_000_000, 100.0, 101.0, 99.0, 100.0, 1000.0, 500.0, 100),
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=0.0,
        body_atr=0.0,
        range_atr=2.0,
        upper_wick_fraction=0.5,
        lower_wick_fraction=0.5,
        close_location=0.5,
        upper_fast=101.0,
        lower_fast=99.0,
        upper_slow=102.0,
        lower_slow=98.0,
        slow_mid=100.0,
        range_position=0.5,
        upper_pool_touches=1,
        lower_pool_touches=1,
    )


class CompositeArbiterTests(unittest.TestCase):
    def test_external_session_has_fixed_priority_when_both_emit(self) -> None:
        engine = MultiTimescaleLiquidityRelayEngine.__new__(MultiTimescaleLiquidityRelayEngine)
        session = _Fake(signal("SESSION"), "SESSION")
        auction = _Fake(signal("AUCTION"), "AUCTION")
        engine._session = session
        engine._auction = auction
        result = engine.observe(snapshot(), allow_new=True)
        self.assertIsNotNone(result.signal)
        assert result.signal is not None
        self.assertEqual(result.signal.scenario_id, "SESSION")
        self.assertTrue(auction.aborted)
        self.assertFalse(session.aborted)
        self.assertEqual(result.transitions[-1].reason_code, "ARBITER_EXTERNAL_SESSION_SIGNAL_SELECTED")


if __name__ == "__main__":
    unittest.main()
