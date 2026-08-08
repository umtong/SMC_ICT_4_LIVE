#!/usr/bin/env python3
"""Collision-safe and diagnostic-preserving Candidate 35 entry point."""
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
    raise RuntimeError(f"Candidate 35 strategy collision: {resolved} != {expected}")

policy_module = importlib.import_module("strategy_v2")
policy_resolved = Path(policy_module.__file__).resolve()
policy_expected = (HERE / "strategy_v2.py").resolve()
if policy_resolved != policy_expected:
    raise RuntimeError(
        f"Candidate 35b policy collision: {policy_resolved} != {policy_expected}",
    )

import event_lifecycle_patch  # noqa: F401,E402 -- installs corrected callback

spec = importlib.util.spec_from_file_location("candidate35_direct_runner", HERE / "run.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 35 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

_original_load_inputs = runner.load_inputs


def _diagnostic_load_inputs(*, start, end, cache, output):
    klines, feature_paths, records = _original_load_inputs(
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


runner.load_inputs = _diagnostic_load_inputs
runner.main()
