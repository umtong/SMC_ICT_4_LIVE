#!/usr/bin/env python3
"""Idempotently register ACSR in candidate-06's existing Nautilus selector."""

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


def main() -> int:
    path = Path(__file__).resolve().with_name("nautilus_strategy.py")
    text = path.read_text(encoding="utf-8")
    if 'name == "ABSORPTION_CONFIRMED_STRUCTURE_REVERSAL"' in text:
        return 0
    if ANCHOR not in text:
        raise RuntimeError("SIAR selector anchor changed; refusing ambiguous ACSR registration")
    path.write_text(text.replace(ANCHOR, INSERTION, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
