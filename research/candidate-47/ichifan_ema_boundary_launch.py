#!/usr/bin/env python3
from __future__ import annotations
import importlib, importlib.util, os, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
os.environ["CANDIDATE47_PRICE_ONLY_INPUTS"] = "1"
for path in (HERE.parent / "candidate-05", HERE.parent / "candidate-16", HERE):
    while str(path) in sys.path: sys.path.remove(str(path))
    sys.path.insert(0, str(path))
router = importlib.import_module("router")
strategy = importlib.import_module("ichifan_ema_boundary_strategy")
if Path(router.__file__).resolve() != (HERE / "router.py").resolve(): raise RuntimeError("router collision")
if Path(strategy.__file__).resolve() != (HERE / "ichifan_ema_boundary_strategy.py").resolve(): raise RuntimeError("strategy collision")
sys.modules["strategy"] = strategy
import event_lifecycle_patch  # noqa: F401,E402
spec = importlib.util.spec_from_file_location("candidate47_ema_boundary_runner", HERE / "run.py")
if spec is None or spec.loader is None: raise RuntimeError("cannot load runner")
runner = importlib.util.module_from_spec(spec); sys.modules[spec.name] = runner; spec.loader.exec_module(runner)
original = runner.build_metrics
def build_metrics(**kwargs):
    metrics = original(**kwargs)
    metrics["candidate"] = "candidate-47-public-ichiv2-ema-boundary-risk"
    metrics["gate_checks"]["risk_fraction_exactly_three_percent"] = abs(float(kwargs["config"]["risk_fraction"]) - 0.03) <= 1e-12
    metrics["gate_pass"] = all(metrics["gate_checks"].values())
    return metrics
runner.build_metrics = build_metrics
runner.main()
