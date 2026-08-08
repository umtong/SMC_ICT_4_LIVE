#!/usr/bin/env python3
"""Run Candidate 39 V5 through the reused four-asset Nautilus harness."""
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
CANDIDATE = "candidate-39-feature-informed-trader-router-v5"
EVIDENCE_STATUS = "DEVELOPMENT_REPLAY_AFTER_V4_STATE_AND_EXECUTION_FAILURE_REPAIR"


def _load_base_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_candidate39_v5_reused_candidate35_run",
        BASE / "run.py",
    )
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


def _activate_v5_modules() -> None:
    here = str(HERE)
    sys.path[:] = [item for item in sys.path if item != here]
    sys.path.insert(0, here)
    for name in (
        "strategy",
        "router",
        "strategy_v4",
        "strategy_v5",
        "router_v4",
        "router_v5",
        "router_v4_core",
        "router_v4_families",
    ):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    _load_named("router", HERE / "router.py")
    _load_named("strategy", HERE / "strategy.py")
    _load_named("strategy_v4", HERE / "strategy_v4.py")
    strategy_v5 = _load_named("strategy_v5", HERE / "strategy_v5.py")
    sys.modules["strategy"] = strategy_v5


def _rewrite_identity(output: Path, metrics: dict[str, Any], base: Any) -> dict[str, Any]:
    metrics.update(
        {
            "candidate": CANDIDATE,
            "non_scalping": True,
            "feature_informed_decision_boundary": True,
            "state_families": [
                "SPONSORED_FIRST_PULLBACK",
                "LIQUIDATION_FAILURE_REACCEPTANCE",
                "OPENING_RANGE_ACCEPTANCE_RETEST",
            ],
            "signal_event": (
                "completed 15m trader-derived price geometry plus causally observed "
                "OI/aggressor-flow state transition"
            ),
            "entry_policy": (
                "passive retest LIMIT; cancel before fill on stop/accepted-value failure"
            ),
            "target_space_policy": "honest same-auction structural objective after costs",
            "intended_holding_horizon_minutes": [30, 240],
            "minimum_operational_horizon_minutes": 60,
            "same_causal_episode_reentry_allowed": False,
            "source_claims_treated_as_hypotheses": True,
            "v4_failure_repairs": [
                "continuation requires OI sponsorship and renewed aligned flow",
                "failed level requires OI flush, flow flip, deep reacceptance, relative isolation",
                "hard invalidation outside level instead of wick-tight stop",
                "pending parent cancelled when state fails before fill",
                "no entry too near mandatory funding flatten",
                "independent 8h opening-range acceptance family",
            ],
            "evidence_status": EVIDENCE_STATUS,
            "success_claim": False,
        }
    )
    base.write_json_atomic(output / "metrics.json", metrics)
    for filename in ("run.json", "data_manifest.json"):
        path = output / filename
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(
            {
                "candidate": CANDIDATE,
                "non_scalping": True,
                "feature_informed_decision_boundary": True,
                "evidence_status": EVIDENCE_STATUS,
                "success_claim": False,
            }
        )
        if filename == "run.json":
            payload["run_id"] = str(payload.get("run_id", "candidate-39-v5")).replace(
                "candidate-35", "candidate-39-v5"
            )
        base.write_json_atomic(path, payload)
    return metrics


def run(
    *,
    config_path: Path,
    start: date,
    end: date,
    cache: Path,
    output: Path,
    workspace: Path,
) -> dict[str, Any]:
    base = _load_base_runner()
    _activate_v5_modules()
    metrics = base.run(
        config_path=config_path,
        start=start,
        end=end,
        cache=cache,
        output=output,
        workspace=workspace,
    )
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
    metrics = run(
        config_path=args.config,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        cache=args.cache,
        output=args.output,
        workspace=args.workspace,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
