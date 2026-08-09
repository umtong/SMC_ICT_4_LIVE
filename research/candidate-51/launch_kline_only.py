#!/usr/bin/env python3
"""Collision-safe Candidate 51 launcher for price/volume-only policies."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"

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

import event_lifecycle_patch  # noqa: F401,E402
import kline_only_inputs  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "candidate51_kline_only_runner",
    HERE / "run.py",
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 51 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

# Replace only the input adapter.  The BacktestNode, fee/fill/latency models,
# account engine, strategy configuration and metric parser remain unchanged.
runner.load_range = kline_only_inputs.load_range
runner.main()
