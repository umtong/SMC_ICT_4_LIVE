#!/usr/bin/env python3
"""Run one predeclared Candidate 15 interval through NautilusTrader."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
CANDIDATE14 = ROOT.parent / "candidate-14"
PROJECT_ROOT = ROOT.parents[1]
# Candidate 15 wrapper must win over Candidate 14's same-named module while all
# inherited modules remain immediately available.
for path in (PROJECT_ROOT / "src", CANDIDATE14, ROOT):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

from run_leadership_scdam_v11 import run  # noqa: E402


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def _write_object(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _event_diagnostics(path: Path) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    initiative_ids: set[str] = set()
    continuation_ids: set[str] = set()
    if not path.is_file():
        return {
            "event_type_counts": {},
            "reason_code_counts": {},
            "initiative_activations": 0,
            "continuation_plans": 0,
        }
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            event_type = str(event.get("event_type", "UNKNOWN"))
            reason = str(event.get("reason_code", "UNKNOWN"))
            scenario_id = str(event.get("scenario_id", ""))
            event_types[event_type] += 1
            reasons[reason] += 1
            if event_type == "QHI_INITIATIVE_ACTIVATED":
                initiative_ids.add(scenario_id)
            if event_type == "QHI_CONTINUATION_PLAN_CONFIRMED":
                continuation_ids.add(scenario_id)
    return {
        "event_type_counts": dict(sorted(event_types.items())),
        "reason_code_counts": dict(sorted(reasons.items())),
        "initiative_activations": len(initiative_ids),
        "continuation_plans": len(continuation_ids),
    }


def execute(interval: str, output_dir: Path) -> dict[str, Any]:
    protocol = _read_object(ROOT / "protocol-v11.json")
    intervals = protocol["selection"]["intervals"]
    if interval not in intervals:
        raise ValueError(f"unknown interval {interval!r}; expected one of {sorted(intervals)}")

    config = _read_object(CANDIDATE14 / "base_config.json")
    config["candidate"] = protocol["candidate"]
    config["selection"]["seed"] = protocol["selection"]["seed"]
    config["selection"]["selection_rule"] = protocol["selection"]["method"]
    config["selection"]["warmup_days"] = protocol["selection"]["warmup_days"]
    config["selection"]["evaluation_days"] = protocol["selection"]["evaluation_days"]
    config["selection"]["weeks"] = {
        name: {"start": record["start"], "end_exclusive": record["end_exclusive"]}
        for name, record in intervals.items()
    }
    config["session_i7"] = _read_object(CANDIDATE14 / "session_i7_config.json")
    config["candidate15_protocol"] = {
        "schema": protocol["schema"],
        "interval": interval,
        "role": intervals[interval]["role"],
        "base_candidate": protocol["base_candidate"],
        "router": protocol["router"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective_config = output_dir / "effective_config.json"
    _write_object(effective_config, config)
    metrics = run(effective_config, interval, output_dir)

    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = _read_object(metrics_path)
    diagnostics = _event_diagnostics(output_dir / "scenario_events.raw.jsonl")
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate15_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
            "interval_role": intervals[interval]["role"],
            "base_candidate": protocol["base_candidate"],
            "candidate15_diagnostics": diagnostics,
            "individual_success_claim": False,
            "success_claim": False,
        },
    )
    _write_object(metrics_path, metrics)

    for evidence_name in ("run.json", "data_manifest.json"):
        evidence_path = output_dir / evidence_name
        if evidence_path.is_file():
            evidence = _read_object(evidence_path)
            evidence.update(
                {
                    "candidate": protocol["candidate"],
                    "candidate15_protocol": protocol["schema"],
                    "validation_mode": protocol["validation_mode"],
                    "interval": interval,
                },
            )
            _write_object(evidence_path, evidence)

    summary = {
        "candidate": protocol["candidate"],
        "candidate15_protocol": protocol["schema"],
        "validation_mode": protocol["validation_mode"],
        "interval": interval,
        "role": intervals[interval]["role"],
        "start": intervals[interval]["start"],
        "end_exclusive": intervals[interval]["end_exclusive"],
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "final_nav": metrics.get("final_nav"),
        "net_return": metrics.get("net_return"),
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "submitted_plans": metrics.get("submitted_plans"),
        "scenario_counts": metrics.get("scenario_counts", {}),
        "module_counts": metrics.get("module_counts", {}),
        "symbol_counts": metrics.get("symbol_counts", {}),
        "skip_reasons": metrics.get("skip_reasons", {}),
        "global_slot_overlap_count": metrics.get("global_slot_overlap_count"),
        "partial_entry_fail_closed_count": metrics.get("partial_entry_fail_closed_count"),
        "liquidation_detected": metrics.get("liquidation_detected"),
        "engine_errors": metrics.get("engine_errors", []),
        "candidate15_diagnostics": diagnostics,
    }
    _write_object(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("interval")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    execute(args.interval, args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
