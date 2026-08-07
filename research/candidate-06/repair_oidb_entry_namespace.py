from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parent
    engine_path = root / "open_interest_deleveraging_engine.py"
    text = engine_path.read_text(encoding="utf-8")

    helper = '''    @staticmethod
    def _entry_scenario_id(wave: _Wave) -> str:
        return f"{wave.scenario_id}:ENTRY"

    @classmethod
    def _entry_transition(
        cls,
        wave: _Wave,
        *,
        reason: str,
        reference: float,
        details: Mapping[str, Any],
    ) -> ScenarioTransition:
        return ScenarioTransition(
            scenario_id=cls._entry_scenario_id(wave),
            event_type="OIDB_ENTRY_TRANSITION",
            previous_state="IDLE",
            next_state="ENTRY_ARMED",
            reason_code=reason,
            reference_price=reference,
            details={
                "context_scenario_id": wave.scenario_id,
                "branch": wave.branch,
                **dict(details),
            },
        )

'''
    text = replace_once(
        text,
        "    def _prune(self, index: int) -> None:\n",
        helper + "    def _prune(self, index: int) -> None:\n",
        "entry namespace helper",
    )

    old_reversal = '''        transition = self._transition(
            wave,
            "DELEVERAGING_WAVE_OBSERVED",
            wave.state,
            "DELEVERAGING_EXHAUSTION_AND_OPPOSITE_RECLAIM_CONFIRMED",
            obs.close,
            {"stop": stop, "target": target[0], "target_reason": target[1]},
        )
        signal = ScenarioSignal(
            scenario_id=wave.scenario_id,
'''
    new_reversal = '''        context_transition = self._transition(
            wave,
            "DELEVERAGING_WAVE_OBSERVED",
            wave.state,
            "DELEVERAGING_EXHAUSTION_AND_OPPOSITE_RECLAIM_CONFIRMED",
            obs.close,
            {"stop": stop, "target": target[0], "target_reason": target[1]},
        )
        entry_transition = self._entry_transition(
            wave,
            reason="DELEVERAGING_EXHAUSTION_ENTRY_ARMED",
            reference=obs.close,
            details={"stop": stop, "target": target[0], "target_reason": target[1]},
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(wave),
'''
    text = replace_once(text, old_reversal, new_reversal, "reversal entry namespace")

    old_continuation = '''        transition = self._transition(
            wave,
            "DELEVERAGING_WAVE_OBSERVED",
            wave.state,
            "OPEN_INTEREST_CONTRACTION_PERSISTED_WITH_PRICE_DISCOVERY",
            obs.close,
            {"stop": stop, "target": target},
        )
        signal = ScenarioSignal(
            scenario_id=wave.scenario_id,
'''
    new_continuation = '''        context_transition = self._transition(
            wave,
            "DELEVERAGING_WAVE_OBSERVED",
            wave.state,
            "OPEN_INTEREST_CONTRACTION_PERSISTED_WITH_PRICE_DISCOVERY",
            obs.close,
            {"stop": stop, "target": target},
        )
        entry_transition = self._entry_transition(
            wave,
            reason="DELEVERAGING_PERSISTENCE_ENTRY_ARMED",
            reference=obs.close,
            details={"stop": stop, "target": target},
        )
        signal = ScenarioSignal(
            scenario_id=self._entry_scenario_id(wave),
'''
    text = replace_once(text, old_continuation, new_continuation, "continuation entry namespace")

    old_return = "        return ScenarioStep(transitions=(transition,), signal=signal)\n"
    if text.count(old_return) != 2:
        raise RuntimeError(f"signal return: expected two matches, found {text.count(old_return)}")
    text = text.replace(
        old_return,
        "        return ScenarioStep(transitions=(context_transition, entry_transition), signal=signal)\n",
    )
    engine_path.write_text(text, encoding="utf-8")

    workflow_path = root.parent.parent / ".github" / "workflows" / "candidate-06-oidb.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    workflow = replace_once(
        workflow,
        '''          test -s research/candidate-06/oidb_payload.py
          test -s research/candidate-06/oidb_payload.chunk-00
          python research/candidate-06/oidb_payload.py
''',
        '''          if [ -s research/candidate-06/oidb_payload.py ]; then
            test -s research/candidate-06/oidb_payload.chunk-00
            python research/candidate-06/oidb_payload.py
          else
            test -s research/candidate-06/open_interest_deleveraging_engine.py
            test -s research/candidate-06/run_open_interest_deleveraging_matrix.py
            echo "OIDB source already materialized"
          fi
''',
        "workflow materialization fallback",
    )
    workflow = replace_once(
        workflow,
        "          git rm -f research/candidate-06/oidb_payload.py research/candidate-06/oidb_payload.chunk-*\n",
        "          git rm -f research/candidate-06/oidb_payload.py research/candidate-06/oidb_payload.chunk-* 2>/dev/null || true\n",
        "workflow payload cleanup",
    )
    workflow_path.write_text(workflow, encoding="utf-8")
    print("OIDB context and entry ledger namespaces repaired")


if __name__ == "__main__":
    main()
