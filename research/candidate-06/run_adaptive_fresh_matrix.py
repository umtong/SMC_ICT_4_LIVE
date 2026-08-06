"""Controlled AFHR ablations through the existing NautilusTrader runner."""

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
        "afhr_full",
        "Prior-only upper-quartile HTF range and volume acceptance plus completed-close extreme freshness; HML pool, response, execution, risk, stop and target rules unchanged.",
        True,
        True,
        True,
    ),
    (
        "afhr_quality_only_ablation",
        "Single-variable ablation: retain adaptive HTF acceptance quality and remove only the directional extreme-freshness expiry.",
        True,
        False,
        True,
    ),
    (
        "afhr_freshness_only_ablation",
        "Single-variable ablation: retain directional extreme freshness and remove only adaptive HTF quality, restoring the parent HML acceptance thresholds.",
        False,
        True,
        True,
    ),
    (
        "afhr_parent_hml_reference",
        "Unchanged failed HML reference with both AFHR mechanisms disabled; reported for controlled comparison and ineligible for selection.",
        False,
        False,
        False,
    ),
)


def _read_events(run_output: Path) -> list[dict[str, Any]]:
    path = run_output / "scenario_events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _evidence(run_output: Path) -> dict[str, Any]:
    events = _read_events(run_output)
    reasons: Counter[str] = Counter(str(event.get("reason_code", "UNKNOWN")) for event in events)
    event_types: Counter[str] = Counter(str(event.get("event_type", "UNKNOWN")) for event in events)
    scenario_to_context: dict[str, str] = {}
    for event in events:
        details = event.get("details") or {}
        context = details.get("bias_context_id")
        if context:
            scenario_to_context[str(event.get("scenario_id"))] = str(context)

    trades_path = run_output / "trades.json"
    trades = []
    if trades_path.exists():
        trades = json.loads(trades_path.read_text(encoding="utf-8")).get("trades", [])
    families: Counter[str] = Counter(str(trade.get("family", "UNKNOWN")) for trade in trades)
    targets: Counter[str] = Counter(str(trade.get("target_reason", "UNKNOWN")) for trade in trades)
    context_counts: Counter[str] = Counter(
        scenario_to_context.get(str(trade.get("scenario_id")), "UNRESOLVED_CONTEXT")
        for trade in trades
    )
    total = len(trades)
    largest_context_share = max(context_counts.values(), default=0) / total if total else 1.0
    independent_contexts = len([name for name in context_counts if name != "UNRESOLVED_CONTEXT"])
    independence_checks = {
        "minimum_distinct_bias_contexts": independent_contexts >= 3,
        "maximum_largest_bias_context_share": largest_context_share <= 0.50,
        "all_trades_resolved_to_context": "UNRESOLVED_CONTEXT" not in context_counts,
    }
    return {
        "reason_counts": dict(reasons),
        "event_type_counts": dict(event_types),
        "trade_family_counts": dict(families),
        "target_reason_counts": dict(targets),
        "bias_context_trade_counts": dict(context_counts),
        "distinct_bias_contexts": independent_contexts,
        "largest_bias_context_trade_share": largest_context_share,
        "structural_independence_checks": independence_checks,
        "structural_independence_passed": all(independence_checks.values()),
    }


def _base(candidate_dir: Path) -> dict[str, Any]:
    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "ADAPTIVE_FRESH_HIERARCHICAL",
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
            "hml_pool_families": "SWING_AND_EQUAL",
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
            "hsc_response_mode": "BREAK_SWEEP_BAR",
            "hsc_stop_buffer_atr_htf": 0.025,
            "hsc_extension_atr_htf": 0.50,
            "hsc_cooldown_bars": 2,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": True,
            "hff_use_sweep_flow": False,
            "hff_use_response_flow": True,
            "afhr_quality_lookback": 24,
            "afhr_quality_min_history": 12,
            "afhr_quality_quantile": 0.75,
            "afhr_quality_body_fraction": 0.65,
            "afhr_stale_periods": 6.0,
            "minimum_structural_rr": 0.75,
            "max_holding_bars": 60,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/afhr-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _base(candidate_dir)
    results: list[dict[str, Any]] = []
    for name, description, adaptive_quality, extreme_freshness, eligible in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "afhr_use_adaptive_quality": adaptive_quality,
                "afhr_use_extreme_freshness": extreme_freshness,
            },
        )
        path = output / f"{name}.json"
        path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run_output = output / name
        record = _run(path, run_output, 0, candidate_dir, repository)
        evidence = _evidence(run_output)
        record.update(
            {
                "name": name,
                "description": description,
                "adaptive_quality": adaptive_quality,
                "extreme_freshness": extreme_freshness,
                "eligible_for_selection": eligible,
                "causal_evidence": evidence,
                "performance_gate_passed": bool(record.get("gate_passed")),
                "selection_gate_passed": bool(record.get("gate_passed")),
            },
        )
        results.append(record)

    selected = next(
        (
            record["name"]
            for record in results
            if record.get("eligible_for_selection") and record.get("selection_gate_passed")
        ),
        None,
    )
    frozen: list[dict[str, Any]] = []
    locked_path: Path | None = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.afhr.locked.json"
        locked_path.write_text(json.dumps(locked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for week_index in (1, 2):
            week_output = output / f"locked-week-{week_index + 1}"
            record = _run(locked_path, week_output, week_index, candidate_dir, repository)
            evidence = _evidence(week_output)
            record.update(
                {
                    "week_index": week_index,
                    "causal_evidence": evidence,
                    "performance_gate_passed": bool(record.get("gate_passed")),
                    "selection_gate_passed": bool(record.get("gate_passed")),
                },
            )
            frozen.append(record)

    all_three = (
        selected is not None
        and len(frozen) == 2
        and all(record.get("selection_gate_passed") for record in frozen)
    )
    summary = {
        "design": "exceptional completed 60m acceptance relative to sealed prior distributions -> fresh directional close progress -> confirmed 5m swing/equal liquidity sweep -> separate flow-confirmed full-sweep response -> unchanged structural objective",
        "market_logic": {
            "adaptive_quality": "large signed participation is continuation evidence only when the completed auction also travels and closes directionally relative to prior completed auctions",
            "extreme_freshness": "an unbroken boundary is necessary but not sufficient; without renewed completed-close progress, persistent order flow is treated as absorbed or fully digested rather than fresh continuation information",
            "independence": "trade count is additionally diagnosed by distinct accepted HTF contexts so repeated pools inside one context are not silently treated as independent evidence",
        },
        "controlled_variables": {
            "unchanged": [
                "HML confirmed swing/equal-pool detector",
                "counter-bias breach and reclaim",
                "separate full-sweep response",
                "bias and response taker-flow stages",
                "structural stop and objective selection",
                "one-bar delayed market entry",
                "3% NAV risk budget",
                "fees, slippage, fills, positions and NAV in NautilusTrader",
            ],
            "ablated_one_at_a_time": ["adaptive HTF quality", "directional extreme freshness"],
        },
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first eligible variant in fixed ex-ante priority passing the existing complete performance gate; bias-context concentration is retained as a non-binary diagnostic rather than an arbitrary hard rejection rule",
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
        "v1.8 Adaptive-Fresh Hierarchical Liquidity Relay",
    )
    independence = [
        "",
        "## Structural independence diagnostic",
        "",
        "Bias-context concentration is reported separately. It informs causal interpretation and later validation but is not an arbitrary first-week hard gate.",
        "",
    ]
    for record in results:
        evidence = record["causal_evidence"]
        independence.append(
            f"- `{record['name']}`: contexts={evidence['distinct_bias_contexts']}, "
            f"largest context share={evidence['largest_bias_context_trade_share']:.2%}, "
            f"selection gate={record['selection_gate_passed']}"
        )
    (output / "SUMMARY.md").write_text(report + "\n".join(independence) + "\n", encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
