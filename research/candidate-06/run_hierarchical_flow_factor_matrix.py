"""Causal factorization of order-flow evidence across the HSP state sequence."""

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
        "hff_all_flow",
        "Signed taker flow must align with HTF acceptance, oppose the bias during the LTF sweep, and realign on the separate response.",
        True,
        True,
        True,
        True,
    ),
    (
        "hff_bias_response_flow",
        "HTF acceptance and the separate LTF response require aligned participation; the sweep itself is defined by confirmed liquidity, price breach and reclaim rather than contemporaneous signed flow.",
        True,
        False,
        True,
        True,
    ),
    (
        "hff_sweep_response_flow",
        "The LTF liquidity take must contain counter-bias aggression and the separate response must realign; HTF acceptance is price/volume displacement without a signed-flow requirement.",
        False,
        True,
        True,
        True,
    ),
    (
        "hff_bias_sweep_flow",
        "HTF acceptance and the counter-bias liquidity take require signed-flow evidence; the separate response is price displacement only.",
        True,
        True,
        False,
        True,
    ),
    (
        "hff_bias_only_flow",
        "Only the completed HTF acceptance requires signed-flow participation; confirmed pool sweep/reclaim and separate response are price structure.",
        True,
        False,
        False,
        True,
    ),
    (
        "hff_response_only_flow",
        "Only the separate LTF response requires directional signed flow; HTF acceptance and the liquidity sweep are price/volume structure.",
        False,
        False,
        True,
        True,
    ),
    (
        "hff_all_price_reference",
        "Already-failed reference: no stage uses signed taker flow. It is reported for controlled comparison but is ineligible for selection.",
        False,
        False,
        False,
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
        default=Path("artifacts/candidate-06/hff-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "HIERARCHICAL_FLOW_FACTORIZED",
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
            "hsc_sweep_min_atr_1m": 0.10,
            "hsc_sweep_opposing_flow_ratio": 0.03,
            "hsc_sweep_reclaim_tolerance_atr_1m": 0.02,
            "hsc_max_impulse_position": 0.70,
            "hsc_response_bars": 3,
            "hsc_response_body_atr_1m": 0.20,
            "hsc_response_flow_ratio": 0.05,
            "hsc_response_close_location": 0.62,
            "hsc_response_mode": "BREAK_LAST_BAR",
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
    for name, description, bias_flow, sweep_flow, response_flow, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
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
        locked_path = candidate_dir / "config.hff.locked.json"
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
        "design": "unchanged 60m accepted-bias/confirmed-5m-pool hierarchy with signed taker flow independently controlled at HTF acceptance, counter-bias sweep and separate directional response",
        "controlled_variables": {
            "unchanged": [
                "completed-bar clock",
                "confirmed swing-pool construction",
                "structural bias invalidation",
                "one-use pool independence",
                "price thresholds",
                "targets",
                "stops",
                "entry delay",
                "Nautilus execution and NAV accounting",
            ],
            "factored": ["bias flow", "sweep flow", "response flow"],
        },
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first eligible complete gate-qualified flow-stage contract in fixed ex-ante priority; all-price reference cannot be selected",
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
        "v1.5 Hierarchical Flow-Stage Factorization",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
