#!/usr/bin/env python3
"""Parity-gated lifecycle anatomy for the frozen public MBE2 account."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import json
import math
from pathlib import Path
import shutil
from typing import Any

import mbe_collision_topology_fresh_v1 as topology

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WORK = ROOT / ".work" / "candidate-57-mbe-lifecycle-forensic-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-mbe-lifecycle-forensic-v1"
EVIDENCE = HERE / "evidence" / "mbe-lifecycle-forensic-v1"
CACHE = ROOT / ".cache" / "candidate-57-mbe-lifecycle-forensic-v1"
FREEZE = HERE / "MBE_LIFECYCLE_FORENSIC_V1_FREEZE.md"
REFERENCE = HERE / "evidence" / "ichi-mbe-n1-fresh-v1" / "cases" / "mbe_only.json"

PERIODS = {
    "march_2024": (date(2024, 3, 1), date(2024, 3, 31)),
    "april_2026": (date(2026, 4, 1), date(2026, 4, 30)),
}
HORIZONS = (15, 41, 114, 180, 420)
METRICS = (
    "estimated_after_cost_r", "source_profit_ratio",
    "mfe_source_profit_ratio", "mae_source_profit_ratio",
    "raw_short_cross_breadth", "rsi_reoverbought_breadth",
    "tema_above_middle_breadth", "tema_rising_breadth",
    "renewed_short_pressure_breadth", "mean_reversion_progress_breadth",
    "entry_symbol_rsi", "entry_symbol_tema_to_middle_bps",
    "entry_symbol_tema_slope_bps", "entry_symbol_return_1h_bps",
    "entry_symbol_return_4h_bps", "entry_symbol_return_8h_bps",
    "entry_symbol_realized_vol_1h_bps", "entry_symbol_range_1h_bps",
)


def number(value: Any, default: float = math.nan) -> float:
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
    topology.REFERENCE = REFERENCE


def outcome_group(trade: dict[str, Any]) -> str:
    actual_r = number(trade.get("actual_r"), 0.0)
    reason = str(trade.get("exit_reason") or "")
    if "PUBLIC_MBE2_ROI_EXIT" in reason and actual_r > 0.0:
        return "roi_winner"
    if actual_r < 0.0:
        return "non_roi_loss"
    return "other_win"


def quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {key: None for key in ("min", "q25", "median", "q75", "max")}

    def q(fraction: float) -> float:
        position = (len(clean) - 1) * fraction
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return clean[lower]
        weight = position - lower
        return clean[lower] * (1.0 - weight) + clean[upper] * weight

    return {
        "min": clean[0], "q25": q(0.25), "median": q(0.50),
        "q75": q(0.75), "max": clean[-1],
    }


def summarize(values: list[float]) -> dict[str, Any]:
    clean = [value for value in values if math.isfinite(value)]
    return {
        "n": len(clean),
        "mean": sum(clean) / len(clean) if clean else None,
        "positive_share": sum(value > 0.0 for value in clean) / len(clean) if clean else None,
        "distribution": quantiles(clean),
    }


def direct_source_recross_failure(snapshot: dict[str, Any]) -> bool:
    """The short entry transition has reversed while the trade has no edge."""
    return (
        number(snapshot.get("estimated_after_cost_r"), math.inf) <= 0.0
        and number(snapshot.get("entry_symbol_rsi"), -math.inf) >= 70.0
        and number(snapshot.get("entry_symbol_tema_slope_bps"), -math.inf) > 0.0
    )


def analyze_period(label: str, row: dict[str, Any], output: Path) -> dict[str, Any]:
    records = json.loads((output / "closed_scenarios.json").read_text(encoding="utf-8"))
    by_id = {str(record.get("scenario_id")): record for record in records}
    ledger = (row.get("trade_forensics") or {}).get("trade_ledger") or []
    groups: dict[str, int] = defaultdict(int)
    horizon_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    enriched: list[dict[str, Any]] = []

    for trade in ledger:
        scenario = by_id.get(str(trade.get("scenario_id"))) or {}
        group = outcome_group(trade)
        groups[group] += 1
        severe = int(number(trade.get("actual_r"), 0.0) <= -0.80)
        snapshots = list(scenario.get("mbe_lifecycle_horizon_snapshots") or [])
        boundaries = list(scenario.get("mbe_lifecycle_boundary_snapshots") or [])
        enriched.append(
            {
                **trade,
                "period": label,
                "outcome_group": group,
                "severe_stop_like": severe,
                "entry_topology_count": int(scenario.get("mbe_actionable_candidates") or 0),
                "horizon_snapshots": snapshots,
                "boundary_snapshot_count": len(boundaries),
            }
        )
        for snapshot in snapshots:
            horizon = int(snapshot.get("source_horizon_minutes") or 0)
            horizon_rows[horizon].append(
                {
                    **snapshot,
                    "scenario_id": trade.get("scenario_id"),
                    "actual_r": trade.get("actual_r"),
                    "exit_reason": trade.get("exit_reason"),
                    "outcome_group": group,
                    "severe_stop_like": severe,
                    "direct_source_recross_failure": int(direct_source_recross_failure(snapshot)),
                }
            )

    total_winners = groups.get("roi_winner", 0)
    total_losses = groups.get("non_roi_loss", 0)
    horizon_summary: dict[str, Any] = {}
    effects: dict[str, Any] = {}
    for horizon in HORIZONS:
        snapshots = horizon_rows.get(horizon, [])
        by_group: dict[str, Any] = {}
        for group in ("roi_winner", "non_roi_loss", "other_win"):
            subset = [item for item in snapshots if item["outcome_group"] == group]
            by_group[group] = {
                "snapshots": len(subset),
                "severe_stop_like_snapshots": sum(int(item["severe_stop_like"]) for item in subset),
                "direct_source_recross_failures": sum(
                    int(item["direct_source_recross_failure"]) for item in subset
                ),
                "metrics": {
                    metric: summarize([number(item.get(metric)) for item in subset])
                    for metric in METRICS
                },
            }
        horizon_summary[str(horizon)] = by_group

        winner_matches = sum(
            int(item["direct_source_recross_failure"])
            for item in snapshots if item["outcome_group"] == "roi_winner"
        )
        loss_matches = sum(
            int(item["direct_source_recross_failure"])
            for item in snapshots if item["outcome_group"] == "non_roi_loss"
        )
        observable_winners = sum(item["outcome_group"] == "roi_winner" for item in snapshots)
        observable_losses = sum(item["outcome_group"] == "non_roi_loss" for item in snapshots)
        effects[str(horizon)] = {
            "total_roi_winners": total_winners,
            "observable_roi_winners": observable_winners,
            "matched_roi_winners": winner_matches,
            "estimated_total_winner_preservation_share": (
                1.0 - winner_matches / total_winners if total_winners else None
            ),
            "total_non_roi_losses": total_losses,
            "observable_non_roi_losses": observable_losses,
            "matched_non_roi_losses": loss_matches,
            "total_non_roi_loss_capture_share": (
                loss_matches / total_losses if total_losses else None
            ),
            "observable_non_roi_loss_capture_share": (
                loss_matches / observable_losses if observable_losses else None
            ),
            "observable_severe_stop_like_losses": sum(
                item["outcome_group"] == "non_roi_loss" and int(item["severe_stop_like"])
                for item in snapshots
            ),
        }

    return {
        "period": label,
        "metrics": row.get("metrics"),
        "account_valid": topology.account_ok(row),
        "outcome_group_counts": dict(groups),
        "horizon_summary": horizon_summary,
        "direct_source_recross_transition_effects": effects,
        "trade_ledger": enriched,
    }


def supported_horizons(periods: dict[str, Any]) -> list[int]:
    supported = []
    for horizon in HORIZONS:
        valid = True
        for period in periods.values():
            effect = period["direct_source_recross_transition_effects"][str(horizon)]
            capture = effect.get("total_non_roi_loss_capture_share")
            preserve = effect.get("estimated_total_winner_preservation_share")
            if (
                capture is None or preserve is None
                or float(capture) < 0.50 or float(preserve) < 0.80
                or int(effect.get("total_non_roi_losses") or 0) < 5
            ):
                valid = False
                break
        if valid:
            supported.append(horizon)
    return supported


def render(result: dict[str, Any]) -> None:
    lines = [
        "# MBE2 lifecycle forensic v1",
        "",
        f"- parity pass: {result['parity']['pass']}",
        f"- mechanically valid: {result['mechanically_valid']}",
        f"- decision: `{result['decision']}`",
        f"- thresholds searched: {result['thresholds_searched']}",
        f"- supported source horizons: {result['supported_source_horizons']}",
        "",
        "The direct invalidation is fixed: estimated after-cost R is non-positive, the entry symbol has re-crossed to RSI ≥ 70, and TEMA slope is positive.",
        "",
        "| period | horizon | ROI winners preserved | all negative trades captured | observable losses |",
        "|---|---:|---:|---:|---:|",
    ]
    for period_name, period in result.get("periods", {}).items():
        for horizon in HORIZONS:
            effect = period["direct_source_recross_transition_effects"][str(horizon)]
            lines.append(
                f"| {period_name} | {horizon} | "
                f"{effect.get('estimated_total_winner_preservation_share')} | "
                f"{effect.get('total_non_roi_loss_capture_share')} | "
                f"{effect.get('observable_non_roi_losses')} |"
            )
    lines += [
        "",
        "A fresh policy is authorized only when the same source-defined horizon captures at least half of all negative trades in both months while preserving at least 80% of ROI winners. The severe-stop subset is diagnostic only.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file() or not REFERENCE.is_file():
        raise RuntimeError("frozen lifecycle specification or parity reference missing")
    configure_reused_campaign()
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    april = topology.run_case(
        label="april_2026_forensic_parity", mode="ge2_control",
        start=PERIODS["april_2026"][0], end=PERIODS["april_2026"][1],
        destination=Path("april_2026_forensic_parity"),
    )
    parity = topology.parity_check(april)
    dump(EVIDENCE / "parity.json", parity)
    if not parity.get("pass"):
        result = {
            "experiment": "candidate-57-mbe-lifecycle-forensic-v1",
            "parity": parity,
            "mechanically_valid": False,
            "decision": "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION",
            "thresholds_searched": False,
            "supported_source_horizons": [],
            "policy_fresh_authorized": False,
            "periods": {},
        }
        dump(EVIDENCE / "analysis.json", result)
        render(result)
        return 2

    march = topology.run_case(
        label="march_2024_forensic", mode="ge2_control",
        start=PERIODS["march_2024"][0], end=PERIODS["march_2024"][1],
        destination=Path("march_2024_forensic"),
    )
    period_results = {
        "march_2024": analyze_period(
            "march_2024", march, ARTIFACTS / "march_2024_forensic"
        ),
        "april_2026": analyze_period(
            "april_2026", april, ARTIFACTS / "april_2026_forensic_parity"
        ),
    }
    supported = supported_horizons(period_results)
    mechanically_valid = bool(
        parity.get("pass")
        and all(period["account_valid"] for period in period_results.values())
    )
    if not mechanically_valid:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif supported:
        decision = "MBE_SOURCE_RECROSS_INVALIDATION_SUPPORTED_FRESH_REQUIRED"
    else:
        decision = "MBE_LIFECYCLE_RECROSS_HYPOTHESIS_REJECTED_NO_RETUNING"
    result = {
        "experiment": "candidate-57-mbe-lifecycle-forensic-v1",
        "policy_changed": False,
        "parity": parity,
        "mechanically_valid": mechanically_valid,
        "decision": decision,
        "thresholds_searched": False,
        "supported_source_horizons": supported,
        "earliest_supported_source_horizon": min(supported) if supported else None,
        "policy_fresh_authorized": bool(supported and mechanically_valid),
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "periods": period_results,
    }
    dump(EVIDENCE / "analysis.json", result)
    for name, period in period_results.items():
        dump(EVIDENCE / "periods" / f"{name}.json", period)
    render(result)
    print(json.dumps(
        {"decision": decision, "supported_source_horizons": supported, "parity": parity.get("pass")},
        indent=2, sort_keys=True,
    ))
    return 0 if mechanically_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
