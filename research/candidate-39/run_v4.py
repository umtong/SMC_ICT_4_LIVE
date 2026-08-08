#!/usr/bin/env python3
"""Run Candidate 39 V4 through the reused four-asset Nautilus harness."""
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
CANDIDATE = "candidate-39-trader-derived-auction-router-v4"
EVIDENCE_STATUS = "DEVELOPMENT_REPLAY_AFTER_DOMESTIC_INTERNATIONAL_TRADER_METHOD_MINING"


def _load_base_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_candidate39_v4_reused_candidate35_run",
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


def _activate_v4_modules() -> None:
    here = str(HERE)
    sys.path[:] = [item for item in sys.path if item != here]
    sys.path.insert(0, here)
    for name in (
        "strategy",
        "router",
        "strategy_v4",
        "router_v4",
        "_candidate39_base_strategy_for_v4",
    ):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()

    # Candidate 39 V2 is the execution shell. It must load against its own
    # router contract; V4's price router is imported separately by strategy_v4.
    _load_named("router", HERE / "router.py")
    _load_named("strategy", HERE / "strategy.py")
    strategy_v4 = _load_named("strategy_v4", HERE / "strategy_v4.py")
    sys.modules["strategy"] = strategy_v4


def _rewrite_identity(
    output: Path,
    metrics: dict[str, Any],
    base: Any,
) -> dict[str, Any]:
    metrics["candidate"] = CANDIDATE
    metrics["non_scalping"] = True
    metrics["price_only_decision_boundary"] = True
    metrics["signal_event"] = (
        "completed 15-minute first-pullback value hold or completed failed "
        "prior-day/session attack followed by a separately completed retest"
    )
    metrics["entry_policy"] = "passive retest LIMIT; causal episode consumed once"
    metrics["target_space_policy"] = (
        "same-leg structural target and cost-after reward/risk family floor"
    )
    metrics["intended_holding_horizon_minutes"] = [30, 240]
    metrics["state_families"] = [
        "FIRST_PULLBACK_CONTINUATION",
        "FAILED_LEVEL_REACCEPTANCE",
    ]
    metrics["source_derived_components"] = {
        "FIRST_PULLBACK_CONTINUATION": [
            "Rounders bull-regime momentum pullback",
            "Linda Raschke momentum-before-pullback / first-pullback logic",
            "Brian Shannon multi-timeframe dynamic-value alignment",
        ],
        "FAILED_LEVEL_REACCEPTANCE": [
            "CryptoCred significant high/low and failed-break logic",
            "Linda Raschke Turtle Soup false-break logic",
            "domestic completed-1h/4h support-resistance and retest practice",
        ],
    }
    metrics["source_claims_treated_as_hypotheses"] = True
    metrics["same_causal_episode_reentry_allowed"] = False
    metrics["evidence_status"] = EVIDENCE_STATUS
    metrics["success_claim"] = False
    base.write_json_atomic(output / "metrics.json", metrics)

    for filename in ("run.json", "data_manifest.json"):
        path = output / filename
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["candidate"] = CANDIDATE
        if filename == "run.json":
            payload["run_id"] = str(
                payload.get("run_id", "candidate-39-v4")
            ).replace("candidate-35", "candidate-39-v4")
        payload["non_scalping"] = True
        payload["price_only_decision_boundary"] = True
        payload["evidence_status"] = EVIDENCE_STATUS
        payload["success_claim"] = False
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
    _activate_v4_modules()
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
