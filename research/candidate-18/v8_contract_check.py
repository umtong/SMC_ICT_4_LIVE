#!/usr/bin/env python3
"""Minimal source contract for Candidate 18 v8."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "basis_dislocation_strategy.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
method = next(
    node
    for node in ast.walk(TREE)
    if isinstance(node, ast.FunctionDef) and node.name == "_submit_entry"
)
text = ast.unparse(method)
assert "route == 'SHOCK'" in text
assert "route != 'SUSTAINED'" in text
assert "setup.side * premium_index >= 0.0" in text
assert "basis_ready" in text
assert "premium_age_seconds" in text
assert "super()._submit_entry(setup, row)" in text
for forbidden in ("BacktestEngine", "MatchingEngine", "PortfolioSimulator", "AccountEngine"):
    assert forbidden not in SOURCE
print({"candidate18_v8_source_contract": "PASS"})
