#!/usr/bin/env python3
"""Repair SPRC context/entry namespaces and live-context state logging."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parent
BASE = ROOT / "sequential_pressure_regime_engine.py"
LIVE = ROOT / "sequential_pressure_live_engine.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        if text.count(old) != 1:
            raise RuntimeError(f"{label}: expected one old fragment, found {text.count(old)}")
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"{label}: neither old nor repaired fragment is present")


def repair_base() -> None:
    text = BASE.read_text(encoding="utf-8")
    helper = '''
    @staticmethod
    def _entry_scenario_id(state: _FlowState) -> str:
        """Return an execution namespace distinct from the pressure context."""
        return f"{state.scenario_id}:ENTRY"

    @classmethod
    def _entry_transition(
        cls,
        state: _FlowState,
        snapshot: PrimitiveSnapshot,
        signal: ScenarioSignal,
    ) -> ScenarioTransition:
        """Arm execution only after the completed pullback/resumption sequence."""
        return ScenarioTransition(
            scenario_id=cls._entry_scenario_id(state),
            event_type="SPRC_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code="SPRC_ENTRY_ARMED_AFTER_COMPLETED_PRESSURE_RESUMPTION",
            reference_price=signal.reference_entry,
            details={
                "context_scenario_id": state.scenario_id,
                "family": signal.family,
                "direction": signal.direction,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "target_reason": signal.target_reason,
                "decision_ts_ns": snapshot.observation.ts_ns,
            },
        )
'''
    anchor = "\n    def _reset(self, snapshot: PrimitiveSnapshot, reason: str) -> ScenarioStep:\n"
    if "def _entry_scenario_id" not in text:
        if text.count(anchor) != 1:
            raise RuntimeError("SPRC helper insertion anchor is not unique")
        text = text.replace(anchor, helper + anchor, 1)

    text = replace_once(
        text,
        "            scenario_id=state.scenario_id,\n            family=\"SPRC\",",
        "            scenario_id=self._entry_scenario_id(state),\n            family=\"SPRC\",",
        "SPRC signal entry namespace",
    )
    text = replace_once(
        text,
        '''            details={
                "pressure_origin": state.origin,''',
        '''            details={
                "context_scenario_id": state.scenario_id,
                "pressure_origin": state.origin,''',
        "SPRC signal context identity",
    )

    old = '''        signal = self._build_signal(state, snapshot) if allow_new else None
        self._state = None
        self._opposite_cusum = 0.0
        self._cooldown_until = snapshot.index + int(self.params.get("sprc_cooldown_bars", 2))
        return ScenarioStep(transitions=(transition,), signal=signal)'''
    new = '''        signal = self._build_signal(state, snapshot) if allow_new else None
        transitions = (transition,)
        if signal is not None:
            transitions += (self._entry_transition(state, snapshot, signal),)
        self._state = None
        self._opposite_cusum = 0.0
        self._cooldown_until = snapshot.index + int(self.params.get("sprc_cooldown_bars", 2))
        return ScenarioStep(transitions=transitions, signal=signal)'''
    text = replace_once(text, old, new, "SPRC entry arming")

    required = (
        "def _entry_scenario_id",
        'event_type="SPRC_ENTRY_TRANSITION"',
        'previous_state="IDLE"',
        'next_state="ENTRY_ARMED"',
        "scenario_id=self._entry_scenario_id(state)",
        '"context_scenario_id": state.scenario_id',
        "transitions += (self._entry_transition(state, snapshot, signal),)",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise RuntimeError(f"SPRC base repair invariant missing: {missing}")
    BASE.write_text(text, encoding="utf-8")


def repair_live() -> None:
    text = LIVE.read_text(encoding="utf-8")
    old = '''        step = super()._advance(snapshot, z_score, allow_new=allow_new)
        if step.signal is not None and state_before is not None:
            state_before.state = "POSITION_CONTEXT"
            self._state = state_before
            self._opposite_cusum = 0.0
        return step'''
    new = '''        step = super()._advance(snapshot, z_score, allow_new=allow_new)
        if step.signal is not None and state_before is not None:
            position_transition = self._transition(
                state_before,
                "CONTINUATION_CONFIRMED",
                "POSITION_CONTEXT",
                "CONFIRMED_PRESSURE_REGIME_PRESERVED_FOR_CAUSAL_EXIT_MONITORING",
                snapshot,
                {"entry_scenario_id": step.signal.scenario_id},
            )
            context_transitions = tuple(
                item for item in step.transitions if item.scenario_id == state_before.scenario_id
            )
            entry_transitions = tuple(
                item for item in step.transitions if item.scenario_id != state_before.scenario_id
            )
            state_before.state = "POSITION_CONTEXT"
            self._state = state_before
            self._opposite_cusum = 0.0
            return ScenarioStep(
                transitions=(*context_transitions, position_transition, *entry_transitions),
                signal=step.signal,
            )
        return step'''
    text = replace_once(text, old, new, "SPRC live context transition")
    required = (
        '"CONTINUATION_CONFIRMED"',
        '"POSITION_CONTEXT"',
        "CONFIRMED_PRESSURE_REGIME_PRESERVED_FOR_CAUSAL_EXIT_MONITORING",
        "context_transitions = tuple(",
        "entry_transitions = tuple(",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise RuntimeError(f"SPRC live repair invariant missing: {missing}")
    LIVE.write_text(text, encoding="utf-8")


def main() -> None:
    repair_base()
    repair_live()
    print("SPRC context, live-context and entry namespaces are causally separated")


if __name__ == "__main__":
    main()
