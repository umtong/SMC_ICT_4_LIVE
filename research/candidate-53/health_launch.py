#!/usr/bin/env python3
"""Launch Candidate 53 counterfactual health router in Candidate 47 Nautilus shell."""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
os.environ["CANDIDATE47_PRICE_ONLY_INPUTS"] = "1"
CANDIDATE16 = HERE.parent / "candidate-16"
CANDIDATE05 = HERE.parent / "candidate-05"
for path in (CANDIDATE05, CANDIDATE16, HERE):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

router_module = importlib.import_module("router")
if Path(router_module.__file__).resolve() != (HERE / "router.py").resolve():
    raise RuntimeError(f"Candidate 53 router collision: {router_module.__file__}")

strategy_module = importlib.import_module("candidate53_health_strategy")
if Path(strategy_module.__file__).resolve() != (HERE / "candidate53_health_strategy.py").resolve():
    raise RuntimeError(f"Candidate 53 strategy collision: {strategy_module.__file__}")
sys.modules["strategy"] = strategy_module

import event_lifecycle_patch  # noqa: F401,E402

spec = importlib.util.spec_from_file_location(
    "candidate53_counterfactual_health_runner",
    HERE / "run.py",
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 47 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

original_build_metrics = runner.build_metrics


def candidate53_build_metrics(**kwargs):
    metrics = original_build_metrics(**kwargs)
    config = kwargs["config"]
    metrics["candidate"] = "candidate-53-counterfactual-health-structural-ichifan"
    metrics["gate_checks"]["risk_fraction_exactly_three_percent"] = (
        abs(float(config["risk_fraction"]) - 0.03) <= 1e-12
    )
    metrics["gate_pass"] = all(metrics["gate_checks"].values())
    return metrics


runner.build_metrics = candidate53_build_metrics
runner.main()
