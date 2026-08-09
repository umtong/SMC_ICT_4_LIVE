#!/usr/bin/env python3
"""Run the inverse-price short mirror through the verified Nautilus runner."""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
os.environ["CANDIDATE47_PRICE_ONLY_INPUTS"] = "1"
for path in (HERE.parent / "candidate-05", HERE.parent / "candidate-16", HERE):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

router_module = importlib.import_module("router")
if Path(router_module.__file__).resolve() != (HERE / "router.py").resolve():
    raise RuntimeError(f"Candidate 47 router collision: {router_module.__file__}")
strategy_module = importlib.import_module("ichifan_inverse_short_strategy")
if Path(strategy_module.__file__).resolve() != (HERE / "ichifan_inverse_short_strategy.py").resolve():
    raise RuntimeError(f"Candidate 47 inverse-short strategy collision: {strategy_module.__file__}")
sys.modules["strategy"] = strategy_module

import event_lifecycle_patch  # noqa: F401,E402

spec = importlib.util.spec_from_file_location("candidate47_inverse_short_runner", HERE / "run.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 47 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
original_build_metrics = runner.build_metrics


def build_metrics(**kwargs):
    metrics = original_build_metrics(**kwargs)
    metrics["candidate"] = "candidate-47-public-ichiv2-inverse-short"
    metrics["gate_checks"]["risk_fraction_exactly_three_percent"] = abs(float(kwargs["config"]["risk_fraction"]) - 0.03) <= 1e-12
    metrics["gate_pass"] = all(metrics["gate_checks"].values())
    return metrics


runner.build_metrics = build_metrics
runner.main()
