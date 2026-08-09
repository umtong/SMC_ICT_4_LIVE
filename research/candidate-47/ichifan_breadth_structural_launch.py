#!/usr/bin/env python3
"""Run the breadth-confirmed structural-risk ichiFan policy in NautilusTrader."""
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
expected_router = (HERE / "router.py").resolve()
if Path(router_module.__file__).resolve() != expected_router:
    raise RuntimeError(
        f"Candidate 47 router collision: {router_module.__file__} != {expected_router}"
    )

strategy_module = importlib.import_module("ichifan_breadth_structural_strategy")
expected_strategy = (HERE / "ichifan_breadth_structural_strategy.py").resolve()
if Path(strategy_module.__file__).resolve() != expected_strategy:
    raise RuntimeError(
        f"Candidate 47 breadth strategy collision: "
        f"{strategy_module.__file__} != {expected_strategy}"
    )
sys.modules["strategy"] = strategy_module

import event_lifecycle_patch  # noqa: F401,E402

spec = importlib.util.spec_from_file_location(
    "candidate47_ichifan_breadth_structural_direct_runner",
    HERE / "run.py",
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load Candidate 47 runner")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

original_build_metrics = runner.build_metrics


def breadth_build_metrics(**kwargs):
    metrics = original_build_metrics(**kwargs)
    config = kwargs["config"]
    metrics["candidate"] = "candidate-47-public-ichiv2-structural-breadth"
    metrics["gate_checks"]["risk_fraction_exactly_three_percent"] = (
        abs(float(config["risk_fraction"]) - 0.03) <= 1e-12
    )
    metrics["gate_pass"] = all(metrics["gate_checks"].values())
    return metrics


runner.build_metrics = breadth_build_metrics
runner.main()
