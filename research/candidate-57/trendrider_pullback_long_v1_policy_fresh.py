#!/usr/bin/env python3
"""Conditional policy-fresh replay for the frozen TrendRider pullback branch."""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
import shutil
from typing import Any

import trendrider_pullback_long_v1_campaign as base
import trendrider_pullback_long_v1_warmup_endflat_v2 as replay

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ELIGIBILITY = (
    HERE
    / "evidence"
    / "trendrider-pullback-long-v1-warmup-endflat-v2"
    / "comparison.json"
)
WORK = ROOT / ".work" / "candidate-57-trendrider-pullback-long-v1-policy-fresh"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-pullback-long-v1-policy-fresh"
EVIDENCE = HERE / "evidence" / "trendrider-pullback-long-v1-policy-fresh"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-pullback-long-v1-policy-fresh"
PRIMARY = base.Stage(
    "primary_policy_fresh_2025_06",
    date(2025, 6, 1),
    date(2025, 6, 28),
    "UNSEEN_POLICY_REGIME",
)
FALLBACK = base.Stage(
    "underinformative_fallback_2025_10",
    date(2025, 10, 1),
    date(2025, 10, 28),
    "UNSEEN_POLICY_REGIME",
)
MIN_INFORMATIVE_TRADES = 7


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(base.safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def configure_replay_paths() -> None:
    replay.WORK = WORK
    replay.ARTIFACTS = ARTIFACTS
    replay.EVIDENCE = EVIDENCE
    replay.CACHE = CACHE


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def mechanically_valid(row: dict[str, Any]) -> bool:
    return base.account_ok(row)


def lifecycle_summary(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    tagged = {
        key: int(diagnostics.get(key) or 0)
        for key in (
            "trendrider_roi_exits",
            "trendrider_trailing_exits",
            "trendrider_indicator_exits",
            "trendrider_early_loss_cut_2h",
            "trendrider_early_loss_cut_4h",
            "trendrider_early_loss_cut_8h",
            "trendrider_early_loss_cut_16h",
            "trendrider_time_exit_24h",
        )
    }
    trades = int(metrics.get("trades") or 0)
    tagged_total = sum(tagged.values())
    symbols = metrics.get("position_counts_by_symbol") or {}
    return {
        "tagged_exit_counts": tagged,
        "tagged_exit_total": tagged_total,
        "unclassified_bracket_or_stop_count": max(0, trades - tagged_total),
        "symbols_traded": len([value for value in symbols.values() if int(value) > 0]),
        "symbol_trade_counts": symbols,
        "largest_winner_share": metrics.get("largest_winner_share"),
    }


def positive_component(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    pf = metrics.get("profit_factor")
    positive_pf = (
        number(pf) > 1.0
        if pf is not None
        else int(metrics.get("wins") or 0) > 0
        and int(metrics.get("losses") or 0) == 0
    )
    lifecycle = lifecycle_summary(row)
    return (
        mechanically_valid(row)
        and int(metrics.get("trades") or 0) >= MIN_INFORMATIVE_TRADES
        and number(metrics.get("expectancy_usdt")) > 0.0
        and number(metrics.get("total_return")) > 0.0
        and positive_pf
        and int(lifecycle["symbols_traded"]) >= 2
        and number(metrics.get("largest_winner_share"), 1.0) <= 0.50
    )


def render(result: dict[str, Any]) -> None:
    lines = [
        "# TrendRider pullback-long v1 policy-fresh result",
        "",
        f"- eligibility: `{result.get('eligibility_decision')}`",
        f"- decision: `{result.get('decision')}`",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| stage | trades | W/L | PF | expectancy USDT | signal-window geo/day | return | MDD | symbols | largest winner |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in (result.get("cases") or {}).items():
        metrics = row.get("metrics") or {}
        lifecycle = (result.get("lifecycle") or {}).get(name) or {}
        lines.append(
            f"| {name} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth_signal_window')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} | {lifecycle.get('symbols_traded')} | {metrics.get('largest_winner_share')} |"
        )
    lines.extend(
        [
            "",
            "The October interval is consumed only when June is mechanically valid but has fewer than seven trades.  A negative informative June result cannot be rescued by October.  Component support does not authorize integration or long evaluation.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    configure_replay_paths()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    if not ELIGIBILITY.is_file():
        result = {
            "eligibility_decision": "MISSING_DEVELOPMENT_EVIDENCE",
            "decision": "SKIPPED_NOT_ELIGIBLE",
            "thresholds_searched": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "cases": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0

    eligibility = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    eligibility_decision = str(eligibility.get("decision") or "")
    if not bool(eligibility.get("mechanically_valid")) or eligibility_decision != "MECHANISM_PROMISING_POLICY_FRESH_REQUIRED":
        result = {
            "eligibility_decision": eligibility_decision,
            "decision": "SKIPPED_NOT_ELIGIBLE",
            "thresholds_searched": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "cases": {},
        }
        dump(EVIDENCE / "comparison.json", result)
        render(result)
        return 0

    cases: dict[str, dict[str, Any]] = {}
    primary = replay.run_stage(PRIMARY)
    cases[PRIMARY.name] = primary
    if not mechanically_valid(primary):
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    else:
        primary_trades = int((primary.get("metrics") or {}).get("trades") or 0)
        if primary_trades < MIN_INFORMATIVE_TRADES:
            fallback = replay.run_stage(FALLBACK)
            cases[FALLBACK.name] = fallback
            if not mechanically_valid(fallback):
                decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
            elif positive_component(fallback):
                decision = "POLICY_FRESH_COMPONENT_SUPPORTED_AFTER_UNDERINFORMATIVE_PRIMARY"
            elif int((fallback.get("metrics") or {}).get("trades") or 0) < MIN_INFORMATIVE_TRADES and number((fallback.get("metrics") or {}).get("expectancy_usdt")) > 0.0:
                decision = "POSITIVE_BUT_TOO_SPARSE_AS_STANDALONE"
            else:
                decision = "POLICY_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING"
        elif positive_component(primary):
            decision = "POLICY_FRESH_COMPONENT_SUPPORTED"
        else:
            decision = "POLICY_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING"

    result = {
        "eligibility_decision": eligibility_decision,
        "decision": decision,
        "thresholds_searched": False,
        "fallback_rule": "October only when mechanically valid June has fewer than seven completed trades",
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "cases": cases,
        "lifecycle": {name: lifecycle_summary(row) for name, row in cases.items()},
    }
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if all(mechanically_valid(row) for row in cases.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
