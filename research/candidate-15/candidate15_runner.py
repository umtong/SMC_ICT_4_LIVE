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
# Deterministic import order is part of the candidate identity.  Candidate 15's
# wrapper must win over Candidate 14's same-named runner, while inherited modules
# remain available immediately after it.
for path in (PROJECT_ROOT / "src", CANDIDATE14, ROOT):
    text = str(path)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

from run_leadership_scdam import run  # noqa: E402


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
    event_type_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    resolved_scenarios: set[str] = set()
    swept_scenarios: set[str] = set()
    if not path.is_file():
        return {
            "event_type_counts": {},
            "router_resolution_counts": {},
            "swept_scenarios": 0,
            "resolved_scenarios": 0,
            "unresolved_scenarios_lower_bound": 0,
        }
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            event_type = str(event.get("event_type", "UNKNOWN"))
            reason = str(event.get("reason_code", "UNKNOWN"))
            scenario_id = str(event.get("scenario_id", ""))
            event_type_counts[event_type] += 1
            if event_type == "LIQUIDITY_SWEEP":
                swept_scenarios.add(scenario_id)
            if event_type == "AUCTION_STATE_RESOLVED" and reason.startswith("C15_"):
                reason_counts[reason] += 1
                resolved_scenarios.add(scenario_id)
    return {
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "router_resolution_counts": dict(sorted(reason_counts.items())),
        "swept_scenarios": len(swept_scenarios),
        "resolved_scenarios": len(resolved_scenarios),
        "unresolved_scenarios_lower_bound": len(swept_scenarios - resolved_scenarios),
    }


def execute(interval: str, output_dir: Path) -> dict[str, Any]:
    protocol = _read_object(ROOT / "protocol.json")
    intervals = protocol["selection"]["intervals"]
    if interval not in intervals:
        raise ValueError(
            f"unknown interval {interval!r}; expected one of {sorted(intervals)}",
        )

    config = _read_object(CANDIDATE14 / "base_config.json")
    config["candidate"] = protocol["candidate"]
    config["selection"]["seed"] = protocol["selection"]["seed"]
    config["selection"]["selection_rule"] = protocol["selection"]["method"]
    config["selection"]["warmup_days"] = protocol["selection"]["warmup_days"]
    config["selection"]["evaluation_days"] = protocol["selection"]["evaluation_days"]
    config["selection"]["weeks"] = {
        name: {
            "start": record["start"],
            "end_exclusive": record["end_exclusive"],
        }
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
            "router_diagnostics": diagnostics,
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
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "promising_gate_passed": metrics.get("promising_gate_passed"),
        "complete_gate_passed": metrics.get("complete_gate_passed"),
        "liquidation_detected": metrics.get("liquidation_detected"),
        "engine_errors": metrics.get("engine_errors", []),
        "router_diagnostics": diagnostics,
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
