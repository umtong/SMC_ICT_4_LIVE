#!/usr/bin/env python3
"""Run Candidate 41 through the reused four-asset NautilusTrader harness."""
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


def _load_base_runner() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_candidate41_reused_candidate35_run",
        BASE / "run.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load reused runner from {BASE / 'run.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _activate_candidate41_modules() -> None:
    here = str(HERE)
    sys.path[:] = [item for item in sys.path if item != here]
    sys.path.insert(0, here)
    sys.modules.pop("strategy", None)
    sys.modules.pop("router", None)
    importlib.invalidate_caches()


def _rewrite_identity(
    output: Path,
    metrics: dict[str, Any],
    base: Any,
) -> dict[str, Any]:
    identity = "candidate-41-leadership-failed-reentry-router"
    metrics["candidate"] = identity
    metrics["non_scalping"] = True
    metrics["signal_event"] = (
        "15-minute repricing plus a separate six-minute response/confirmation sequence"
    )
    metrics["entry_policy"] = (
        "passive structural retest limit with causal-episode de-duplication"
    )
    metrics["target_space_policy"] = (
        "natural objective must clear realistic cost-after net R floor"
    )
    metrics["intended_holding_horizon_minutes"] = [30, 240]
    metrics["state_families"] = [
        "LEADER_FIRST_PULLBACK_CONTINUATION",
        "MATURE_EXTENSION_FAILED_REENTRY",
    ]
    metrics["causal_independence"] = (
        "one submitted opportunity per symbol and stable one-hour trend-origin episode"
    )
    metrics["evidence_status"] = "SHORT_DEVELOPMENT_DIAGNOSTIC"
    base.write_json_atomic(output / "metrics.json", metrics)

    for filename in ("run.json", "data_manifest.json"):
        path = output / filename
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["candidate"] = identity
        if filename == "run.json":
            payload["run_id"] = str(payload.get("run_id", "candidate-41")).replace(
                "candidate-35",
                "candidate-41",
            )
        payload["non_scalping"] = True
        payload["evidence_status"] = "SHORT_DEVELOPMENT_DIAGNOSTIC"
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
    _activate_candidate41_modules()
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
