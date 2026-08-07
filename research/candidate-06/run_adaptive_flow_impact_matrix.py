#!/usr/bin/env python3
"""Run the predeclared AFIB mechanism matrix and staged frozen validation."""

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
        "afib_full_bifurcation",
        "Efficient surprising flow continues; absorbed surprising flow reverses after a separate response.",
        True,
        True,
        True,
        True,
    ),
    (
        "afib_absorbed_reversal_only",
        "Mechanism isolation: only extreme aggressive flow with weak impact and opposite response may trade.",
        False,
        True,
        True,
        True,
    ),
    (
        "afib_efficient_continuation_only",
        "Mechanism isolation: only efficient flow surprise with same-direction follow-through may trade.",
        True,
        False,
        True,
        True,
    ),
    (
        "afib_raw_flow_reference",
        "Information-source ablation: fixed raw signed-flow ratio replaces prior-only robust surprise normalization.",
        True,
        True,
        False,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["candidate"] = "candidate-06-afib-v6"
    config["version"] = "6.0.0"
    config["hypothesis"] = (
        "Aggressive order flow is tradable only after realized price impact identifies whether "
        "liquidity was consumed efficiently or replenished strongly enough to absorb the shock, "
        "and a separate completed minute confirms the corresponding continuation or reversal."
    )
    config["logic"].update(
        {
            "engine": "ADAPTIVE_FLOW_IMPACT_BIFURCATION",
            "signal_submission_timing": "ON_SIGNAL_CLOSE",
            "afib_profile_period_minutes": 15,
            "afib_value_area_fraction": 0.70,
            "afib_flow_history": 180,
            "afib_activity_history": 120,
            "afib_minimum_history": 90,
            "afib_flow_scale_floor": 0.005,
            "afib_flow_z_threshold": 2.0,
            "afib_raw_flow_ratio_threshold": 0.08,
            "afib_min_volume_ratio": 1.10,
            "afib_min_trade_ratio": 0.95,
            "afib_min_range_atr": 0.35,
            "afib_continuation_impact_atr": 0.18,
            "afib_continuation_body_atr": 0.18,
            "afib_continuation_close_location": 0.68,
            "afib_absorption_impact_atr": 0.08,
            "afib_absorption_wick_fraction": 0.25,
            "afib_absorption_close_location_ceiling": 0.58,
            "afib_confirmation_bars": 3,
            "afib_confirmation_flow_z": 0.35,
            "afib_confirmation_body_atr": 0.10,
            "afib_confirmation_close_location": 0.58,
            "afib_midpoint_tolerance_atr": 0.03,
            "afib_stop_buffer_atr": 0.05,
            "afib_projection_fraction": 1.0,
            "afib_cooldown_bars": 2,
            "minimum_structural_rr": 0.80,
            "minimum_net_rr_after_entry_delay": 0.75,
            "max_entry_drift_atr": 0.40,
            "max_holding_bars": 30,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
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
            str(candidate_dir / "run_adaptive_flow_impact_validation.py"),
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
        "stdout_tail": completed.stdout[-6000:],
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


def _causal_counts(run_output: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    events = run_output / "scenario_events.jsonl"
    if events.exists():
        for line in events.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                reasons[str(payload.get("reason_code", "UNKNOWN"))] += 1
    families: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    trades_path = run_output / "trades.json"
    if trades_path.exists():
        for trade in json.loads(trades_path.read_text(encoding="utf-8")).get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
            outcomes[str(trade.get("outcome", "UNKNOWN"))] += 1
    return {
        "reason_counts": dict(reasons),
        "trade_family_counts": dict(families),
        "outcome_counts": dict(outcomes),
    }


def _diagnose(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(record.get("metrics", {}))
    if not metrics:
        return {
            "classification": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": record.get("error") or record.get("stderr_tail"),
        }
    growth = float(metrics.get("geometric_daily_nav_growth", 0.0))
    trades = int(metrics.get("trades", 0))
    profit_factor = metrics.get("profit_factor")
    if trades == 0:
        classification = "NO_COMPLETED_FLOW_IMPACT_RESPONSE"
    elif growth <= 0.0 or (profit_factor is not None and float(profit_factor) < 1.0):
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
    elif not metrics.get("gate_passed"):
        classification = "POSITIVE_BUT_GATE_INCOMPLETE"
    else:
        classification = "GATE_PASSED"
    return {
        "classification": classification,
        "geometric_daily_nav_growth": growth,
        "trades": trades,
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": profit_factor,
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "mean_r_after_cost": metrics.get("mean_r_after_cost"),
        "scenario_breakdown": metrics.get("scenario_breakdown", {}),
        "gate_failures": metrics.get("gate_failures", []),
        "entry_abstentions": metrics.get("diagnostics", {}).get("entry_abstentions", {}),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 v6.0 Adaptive Flow-Impact Bifurcation",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Selected: `{summary.get('selected')}`" if summary.get("selected") else "Selected: none",
        "",
        "|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|mean R|max DD|failures|",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    records = [*summary.get("first_week_results", []), *summary.get("frozen_validation", [])]
    for record in records:
        metrics = record.get("metrics", {})
        lines.append(
            "|{name}|{week}|{eligible}|{gate}|{growth:.6%}|{trades}|{wins}|{win:.2%}|{pf}|{mean_r:.3f}|{dd:.2%}|{failures}|".format(
                name=record.get("name"),
                week=int(record.get("week_index", 0)) + 1,
                eligible=record.get("eligible_for_selection"),
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", 0.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                mean_r=float(metrics.get("mean_r_after_cost", 0.0)),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    lines.extend(["", "## Diagnoses", ""])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(f"- **{name}**: `{diagnosis.get('classification')}` — `{diagnosis}`")
    lines.extend(
        [
            "",
            "## Fixed causal contract",
            "",
            "- Signed aggressive flow is normalized only with completed prior minutes.",
            "- The initiating flow shock cannot emit an order.",
            "- Efficient impact and absorbed impact are mutually classified before confirmation.",
            "- A separate completed response is mandatory for continuation or reversal.",
            "- All orders, fills, fees, margin, positions and NAV are native NautilusTrader outputs.",
            "- Approved trades risk three percent of whole-account NAV after explicit costs.",
        ],
    )
    if summary.get("error"):
        lines.extend(["", "## Error", "", "```text", str(summary["error"])[-16000:], "```"])
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/afib-staged"),
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
    config_dir = root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)

    for name, description, continuation, reversal, robust, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "afib_enable_continuation": continuation,
                "afib_enable_reversal": reversal,
                "afib_use_robust_surprise": robust,
            },
        )
        configs[name] = config
        config_path = config_dir / f"{name}-week-1.json"
        config_path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / name / "week-1"
        record = _run(config_path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "eligible_for_selection": eligible,
                "week_index": 0,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _causal_counts(run_output),
            },
        )
        first.append(record)

    diagnoses = {record["name"]: _diagnose(record) for record in first}
    valid_metrics = all(
        int(record.get("returncode", 1)) == 0 and record.get("metrics")
        for record in first
    )
    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-afib-v6.0",
        "design": (
            "prior-only signed-flow surprise -> realized impact bifurcation -> "
            "separate continuation/reversal response -> structural objective"
        ),
        "variant_priority": [value[0] for value in VARIANTS],
        "selection_rule": (
            "first eligible gate-qualified mechanism in fixed ex-ante priority; "
            "raw-flow reference cannot be selected"
        ),
        "first_week_results": first,
        "frozen_validation": [],
        "diagnoses": diagnoses,
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not valid_metrics:
        summary = {
            **base_summary,
            "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE",
            "error": "At least one predeclared first-week variant did not produce valid Nautilus metrics.",
        }
        _write(root, summary)
        return 5

    selected = next(
        (
            record["name"]
            for record in first
            if record.get("eligible_for_selection") and record.get("gate_passed")
        ),
        None,
    )
    if selected is None:
        summary = {**base_summary, "terminal_status": "FIRST_WEEK_LOGIC_GATE_FAILED"}
        _write(root, summary)
        return 2

    locked = copy.deepcopy(configs[selected])
    locked["validation"]["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.afib.locked.json"
    locked_path.write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        config_path = config_dir / f"{selected}-week-{week_index + 1}.json"
        config_path.write_text(
            json.dumps(locked, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        run_output = root / "runs" / selected / f"week-{week_index + 1}"
        record = _run(config_path, run_output, week_index, candidate_dir, repository)
        record.update(
            {
                "name": selected,
                "description": next(value[1] for value in VARIANTS if value[0] == selected),
                "eligible_for_selection": True,
                "week_index": week_index,
                "config_path": str(config_path.relative_to(repository)),
                "run_output": str(run_output.relative_to(repository)),
                "causal_counts": _causal_counts(run_output),
            },
        )
        frozen.append(record)
        if int(record.get("returncode", 1)) != 0 or not record.get("metrics"):
            summary = {
                **base_summary,
                "selected": selected,
                "locked_config": str(locked_path.relative_to(repository)),
                "frozen_validation": frozen,
                "terminal_status": "IMPLEMENTATION_OR_DATA_FAILURE_ON_FROZEN_WEEK",
                "error": record.get("error") or record.get("stderr_tail"),
            }
            _write(root, summary)
            return 5
        if not record.get("gate_passed"):
            summary = {
                **base_summary,
                "selected": selected,
                "locked_config": str(locked_path.relative_to(repository)),
                "frozen_validation": frozen,
                "terminal_status": f"FROZEN_WEEK_{week_index + 1}_LOGIC_GATE_FAILED",
            }
            _write(root, summary)
            return 3

    summary = {
        **base_summary,
        "selected": selected,
        "locked_config": str(locked_path.relative_to(repository)),
        "frozen_validation": frozen,
        "terminal_status": "THREE_WEEK_GATE_PASSED",
        "all_three_weeks_passed": True,
        "long_evaluation_authorized": True,
    }
    _write(root, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
