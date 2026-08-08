#!/usr/bin/env python3
"""Run Candidate 39 V3 through the reused four-asset Nautilus harness."""
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
CANDIDATE = "candidate-39-causal-auction-state-router-v3"
EVIDENCE_STATUS = "DEVELOPMENT_REPLAY_V3_AFTER_V1_V2_INTERVAL_INSPECTION"


def _load_base_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_candidate39_v3_reused_candidate35_run",
        BASE / "run.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load reused runner from {BASE / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _activate_v3_modules() -> None:
    here = str(HERE)
    sys.path[:] = [item for item in sys.path if item != here]
    sys.path.insert(0, here)
    for name in ("strategy", "router", "strategy_v3", "router_v3"):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    router_v3 = importlib.import_module("router_v3")
    sys.modules["router"] = router_v3
    strategy_v3 = importlib.import_module("strategy_v3")
    sys.modules["strategy"] = strategy_v3


def _rewrite_identity(
    output: Path,
    metrics: dict[str, Any],
    base: Any,
) -> dict[str, Any]:
    metrics["candidate"] = CANDIDATE
    metrics["non_scalping"] = True
    metrics["signal_event"] = (
        "completed 15-minute attack plus persistent failed-attack setup and "
        "a separately completed opposite release auction"
    )
    metrics["entry_policy"] = (
        "V2 passive boundary retest or V3 passive new-release-boundary retest"
    )
    metrics["target_space_policy"] = (
        "cost-after net reward/risk must clear the scenario family floor"
    )
    metrics["intended_holding_horizon_minutes"] = [30, 240]
    metrics["state_families"] = [
        "BUILD_ACCEPT_CONTINUATION",
        "CASCADE_RECLAIM_REVERSAL",
        "PEER_LED_REPRICING",
        "TRAPPED_BUILD_RELEASE",
    ]
    metrics["trapped_build_sequence"] = [
        "PRE_ATTACK_OI_BUILD",
        "LEVERAGED_ATTACK",
        "FAILURE_BACK_INTO_VALUE_WITH_UNCLEARED_OI",
        "SEPARATE_OPPOSITE_MICRO_AUCTION_RELEASE",
        "PASSIVE_RELEASE_BOUNDARY_RETEST",
    ]
    metrics["open_interest_semantics"] = (
        "OI is non-directional; price attack and aggressor flow establish the "
        "side, while later OI contraction may describe trapped-position exit"
    )
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
            payload["run_id"] = str(payload.get("run_id", "candidate-39-v3")).replace(
                "candidate-35",
                "candidate-39-v3",
            )
        payload["non_scalping"] = True
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
    _activate_v3_modules()
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
