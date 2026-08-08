#!/usr/bin/env python3
"""Repair VCIB context/entry state namespaces without changing its market logic."""

from __future__ import annotations

from pathlib import Path


ENGINE = Path(__file__).with_name("volume_clock_impact_engine.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        if text.count(old) != 1:
            raise RuntimeError(f"{label}: expected exactly one old fragment, found {text.count(old)}")
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"{label}: neither the old nor repaired fragment is present")


def main() -> None:
    text = ENGINE.read_text(encoding="utf-8")

    helper = '''
    @staticmethod
    def _entry_scenario_id(episode: _Episode) -> str:
        """Return an execution namespace distinct from the market context."""
        return f"{episode.scenario_id}:ENTRY"

    @classmethod
    def _entry_transition(
        cls,
        episode: _Episode,
        snapshot: PrimitiveSnapshot,
        *,
        branch: str,
        signal: ScenarioSignal,
    ) -> ScenarioTransition:
        """Arm execution only after a completed causal branch confirmation."""
        return ScenarioTransition(
            scenario_id=cls._entry_scenario_id(episode),
            event_type="VCIB_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code=f"VCIB_{branch}_ENTRY_ARMED_AFTER_COMPLETED_RESPONSE",
            reference_price=signal.reference_entry,
            details={
                "context_scenario_id": episode.scenario_id,
                "family": signal.family,
                "direction": signal.direction,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "target_reason": signal.target_reason,
                "decision_ts_ns": snapshot.observation.ts_ns,
            },
        )
'''
    anchor = "\n    def _start_bucket(self, snapshot: PrimitiveSnapshot) -> bool:\n"
    if "def _entry_scenario_id" not in text:
        if text.count(anchor) != 1:
            raise RuntimeError("VCIB helper insertion anchor is not unique")
        text = text.replace(anchor, helper + anchor, 1)

    text = replace_once(
        text,
        "            scenario_id=episode.scenario_id,\n            family=family,",
        "            scenario_id=self._entry_scenario_id(episode),\n            family=family,",
        "signal entry namespace",
    )
    text = replace_once(
        text,
        '''            details={
                "first_bucket_end_ts_ns": first.end_ts_ns,''',
        '''            details={
                "context_scenario_id": episode.scenario_id,
                "first_bucket_end_ts_ns": first.end_ts_ns,''',
        "signal context identity",
    )

    exhaustion_old = '''                transition = self._transition(episode, "EXHAUSTION_CONTEXT", "EXHAUSTION_CONFIRMED", "MARGINAL_IMPACT_COLLAPSE_CONFIRMED_BY_OPPOSITE_RESPONSE", snapshot)
                signal = self._signal(episode, snapshot, branch="EXHAUSTION") if allow_new else None
                self._episode = None
                self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
                return ScenarioStep(transitions=(transition,), signal=signal)'''
    exhaustion_new = '''                context_transition = self._transition(
                    episode,
                    "EXHAUSTION_CONTEXT",
                    "EXHAUSTION_CONFIRMED",
                    "MARGINAL_IMPACT_COLLAPSE_CONFIRMED_BY_OPPOSITE_RESPONSE",
                    snapshot,
                )
                signal = self._signal(episode, snapshot, branch="EXHAUSTION") if allow_new else None
                transitions = (context_transition,)
                if signal is not None:
                    transitions += (
                        self._entry_transition(
                            episode,
                            snapshot,
                            branch="EXHAUSTION",
                            signal=signal,
                        ),
                    )
                self._episode = None
                self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
                return ScenarioStep(transitions=transitions, signal=signal)'''
    text = replace_once(text, exhaustion_old, exhaustion_new, "exhaustion entry arming")

    continuation_old = '''            transition = self._transition(episode, "CONTINUATION_RETEST", "CONTINUATION_CONFIRMED", "SEQUENTIAL_IMPACT_RETEST_HELD_AND_SEPARATE_RESPONSE_RESUMED", snapshot)
            signal = self._signal(episode, snapshot, branch="CONTINUATION") if allow_new else None
            self._episode = None
            self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
            return ScenarioStep(transitions=(transition,), signal=signal)'''
    continuation_new = '''            context_transition = self._transition(
                episode,
                "CONTINUATION_RETEST",
                "CONTINUATION_CONFIRMED",
                "SEQUENTIAL_IMPACT_RETEST_HELD_AND_SEPARATE_RESPONSE_RESUMED",
                snapshot,
            )
            signal = self._signal(episode, snapshot, branch="CONTINUATION") if allow_new else None
            transitions = (context_transition,)
            if signal is not None:
                transitions += (
                    self._entry_transition(
                        episode,
                        snapshot,
                        branch="CONTINUATION",
                        signal=signal,
                    ),
                )
            self._episode = None
            self._cooldown_until = snapshot.index + int(self.params.get("vcib_cooldown_bars", 2))
            return ScenarioStep(transitions=transitions, signal=signal)'''
    text = replace_once(text, continuation_old, continuation_new, "continuation entry arming")

    required = (
        "def _entry_scenario_id",
        'event_type="VCIB_ENTRY_TRANSITION"',
        'previous_state="IDLE"',
        'next_state="ENTRY_ARMED"',
        "scenario_id=self._entry_scenario_id(episode)",
        '"context_scenario_id": episode.scenario_id',
        "branch=\"EXHAUSTION\"",
        "branch=\"CONTINUATION\"",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise RuntimeError(f"VCIB repair invariant missing: {missing}")
    if 'return ScenarioStep(transitions=(transition,), signal=signal)' in text:
        raise RuntimeError("unrepaired same-namespace signal return remains")

    ENGINE.write_text(text, encoding="utf-8")
    print("VCIB context and entry namespaces are now causally separated")


if __name__ == "__main__":
    main()
