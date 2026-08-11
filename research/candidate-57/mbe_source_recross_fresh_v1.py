#!/usr/bin/env python3
"""Conditional fresh comparison of the frozen MBE source-recross repair."""
from __future__ import annotations

from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import mbe_collision_topology_fresh_v1 as topology

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-mbe-source-recross-fresh-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-mbe-source-recross-fresh-v1"
EVIDENCE = HERE / "evidence" / "mbe-source-recross-fresh-v1"
CACHE = ROOT / ".cache" / "candidate-57-mbe-source-recross-fresh-v1"
FREEZE = HERE / "MBE_SOURCE_RECROSS_FRESH_V1_FREEZE.md"
ELIGIBILITY = HERE / "evidence" / "mbe-lifecycle-forensic-v1" / "analysis.json"

FRESH_START = date(2024, 4, 1)
FRESH_END = date(2024, 4, 30)
FRESH_DAYS = (FRESH_END - FRESH_START).days + 1
CASES = ("source_control", "source_recross")
FEATURE_KEYS = (
    "rsi", "rsi_cross_magnitude", "tema_to_middle_bps", "tema_slope_bps",
    "bb_width_bps", "return_1h_bps", "return_4h_bps", "return_8h_bps",
    "realized_vol_1h_bps", "range_1h_bps",
)
ELIGIBLE_DECISION = "MBE_SOURCE_RECROSS_INVALIDATION_SUPPORTED_FRESH_REQUIRED"


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def configure_reused_campaign() -> None:
    topology.WORK = WORK
    topology.ARTIFACTS = ARTIFACTS
    topology.EVIDENCE = EVIDENCE
    topology.CACHE = CACHE


def build_config(case: str, horizon: int) -> Path:
    path = topology.build_config(f"fresh_2024_04_{case}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["strategy"].update(
        {
            "mbe_source_recross_enabled": case == "source_recross",
            "mbe_source_recross_min_age_minutes": int(horizon),
        }
    )
    dump(path, payload)
    return path


def run_case(case: str, horizon: int) -> dict[str, Any]:
    output = ARTIFACTS / case
    workspace = WORK / "workspace" / case
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable, str(C51 / "launch.py"),
            "--config", str(build_config(case, horizon)),
            "--start", FRESH_START.isoformat(), "--end", FRESH_END.isoformat(),
            "--cache", str(CACHE), "--output", str(output),
            "--workspace", str(workspace),
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": f"{C51}:{HERE}",
            "C57_MBE_TOPOLOGY_MODE": "ge2_control",
        },
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {"case": case, "produced": False, "returncode": int(completed.returncode)}
        dump(EVIDENCE / "cases" / f"{case}.json", row)
        return row
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    forensic = topology.analyze_trades(
        output,
        int(metrics.get("trades") or 0),
        FEATURE_KEYS,
    )
    row = {
        "case": case,
        "produced": True,
        "returncode": 0,
        "horizon_minutes": int(horizon),
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": forensic,
    }
    dump(EVIDENCE / "cases" / f"{case}.json", row)
    return row


def key(row: dict[str, Any]) -> tuple[int, str, int]:
    return (
        int(row.get("episode_ts") or 0),
        str(row.get("symbol")),
        int(row.get("side") or 0),
    )


def paired_effect(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left_rows = (control.get("trade_forensics") or {}).get("trade_ledger") or []
    right_rows = (candidate.get("trade_forensics") or {}).get("trade_ledger") or []
    left = {key(row): row for row in left_rows}
    right = {key(row): row for row in right_rows}
    shared = sorted(set(left) & set(right), key=str)
    omitted = [left[item] for item in sorted(set(left) - set(right), key=str)]
    added = [right[item] for item in sorted(set(right) - set(left), key=str)]
    paired = [
        {
            "key": list(item),
            "control_r": left[item].get("actual_r"),
            "candidate_r": right[item].get("actual_r"),
            "delta_r": number(right[item].get("actual_r")) - number(left[item].get("actual_r")),
            "control_exit": left[item].get("exit_reason"),
            "candidate_exit": right[item].get("exit_reason"),
        }
        for item in shared
    ]
    control_roi_winners = [
        row for row in left_rows
        if "PUBLIC_MBE2_ROI_EXIT" in str(row.get("exit_reason"))
        and number(row.get("actual_r")) > 0.0
    ]
    preserved = [
        row for row in control_roi_winners
        if key(row) in right and number(right[key(row)].get("actual_r")) > 0.0
    ]
    best = max(left_rows, key=lambda row: number(row.get("actual_r"), -math.inf), default=None)
    best_key = key(best) if best else None
    recross_pairs = [
        row for row in paired
        if "PUBLIC_MBE2_SOURCE_RECROSS_INVALIDATION" in str(row.get("candidate_exit"))
    ]
    paired_delta = sum(number(row.get("delta_r")) for row in paired)
    omitted_sum = sum(number(row.get("actual_r")) for row in omitted)
    added_sum = sum(number(row.get("actual_r")) for row in added)
    total_delta = paired_delta - omitted_sum + added_sum
    return {
        "shared_count": len(shared),
        "omitted_control_trades": omitted,
        "added_candidate_trades": added,
        "paired": paired,
        "paired_delta_r": paired_delta,
        "omitted_control_sum_r": omitted_sum,
        "added_candidate_sum_r": added_sum,
        "total_structural_delta_r": total_delta,
        "added_share_of_positive_delta": (
            abs(added_sum) / max(abs(total_delta), 1e-12) if total_delta > 0.0 else None
        ),
        "control_roi_winner_count": len(control_roi_winners),
        "preserved_positive_roi_winner_count": len(preserved),
        "roi_winner_preservation_share": (
            len(preserved) / len(control_roi_winners) if control_roi_winners else None
        ),
        "best_control_key": list(best_key) if best_key else None,
        "best_control_r": best.get("actual_r") if best else None,
        "best_control_preserved_positive": (
            bool(best_key in right and number(right[best_key].get("actual_r")) > 0.0)
            if best_key else False
        ),
        "recross_exit_pairs": recross_pairs,
        "recross_exit_count": len(recross_pairs),
        "recross_exit_improved_share": (
            sum(number(row.get("delta_r")) > 0.0 for row in recross_pairs) / len(recross_pairs)
            if recross_pairs else None
        ),
        "recross_exit_delta_r": sum(number(row.get("delta_r")) for row in recross_pairs),
    }


def metric_delta(candidate: dict[str, Any], control: dict[str, Any]) -> dict[str, float]:
    left, right = candidate.get("metrics") or {}, control.get("metrics") or {}
    keys = (
        "ending_nav", "total_return", "geometric_daily_growth", "max_drawdown",
        "trades", "wins", "losses", "win_rate", "profit_factor",
        "expectancy_usdt", "largest_winner_share",
    )
    return {metric: number(left.get(metric)) - number(right.get(metric)) for metric in keys}


def strict_target(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return (
        topology.account_ok(row)
        and int(metrics.get("trades") or 0) >= FRESH_DAYS
        and number(metrics.get("geometric_daily_growth")) >= 0.01
        and number(metrics.get("expectancy_usdt")) > 0.0
        and number(metrics.get("profit_factor")) > 1.0
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
        and number(metrics.get("min_equity")) > 0.0
    )


def classify(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    mechanics = topology.account_ok(control) and topology.account_ok(candidate)
    effect = paired_effect(control, candidate)
    cm, nm = control.get("metrics") or {}, candidate.get("metrics") or {}
    metric_improvement = (
        number(nm.get("expectancy_usdt")) > number(cm.get("expectancy_usdt"))
        and number(nm.get("profit_factor")) > number(cm.get("profit_factor"))
        and number(nm.get("geometric_daily_growth")) > number(cm.get("geometric_daily_growth"))
        and number(nm.get("total_return")) > number(cm.get("total_return"))
    )
    winner_preserved = (
        number(effect.get("roi_winner_preservation_share"), 0.0) >= 0.80
        and bool(effect.get("best_control_preserved_positive"))
    )
    recross_causal = (
        int(effect.get("recross_exit_count") or 0) > 0
        and number(effect.get("recross_exit_improved_share"), 0.0) >= 0.70
        and number(effect.get("recross_exit_delta_r")) > 0.0
    )
    no_added_outlier = (
        effect.get("added_share_of_positive_delta") is None
        or number(effect.get("added_share_of_positive_delta"), 1.0) <= 0.50
    )
    causal_support = (
        mechanics and metric_improvement and winner_preserved
        and recross_causal and no_added_outlier
    )
    target = strict_target(candidate)
    if not mechanics:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif target and causal_support:
        decision = "MBE_RECROSS_SHORT_TARGET_MET_INTEGRATION_VALIDATION_REQUIRED"
    elif causal_support:
        decision = "MBE_RECROSS_REPAIR_SUPPORTED_INTEGRATION_REQUIRED"
    else:
        decision = "MBE_SOURCE_RECROSS_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING"
    return {
        "mechanically_valid": mechanics,
        "decision": decision,
        "strict_project_target": target,
        "causal_support": causal_support,
        "thresholds_searched": False,
        "integration_authorized": causal_support,
        "long_evaluation_authorized": False,
        "predictions": {
            "metric_improvement": metric_improvement,
            "winner_engine_preserved": winner_preserved,
            "recross_exits_causally_improved": recross_causal,
            "improvement_not_added_outlier_dominated": no_added_outlier,
        },
        "metric_delta": metric_delta(candidate, control),
        "paired_effect": effect,
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# MBE source-recross fresh v1",
        "",
        f"- eligibility: `{result.get('eligibility_decision')}`",
        f"- horizon: {result.get('horizon_minutes')}",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- decision: `{result.get('decision')}`",
        f"- strict project target: {result.get('strict_project_target')}",
        f"- causal support: {result.get('causal_support')}",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        "",
    ]
    if result.get("skipped"):
        lines.append("The consumed lifecycle evidence did not authorize this pre-frozen fresh replay.")
    else:
        lines += [
            f"Fresh interval: `{FRESH_START}` through `{FRESH_END}` UTC.",
            "",
            "| case | trades | W/L | PF | expectancy | geo/day | return | MDD | recross exits |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for case in CASES:
            row = result["cases"][case]
            metrics = row.get("metrics") or {}
            diagnostics = row.get("diagnostics") or {}
            lines.append(
                f"| {case} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | "
                f"{metrics.get('geometric_daily_growth')} | {metrics.get('total_return')} | "
                f"{metrics.get('max_drawdown')} | {diagnostics.get('mbe_source_recross_exit_requests')} |"
            )
        lines += [
            "", "## Predeclared predictions", "",
            f"`{json.dumps(result.get('predictions'), sort_keys=True)}`",
        ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("fresh recross freeze missing")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if not ELIGIBILITY.is_file():
        result = {
            "experiment": "candidate-57-mbe-source-recross-fresh-v1",
            "eligibility_decision": "EVIDENCE_NOT_AVAILABLE",
            "horizon_minutes": None,
            "skipped": True,
            "mechanically_valid": True,
            "decision": "CONDITIONAL_REPLAY_NOT_YET_AUTHORIZED",
            "strict_project_target": False,
            "causal_support": False,
            "thresholds_searched": False,
            "cases": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0

    eligibility = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    eligibility_decision = str(eligibility.get("decision") or "")
    horizon = int(eligibility.get("earliest_supported_source_horizon") or 0)
    if eligibility_decision != ELIGIBLE_DECISION or horizon not in (15, 41, 114, 180, 420):
        result = {
            "experiment": "candidate-57-mbe-source-recross-fresh-v1",
            "eligibility_decision": eligibility_decision,
            "horizon_minutes": horizon or None,
            "skipped": True,
            "mechanically_valid": True,
            "decision": "CONSUMED_LIFECYCLE_EVIDENCE_REJECTED_FRESH_REPLAY",
            "strict_project_target": False,
            "causal_support": False,
            "thresholds_searched": False,
            "cases": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0

    configure_reused_campaign()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    cases = {case: run_case(case, horizon) for case in CASES}
    verdict = classify(cases["source_control"], cases["source_recross"])
    result = {
        "experiment": "candidate-57-mbe-source-recross-fresh-v1",
        "policy_frozen_before_interval": True,
        "eligibility_decision": eligibility_decision,
        "horizon_minutes": horizon,
        "fresh_interval": {"start": FRESH_START, "end": FRESH_END, "days": FRESH_DAYS},
        "skipped": False,
        "cases": cases,
        **verdict,
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    print(json.dumps({"decision": result["decision"], "horizon": horizon}, indent=2))
    if any(not row.get("produced") for row in cases.values()):
        return 1
    return 0 if result["mechanically_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
