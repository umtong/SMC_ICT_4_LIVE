#!/usr/bin/env python3
"""Idempotently register AFHR in candidate-06's existing Nautilus strategy selector."""

from __future__ import annotations

from pathlib import Path


ANCHOR = '''    if name == "HIERARCHICAL_MULTI_LIQUIDITY":
        from hierarchical_multi_liquidity_engine import HierarchicalMultiLiquidityEngine

        return HierarchicalMultiLiquidityEngine(logic_params)
'''
INSERTION = ANCHOR + '''    if name == "ADAPTIVE_FRESH_HIERARCHICAL":
        from adaptive_fresh_hierarchical_engine import AdaptiveFreshHierarchicalEngine

        return AdaptiveFreshHierarchicalEngine(logic_params)
'''


def main() -> int:
    path = Path(__file__).resolve().with_name("nautilus_strategy.py")
    text = path.read_text(encoding="utf-8")
    if 'name == "ADAPTIVE_FRESH_HIERARCHICAL"' in text:
        return 0
    if ANCHOR not in text:
        raise RuntimeError("candidate-06 scenario selector anchor changed; refusing ambiguous edit")
    path.write_text(text.replace(ANCHOR, INSERTION, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
