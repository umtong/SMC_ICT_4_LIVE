#!/usr/bin/env python3
"""Predeclared October replay for the frozen RAHTF clean-state component."""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
from pathlib import Path
import shutil
from typing import Any

import trendrider_exact_public_mtf_v2_campaign as exact_campaign
import trendrider_pullback_long_v1_campaign as base
import trendrider_rahtf_clean_v3_campaign as diagnostic
from trendrider_public_mtf_context_v2 import build_sidecar

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ELIGIBILITY = HERE / "evidence" / "trendrider-rahtf-clean-v3" / "comparison.json"
WORK = ROOT / ".work" / "candidate-57-trendrider-rahtf-clean-v3-policy-fresh"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-rahtf-clean-v3-policy-fresh"
EVIDENCE = HERE / "evidence" / "trendrider-rahtf-clean-v3-policy-fresh"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-rahtf-clean-v3-policy-fresh"
STAGE = base.Stage(
    "policy_fresh_2025_10",
    date(2025, 10, 1),
    date(2025, 10, 28),
    "PREDECLARED_UNSEEN_RAHTF_REGIME",
)
MIN_INFORMATIVE_TRADES = 7


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(base.safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def configure_paths() -> None:
    exact_campaign.WORK = WORK
    exact_campaign.ARTIFACTS = ARTIFACTS
    exact_campaign.EVIDENCE = EVIDENCE
    exact_campaign.CACHE = CACHE
    diagnostic.WORK = WORK
    diagnostic.ARTIFACTS = ARTIFACTS
    diagnostic.EVIDENCE = EVIDENCE
    diagnostic.CACHE = CACHE


def render(result: dict[str, Any]) -> None:
    row = result.get("case") or {}
    metrics = row.get("metrics") or {}
    lines = [
        "# TrendRider + RAHTF clean-state v3 policy-fresh result",
        "",
        f"- eligibility: `{result.get('eligibility_decision')}`",
        f"- decision: `{result.get('decision')}`",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| trades | W/L | PF | expectancy USDT | signal-window geo/day | return | MDD | symbols | largest winner |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth_signal_window')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} | {result.get('symbols_traded')} | {metrics.get('largest_winner_share')} |",
        "",
        "One policy-fresh success grants state-component status only.  A mechanically valid informative failure closes the frozen RAHTF gate without another interval or parameter change.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    configure_paths()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if not ELIGIBILITY.is_file():
        result = {
            "eligibility_decision": "MISSING_RAHTF_DIAGNOSTIC_EVIDENCE",
            "decision": "SKIPPED_NOT_ELIGIBLE",
            "mechanically_valid": True,
            "thresholds_searched": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "case": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0
    eligibility = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    eligibility_decision = str(eligibility.get("decision") or "")
    if not bool(eligibility.get("mechanically_valid")) or eligibility_decision != "RAHTF_STATE_COMPONENT_SUPPORTED_POLICY_FRESH_REQUIRED":
        result = {
            "eligibility_decision": eligibility_decision,
            "decision": "SKIPPED_NOT_ELIGIBLE",
            "mechanically_valid": True,
            "thresholds_searched": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "case": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0

    sidecar = WORK / "mtf" / f"{STAGE.name}.json"
    build_sidecar(sidecar, STAGE.start, STAGE.end + timedelta(days=exact_campaign.RUNOFF_DAYS))
    row = diagnostic.run_case(STAGE, "rahtf_clean", sidecar)
    mechanics = exact_campaign.account_ok(row)
    metrics = row.get("metrics") or {}
    symbols = metrics.get("position_counts_by_symbol") or {}
    symbols_traded = len([value for value in symbols.values() if int(value) > 0])
    trades = int(metrics.get("trades") or 0)
    pf = metrics.get("profit_factor")
    positive_pf = (
        number(pf) > 1.0
        if pf is not None
        else int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    )
    supported = (
        mechanics
        and trades >= MIN_INFORMATIVE_TRADES
        and number(metrics.get("expectancy_usdt")) > 0.0
        and number(metrics.get("total_return")) > 0.0
        and positive_pf
        and symbols_traded >= 2
        and number(metrics.get("largest_winner_share"), 1.0) <= 0.50
    )
    if not mechanics:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif supported:
        decision = "POLICY_FRESH_STATE_COMPONENT_SUPPORTED_NOT_LONG_READY"
    elif trades < MIN_INFORMATIVE_TRADES and number(metrics.get("expectancy_usdt")) > 0.0:
        decision = "UNDERINFORMATIVE_POSITIVE_NO_SECOND_INTERVAL"
    else:
        decision = "POLICY_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING"
    result = {
        "eligibility_decision": eligibility_decision,
        "decision": decision,
        "mechanically_valid": mechanics,
        "thresholds_searched": False,
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "symbols_traded": symbols_traded,
        "case": row,
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if mechanics else 2


if __name__ == "__main__":
    raise SystemExit(main())
