#!/usr/bin/env python3
"""Process-isolated staged validation for candidate-07.

NautilusTrader 1.230.0 initializes its Rust logging subsystem once per process.
This orchestrator therefore launches each bounded replay in a fresh Python
process. It does not calculate signals, fills, positions, PnL, or NAV; every
stage remains a normal ``candidate.py week`` NautilusTrader replay.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _validate_week(item: Mapping[str, Any]) -> tuple[str, date, date]:
    stage = str(item["stage"])
    start = date.fromisoformat(str(item["start"]))
    end = date.fromisoformat(str(item["end"]))
    if end <= start:
        raise ValueError(f"invalid interval for {stage}: {start} -> {end}")
    return stage, start, end


def _stage_record(metrics: Mapping[str, Any], metrics_path: Path) -> dict[str, Any]:
    gate = metrics.get("weekly_gate")
    if not isinstance(gate, Mapping):
        raise ValueError(f"weekly_gate missing from {metrics_path}")
    checks = gate.get("checks")
    if not isinstance(checks, Mapping):
        raise ValueError(f"weekly_gate.checks missing from {metrics_path}")
    period = metrics.get("period")
    if not isinstance(period, Mapping):
        raise ValueError(f"period missing from {metrics_path}")
    return {
        "stage": str(metrics["stage"]),
        "start": str(period["start"]),
        "end_exclusive": str(period["end_exclusive"]),
        "metrics_path": str(metrics_path),
        "daily_geometric_growth": float(metrics["daily_geometric_growth"]),
        "net_return": float(metrics["net_return"]),
        "trades": int(metrics["trades"]),
        "win_rate": float(metrics["win_rate"]),
        "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown": float(metrics["max_drawdown"]),
        "gate_passed": bool(gate["passed"]),
        "checks": {str(key): bool(value) for key, value in checks.items()},
    }


def _run_child(
    *,
    candidate_script: Path,
    config: Path,
    stage: str,
    start: date,
    end: date,
    destination: Path,
    data_root: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(candidate_script),
        "week",
        "--config",
        str(config),
        "--stage",
        stage,
        "--start",
        start.isoformat(),
        "--end",
        end.isoformat(),
        "--output",
        str(destination),
        "--data-root",
        str(data_root),
    ]
    print(f"[candidate-07] launching isolated NautilusTrader stage: {stage}", flush=True)
    subprocess.run(command, check=True)
    metrics_path = destination / "metrics.json"
    if not metrics_path.is_file():
        raise RuntimeError(f"stage completed without metrics: {metrics_path}")
    return metrics_path


def run(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    week_plan_path = args.week_plan.resolve()
    output = args.output.resolve()
    data_root = args.data_root.resolve()
    candidate_script = (Path(__file__).resolve().parent / "candidate.py").resolve()

    config = _read_json(config_path)
    week_plan = _read_json(week_plan_path)
    raw_weeks = week_plan.get("weeks")
    if not isinstance(raw_weeks, list) or len(raw_weeks) < 3:
        raise ValueError("week_plan.json must contain the three frozen weeks")
    selected = raw_weeks[: args.max_weeks]

    summary: dict[str, Any] = {
        "candidate": "candidate-07",
        "engine": "NautilusTrader BacktestEngine",
        "process_isolation": "one fresh Python process per bounded replay",
        "config": str(config_path),
        "week_plan": str(week_plan_path),
        "stages": [],
        "stopped_after": None,
        "all_requested_passed": False,
        "long_evaluation_run": False,
    }
    summary_path = output / "pipeline_summary.json"
    output.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(summary_path, summary)

    for raw_item in selected:
        if not isinstance(raw_item, Mapping):
            raise ValueError("week plan entries must be JSON objects")
        stage, start, end = _validate_week(raw_item)
        metrics_path = _run_child(
            candidate_script=candidate_script,
            config=config_path,
            stage=stage,
            start=start,
            end=end,
            destination=output / stage,
            data_root=data_root,
        )
        metrics = _read_json(metrics_path)
        record = _stage_record(metrics, metrics_path)
        summary["stages"].append(record)
        if not record["gate_passed"]:
            summary["stopped_after"] = stage
            _write_json_atomic(summary_path, summary)
            print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
            return 0
        _write_json_atomic(summary_path, summary)

    summary["all_requested_passed"] = len(summary["stages"]) == len(selected) and all(
        bool(item["gate_passed"]) for item in summary["stages"]
    )

    all_three_passed = (
        len(summary["stages"]) == len(raw_weeks)
        and all(bool(item["gate_passed"]) for item in summary["stages"])
    )
    if args.run_long and all_three_passed:
        long_values = config.get("long_evaluation")
        if not isinstance(long_values, Mapping):
            raise ValueError("long_evaluation missing from config")
        long_start = date.fromisoformat(str(long_values["start"]))
        long_end = date.fromisoformat(str(long_values["end"]))
        metrics_path = _run_child(
            candidate_script=candidate_script,
            config=config_path,
            stage="long-evaluation",
            start=long_start,
            end=long_end,
            destination=output / "long-evaluation",
            data_root=data_root,
        )
        long_metrics = _read_json(metrics_path)
        summary["long_evaluation_run"] = True
        summary["long_evaluation"] = {
            "metrics_path": str(metrics_path),
            "start": long_start.isoformat(),
            "end_exclusive": long_end.isoformat(),
            "daily_geometric_growth": float(long_metrics["daily_geometric_growth"]),
            "net_return": float(long_metrics["net_return"]),
            "trades": int(long_metrics["trades"]),
            "win_rate": float(long_metrics["win_rate"]),
            "profit_factor": float(long_metrics["profit_factor"]),
            "max_drawdown": float(long_metrics["max_drawdown"]),
            "target_met": bool(long_metrics["target_met"]),
        }

    _write_json_atomic(summary_path, summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--week-plan", type=Path, default=candidate_dir / "week_plan.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    parser.add_argument("--max-weeks", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--run-long", action="store_true")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
