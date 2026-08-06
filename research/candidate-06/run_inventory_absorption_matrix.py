"""Staged first-week matrix for inventory-absorption pullback continuation."""

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
        "iapc_15m_flow_break_pullback",
        "Completed 15-minute accepted inventory imbalance; genuinely opposing pullback flow is absorbed above/below the accepted boundary; a separate response closes through the full pullback structure.",
        15,
        "FLOW_ABSORPTION",
        "BREAK_PULLBACK_STRUCTURE",
    ),
    (
        "iapc_15m_flow_break_last",
        "Same accepted 15-minute inventory regime and opposing-flow absorption, with a separate one-minute micro-structure break through the last pullback bar.",
        15,
        "FLOW_ABSORPTION",
        "BREAK_LAST_BAR",
    ),
    (
        "iapc_30m_flow_break_last",
        "Completed 30-minute accepted inventory regime; opposing-flow pullback absorption; separate one-minute micro-structure resumption.",
        30,
        "FLOW_ABSORPTION",
        "BREAK_LAST_BAR",
    ),
    (
        "iapc_15m_price_break_pullback",
        "Ablation: identical accepted 15-minute regime and full pullback-structure response, without requiring aggregate opposing taker flow.",
        15,
        "STRUCTURAL_PULLBACK",
        "BREAK_PULLBACK_STRUCTURE",
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
    if trades_path.exists():
        payload = json.loads(trades_path.read_text(encoding="utf-8"))
        for trade in payload.get("trades", []):
            families[str(trade.get("family", "UNKNOWN"))] += 1
    return {
        "reason_counts": dict(reasons),
        "event_type_counts": dict(event_types),
        "trade_family_counts": dict(families),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-06/iapc-first-week"),
    )
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    base = _equilibrium_base(json.loads((candidate_dir / "config.json").read_text(encoding="utf-8")))
    base["logic"].update(
        {
            "engine": "INVENTORY_ABSORPTION_PULLBACK",
            "iapc_atr_bars": 12,
            "iapc_volume_bars": 12,
            "iapc_breakout_lookback": 4,
            "iapc_acceptance_close_atr": 0.02,
            "iapc_regime_range_atr": 0.70,
            "iapc_regime_body_fraction": 0.50,
            "iapc_regime_relative_volume": 0.95,
            "iapc_regime_flow_ratio": 0.04,
            "iapc_regime_close_location": 0.68,
            "iapc_regime_lifetime_periods": 4.0,
            "iapc_boundary_loss_atr": 0.08,
            "iapc_pullback_min_atr": 0.08,
            "iapc_pullback_max_atr": 0.55,
            "iapc_pullback_start_flow": 0.02,
            "iapc_pullback_min_bars": 2,
            "iapc_pullback_max_bars": 8,
            "iapc_absorption_opposing_flow": 0.03,
            "iapc_response_body_atr_1m": 0.12,
            "iapc_response_flow_ratio": 0.03,
            "iapc_response_close_location": 0.58,
            "iapc_stop_buffer_atr": 0.04,
            "iapc_extension_atr": 0.75,
            "minimum_structural_rr": 1.05,
            "max_holding_bars": 60,
            "sac_entry_confirmation": "NONE",
            "sac_failed_defense_action": "ABSTAIN",
            "enforce_favorable_drift_guard": True,
        },
    )

    results: list[dict[str, Any]] = []
    for name, description, period, pullback_mode, response_mode in VARIANTS:
        config = copy.deepcopy(base)
        config["candidate_variant"] = name
        config["variant_description"] = description
        config["logic"].update(
            {
                "iapc_period_minutes": period,
                "iapc_pullback_mode": pullback_mode,
                "iapc_response_mode": response_mode,
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
                "period_minutes": period,
                "pullback_mode": pullback_mode,
                "response_mode": response_mode,
                "causal_evidence": _evidence_counts(run_output),
            },
        )
        results.append(record)

    selected = next((record["name"] for record in results if record.get("gate_passed")), None)
    frozen: list[dict[str, Any]] = []
    locked_path: Path | None = None
    if selected is not None:
        locked = json.loads((output / f"{selected}.json").read_text(encoding="utf-8"))
        locked.setdefault("validation", {})["stage"] = "three_week_validation"
        locked_path = candidate_dir / "config.iapc.locked.json"
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
        "design": "completed HTF accepted range break -> opposing signed-flow pullback -> boundary defense -> separate micro-structure resumption -> structural objective",
        "research_basis": {
            "order_flow": "signed aggressive flow is used only as evidence of pressure; the signal requires price response and accepted-boundary defense rather than treating contemporaneous flow as a forecast by itself",
            "absorption": "opposing taker flow with bounded retracement operationalizes effort without equivalent price result",
            "state_separation": "regime formation, pullback evidence, response confirmation, execution and accounting remain separate",
        },
        "variant_priority": [name for name, *_ in VARIANTS],
        "selection_rule": "first complete gate-qualified causal variant in fixed ex-ante priority",
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
        "v1.2 Inventory Absorption Pullback Continuation",
    )
    (output / "SUMMARY.md").write_text(report, encoding="utf-8")

    if selected is None:
        return 2
    if not all_three:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
