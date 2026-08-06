#!/usr/bin/env python3
"""Idempotently register SIPR in candidate-06's Nautilus selector."""

from __future__ import annotations

from pathlib import Path


ANCHOR = '''    if name == "ABSORPTION_CONFIRMED_STRUCTURE_REVERSAL":
        from absorption_structure_engine import AbsorptionConfirmedStructureReversalEngine

        return AbsorptionConfirmedStructureReversalEngine(logic_params)
'''
INSERTION = ANCHOR + '''    if name == "SEQUENTIAL_IMPACT_PERSISTENCE_RELAY":
        from sequential_impact_persistence_engine import SequentialImpactPersistenceRelayEngine

        return SequentialImpactPersistenceRelayEngine(logic_params)
'''


def main() -> int:
    path = Path(__file__).resolve().with_name("nautilus_strategy.py")
    text = path.read_text(encoding="utf-8")
    if 'name == "SEQUENTIAL_IMPACT_PERSISTENCE_RELAY"' in text:
        return 0
    if ANCHOR not in text:
        raise RuntimeError("ACSR selector anchor changed; refusing ambiguous SIPR registration")
    path.write_text(text.replace(ANCHOR, INSERTION, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
