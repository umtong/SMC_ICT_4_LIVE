"""Controlled UOAM first-week matrix and sealed-week promotion."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _run


VARIANTS = (
    (
        "uoam_bound_objective_with_causal_exit",
        "Pre-existing untouched objective ladder; one entry per objective; full-origin, boundary, and bias-replacement invalidation also close a live position.",
        True,
        True,
    ),
    (
        "uoam_bound_objective_no_position_exit_ablation",
        "One-variable ablation: identical objective binding and pre-entry invalidation, but an already-open position remains managed only by its unchanged bracket and timeout.",
        False,
        True,
    ),
    (
        "uoam_dynamic_nearest_hml_reference",
        "Committed HML reference: dynamic nearest objective at signal time, no objective lifecycle, no scenario-declared position exit; ineligible for selection.",
        False,
        False,
    ),
)


def _base(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _equilibrium_base(copy.deepcopy(dict(raw)))
    config["logic"].update(
        {
            "engine": "UNRESOLVED_OBJECTIVE_LIFECYCLE",
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsc_bias_atr_bars": 12,
            "hsc_bias_volume_bars": 12,
            "hsc_bias_breakout_lookback": 4,
            "hsc_bias_acceptance_close_atr": 0.02,
            "hsc_bias_range_atr": 0.75,
            "hsc_bias_body_fraction": 0.50,
            "hsc_bias_relative_volume": 0.95,
            "hsc_bias_flow_ratio": 0.04,
            "hsc_bias_close_location": 0.68,
            "hsc_bias_lifetime_periods": 3.0,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
            "hml_pool_families": "SWING_AND_EQUAL",
            "hml_equal_lookback_bars": 8,
            "hml_equal_min_intervening_bars": 1,
            "hml_equal_tolerance_range_fraction": 0.08,
            "hml_equal_rejection_close_fraction": 0.35,
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_max_impulse_position": 0.70,
            "hsc_response_bars": 3,
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "minimum_structural_rr": 0.75,
            "max_holding_bars": 60,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
            "uoam_use_origin_invalidation": True,
        },
    )
    return config


def _run_variant(
    config: Mapping[str, Any],
    *,
    name: str,
    description: str,
    eligible: bool,
    week_index: int,
    root: Path,
    candidate_dir: Path,
    repository: Path,
) -> dict[str, Any]:
    runtime = copy.deepcopy(dict(config))
    if week_index > 0:
        runtime.setdefault("validation", {})["stage"] = "three_week_validation"
    configs = root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    path = configs / f"{name}-week-{week_index + 1}.json"
    path.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = root / "runs" / name / f"week-{week_index + 1}"
    record = _run(path, output, week_index, candidate_dir, repository)
    record.update(
        {
            "name": name,
            "description": description,
            "eligible_for_selection": eligible,
            "week_index": week_index,
            "config_path": str(path.relative_to(repository)),
            "run_output": str(output.relative_to(repository)),
            "causal_counts": _evidence_counts(output),
        },
    )
    return record


def _evidence_counts(output: Path) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    path = output / "scenario_events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            reasons[str(payload.get("reason_code", "UNKNOWN"))] += 1
    return {"reason_counts": dict(reasons)}


def _implementation_ok(records: list[Mapping[str, Any]]) -> bool:
    return bool(records) and all(
        int(record.get("returncode", 1)) == 0
        and isinstance(record.get("metrics"), dict)
        and not record["metrics"].get("errors")
        for record in records
    )


def _reference_regression(reference: Mapping[str, Any], repository: Path) -> dict[str, Any]:
    baseline_path = (
        repository
        / "artifacts/candidate-06/hml-first-week/hml_60m_5m_swing_equal_full_response/metrics.json"
    )
    if not baseline_path.exists() or not reference.get("metrics"):
        return {"passed": False, "reason": "HML_REFERENCE_OR_METRICS_MISSING"}
    expected = json.loads(baseline_path.read_text(encoding="utf-8"))
    actual = reference["metrics"]
    keys = (
        "geometric_daily_nav_growth",
        "trades",
        "wins",
        "win_rate",
        "profit_factor",
        "max_drawdown_nav",
        "net_pnl_after_cost",
    )
    differences: dict[str, Any] = {}
    for key in keys:
        left, right = expected.get(key), actual.get(key)
        equal = (
            abs(float(left) - float(right)) <= 1e-12
            if isinstance(left, (int, float)) and isinstance(right, (int, float))
            else left == right
        )
        if not equal:
            differences[key] = {"expected": left, "actual": right}
    return {
        "passed": not differences,
        "baseline_path": str(baseline_path.relative_to(repository)),
        "keys": list(keys),
        "differences": differences,
    }


def _diagnose(record: Mapping[str, Any]) -> dict[str, Any]:
    metrics = dict(record.get("metrics") or {})
    if not metrics:
        return {"classification": "IMPLEMENTATION_OR_RUNTIME_FAILURE"}
    reasons = dict((record.get("causal_counts") or {}).get("reason_counts", {}))
    if metrics.get("gate_passed"):
        classification = "GATE_PASSED"
    elif float(metrics.get("geometric_daily_nav_growth", -1.0)) <= 0.0:
        classification = "NEGATIVE_COST_AFTER_EXPECTANCY"
    elif int(metrics.get("trades", 0)) < 10:
        classification = "INSUFFICIENT_INDEPENDENT_BOUND_OBJECTIVES"
    elif float(metrics.get("win_rate", 0.0)) < 0.45:
        classification = "BOUND_OBJECTIVE_PATH_FAILED"
    else:
        classification = "PARTIAL_GATE_FAILURE"
    return {
        "classification": classification,
        "geometric_daily_nav_growth": metrics.get("geometric_daily_nav_growth"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown_nav": metrics.get("max_drawdown_nav"),
        "gate_failures": metrics.get("gate_failures", []),
        "objective_bindings": reasons.get("PREEXISTING_UNRESOLVED_OBJECTIVE_BOUND", 0),
        "objective_consumptions": reasons.get("BOUND_OBJECTIVE_CONSUMED", 0),
        "origin_invalidations": reasons.get("UOAM_BOUND_IMPULSE_ORIGIN_REBALANCED", 0),
        "no_objective_contexts": reasons.get("NO_PREEXISTING_UNRESOLVED_OBJECTIVE", 0),
    }


def _render(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Candidate 06 Unresolved-Objective Auction Mitigation (UOAM)",
        "",
        f"Implementation status: `{summary.get('implementation_status')}`",
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
                eligible=record.get("eligible_for_selection", True),
                gate=record.get("gate_passed"),
                growth=float(metrics.get("geometric_daily_nav_growth", -1.0)),
                trades=metrics.get("trades"),
                wins=metrics.get("wins"),
                win=float(metrics.get("win_rate", 0.0)),
                pf=metrics.get("profit_factor"),
                dd=float(metrics.get("max_drawdown_nav", 0.0)),
                failures=", ".join(metrics.get("gate_failures", [])),
            ),
        )
    lines.extend(["", "## Reference regression", "", f"`{summary.get('reference_regression')}`", "", "## Diagnoses"])
    for name, diagnosis in summary.get("diagnoses", {}).items():
        lines.append(f"- **{name}**: `{diagnosis}`")
    return "\n".join(lines) + "\n"


def _write(root: Path, summary: Mapping[str, Any]) -> None:
    (root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "SUMMARY.md").write_text(_render(summary), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/uoam-first-week"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    raw = json.loads((candidate_dir / "config.json").read_text(encoding="utf-8"))
    base = _base(raw)

    configs: dict[str, dict[str, Any]] = {}
    first: list[dict[str, Any]] = []
    for name, description, exit_open, lifecycle in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "uoam_use_objective_lifecycle": lifecycle,
                "uoam_exit_open_position_on_invalidation": exit_open,
            },
        )
        eligible = lifecycle
        configs[name] = config
        first.append(
            _run_variant(
                config,
                name=name,
                description=description,
                eligible=eligible,
                week_index=0,
                root=root,
                candidate_dir=candidate_dir,
                repository=repository,
            ),
        )

    reference = next(value for value in first if value["name"] == "uoam_dynamic_nearest_hml_reference")
    regression = _reference_regression(reference, repository)
    diagnoses = {value["name"]: _diagnose(value) for value in first}
    summary: dict[str, Any] = {
        "candidate": "candidate-06-uoam-v2.1",
        "design": "completed HTF acceptance -> bind pre-existing untouched opposite liquidity ladder -> one-use counter-bias LTF sweep -> separate response -> bound unresolved objective -> event-driven invalidation",
        "causality_contract": "an objective must have been confirmed before the accepting HTF bar and lie beyond that bar's extreme; current or later pools cannot retroactively become its target",
        "independence_contract": "each bound objective can arm at most one entry and is consumed once before the ladder advances",
        "first_week_results": first,
        "frozen_validation": [],
        "selected": None,
        "all_three_weeks_passed": False,
        "long_evaluation_authorized": False,
        "reference_regression": regression,
        "diagnoses": diagnoses,
    }
    if not _implementation_ok(first) or not regression.get("passed"):
        summary["implementation_status"] = "FAIL"
        summary["terminal_status"] = "IMPLEMENTATION_OR_REFERENCE_REGRESSION_FAILURE"
        _write(root, summary)
        return 5

    selected = next(
        (
            value["name"]
            for value in first
            if value.get("eligible_for_selection") and value.get("gate_passed")
        ),
        None,
    )
    summary["implementation_status"] = "PASS"
    if selected is None:
        summary["terminal_status"] = "FIRST_WEEK_LOGIC_GATE_FAILED"
        summary["discarded"] = {name: diagnoses[name] for name, *_ in VARIANTS if name != "uoam_dynamic_nearest_hml_reference"}
        _write(root, summary)
        return 2

    summary["selected"] = selected
    locked = copy.deepcopy(configs[selected])
    locked.setdefault("validation", {})["stage"] = "three_week_validation"
    locked_path = candidate_dir / "config.uoam.locked.json"
    locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["locked_config"] = str(locked_path.relative_to(repository))

    frozen: list[dict[str, Any]] = []
    for week_index in (1, 2):
        record = _run_variant(
            locked,
            name=selected,
            description=next(value[1] for value in VARIANTS if value[0] == selected),
            eligible=True,
            week_index=week_index,
            root=root,
            candidate_dir=candidate_dir,
            repository=repository,
        )
        frozen.append(record)
        if not _implementation_ok([record]):
            summary.update(
                {
                    "frozen_validation": frozen,
                    "implementation_status": "HOLDOUT_RUNTIME_FAILURE",
                    "terminal_status": "IMPLEMENTATION_OR_RUNTIME_FAILURE",
                },
            )
            _write(root, summary)
            return 5

    all_three = all(value.get("gate_passed") for value in frozen)
    summary.update(
        {
            "frozen_validation": frozen,
            "holdout_diagnoses": {
                f"week-{int(value['week_index']) + 1}": _diagnose(value)
                for value in frozen
            },
            "all_three_weeks_passed": all_three,
            "long_evaluation_authorized": all_three,
            "terminal_status": "THREE_WEEK_GATE_PASSED" if all_three else "FROZEN_HOLDOUT_LOGIC_GATE_FAILED",
        },
    )
    if not all_three:
        summary["discarded"] = {
            selected: {
                "classification": "FAILED_UNCHANGED_FROZEN_HOLDOUT",
                "first_week": diagnoses[selected],
                "holdouts": summary["holdout_diagnoses"],
            },
        }
    _write(root, summary)
    return 0 if all_three else 3


if __name__ == "__main__":
    raise SystemExit(main())
