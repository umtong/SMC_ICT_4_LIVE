#!/usr/bin/env python3
"""Conditional causal diagnostic for the external RAHTF clean-state gate."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import timedelta
from typing import Any

import trendrider_exact_public_mtf_v2_campaign as exact_campaign
import trendrider_pullback_long_v1_campaign as base
from trade_ledger_forensics import analyze as analyze_trades
from trendrider_public_mtf_context_v2 import build_sidecar

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
ELIGIBILITY = HERE / "evidence" / "trendrider-exact-public-mtf-v2" / "comparison.json"
WORK = ROOT / ".work" / "candidate-57-trendrider-rahtf-clean-v3"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-rahtf-clean-v3"
EVIDENCE = HERE / "evidence" / "trendrider-rahtf-clean-v3"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-rahtf-clean-v3"
FREEZE = HERE / "TRENDRIDER_RAHTF_CLEAN_STATE_V3_FREEZE.md"
ELIGIBLE_DECISIONS = {
    "SOURCE_STATE_INFORMATIVE_BUT_STANDALONE_STILL_WEAK",
    "EXACT_PUBLIC_MTF_HYPOTHESIS_REJECTED_NO_RETUNING",
}
CASES = ("exact_control", "rahtf_clean")
STAGES = exact_campaign.STAGES
FEATURE_KEYS = exact_campaign.FEATURE_KEYS + (
    "rahtf_raw_label",
    "rahtf_confirmed_label",
    "rahtf_slow_eff",
    "rahtf_clean_state_pass",
)


def safe(value: Any) -> Any:
    return base.safe(value)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def configure_exact_paths() -> None:
    exact_campaign.WORK = WORK
    exact_campaign.ARTIFACTS = ARTIFACTS
    exact_campaign.EVIDENCE = EVIDENCE
    exact_campaign.CACHE = CACHE


def assemble_case(case: str) -> None:
    if case == "exact_control":
        shutil.copy2(C51 / "router_trendrider_exact_impl.py", C51 / "router_trendrider_impl.py")
        shutil.copy2(C51 / "router_trendrider_exact_runtime.py", C51 / "router.py")
    elif case == "rahtf_clean":
        shutil.copy2(C51 / "router_trendrider_rahtf_impl.py", C51 / "router_trendrider_impl.py")
        shutil.copy2(C51 / "router_trendrider_rahtf_runtime.py", C51 / "router.py")
    else:
        raise ValueError(case)
    shutil.copy2(C51 / "strategy_trendrider_exact_base.py", C51 / "strategy.py")


def run_case(stage: base.Stage, case: str, sidecar: Path) -> dict[str, Any]:
    assemble_case(case)
    output = ARTIFACTS / stage.name / case
    workspace = WORK / "workspace" / stage.name / case
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    data_start = stage.start - timedelta(days=exact_campaign.WARMUP_DAYS)
    data_end = stage.end + timedelta(days=exact_campaign.RUNOFF_DAYS)
    config = exact_campaign.build_config(stage, "exact_public_mtf", sidecar)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config),
        "--start",
        data_start.isoformat(),
        "--end",
        data_end.isoformat(),
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": {"name": stage.name, "start": str(stage.start), "end": str(stage.end), "days": stage.days},
            "case": case,
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / stage.name / f"{case}.json", row)
        return row
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    starting = float(metrics.get("starting_nav") or 0.0)
    ending = float(metrics.get("ending_nav") or 0.0)
    metrics["geometric_daily_growth_signal_window"] = (
        (ending / starting) ** (1.0 / stage.days) - 1.0
        if starting > 0.0 and ending > 0.0
        else math.nan
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": {"name": stage.name, "start": str(stage.start), "end": str(stage.end), "days": stage.days},
        "case": case,
        "produced": True,
        "returncode": 0,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / stage.name / f"{case}.json", row)
    return row


def key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("episode_ts"), row.get("symbol"), row.get("side")


def paired_effect(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    control_rows = (control.get("trade_forensics") or {}).get("trade_ledger") or []
    candidate_rows = (candidate.get("trade_forensics") or {}).get("trade_ledger") or []
    left = {key(row): row for row in control_rows}
    right = {key(row): row for row in candidate_rows}
    shared = sorted(set(left) & set(right), key=str)
    removed = [left[item] for item in sorted(set(left) - set(right), key=str)]
    added = [right[item] for item in sorted(set(right) - set(left), key=str)]
    paired = [
        {
            "key": list(item),
            "control_exit": left[item].get("exit_reason"),
            "candidate_exit": right[item].get("exit_reason"),
            "control_r": left[item].get("actual_r"),
            "candidate_r": right[item].get("actual_r"),
            "delta_r": number(right[item].get("actual_r")) - number(left[item].get("actual_r")),
        }
        for item in shared
    ]
    source_winner = lambda row: (
        any(token in str(row.get("exit_reason") or "") for token in ("ROI", "TRAILING"))
        and number(row.get("actual_r")) > 0.0
    )
    early_loss = lambda row: "EARLY_LOSS" in str(row.get("exit_reason") or "")
    control_winners = [row for row in control_rows if source_winner(row)]
    removed_winners = [row for row in removed if source_winner(row)]
    removed_early = [row for row in removed if early_loss(row)]
    preserved_winners = [
        item
        for item in shared
        if source_winner(left[item]) and number(right[item].get("actual_r")) > 0.0
    ]
    best = max(control_rows, key=lambda row: number(row.get("actual_r")), default=None)
    best_key = key(best) if best else None
    best_preserved = (
        best_key in right and number(right[best_key].get("actual_r")) > 0.0
        if best_key is not None
        else False
    )
    paired_delta = sum(number(row.get("delta_r")) for row in paired)
    removed_sum = sum(number(row.get("actual_r")) for row in removed)
    added_sum = sum(number(row.get("actual_r")) for row in added)
    total_structural_delta = paired_delta - removed_sum + added_sum
    added_share = (
        abs(added_sum) / max(abs(total_structural_delta), 1e-12)
        if total_structural_delta > 0.0
        else None
    )
    return {
        "paired_count": len(shared),
        "paired": paired,
        "removed_control_trades": removed,
        "added_candidate_trades": added,
        "control_source_winner_count": len(control_winners),
        "removed_source_winner_count": len(removed_winners),
        "preserved_source_winner_count": len(preserved_winners),
        "source_winner_preservation_share": (
            len(preserved_winners) / len(control_winners) if control_winners else None
        ),
        "removed_early_loss_count": len(removed_early),
        "best_control_trade_key": list(best_key) if best_key else None,
        "best_control_trade_r": best.get("actual_r") if best else None,
        "best_control_trade_preserved_positive": best_preserved,
        "paired_delta_r": paired_delta,
        "removed_control_sum_r": removed_sum,
        "added_candidate_sum_r": added_sum,
        "total_structural_delta_r": total_structural_delta,
        "added_candidate_share_of_positive_delta": added_share,
    }


def metric_delta(candidate: dict[str, Any], control: dict[str, Any]) -> dict[str, float]:
    left = candidate.get("metrics") or {}
    right = control.get("metrics") or {}
    return {
        metric: number(left.get(metric)) - number(right.get(metric))
        for metric in (
            "total_return",
            "geometric_daily_growth_signal_window",
            "max_drawdown",
            "expectancy_usdt",
            "profit_factor",
            "trades",
        )
    }


def classify(results: dict[str, dict[str, dict[str, Any]]], eligibility_decision: str) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    mechanics = True
    for stage in STAGES:
        control = results[stage.name]["exact_control"]
        candidate = results[stage.name]["rahtf_clean"]
        mechanics = mechanics and exact_campaign.account_ok(control) and exact_campaign.account_ok(candidate)
        comparisons[stage.name] = {
            "metric_delta": metric_delta(candidate, control),
            "paired_effect": paired_effect(control, candidate),
            "control": control,
            "candidate": candidate,
        }
    november = comparisons[STAGES[0].name]
    june = comparisons[STAGES[1].name]
    nov_effect = november["paired_effect"]
    june_effect = june["paired_effect"]
    nov_metrics = november["candidate"].get("metrics") or {}
    june_control_metrics = june["control"].get("metrics") or {}
    june_metrics = june["candidate"].get("metrics") or {}
    june_diag = june["candidate"].get("diagnostics") or {}
    reasons = june_diag.get("unresolved_reason_counts") or {}
    label_rejections = int(reasons.get("RAHTF_CONFIRMED_LABEL_REJECTED") or 0)
    drift_rejections = int(reasons.get("RAHTF_SLOW_DRIFT_REJECTED") or 0)
    context_rejections = int(reasons.get("RAHTF_CONTEXT_NOT_READY") or 0)
    november_preserved = (
        number(nov_effect.get("source_winner_preservation_share"), 0.0) >= 0.75
        and bool(nov_effect.get("best_control_trade_preserved_positive"))
    )
    selective = (
        int(june_effect.get("removed_early_loss_count") or 0)
        > int(nov_effect.get("removed_source_winner_count") or 0)
    )
    june_best_preserved = bool(june_effect.get("best_control_trade_preserved_positive"))
    june_improved = (
        number(june_metrics.get("expectancy_usdt")) > number(june_control_metrics.get("expectancy_usdt"))
        and number(june_metrics.get("profit_factor")) > number(june_control_metrics.get("profit_factor"))
    )
    no_slot_outlier = (
        june_effect.get("added_candidate_share_of_positive_delta") is None
        or number(june_effect.get("added_candidate_share_of_positive_delta"), 1.0) <= 0.50
    )
    state_changed = label_rejections + drift_rejections > 0
    causal_support = (
        mechanics
        and november_preserved
        and selective
        and june_best_preserved
        and june_improved
        and no_slot_outlier
        and state_changed
    )
    june_positive = (
        number(june_metrics.get("expectancy_usdt")) > 0.0
        and number(june_metrics.get("total_return")) > 0.0
        and number(june_metrics.get("profit_factor")) > 1.0
    )
    november_positive = (
        number(nov_metrics.get("expectancy_usdt")) > 0.0
        and number(nov_metrics.get("total_return")) > 0.0
        and number(nov_metrics.get("profit_factor")) > 1.0
    )
    if not mechanics:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif causal_support and june_positive and november_positive:
        decision = "RAHTF_STATE_COMPONENT_SUPPORTED_POLICY_FRESH_REQUIRED"
    elif causal_support:
        decision = "RAHTF_CAUSAL_STATE_INFORMATIVE_BUT_STANDALONE_WEAK"
    else:
        decision = "RAHTF_CLEAN_STATE_HYPOTHESIS_REJECTED_NO_RETUNING"
    return {
        "eligibility_decision": eligibility_decision,
        "mechanically_valid": mechanics,
        "decision": decision,
        "policy_fresh_authorized": decision == "RAHTF_STATE_COMPONENT_SUPPORTED_POLICY_FRESH_REQUIRED",
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "thresholds_searched": False,
        "predictions": {
            "november_winner_engine_preserved": november_preserved,
            "selective_june_early_loss_rejection": selective,
            "june_best_control_trade_preserved": june_best_preserved,
            "june_expectancy_and_pf_improved": june_improved,
            "improvement_not_slot_outlier_dominated": no_slot_outlier,
            "state_gate_changed_entries": state_changed,
            "june_label_rejections": label_rejections,
            "june_slow_drift_rejections": drift_rejections,
            "june_context_not_ready": context_rejections,
            "june_candidate_positive": june_positive,
            "november_candidate_positive": november_positive,
        },
        "comparisons": comparisons,
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# TrendRider + external RAHTF clean-state v3 diagnostic",
        "",
        f"- eligibility: `{result.get('eligibility_decision')}`",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- decision: `{result.get('decision')}`",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- policy-fresh authorized: {result.get('policy_fresh_authorized')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| stage | case | trades | W/L | PF | expectancy USDT | signal-window geo/day | return | MDD |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage in STAGES:
        comparison = (result.get("comparisons") or {}).get(stage.name) or {}
        for case in CASES:
            row = comparison.get("control" if case == "exact_control" else "candidate") or {}
            metrics = row.get("metrics") or {}
            lines.append(
                f"| {stage.name} | {case} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth_signal_window')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} |"
            )
    lines.extend(
        [
            "",
            "## Predeclared causal predictions",
            "",
            f"`{json.dumps(result.get('predictions') or {}, sort_keys=True)}`",
            "",
            "The complete external RAHTF fade strategy is not used.  This result measures only whether its frozen clean-trend label and slow-drift confirmation solve the observed TrendRider state error.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError(f"missing freeze: {FREEZE}")
    configure_exact_paths()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if not ELIGIBILITY.is_file():
        result = {
            "eligibility_decision": "MISSING_EXACT_MTF_EVIDENCE",
            "mechanically_valid": True,
            "decision": "SKIPPED_NOT_ELIGIBLE",
            "policy_fresh_authorized": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "thresholds_searched": False,
            "comparisons": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0
    eligibility = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    eligibility_decision = str(eligibility.get("decision") or "")
    if not bool(eligibility.get("mechanically_valid")) or eligibility_decision not in ELIGIBLE_DECISIONS:
        result = {
            "eligibility_decision": eligibility_decision,
            "mechanically_valid": True,
            "decision": "SKIPPED_NOT_ELIGIBLE",
            "policy_fresh_authorized": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "thresholds_searched": False,
            "comparisons": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for stage in STAGES:
        sidecar = WORK / "mtf" / f"{stage.name}.json"
        build_sidecar(sidecar, stage.start, stage.end + timedelta(days=exact_campaign.RUNOFF_DAYS))
        results[stage.name] = {
            case: run_case(stage, case, sidecar) for case in CASES
        }
    result = classify(results, eligibility_decision)
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if result.get("mechanically_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
