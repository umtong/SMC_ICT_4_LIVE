"""Current-generation evidence wrapper around the stable Nautilus runner."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from smc_ict_4.manifest import write_json_atomic

from c10_research import reproducible_weeks
from c10_research import run_backtest as _run_backtest


def _event_diagnostics(path: Path) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    approach_types: Counter[str] = Counter()
    causality_violations: list[dict[str, Any]] = []
    scenario_sequences: dict[str, list[str]] = {}

    if not path.exists():
        return {
            "event_type_counts": {},
            "reason_counts": {},
            "approach_structure_counts": {},
            "causality_violation_count": 0,
            "scenario_count": 0,
        }

    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            event_type = str(event.get("event_type", "UNKNOWN"))
            reason = str(event.get("reason_code", "UNKNOWN"))
            event_types[event_type] += 1
            reasons[reason] += 1
            scenario_id = str(event.get("scenario_id", "UNKNOWN"))
            scenario_sequences.setdefault(scenario_id, []).append(event_type)

            details = event.get("details") or {}
            approach = details.get("approach_structure_type")
            if approach:
                approach_types[str(approach)] += 1

            event_time = int(event.get("event_time_ns", 0))
            observed_time = int(event.get("observed_time_ns", 0))
            if observed_time < event_time:
                causality_violations.append(
                    {
                        "line": line_number,
                        "scenario_id": scenario_id,
                        "event_type": event_type,
                        "event_time_ns": event_time,
                        "observed_time_ns": observed_time,
                    },
                )

    return {
        "event_type_counts": dict(event_types),
        "reason_counts": dict(reasons),
        "approach_structure_counts": dict(approach_types),
        "causality_violation_count": len(causality_violations),
        "causality_violations": causality_violations[:50],
        "scenario_count": len(scenario_sequences),
    }


def run_backtest(**kwargs: Any) -> dict[str, Any]:
    """Run the pinned Nautilus engine, then stamp current causal diagnostics."""

    metrics = _run_backtest(**kwargs)
    destination = Path(kwargs["output_dir"])
    diagnostics = _event_diagnostics(destination / "scenario_events.jsonl")
    metrics["candidate_generation"] = (
        "v2.2-nearest-right-confirmed-micro-pivot"
    )
    metrics["execution_generation"] = (
        "v2.2-structural-pool-maker-retrace"
    )
    metrics["state_diagnostics"] = diagnostics
    metrics["causal_gate_pass"] = diagnostics["causality_violation_count"] == 0
    metrics["target_pass"] = bool(
        metrics.get("target_pass", False) and metrics["causal_gate_pass"]
    )
    write_json_atomic(destination / "metrics.json", metrics)

    run_path = destination / "run.json"
    if run_path.exists():
        run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        run_manifest["candidate_generation"] = metrics["candidate_generation"]
        run_manifest["causal_gate_pass"] = metrics["causal_gate_pass"]
        write_json_atomic(run_path, run_manifest)
    return metrics


__all__ = ["reproducible_weeks", "run_backtest"]
