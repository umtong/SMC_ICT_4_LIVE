#!/usr/bin/env python3
"""Idempotently register ACSR and apply its controlled state-chain repair."""

from __future__ import annotations

from pathlib import Path


ANCHOR = '''    if name == "SURPRISE_IMPACT_HIERARCHICAL":
        from surprise_impact_hierarchical_engine import SurpriseImpactHierarchicalEngine

        return SurpriseImpactHierarchicalEngine(logic_params)
'''
INSERTION = ANCHOR + '''    if name == "ABSORPTION_CONFIRMED_STRUCTURE_REVERSAL":
        from absorption_structure_engine import AbsorptionConfirmedStructureReversalEngine

        return AbsorptionConfirmedStructureReversalEngine(logic_params)
'''

STATE_CHAIN_OLD = '''        self._bias_sequence += 1
        direction = anchor.reversal_direction
        context_id = f"ACSR-BIAS-{bar.end_ts_ns}-{self._bias_sequence:06d}"
'''
STATE_CHAIN_NEW = '''        self._bias_sequence += 1
        direction = anchor.reversal_direction
        # Preserve one causal state chain from ABSORPTION_ARMED into BIAS_ACTIVE.
        # The event recorder keys state by scenario_id, so a new ID here would
        # incorrectly make the transition appear to start from IDLE.
        context_id = anchor.anchor_id
'''


def _repair_state_chain(candidate_dir: Path) -> None:
    """Repair only the scenario identity; no market or execution rule changes."""

    path = candidate_dir / "absorption_structure_engine.py"
    text = path.read_text(encoding="utf-8")
    if STATE_CHAIN_NEW in text:
        return
    if STATE_CHAIN_OLD not in text:
        raise RuntimeError("ACSR state-chain anchor changed; refusing ambiguous repair")
    path.write_text(text.replace(STATE_CHAIN_OLD, STATE_CHAIN_NEW, 1), encoding="utf-8")


def _register_selector(candidate_dir: Path) -> None:
    path = candidate_dir / "nautilus_strategy.py"
    text = path.read_text(encoding="utf-8")
    if 'name == "ABSORPTION_CONFIRMED_STRUCTURE_REVERSAL"' in text:
        return
    if ANCHOR not in text:
        raise RuntimeError("SIAR selector anchor changed; refusing ambiguous ACSR registration")
    path.write_text(text.replace(ANCHOR, INSERTION, 1), encoding="utf-8")


def main() -> int:
    candidate_dir = Path(__file__).resolve().parent
    _repair_state_chain(candidate_dir)
    _register_selector(candidate_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
