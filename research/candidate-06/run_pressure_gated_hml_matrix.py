#!/usr/bin/env python3
"""Pressure-gated HML full mechanism and causal attributions."""
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
        "phml_gate_and_exit",
        "HML entry must align with a live sequential pressure regime; opposite pressure change, origin loss or expiry invalidates a pending/live trade.",
        True,
        True,
        True,
    ),
    (
        "phml_gate_only_ablation",
        "One-variable attribution: pressure alignment gates entry, while the original HML bracket and timeout alone manage an open position.",
        True,
        False,
        False,
    ),
    (
        "phml_exit_only_ablation",
        "One-variable attribution: all original HML entries remain; live sequential pressure termination can close a position early.",
        False,
        True,
        False,
    ),
    (
        "phml_hml_reference",
        "Exact HML reference: pressure state is observed diagnostically but cannot gate or exit a trade.",
        False,
        False,
        False,
    ),
)

REFERENCE = {
    "geometric_daily_nav_growth": 0.010243468057223204,
    "trades": 10,
    "wins": 7,
    "win_rate": 0.7,
    "profit_factor": 1.806344565682457,
    "max_drawdown_nav": 0.09329252748844692,
}


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(dict(raw))
    config["version"] = "5.3.0"
    config["hypothesis"] = (
        "The HML sweep-response entry has positive local information only while an independently detected sequential signed-flow pressure state is aligned and alive; pressure termination is a causal invalidation rather than a risk overlay."
    )
    config["logic"].update(
        {
            "engine": "PRESSURE_GATED_HIERARCHICAL_MULTI_LIQUIDITY",
            "signal_submission_timing": "NEXT_COMPLETED_BAR",
            "hsc_bias_period_minutes": 60,
            "hsc_bias_atr_bars": 12,
            "hsc_bias_volume_bars": 12,
            "hsc_bias_breakout_lookback": 4,
            "hsc_bias_range_atr": 0.75,
            "hsc_bias_body_fraction": 0.50,
            "hsc_bias_relative_volume": 0.95,
            "hsc_bias_close_location": 0.68,
            "hsc_bias_flow_ratio": 0.04,
            "hsc_bias_acceptance_close_atr": 0.02,
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsc_bias_lifetime_periods": 3.0,
            "hsc_liquidity_period_minutes": 5,
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_response_bars": 3,
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_max_impulse_position": 0.70,
            "hsc_cooldown_bars": 2,
            "hsc_use_flow_proxy": True,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
            "hml_pool_families": "SWING_AND_EQUAL",
            "hml_equal_lookback_bars": 8,
            "hml_equal_min_intervening_bars": 1,
            "hml_equal_tolerance_range_fraction": 0.08,
            "hml_equal_rejection_close_fraction": 0.35,
            "phml_flow_history": 120,
            "phml_minimum_history": 60,
            "phml_cusum_drift": 0.25,
            "phml_onset_threshold": 4.0,
            "phml_onset_window_bars": 5,
            "phml_onset_displacement_atr": 0.35,
            "phml_exit_cusum_drift": 0.20,
            "phml_exit_threshold": 3.5,
            "phml_max_regime_bars": 30,
            "max_holding_bars": 60,
            "minimum_structural_rr": 0.75,
            "minimum_net_rr_after_entry_delay": 0.60,
            "max_entry_drift_atr": 0.40,
            "enforce_favorable_drift_guard": True,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
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
            str(candidate_dir / "run_pressure_gated_hml_validation.py"),
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


def _reference_regression(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = record.get("metrics")
    if not isinstance(metrics, Mapping):
        return {"passed": False, "reason": "missing metrics"}
    differences: dict[str, Any] = {}
    for key, expected in REFERENCE.items():
        actual = metrics.get(key)
        if isinstance(expected, int):
            if int(actual) != expected:
                differences[key] = {"expected": expected, "actual": actual}
        elif actual is None or abs(float(actual) - expected) > 1e-9:
            differences[key] = {"expected": expected, "actual": actual}
    return {"passed": not differences, "differences": differences}


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
        classification = "NO_COMPLETED_PRESSURE_ALIGNED_HML_ENTRY"
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
        "# Candidate 06 v5.3 Pressure-Gated HML",
        "",
        f"Terminal status: `{summary.get('terminal_status')}`",
        f"Selected: `{summary.get('selected')}`" if summary.get("selected") else "Selected: none",
        f"HML reference regression: `{summary.get('reference_regression')}`",
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
        default=Path("artifacts/candidate-06/phml-first-week"),
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

    for name, description, use_gate, use_exit, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "phml_use_pressure_gate": use_gate,
                "phml_use_pressure_exit": use_exit,
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
    reference = next(record for record in first if record["name"] == "phml_hml_reference")
    regression = _reference_regression(reference)
    base_summary: dict[str, Any] = {
        "candidate": "candidate-06-phml-v5.3",
        "design": "exact HML signal -> independently active pressure alignment -> original delayed entry -> live opposite change-point invalidation",
        "first_week_results": first,
        "frozen_validation": [],
        "diagnoses": diagnoses,
        "reference_regression": regression,
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
    }
    if not implementation_ok or not regression["passed"]:
        _write(root, {**base_summary, "terminal_status": "IMPLEMENTATION_OR_REFERENCE_REGRESSION_FAILURE"})
        return 5

    full = next(record for record in first if record["name"] == "phml_gate_and_exit")
    if not full.get("gate_passed"):
        _write(
            root,
            {
                **base_summary,
                "terminal_status": "FIRST_WEEK_LOGIC_GATE_FAILED",
                "discarded": {"phml_gate_and_exit": diagnoses["phml_gate_and_exit"]},
            },
        )
        return 2

    selected = "phml_gate_and_exit"
    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.phml.locked.json"
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
