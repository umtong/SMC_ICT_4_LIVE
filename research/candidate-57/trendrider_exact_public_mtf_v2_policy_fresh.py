#!/usr/bin/env python3
"""Conditional predeclared October replay for exact public TrendRider MTF v2."""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
from pathlib import Path
import shutil
from typing import Any

import trendrider_exact_public_mtf_v2_campaign as source
import trendrider_pullback_long_v1_campaign as base
from trendrider_public_mtf_context_v2 import build_sidecar

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ELIGIBILITY = HERE / "evidence" / "trendrider-exact-public-mtf-v2" / "comparison.json"
WORK = ROOT / ".work" / "candidate-57-trendrider-exact-public-mtf-v2-policy-fresh"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-exact-public-mtf-v2-policy-fresh"
EVIDENCE = HERE / "evidence" / "trendrider-exact-public-mtf-v2-policy-fresh"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-exact-public-mtf-v2-policy-fresh"
STAGE = base.Stage(
    "policy_fresh_2025_10",
    date(2025, 10, 1),
    date(2025, 10, 28),
    "PREDECLARED_UNSEEN_EXACT_MTF_REGIME",
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


def configure_source_paths() -> None:
    source.WORK = WORK
    source.ARTIFACTS = ARTIFACTS
    source.EVIDENCE = EVIDENCE
    source.CACHE = CACHE


def render(result: dict[str, Any]) -> None:
    row = result.get("case") or {}
    metrics = row.get("metrics") or {}
    lines = [
        "# TrendRider exact public MTF v2 policy-fresh result",
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
        "One policy-fresh success earns component status only.  A failure closes the exact source branch without threshold, lifecycle or date retuning.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    configure_source_paths()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    if not ELIGIBILITY.is_file():
        result = {
            "eligibility_decision": "MISSING_DIAGNOSTIC_EVIDENCE",
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
    if not bool(eligibility.get("mechanically_valid")) or eligibility_decision != "SOURCE_FIDELITY_SUPPORTED_POLICY_FRESH_REQUIRED":
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
    build_sidecar(sidecar, STAGE.start, STAGE.end + timedelta(days=source.RUNOFF_DAYS))
    row = source.run_case(STAGE, "exact_public_mtf", sidecar)
    mechanics = source.account_ok(row)
    metrics = row.get("metrics") or {}
    symbols = metrics.get("position_counts_by_symbol") or {}
    symbols_traded = len([value for value in symbols.values() if int(value) > 0])
    pf = metrics.get("profit_factor")
    positive_pf = (
        number(pf) > 1.0
        if pf is not None
        else int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    )
    informative = int(metrics.get("trades") or 0) >= MIN_INFORMATIVE_TRADES
    supported = (
        mechanics
        and informative
        and number(metrics.get("expectancy_usdt")) > 0.0
        and number(metrics.get("total_return")) > 0.0
        and positive_pf
        and symbols_traded >= 2
        and number(metrics.get("largest_winner_share"), 1.0) <= 0.50
    )
    if not mechanics:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif supported:
        decision = "POLICY_FRESH_COMPONENT_SUPPORTED_NOT_LONG_READY"
    elif not informative and number(metrics.get("expectancy_usdt")) > 0.0:
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
