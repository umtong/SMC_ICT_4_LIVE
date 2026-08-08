#!/usr/bin/env python3
"""Collision-safe entry point for Candidate 35.

Several inherited candidates expose top-level modules named ``strategy`` and
``backtest``. Candidate 35 intentionally reuses those dependencies, so the
launcher imports this candidate's strategy before the runner adds dependency
paths. Nautilus' importable strategy resolver then receives the already
validated local module rather than another candidate's implementation.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

for path in (CANDIDATE05, CANDIDATE16, HERE):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

module = importlib.import_module("strategy")
resolved = Path(module.__file__).resolve()
expected = (HERE / "strategy.py").resolve()
if resolved != expected:
    raise RuntimeError(f"Candidate 35 strategy collision: {resolved} != {expected}")

runpy.run_path(str(HERE / "run.py"), run_name="__main__")
