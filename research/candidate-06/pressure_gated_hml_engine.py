"""Hierarchical multi-liquidity entries gated by a live pressure regime."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from pressure_state_tracker import PressureStateTracker


class PressureGatedHierarchicalEngine:
    def __init__(self, params: Mapping[str, Any]):
        self.params = dict(params)
        self._hml = HierarchicalMultiLiquidityEngine(params)
        self._pressure = PressureStateTracker(params)

    def _rejection_transition(
        self,
        signal: ScenarioSignal,
        snapshot: PrimitiveSnapshot,
        active_direction: str | None,
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=signal.scenario_id,
            event_type="PHML_SIGNAL_DECISION",
            previous_state="ENTRY_ARMED",
            next_state="RESET",
            reason_code="HML_SIGNAL_REJECTED_WITHOUT_ALIGNED_LIVE_PRESSURE_REGIME",
            reference_price=signal.reference_entry,
            details={
                "signal_direction": signal.direction,
                "active_pressure_direction": active_direction,
                "observed_ts_ns": snapshot.observation.ts_ns,
            },
        )

    def observe(self, snapshot: PrimitiveSnapshot, *, allow_new: bool = True) -> ScenarioStep:
        pressure_transitions = self._pressure.update(snapshot)
        hml_step = self._hml.observe(snapshot, allow_new=allow_new)
        transitions = [*pressure_transitions, *hml_step.transitions]
        signal = hml_step.signal
        if signal is None:
            return ScenarioStep(transitions=tuple(transitions))

        use_gate = bool(self.params.get("phml_use_pressure_gate", True))
        use_exit = bool(self.params.get("phml_use_pressure_exit", True))
        active = self._pressure.active_direction
        if use_gate and active != signal.direction:
            transitions.append(
                self._rejection_transition(signal, snapshot, active),
            )
            return ScenarioStep(transitions=tuple(transitions))

        details = dict(signal.details)
        details.update(
            {
                "pressure_gate_enabled": use_gate,
                "pressure_exit_enabled": use_exit,
                "active_pressure_direction": active,
            },
        )
        if use_exit:
            existing_codes = tuple(
                str(value)
                for value in details.get("causal_exit_reason_codes", ())
            )
            details["causal_exit_reason_codes"] = tuple(
                dict.fromkeys(
                    (
                        *existing_codes,
                        "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM",
                        "PRESSURE_REGIME_ORIGIN_LOST",
                        "PRESSURE_REGIME_EXPIRED",
                    ),
                ),
            )
            details["causal_exit_open_position"] = True
        resolved = replace(signal, family="PHML", details=details)
        transitions.append(
            ScenarioTransition(
                scenario_id=signal.scenario_id,
                event_type="PHML_SIGNAL_DECISION",
                previous_state="ENTRY_ARMED",
                next_state="PRESSURE_ALIGNED_ENTRY_ARMED",
                reason_code="HML_SIGNAL_ALIGNED_WITH_LIVE_SEQUENTIAL_PRESSURE_REGIME",
                reference_price=signal.reference_entry,
                details={
                    "direction": signal.direction,
                    "pressure_gate_enabled": use_gate,
                    "pressure_exit_enabled": use_exit,
                    "active_pressure_direction": active,
                },
            ),
        )
        return ScenarioStep(transitions=tuple(transitions), signal=resolved)

    def abort_active(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:
        hml_step = self._hml.abort_active(snapshot, reason)
        return ScenarioStep(transitions=hml_step.transitions)
