#!/usr/bin/env python3
"""Collision-safe Candidate 47 entry point over the verified runner."""
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
    raise RuntimeError(f"Candidate 47 strategy collision: {resolved} != {expected}")

import event_lifecycle_patch  # noqa: F401,E402 -- installs corrected callback

spec = importlib.util.spec_from_file_location(
    "candidate47_direct_runner",
    HERE / "run.py",
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 47 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

original_load_inputs = runner.load_inputs


def diagnostic_load_inputs(*, start, end, cache, output):
    klines, feature_paths, records = original_load_inputs(
        start=start,
        end=end,
        cache=cache,
        output=output,
    )
    for symbol, frame in klines.items():
        destination = output / "source" / symbol / "klines.csv.gz"
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(destination, index=False, compression="gzip")
    return klines, feature_paths, records


runner.load_inputs = diagnostic_load_inputs
runner.main()
