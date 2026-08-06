"""Swing plus equal-high/low pool coverage under factorized flow evidence."""

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
        "hml_60m_5m_swing_equal_all_flow",
        "60-minute flow-confirmed HTF acceptance; confirmed 5-minute swings and equal highs/lows; counter-bias sweep flow plus separate response flow.",
        60,
        "SWING_AND_EQUAL",
        "BREAK_LAST_BAR",
        True,
        True,
        True,
        True,
    ),
    (
        "hml_60m_5m_swing_equal_bias_response",
        "60-minute flow-confirmed HTF acceptance; confirmed 5-minute swings and equal highs/lows; sweep defined by price breach/reclaim; separate response flow required.",
        60,
        "SWING_AND_EQUAL",
        "BREAK_LAST_BAR",
        True,
        False,
        True,
        True,
    ),
    (
        "hml_45m_5m_swing_equal_bias_response",
        "Completed 45-minute accepted structure with the same swing/equal pool and bias-plus-response flow contract.",
        45,
        "SWING_AND_EQUAL",
        "BREAK_LAST_BAR",
        True,
        False,
        True,
        True,
    ),
    (
        "hml_60m_5m_equal_only_bias_response",
        "Ablation: 60-minute flow-confirmed bias and response flow, with repeated 5-minute highs/lows only; strict swing pivots are excluded.",
        60,
        "EQUAL_ONLY",
        "BREAK_LAST_BAR",
        True,
        False,
        True,
        True,
    ),
    (
        "hml_60m_5m_swing_equal_full_response",
        "Stricter response ablation: the separate one-minute response must break the full sweep bar rather than only the previous pullback bar.",
        60,
        "SWING_AND_EQUAL",
        "BREAK_SWEEP_BAR",
        True,
        False,
        True,
        True,
    ),
    (
        "hml_60m_5m_swing_only_reference",
        "Known reference: the prior 60-minute/5-minute confirmed-swing bias-plus-response contract without equal pools. Reported but ineligible for selection.",
        60,
        "SWING_ONLY",
        "BREAK_LAST_BAR",
        True,
        False,
        True,
        False,
    ),
)


def _evidence_counts(run_output: Path) -> dict[str, Any]:
    events_path = run_output / "scenario_events.jsonl"
    trades_path = run_output / "trades.json"
    reasons: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            reasons[str(payload.get("reason_code", "UNKNOWN"))] += 1
            event_types[str(payload.get("event_type", "UNKNOWN"))] += 1
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
        "trade_family_counts": dict(families),
        "target_reason_counts": dict(targets),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/hml-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "HIERARCHICAL_MULTI_LIQUIDITY",
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
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "hsc_use_flow_proxy": True,
            "minimum_structural_rr": 0.75,
            "max_holding_bars": 60,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )

    results: list[dict[str, Any]] = []
    for (
        name,
        description,
        bias_period,
        pool_families,
        response_mode,
        bias_flow,
        sweep_flow,
        response_flow,
        eligible,
    ) in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "hsc_bias_period_minutes": bias_period,
                "hml_pool_families": pool_families,
                "hsc_response_mode": response_mode,
                "hff_use_bias_flow": bias_flow,
                "hff_use_sweep_flow": sweep_flow,
                "hff_use_response_flow": response_flow,
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
                "bias_period_minutes": bias_period,
                "pool_families": pool_families,
                "response_mode": response_mode,
                "bias_flow": bias_flow,
                "sweep_flow": sweep_flow,
                "response_flow": response_flow,
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
        locked_path = candidate_dir / "config.hml.locked.json"
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
        "design": "flow-confirmed HTF acceptance -> confirmed LTF swing and/or equal-high/low liquidity -> one-use counter-bias sweep/reclaim -> separate response with directional flow -> nearest unresolved opposite pool or HTF objective",
        "causality_contract": "equal pools become visible only after a separate completed LTF auction confirms a repeated extreme; swing pivots remain right-bar confirmed",
        "controlled_extension": "the prior bias-plus-response flow contract, price thresholds, risk, execution and targets are unchanged; only the source set of pre-existing LTF liquidity is extended",
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first eligible complete gate-qualified liquidity-family contract in fixed ex-ante priority; known swing-only reference cannot be selected",
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
        "v1.6 Hierarchical Swing and Equal-Liquidity Relay",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
