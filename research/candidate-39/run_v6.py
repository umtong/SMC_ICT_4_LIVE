#!/usr/bin/env python3
"""Run Candidate 39 V6 through the reused four-asset Nautilus harness."""
from __future__ import annotations

import argparse
from datetime import date
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "candidate-35"
CANDIDATE = "candidate-39-deep-value-acd-router-v6"
EVIDENCE_STATUS = "DEVELOPMENT_REPLAY_AFTER_V5_DEEP_VALUE_AND_ACD_REDESIGN"


def _load_base_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_candidate39_v6_reused_candidate35_run", BASE / "run.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load reused runner from {BASE / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_named(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _activate_v6_modules() -> None:
    here = str(HERE)
    sys.path[:] = [item for item in sys.path if item != here]
    sys.path.insert(0, here)
    for name in ("strategy", "router", "strategy_v4", "strategy_v5", "strategy_v6", "router_v4", "router_v5", "router_v6", "router_v4_core", "router_v4_families"):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    _load_named("router", HERE / "router.py")
    _load_named("strategy", HERE / "strategy.py")
    _load_named("strategy_v4", HERE / "strategy_v4.py")
    _load_named("strategy_v5", HERE / "strategy_v5.py")
    strategy_v6 = _load_named("strategy_v6", HERE / "strategy_v6.py")
    sys.modules["strategy"] = strategy_v6


def _rewrite_identity(output: Path, metrics: dict[str, Any], base: Any) -> dict[str, Any]:
    metrics.update({
        "candidate": CANDIDATE,
        "non_scalping": True,
        "feature_informed_decision_boundary": True,
        "state_families": ["DEEP_VALUE_SPONSORED_PULLBACK", "LIQUIDATION_FAILURE_REACCEPTANCE", "ACD_A_ESTABLISHMENT_RETEST", "ACD_C_FAILED_A_REVERSAL"],
        "signal_event": "completed deep-value pullback or persistent Fisher-style A/C state, confirmed by causally observed positioning/aggressor transition",
        "entry_policy": "passive structural-value retest LIMIT; cancel before fill on stop/value loss",
        "target_space_policy": "measured same-leg objective for deep pullback; nearest external time/range objective for ACD; cost-after family floor",
        "intended_holding_horizon_minutes": [30, 240],
        "minimum_operational_horizon_minutes": 60,
        "same_causal_episode_reentry_allowed": False,
        "source_claims_treated_as_hypotheses": True,
        "v6_structural_changes": [
            "deeper of impulse AVWAP and 20-bar value must be touched",
            "touch bar and later direction-resumption confirmation are separate",
            "ACD A requires three persistent completed one-minute closes beyond A-distance",
            "ACD B invalidation is the far side of the opening range",
            "ACD C requires a previously established A and persistent opposite break",
            "router audit records causal rejection families before parameter work",
        ],
        "evidence_status": EVIDENCE_STATUS,
        "success_claim": False,
    })
    base.write_json_atomic(output / "metrics.json", metrics)
    for filename in ("run.json", "data_manifest.json"):
        path = output / filename
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update({"candidate": CANDIDATE, "non_scalping": True, "feature_informed_decision_boundary": True, "evidence_status": EVIDENCE_STATUS, "success_claim": False})
        if filename == "run.json":
            payload["run_id"] = str(payload.get("run_id", "candidate-39-v6")).replace("candidate-35", "candidate-39-v6")
        base.write_json_atomic(path, payload)
    return metrics


def run(*, config_path: Path, start: date, end: date, cache: Path, output: Path, workspace: Path) -> dict[str, Any]:
    base = _load_base_runner()
    _activate_v6_modules()
    metrics = base.run(config_path=config_path, start=start, end=end, cache=cache, output=output, workspace=workspace)
    return _rewrite_identity(output.resolve(), metrics, base)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    metrics = run(config_path=args.config, start=date.fromisoformat(args.start), end=date.fromisoformat(args.end), cache=args.cache, output=args.output, workspace=args.workspace)
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
