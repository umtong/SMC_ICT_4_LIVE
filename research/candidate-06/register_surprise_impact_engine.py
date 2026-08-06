#!/usr/bin/env python3
"""Idempotently register SIAR in candidate-06's existing Nautilus selector."""

from __future__ import annotations

from pathlib import Path


ANCHOR = '''    if name == "ADAPTIVE_FRESH_HIERARCHICAL":
        from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine

        return AdaptiveFreshHierarchicalEngine(logic_params)
'''
INSERTION = ANCHOR + '''    if name == "SURPRISE_IMPACT_HIERARCHICAL":
        from surprise_impact_hierarchical_engine import SurpriseImpactHierarchicalEngine

        return SurpriseImpactHierarchicalEngine(logic_params)
'''


def main() -> int:
    path = Path(__file__).resolve().with_name("nautilus_strategy.py")
    text = path.read_text(encoding="utf-8")
    if 'name == "SURPRISE_IMPACT_HIERARCHICAL"' in text:
        return 0
    if ANCHOR not in text:
        raise RuntimeError("AFHR selector anchor changed; refusing ambiguous SIAR registration")
    path.write_text(text.replace(ANCHOR, INSERTION, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
