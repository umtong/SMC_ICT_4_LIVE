#!/usr/bin/env python3
from __future__ import annotations

from lrb_types import BarObservation, PrimitiveSnapshot, ScenarioSignal, ScenarioStep
from pressure_gated_hml_engine import PressureGatedHierarchicalEngine
from pressure_state_tracker import PressureState

MINUTE = 60_000_000_000


def snapshot(index: int = 10) -> PrimitiveSnapshot:
    observation = BarObservation(
        ts_ns=(index + 1) * MINUTE,
        open=100.0,
        high=100.5,
        low=99.8,
        close=100.4,
        volume=100.0,
        taker_buy_volume=70.0,
        trades=10,
    )
    return PrimitiveSnapshot(
        index=index,
        observation=observation,
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=0.4,
        body_atr=0.4,
        range_atr=0.7,
        upper_wick_fraction=0.1,
        lower_wick_fraction=0.1,
        close_location=(100.4 - 99.8) / 0.7,
        upper_fast=None,
        lower_fast=None,
        upper_slow=None,
        lower_slow=None,
        slow_mid=None,
        range_position=None,
        upper_pool_touches=0,
        lower_pool_touches=0,
    )


class FakeHML:
    def __init__(self, signal: ScenarioSignal):
        self.signal = signal

    def observe(self, _snapshot: PrimitiveSnapshot, *, allow_new: bool = True) -> ScenarioStep:
        return ScenarioStep(signal=self.signal if allow_new else None)

    def abort_active(self, _snapshot: PrimitiveSnapshot, _reason: str) -> ScenarioStep:
        return ScenarioStep()


snap = snapshot()
signal = ScenarioSignal(
    scenario_id="HML-TEST",
    family="HML",
    direction="LONG",
    observed_ts_ns=snap.observation.ts_ns,
    reference_entry=100.4,
    stop_price=99.4,
    target_price=102.0,
    target_reason="TEST",
    atr=1.0,
    liquidity_level=100.0,
    details={},
)
params = {
    "phml_use_pressure_gate": True,
    "phml_use_pressure_exit": True,
    "phml_flow_history": 120,
    "phml_minimum_history": 60,
}
engine = PressureGatedHierarchicalEngine(params)
engine._hml = FakeHML(signal)
engine._pressure.state = PressureState(
    scenario_id="PRESSURE-TEST",
    direction="LONG",
    created_index=1,
    created_ts_ns=MINUTE,
    origin=99.0,
    onset_close=100.0,
    onset_score=5.0,
)
step = engine.observe(snap, allow_new=True)
assert step.signal is not None
assert step.signal.family == "PHML"
assert step.signal.details["causal_exit_open_position"] is True
assert "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM" in step.signal.details["causal_exit_reason_codes"]

engine._pressure.state.direction = "SHORT"
rejected = engine.observe(snapshot(11), allow_new=True)
assert rejected.signal is None
assert any(
    transition.reason_code == "HML_SIGNAL_REJECTED_WITHOUT_ALIGNED_LIVE_PRESSURE_REGIME"
    for transition in rejected.transitions
)
print("pressure-gated HML signal and exit-contract tests passed")
