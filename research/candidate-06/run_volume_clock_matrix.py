#!/usr/bin/env python3
"""Predeclared VCIB full mechanism and one core ablation."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

VARIANTS = (
    (
        "vcib_full_marginal_impact",
        "Two same-direction causal volume buckets; continuation requires retained marginal impact, exhaustion requires impact collapse without extension.",
        True,
        True,
    ),
    (
        "vcib_without_impact_ablation",
        "One-variable ablation: identical volume clock, sequential flow, response, stop, target and execution with marginal-impact classification removed.",
        False,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["version"] = "5.1.0"
    config["hypothesis"] = (
        "On an adaptive volume clock, persistence or collapse of marginal price impact across two same-direction aggressive-flow buckets identifies continuation or exhaustion before a separate response entry."
    )
    config["logic"].update(
        {
            "engine": "VOLUME_CLOCK_IMPACT_BIFURCATION",
            "signal_submission_timing": "ON_SIGNAL_CLOSE",
            "vcib_volume_lookback": 60,
            "vcib_minimum_volume_history": 30,
            "vcib_target_minutes": 3.0,
            "vcib_flow_floor": 0.10,
            "vcib_displacement_atr": 0.25,
            "vcib_close_location": 0.65,
            "vcib_efficiency_history": 40,
            "vcib_minimum_efficiency_history": 20,
            "vcib_continuation_quantile": 0.50,
            "vcib_exhaustion_quantile": 0.25,
            "vcib_response_bars": 10,
            "vcib_response_body_atr": 0.12,
            "vcib_response_flow_ratio": 0.03,
            "vcib_response_close_location": 0.62,
            "vcib_stop_buffer_atr": 0.08,
            "vcib_projection_fraction": 0.75,
            "vcib_cooldown_bars": 2,
            "minimum_structural_rr": 0.75,
            "minimum_net_rr_after_entry_delay": 0.60,
            "max_entry_drift_atr": 0.40,
            "max_holding_bars": 45,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": False,
        },
    )
    return config


def _run(
    config_path: Path,
    output: Path,
    week_index: int,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(candidate_dir / "run_volume_clock_validation.py"),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--week-index",
            str(week_index),
            "--allow-gate-fail",
        ],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    record: dict[str, Any] = {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-5000:],
        "stderr_tail": completed.stderr[-16000:],
    }
    metrics_path = output / "metrics.json"
    if metrics_path.exists():
        record["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
        record["gate_passed"] = bool(record["metrics"].get("gate_passed"))
    else:
        record["gate_passed"] = False
        error_path = output / "errors.log"
        if error_path.exists():
            record["error"] = error_path.read_text(encoding="utf-8", errors="replace")[-16000:]
    return record


def _counts(root: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    path = root / "scenario_events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                reasons[str(json.loads(line).get("reason_code", "UNKNOWN"))] += 1
    return {"reason_counts": dict(reasons)}


def _diagnose(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return {
            "classification": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": record.get("error") or record.get("stderr_tail"),
        }
    trades = int(metrics.get("trades", 0))
    growth = float(metrics.get("geometric_daily_nav_growth", 0.0))
    pf = metrics.get("profit_factor")
    if trades == 0:
        classification = "NO_COMPLETED_VOLUME_CLOCK_RESPONSE"
    elif growth <= 0.0 or (pf is not None and float(pf) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
    elif not metrics.get("gate_passed"):
        classification = "POSITIVE_BUT_GATE_FAILED"
    else:
        classification = "GATE_PASSED"
    return {
        "classification": classification,
        "geometric_daily_nav_growth": growth,
        "trades": trades,
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": pf,
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "largest_positive_trade_share": metrics.get("largest_positive_trade_share"),
        "gate_failures": metrics.get("gate_failures", []),
        "entry_abstentions": metrics.get("diagnostics", {}).get("entry_abstentions", {}),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v5.1 Volume-Clock Impact Bifurcation",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Selected: `{summary.get('selected')}`" if summary.get("selected") else "Selected: none",
        "",
        "|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in [*summary.get("first_week_results", []), *summary.get("frozen_validation", [])]:
        metrics = record.get("metrics", {})
        lines.append(
            "|{name}|{week}|{eligible}|{gate}|{growth:.6%}|{trades}|{wins}|{win:.2%}|{pf}|{dd:.2%}|{failures}|".format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/vcib-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first: list[dict[str, Any]] = []

    for name, description, use_impact, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"]["vcib_use_impact_efficiency"] = use_impact
        configs[name] = config
        config_dir = root / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{name}-week-1.json"
        config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run_output = root / "runs" / name / "week-1"
        record = _run(config_path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": eligible,
                "week_index": 0,
                "config_path": str(config_path.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        first.append(record)

    diagnoses = {record["name"]: _diagnose(record) for record in first}
    implementation_ok = all(
        int(record.get("returncode", 1)) == 0 and isinstance(record.get("metrics"), Mapping)
        for record in first
    )
    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-vcib-v5.1",
        "design": "prior-only volume clock -> two same-direction flow buckets -> marginal-impact persistence/exhaustion -> later response -> structural objective",
        "first_week_results": first,
        "frozen_validation": [],
        "diagnoses": diagnoses,
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not implementation_ok:
        _write(root, {**base_summary, "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE"})
        return 5

    full = next(record for record in first if record["name"] == "vcib_full_marginal_impact")
    if not full.get("gate_passed"):
        _write(
            root,
            {
                **base_summary,
                "terminal_status": "FIRST_WEEK_LOGIC_GATE_FAILED",
                "discarded": {
                    "vcib_full_marginal_impact": diagnoses["vcib_full_marginal_impact"],
                },
            },
        )
        return 2

    selected = "vcib_full_marginal_impact"
    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.vcib.locked.json"
    locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        run_output = root / "runs" / selected / f"week-{week_index + 1}"
        config_path = root / "configs" / f"{selected}-week-{week_index + 1}.json"
        config_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        record = _run(config_path, run_output, week_index, candidate_dir, repository)
        record.update(
            {
                "name": selected,
                "description": VARIANTS[0][1],
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "causal_counts": _counts(run_output),
            },
        )
        frozen.append(record)
    all_three = len(frozen) == 2 and all(record.get("gate_passed") for record in frozen)
    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(locked_path.relative_to(repository)),
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
        "terminal_status": (
            "THREE_WEEK_GATE_PASSED" if all_three else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED"
        ),
        "holdout_diagnoses": {
            f"week-{record['week_index'] + 1}": _diagnose(record)
            for record in frozen
        },
    }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
