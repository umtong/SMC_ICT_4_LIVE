"""Periodic quarter-hour opening imbalance with confirmed-liquidity entries."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
from typing import Any

from run_equilibrium_matrix import _base as _equilibrium_base
from run_equilibrium_matrix import _render, _run


VARIANTS = (
    (
        "poil_strict_4h_swing_full",
        "Unusually strong aligned quarter-hour opening volume and signed pressure create a four-hour bias; confirmed 5-minute swing liquidity is swept; a separate response breaks the full sweep bar.",
        240,
        "SWING_ONLY",
        "BREAK_SWEEP_BAR",
        "STRICT",
        True,
    ),
    (
        "poil_strict_4h_swing_equal_full",
        "Same strict four-hour periodic bias and full-sweep response, with both confirmed 5-minute swing and equal-high/low liquidity pools.",
        240,
        "SWING_AND_EQUAL",
        "BREAK_SWEEP_BAR",
        "STRICT",
        True,
    ),
    (
        "poil_strict_8h_swing_full",
        "Strict periodic opening imbalance with an eight-hour finite horizon, confirmed 5-minute swing liquidity and full-sweep response.",
        480,
        "SWING_ONLY",
        "BREAK_SWEEP_BAR",
        "STRICT",
        True,
    ),
    (
        "poil_moderate_4h_swing_full",
        "Moderate but still jointly aligned opening volume, signed pressure, body and location thresholds; four-hour bias; confirmed swing pool; full-sweep response.",
        240,
        "SWING_ONLY",
        "BREAK_SWEEP_BAR",
        "MODERATE",
        True,
    ),
    (
        "poil_strict_4h_swing_last",
        "Strict four-hour periodic bias and confirmed swing pool; the separate response breaks the immediately preceding pullback bar rather than the full sweep bar.",
        240,
        "SWING_ONLY",
        "BREAK_LAST_BAR",
        "STRICT",
        True,
    ),
    (
        "poil_price_dominant_reference",
        "Ineligible reference: periodic opening body and volume dominate; only the sign, not the magnitude, of taker imbalance is required.",
        240,
        "SWING_ONLY",
        "BREAK_SWEEP_BAR",
        "PRICE_REFERENCE",
        False,
    ),
)

THRESHOLDS = {
    "STRICT": {
        "poil_opening_volume_multiple": 1.15,
        "poil_opening_pressure_multiple": 1.50,
        "poil_opening_flow_ratio": 0.12,
        "poil_opening_body_atr_1m": 0.25,
        "poil_opening_range_atr_1m": 0.50,
        "poil_opening_body_fraction": 0.45,
        "poil_opening_close_location": 0.65,
    },
    "MODERATE": {
        "poil_opening_volume_multiple": 1.00,
        "poil_opening_pressure_multiple": 1.25,
        "poil_opening_flow_ratio": 0.08,
        "poil_opening_body_atr_1m": 0.20,
        "poil_opening_range_atr_1m": 0.40,
        "poil_opening_body_fraction": 0.40,
        "poil_opening_close_location": 0.60,
    },
    "PRICE_REFERENCE": {
        "poil_opening_volume_multiple": 1.15,
        "poil_opening_pressure_multiple": 0.00,
        "poil_opening_flow_ratio": 0.00,
        "poil_opening_body_atr_1m": 0.25,
        "poil_opening_range_atr_1m": 0.50,
        "poil_opening_body_fraction": 0.45,
        "poil_opening_close_location": 0.65,
    },
}


def _evidence_counts(run_output: Path) -> dict[str, Any]:
    events_path = run_output / "scenario_events.jsonl"
    trades_path = run_output / "trades.json"
    reasons: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            reasons[str(payload.get("reason_code", "UNKNOWN"))] += 1
            event_types[str(payload.get("event_type", "UNKNOWN"))] += 1
            if payload.get("reason_code") == "QUARTER_HOUR_OPENING_IMBALANCE_ACCEPTED":
                directions[str(payload.get("details", {}).get("direction", "UNKNOWN"))] += 1
    families: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    if trades_path.exists():
        payload = json.loads(trades_path.read_text(encoding="utf-8"))
        for trade in payload.get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
            targets[str(trade.get("target_reason", "UNKNOWN"))] += 1
    return {
        "reason_counts": dict(reasons),
        "event_type_counts": dict(event_types),
        "periodic_bias_direction_counts": dict(directions),
        "trade_family_counts": dict(families),
        "target_reason_counts": dict(targets),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/poil-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "PERIODIC_OPENING_LIQUIDITY_RELAY",
            "hsc_bias_period_minutes": 60,
            "hsc_liquidity_period_minutes": 5,
            "hsp_bias_expiry_mode": "FIXED_PERIODS",
            "hsc_bias_boundary_loss_atr": 0.10,
            "hsp_liquidity_pool_mode": "CONFIRMED_SWING",
            "hml_equal_lookback_bars": 8,
            "hml_equal_min_intervening_bars": 1,
            "hml_equal_tolerance_range_fraction": 0.08,
            "hml_equal_rejection_close_fraction": 0.35,
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_max_impulse_position": 0.85,
            "hsc_response_bars": 3,
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "poil_opening_history": 16,
            "poil_quarter_atr_bars": 8,
            "minimum_structural_rr": 0.75,
            "max_holding_bars": 60,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )

    results: list[dict[str, Any]] = []
    for name, description, horizon, pool_families, response_mode, threshold_name, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(THRESHOLDS[threshold_name])
        config["logic"].update(
            {
                "poil_bias_horizon_minutes": horizon,
                "hml_pool_families": pool_families,
                "hsc_response_mode": response_mode,
            },
        )
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run_output = output / name
        record = _run(path, run_output, 0, candidate_dir, repository)
        record.update(
            {
                "name": name,
                "description": description,
                "bias_horizon_minutes": horizon,
                "pool_families": pool_families,
                "response_mode": response_mode,
                "threshold_contract": threshold_name,
                "eligible_for_selection": eligible,
                "causal_evidence": _evidence_counts(run_output),
            },
        )
        results.append(record)

    selected = next(
        (
            record["name"]
            for record in results
            if record.get("eligible_for_selection") and record.get("gate_passed")
        ),
        None,
    )
    frozen: list[dict[str, Any]] = []
    locked_path: Path | None = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.poil.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            week_output = output / f"locked-week-{week_index + 1}"
            record = _run(
                locked_path,
                week_output,
                week_index,
                candidate_dir,
                repository,
            )
            record["week_index"] = week_index
            record["causal_evidence"] = _evidence_counts(week_output)
            frozen.append(record)

    all_three = selected is not None and len(frozen) == 2 and all(record.get("gate_passed") for record in frozen)
    summary = {
        "design": "prior-normalized quarter-hour opening volume and signed aggressive pressure -> finite 4h/8h directional context -> confirmed 5m liquidity sweep/reclaim -> separate response flow -> structural objective",
        "external_research_hypothesis": "periodic algorithmic participation is concentrated at quarter-hour openings; opening order imbalance may contain multi-hour directional information",
        "causality_contract": "the opening minute is evaluated only after completion against prior opening and prior completed-quarter baselines; it cannot consume a pool or confirm an entry on the same bar",
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first eligible complete gate-qualified periodic-context contract in fixed ex-ante priority; price-dominant reference cannot be selected",
        "selected": selected,
        "locked_config": None if locked_path is None else str(locked_path.relative_to(repository)),
        "first_week_results": results,
        "frozen_validation": frozen,
        "all_three_weeks_passed": all_three,
        "long_evaluation_authorized": all_three,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = _render(results, selected, frozen).replace(
        "v0.5 Session Equilibrium Retest",
        "v1.7 Periodic Opening-Imbalance Liquidity Relay",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
