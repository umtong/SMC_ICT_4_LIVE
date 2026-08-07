#!/usr/bin/env python3
"""Materialize UOAM sources and apply the narrow objective-ledger namespace repair."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import zlib


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label} anchor changed; refusing ambiguous UOAM repair")
    return text.replace(old, new, 1)


def _patch_engine(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        '''    @staticmethod\n    def _objective_transition(\n''',
        '''    @staticmethod\n    def _objective_scenario_id(context_id: str) -> str:\n        \"\"\"Return an objective ledger namespace independent of the parent bias.\"\"\"\n\n        return f\"{context_id}:UOAM-OBJECTIVE\"\n\n    @staticmethod\n    def _objective_transition(\n''',
        "objective namespace helper",
    )
    text = _replace_once(
        text,
        '''                    scenario_id=bias.context_id,\n                    previous_state="BIAS_ACTIVE",\n                    next_state="RESET",\n                    reason="NO_PREEXISTING_UNRESOLVED_OBJECTIVE",\n''',
        '''                    scenario_id=self._objective_scenario_id(bias.context_id),\n                    previous_state="IDLE",\n                    next_state="RESET",\n                    reason="NO_PREEXISTING_UNRESOLVED_OBJECTIVE",\n''',
        "no-objective transition",
    )
    text = _replace_once(
        text,
        '''                scenario_id=bias.context_id,\n                previous_state="BIAS_ACTIVE",\n                next_state="OBJECTIVE_ACTIVE",\n                reason="PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND",\n''',
        '''                scenario_id=self._objective_scenario_id(bias.context_id),\n                previous_state="IDLE",\n                next_state="OBJECTIVE_ACTIVE",\n                reason="PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND",\n''',
        "objective binding transition",
    )
    text = _replace_once(
        text,
        '''                    scenario_id=bias.context_id,\n                    previous_state=("OBJECTIVE_ENTRY_ARMED" if objective.entry_armed else "OBJECTIVE_ACTIVE"),\n                    next_state="OBJECTIVE_CONSUMED",\n''',
        '''                    scenario_id=self._objective_scenario_id(bias.context_id),\n                    previous_state=("OBJECTIVE_ENTRY_ARMED" if objective.entry_armed else "OBJECTIVE_ACTIVE"),\n                    next_state="OBJECTIVE_CONSUMED",\n''',
        "objective consumption transition",
    )
    text = _replace_once(
        text,
        '''                        scenario_id=bias.context_id,\n                        previous_state="OBJECTIVE_CONSUMED",\n                        next_state="OBJECTIVE_ACTIVE",\n''',
        '''                        scenario_id=self._objective_scenario_id(bias.context_id),\n                        previous_state="OBJECTIVE_CONSUMED",\n                        next_state="OBJECTIVE_ACTIVE",\n''',
        "objective ladder transition",
    )
    text = _replace_once(
        text,
        '''                transitions.append(\n                    self._objective_transition(\n                        scenario_id=bias.context_id,\n                        previous_state="OBJECTIVE_ACTIVE",\n                        next_state="RESET",\n                        reason="UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED",\n''',
        '''                objective = self._current_objective()\n                objective_previous_state = (\n                    "OBJECTIVE_ENTRY_ARMED"\n                    if objective is not None and objective.entry_armed\n                    else "OBJECTIVE_ACTIVE"\n                )\n                transitions.append(\n                    self._objective_transition(\n                        scenario_id=self._objective_scenario_id(bias.context_id),\n                        previous_state=objective_previous_state,\n                        next_state="RESET",\n                        reason="UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED",\n''',
        "origin invalidation transition",
    )
    text = _replace_once(
        text,
        '''            scenario_id=bias.context_id,\n            previous_state="OBJECTIVE_ACTIVE",\n            next_state="OBJECTIVE_ENTRY_ARMED",\n''',
        '''            scenario_id=self._objective_scenario_id(bias.context_id),\n            previous_state="OBJECTIVE_ACTIVE",\n            next_state="OBJECTIVE_ENTRY_ARMED",\n''',
        "entry-armed transition",
    )
    path.write_text(text, encoding="utf-8")


def _patch_test(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    marker = "    def test_objective_ledger_uses_independent_namespace(self):\n"
    if marker in text:
        return
    anchor = "    def test_consumption_advances_ladder_then_ends_context(self):\n"
    if anchor not in text:
        raise RuntimeError("objective lifecycle test anchor changed")
    tests = '''    def test_objective_ledger_uses_independent_namespace(self):\n        engine = self.engine()\n        engine._bias = bias("LONG")\n        engine._liquidity_pools = [_LiquidityPool("UPPER", 118.0, 1, 8)]\n        transitions = engine._bind_objective_ladder(\n            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),\n            engine._bias,\n        )\n        transition = transitions[0]\n        self.assertEqual(transition.scenario_id, "BIAS-1:UOAM-OBJECTIVE")\n        self.assertNotEqual(transition.scenario_id, engine._bias.context_id)\n        self.assertEqual(transition.previous_state, "IDLE")\n        self.assertEqual(transition.next_state, "OBJECTIVE_ACTIVE")\n\n    def test_entry_armed_origin_invalidation_uses_objective_state(self):\n        engine = self.engine()\n        engine._bias = bias("LONG")\n        engine._objective_context_id = "BIAS-1"\n        engine._liquidity_pools = [_LiquidityPool("UPPER", 118.0, 1, 8)]\n        engine._bind_objective_ladder(\n            bar(10, open_=101.0, high=110.0, low=100.0, close=109.0),\n            engine._bias,\n        )\n        engine._current_objective().entry_armed = True\n        step = engine._advance_bias(\n            snap(11, 11, open_=105.0, high=105.5, low=100.5, close=100.8),\n        )\n        resets = [\n            value for value in step.transitions\n            if value.event_type == "UOAM_OBJECTIVE_TRANSITION"\n            and value.reason_code == "UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED"\n        ]\n        self.assertEqual(len(resets), 1)\n        self.assertEqual(resets[0].previous_state, "OBJECTIVE_ENTRY_ARMED")\n        self.assertTrue(resets[0].scenario_id.endswith(":UOAM-OBJECTIVE"))\n\n'''
    path.write_text(text.replace(anchor, tests + anchor, 1), encoding="utf-8")


def main() -> int:
    here = Path(__file__).resolve().parent
    parts = sorted(here.glob("uoam_payload.part-*"))
    if not parts:
        raise RuntimeError("UOAM payload parts missing")
    encoded = "".join(p.read_text(encoding="ascii").strip() for p in parts)
    payload = json.loads(zlib.decompress(base64.b64decode(encoded)).decode("utf-8"))
    for name, value in payload.items():
        destination = here / name
        if not destination.exists():
            destination.write_bytes(base64.b64decode(value))
    _patch_engine(here / "objective_lifecycle_engine.py")
    _patch_test(here / "test_objective_lifecycle_engine.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
