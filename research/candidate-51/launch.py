#!/usr/bin/env python3
"""Collision-safe Candidate 51 entry point over the reused Nautilus runner."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

# Candidate 51 must win ambiguous module names.  The vendored dependency paths
# remain available only for data/contracts/backtest helpers.
for path in (CANDIDATE05, CANDIDATE16, HERE):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

strategy_module = importlib.import_module("strategy")
resolved = Path(strategy_module.__file__).resolve()
expected = (HERE / "strategy.py").resolve()
if resolved != expected:
    raise RuntimeError(f"Candidate 51 strategy collision: {resolved} != {expected}")

import event_lifecycle_patch  # noqa: F401,E402 -- installs corrected callback

spec = importlib.util.spec_from_file_location("candidate51_direct_runner", HERE / "run.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 51 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
runner.main()
