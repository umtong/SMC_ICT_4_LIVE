#!/usr/bin/env python3
"""Causal source-control comparison for exact public TrendRider MTF state."""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import trendrider_pullback_long_v1_campaign as base
from trade_ledger_forensics import analyze as analyze_trades
from trendrider_public_mtf_context_v2 import build_sidecar

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-trendrider-exact-public-mtf-v2"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-exact-public-mtf-v2"
EVIDENCE = HERE / "evidence" / "trendrider-exact-public-mtf-v2"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-exact-public-mtf-v2"
FREEZE = HERE / "TRENDRIDER_EXACT_PUBLIC_MTF_V2_FREEZE.md"
WARMUP_DAYS = 10
RUNOFF_DAYS = 2
CASES = ("fallback_control", "exact_public_mtf")
STAGES = (
    base.Stage("november_winner_development", date(2024, 11, 1), date(2024, 11, 14), "KNOWN_WINNER_ENGINE"),
    base.Stage("june_failure_development", date(2025, 6, 1), date(2025, 6, 28), "KNOWN_EARLY_LOSS_ENGINE"),
)
FEATURE_KEYS = base.FEATURE_KEYS + (
    "public_daily_ema_200",
    "public_daily_ema_200_pass",
    "public_pair_4h_is_bull",
    "public_pair_4h_adx",
    "public_exact_confidence_numeric",
    "public_exact_confidence_pass",
    "public_exact_source_signal",
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


def boundary_ns(day: date, *, end: bool) -> int:
    moment = datetime.combine(day, time.min, tzinfo=timezone.utc)
    if end:
        moment += timedelta(days=1)
        moment -= timedelta(microseconds=1)
    return int(moment.timestamp() * 1_000_000_000)


def build_config(stage: base.Stage, case: str, sidecar: Path) -> Path:
    payload = copy.deepcopy(
        json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    )
    strategy = payload["strategy"]
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 1440,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 60,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.060,
            "picasso_trailing_positive": 0.030,
            "picasso_trailing_offset": 0.050,
            "picasso_emergency_target_fraction": 0.229,
            "picasso_roi_0": 0.229,
            "picasso_roi_416": 0.044,
            "picasso_roi_933": 0.0,
            "picasso_roi_1982": 0.0,
            "trendrider_ema_fast": 9,
            "trendrider_ema_slow": 16,
            "trendrider_ema_regime_fast": 50,
            "trendrider_ema_regime_slow": 200,
            "trendrider_rsi_period": 16,
            "trendrider_adx_period": 14,
            "trendrider_volume_ema_period": 20,
            "trendrider_obv_ema_period": 20,
            "trendrider_rsi_pullback_low": 30.0,
            "trendrider_rsi_pullback_high": 65.0,
            "trendrider_adx_threshold": 18.0,
            "trendrider_volume_factor": 0.7,
            "trendrider_pullback_tolerance": 0.02,
            "trendrider_min_confidence": 5,
            "trendrider_stop_fraction": 0.06,
            "trendrider_emergency_objective_fraction": 0.229,
            "trendrider_trailing_positive": 0.03,
            "trendrider_trailing_offset": 0.05,
            "trendrider_roi_0": 0.229,
            "trendrider_roi_t1_minutes": 124,
            "trendrider_roi_t1": 0.136,
            "trendrider_roi_t2_minutes": 290,
            "trendrider_roi_t2": 0.044,
            "trendrider_roi_t3_minutes": 764,
            "trendrider_roi_t3": 0.0,
            "trendrider_rsi_exit": 78.0,
            "trendrider_early_loss_2h": -0.015,
            "trendrider_early_loss_4h": 0.0,
            "trendrider_early_loss_8h": 0.005,
            "trendrider_early_loss_16h": 0.010,
            "trendrider_round_trip_cost_fraction": 0.0021,
            "trendrider_history_minutes": 16000,
            "trendrider_signal_start_ns": boundary_ns(stage.start, end=False),
            "trendrider_signal_end_ns": boundary_ns(stage.end, end=True),
        }
    )
    if case == "exact_public_mtf":
        strategy["trendrider_mtf_context_path"] = str(sidecar.resolve())
    path = WORK / "configs" / stage.name / f"{case}.json"
    dump(path, payload)
    return path


def assemble_case(case: str) -> None:
    if case == "fallback_control":
        shutil.copy2(C51 / "router_trendrider_fallback_impl.py", C51 / "router_trendrider_impl.py")
        shutil.copy2(C51 / "router_trendrider_fallback_runtime.py", C51 / "router.py")
        shutil.copy2(C51 / "strategy_trendrider_runoff_base.py", C51 / "strategy.py")
    elif case == "exact_public_mtf":
        shutil.copy2(C51 / "router_trendrider_exact_impl.py", C51 / "router_trendrider_impl.py")
        shutil.copy2(C51 / "router_trendrider_exact_runtime.py", C51 / "router.py")
        shutil.copy2(C51 / "strategy_trendrider_exact_base.py", C51 / "strategy.py")
    else:
        raise ValueError(case)


def run_case(stage: base.Stage, case: str, sidecar: Path) -> dict[str, Any]:
    assemble_case(case)
    output = ARTIFACTS / stage.name / case
    workspace = WORK / "workspace" / stage.name / case
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    data_start = stage.start - timedelta(days=WARMUP_DAYS)
    data_end = stage.end + timedelta(days=RUNOFF_DAYS)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(stage, case, sidecar)),
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
    stage_record = asdict(stage) | {
        "days": stage.days,
        "data_start": str(data_start),
        "data_end": str(data_end),
        "warmup_days": WARMUP_DAYS,
        "runoff_days": RUNOFF_DAYS,
    }
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": stage_record,
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
        "stage": stage_record,
        "case": case,
        "produced": True,
        "returncode": 0,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / stage.name / f"{case}.json", row)
    return row


def account_ok(row: dict[str, Any]) -> bool:
    return base.account_ok(row)


def key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("episode_ts"), row.get("symbol"), row.get("side")


def paired_effect(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    control_rows = (control.get("trade_forensics") or {}).get("trade_ledger") or []
    candidate_rows = (candidate.get("trade_forensics") or {}).get("trade_ledger") or []
    control_by = {key(row): row for row in control_rows}
    candidate_by = {key(row): row for row in candidate_rows}
    shared = sorted(set(control_by) & set(candidate_by), key=str)
    removed = [control_by[item] for item in sorted(set(control_by) - set(candidate_by), key=str)]
    added = [candidate_by[item] for item in sorted(set(candidate_by) - set(control_by), key=str)]
    paired = [
        {
            "key": list(item),
            "control_exit": control_by[item].get("exit_reason"),
            "candidate_exit": candidate_by[item].get("exit_reason"),
            "control_r": control_by[item].get("actual_r"),
            "candidate_r": candidate_by[item].get("actual_r"),
            "delta_r": number(candidate_by[item].get("actual_r")) - number(control_by[item].get("actual_r")),
        }
        for item in shared
    ]
    early_token = "EARLY_LOSS"
    winner_tokens = ("ROI", "TRAILING")
    removed_early = [row for row in removed if early_token in str(row.get("exit_reason") or "")]
    removed_source_winners = [
        row
        for row in removed
        if any(token in str(row.get("exit_reason") or "") for token in winner_tokens)
        and number(row.get("actual_r")) > 0.0
    ]
    control_source_winners = [
        row
        for row in control_rows
        if any(token in str(row.get("exit_reason") or "") for token in winner_tokens)
        and number(row.get("actual_r")) > 0.0
    ]
    preserved_source_winner_keys = [
        item
        for item in shared
        if control_by[item] in control_source_winners
        and number(candidate_by[item].get("actual_r")) > 0.0
    ]
    best_control = max(control_rows, key=lambda row: number(row.get("actual_r")), default=None)
    best_key = key(best_control) if best_control else None
    best_preserved = (
        best_key in candidate_by and number(candidate_by[best_key].get("actual_r")) > 0.0
        if best_key is not None
        else False
    )
    added_sum_r = sum(number(row.get("actual_r")) for row in added)
    removed_sum_r = sum(number(row.get("actual_r")) for row in removed)
    return {
        "paired_count": len(shared),
        "paired": paired,
        "removed_control_trades": removed,
        "added_candidate_trades": added,
        "removed_early_loss_count": len(removed_early),
        "removed_source_winner_count": len(removed_source_winners),
        "control_source_winner_count": len(control_source_winners),
        "preserved_source_winner_count": len(preserved_source_winner_keys),
        "source_winner_preservation_share": (
            len(preserved_source_winner_keys) / len(control_source_winners)
            if control_source_winners
            else None
        ),
        "best_control_trade_key": list(best_key) if best_key else None,
        "best_control_trade_r": best_control.get("actual_r") if best_control else None,
        "best_control_trade_preserved_positive": best_preserved,
        "removed_control_sum_r": removed_sum_r,
        "added_candidate_sum_r": added_sum_r,
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


def classify(results: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    mechanics = True
    for stage in STAGES:
        control = results[stage.name]["fallback_control"]
        candidate = results[stage.name]["exact_public_mtf"]
        mechanics = mechanics and account_ok(control) and account_ok(candidate)
        comparisons[stage.name] = {
            "metric_delta": metric_delta(candidate, control),
            "paired_effect": paired_effect(control, candidate),
            "control": control,
            "candidate": candidate,
        }

    november = comparisons[STAGES[0].name]
    june = comparisons[STAGES[1].name]
    november_candidate = november["candidate"].get("metrics") or {}
    june_candidate = june["candidate"].get("metrics") or {}
    june_control = june["control"].get("metrics") or {}
    nov_effect = november["paired_effect"]
    june_effect = june["paired_effect"]
    route_counts = (june["candidate"].get("diagnostics") or {}).get("route_counts") or {}
    daily_rejections = int(route_counts.get("UNRESOLVED") or 0)  # detailed reasons live in unresolved counts
    unresolved_reasons = (june["candidate"].get("diagnostics") or {}).get("unresolved_reason_counts") or {}
    explicit_daily = int(unresolved_reasons.get("PUBLIC_DAILY_EMA200_REJECTED") or 0)
    explicit_4h = int(unresolved_reasons.get("PUBLIC_EXACT_CONFIDENCE_REJECTED") or 0)

    november_positive = (
        number(november_candidate.get("expectancy_usdt")) > 0.0
        and number(november_candidate.get("total_return")) > 0.0
        and number(november_candidate.get("profit_factor")) > 1.0
    )
    june_positive = (
        number(june_candidate.get("expectancy_usdt")) > 0.0
        and number(june_candidate.get("total_return")) > 0.0
        and number(june_candidate.get("profit_factor")) > 1.0
    )
    june_improved = (
        number(june_candidate.get("expectancy_usdt")) > number(june_control.get("expectancy_usdt"))
        and number(june_candidate.get("profit_factor")) > number(june_control.get("profit_factor"))
    )
    november_winner_engine_preserved = (
        number(nov_effect.get("source_winner_preservation_share"), 0.0) >= 0.75
    )
    selective_loss_rejection = (
        int(june_effect.get("removed_early_loss_count") or 0)
        > int(june_effect.get("removed_source_winner_count") or 0)
    )
    best_june_preserved = bool(june_effect.get("best_control_trade_preserved_positive"))
    exact_context_changed_state = explicit_daily + explicit_4h > 0
    support = (
        mechanics
        and november_positive
        and june_positive
        and june_improved
        and november_winner_engine_preserved
        and selective_loss_rejection
        and best_june_preserved
        and exact_context_changed_state
    )
    if not mechanics:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif support:
        decision = "SOURCE_FIDELITY_SUPPORTED_POLICY_FRESH_REQUIRED"
    elif june_improved and exact_context_changed_state:
        decision = "SOURCE_STATE_INFORMATIVE_BUT_STANDALONE_STILL_WEAK"
    else:
        decision = "EXACT_PUBLIC_MTF_HYPOTHESIS_REJECTED_NO_RETUNING"
    return {
        "mechanically_valid": mechanics,
        "decision": decision,
        "policy_fresh_authorized": support,
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "thresholds_searched": False,
        "predictions": {
            "november_positive": november_positive,
            "june_positive": june_positive,
            "june_improved": june_improved,
            "november_winner_engine_preserved": november_winner_engine_preserved,
            "selective_loss_rejection": selective_loss_rejection,
            "best_june_winner_preserved": best_june_preserved,
            "exact_context_changed_state": exact_context_changed_state,
            "june_daily_rejections": explicit_daily,
            "june_4h_confidence_rejections": explicit_4h,
            "june_unresolved_total": daily_rejections,
        },
        "comparisons": comparisons,
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# TrendRider exact public MTF v2 source-fidelity diagnostic",
        "",
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
            row = comparison.get("control" if case == "fallback_control" else "candidate") or {}
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
            "These intervals are consumed diagnostics.  Only the exact policy frozen before this result can move to the predeclared October policy-fresh interval, and only when the transaction-level predictions—not merely aggregate return—are satisfied.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError(f"missing freeze: {FREEZE}")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict[str, dict[str, Any]]] = {}
    for stage in STAGES:
        sidecar = WORK / "mtf" / f"{stage.name}.json"
        build_sidecar(sidecar, stage.start, stage.end + timedelta(days=RUNOFF_DAYS))
        results[stage.name] = {
            case: run_case(stage, case, sidecar) for case in CASES
        }
    result = classify(results)
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if result.get("mechanically_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
