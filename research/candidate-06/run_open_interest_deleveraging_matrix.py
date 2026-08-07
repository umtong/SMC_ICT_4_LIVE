#!/usr/bin/env python3
"""Predeclared OIDB mechanism and one core-variable ablation."""

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
        "oidb_deleveraging_bifurcation_full",
        "Extreme prior-relative OI contraction plus aligned price/taker flow; later completed response bifurcates into exhaustion reversal or persistent price discovery.",
        True,
        True,
        True,
        True,
    ),
    (
        "oidb_exhaustion_reversal_only",
        "Branch isolation: identical OI shock and reversal response; continuation branch disabled.",
        True,
        True,
        False,
        False,
    ),
    (
        "oidb_without_open_interest_ablation",
        "One core-variable ablation: price and flow shock plus the same response logic, but OI contraction is removed.",
        False,
        True,
        True,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["version"] = "5.0.0"
    config["hypothesis"] = (
        "An extreme completed open-interest contraction with aligned aggressive flow is a forced inventory shock; "
        "a later completed response determines whether inventory is exhausted and reclaims or continues liquidating into price discovery."
    )
    config["logic"].update(
        {
            "engine": "OPEN_INTEREST_DELEVERAGING_BIFURCATION",
            "signal_submission_timing": "ON_SIGNAL_CLOSE",
            "oidb_history_minutes": 1440,
            "oidb_min_prior_drops": 36,
            "oidb_drop_quantile": 0.85,
            "oidb_event_move_atr": 0.30,
            "oidb_metric_flow_floor": 0.06,
            "oidb_response_bars": 6,
            "oidb_response_flow_ratio": 0.05,
            "oidb_reclaim_close_location": 0.58,
            "oidb_persistence_fraction": 0.35,
            "oidb_extension_atr": 0.05,
            "oidb_stop_buffer_atr": 0.08,
            "oidb_projection_fraction": 1.0,
            "oidb_cooldown_bars": 2,
            "oidb_invalidation_observation_bars": 6,
            "minimum_structural_rr": 0.75,
            "minimum_net_rr_after_entry_delay": 0.60,
            "max_entry_drift_atr": 0.40,
            "max_holding_bars": 45,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )
    return config


def _run(config_path: Path, output: Path, week_index: int, candidate_dir: Path, repository: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(candidate_dir / "run_open_interest_deleveraging_validation.py"),
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
    families: Counter[str] = Counter()
    path = root / "scenario_events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            reasons[str(event.get("reason_code", "UNKNOWN"))] += 1
    trades_path = root / "trades.json"
    if trades_path.exists():
        for trade in json.loads(trades_path.read_text(encoding="utf-8")).get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
    return {"reason_counts": dict(reasons), "trade_family_counts": dict(families)}


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
        classification = "NO_COMPLETED_DELEVERAGING_RESPONSE"
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
        "metrics_context": metrics.get("futures_metrics_context", {}),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v5.0 Open-Interest Deleveraging Bifurcation",
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
    lines.extend(["", "## Diagnoses", ""])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(f"- **{name}**: `{diagnosis.get('classification')}` — `{diagnosis}`")
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/oidb-first-week"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)
    configs: dict[str, dict[str, Any]] = {}
    first: list[dict[str, Any]] = []

    for name, description, use_oi, reversal, continuation, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "oidb_use_open_interest": use_oi,
                "oidb_enable_reversal": reversal,
                "oidb_enable_continuation": continuation,
            },
        )
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
        "candidate": "candidate-06-oidb-v5.0",
        "design": "completed extreme OI contraction and aligned flow -> later exhaustion reclaim or persistent contraction -> structural objective",
        "first_week_results": first,
        "frozen_validation": [],
        "diagnoses": diagnoses,
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not implementation_ok:
        summary = {**base_summary, "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE"}
        _write(root, summary)
        return 5

    full = next(record for record in first if record["name"] == "oidb_deleveraging_bifurcation_full")
    if not full.get("gate_passed"):
        summary = {
            **base_summary,
            "terminal_status": "FIRST_WEEK_LOGIC_GATE_FAILED",
            "discarded": {"oidb_deleveraging_bifurcation_full": diagnoses["oidb_deleveraging_bifurcation_full"]},
        }
        _write(root, summary)
        return 2

    selected = "oidb_deleveraging_bifurcation_full"
    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.oidb.locked.json"
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
        "terminal_status": "THREE_WEEK_GATE_PASSED" if all_three else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED",
        "holdout_diagnoses": {f"week-{record['week_index'] + 1}": _diagnose(record) for record in frozen},
    }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
