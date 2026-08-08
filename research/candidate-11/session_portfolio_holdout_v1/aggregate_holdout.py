#!/usr/bin/env python3
"""Aggregate untouched H1-H3 evidence and authorize a holdout claim only.

The tested Candidate 14 aggregator performs all metric reconstruction. This
wrapper changes no calculation; it adds the pre-data protocol lineage, marks
holdout evidence as claim-eligible, and reports diagnostic+holdout context
without pooling the diagnostic periods into the holdout decision.
"""
from __future__ import annotations

import argparse
import json
from math import exp, log
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
STRATEGY_ROOT = HERE.parent / "session_portfolio_v1"
if str(STRATEGY_ROOT) not in sys.path:
    sys.path.insert(0, str(STRATEGY_ROOT))

import aggregate as base_aggregate  # noqa: E402


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    original_argv = sys.argv
    try:
        sys.argv = [
            "aggregate.py",
            "--results", str(args.results),
            "--protocol", str(args.protocol),
            "--output", str(args.output),
        ]
        status = base_aggregate.main()
    finally:
        sys.argv = original_argv
    if status != 0:
        return int(status)

    protocol = load_object(args.protocol)
    result = load_object(args.output)
    if protocol.get("validation_mode") != "holdout":
        raise SystemExit("aggregate_holdout requires a pre-data holdout protocol")
    if protocol.get("market_data_opened") is not False:
        raise SystemExit("pre-data holdout marker is not intact")

    gate_passed = result.get("gate_passed") is True
    result.update(
        {
            "schema": "candidate-11-multi-session-holdout-aggregate-v1",
            "classification": (
                "CANDIDATE11_UNTOUCHED_HOLDOUT_GATE_PASSED"
                if gate_passed
                else "CANDIDATE11_UNTOUCHED_HOLDOUT_GATE_FAILED"
            ),
            "claim_allowed": True,
            "success_claim": gate_passed,
            "source_commit_before_market_data": protocol["source_commit_before_market_data"],
            "diagnostic_evidence_commit": protocol["diagnostic_evidence_commit"],
            "pre_data_selection_seed": protocol["selection"]["seed"],
            "prior_opened_data_buffer_days": protocol["selection"]["prior_opened_data_buffer_days"],
        }
    )

    diagnostic = protocol["diagnostic_result"]
    diagnostic_days = int(diagnostic["calendar_days"])
    holdout_days = int(result["observed_calendar_days"])
    diagnostic_multiple = float(diagnostic["pooled_nav_multiple"])
    holdout_multiple = float(result["pooled_nav_multiple"])
    combined_days = diagnostic_days + holdout_days
    combined_multiple = diagnostic_multiple * holdout_multiple
    combined_daily = (
        exp(log(combined_multiple) / combined_days) - 1.0
        if combined_days > 0 and combined_multiple > 0.0
        else -1.0
    )
    result["non_decisional_combined_context"] = {
        "calendar_days": combined_days,
        "nav_multiple": combined_multiple,
        "daily_geometric_growth": combined_daily,
        "closed_trades": int(diagnostic["closed_trades"]) + int(result["closed_trades"]),
        "wins": int(diagnostic["wins"]) + int(result["wins"]),
        "losses": int(diagnostic["losses"]) + int(result["losses"]),
        "note": "Reported for continuity only; H1-H3 alone determine the holdout gate.",
    }
    write_json(args.output, result)

    lines = [
        "# Candidate 11 untouched holdout result",
        "",
        f"**{result['classification']}**",
        "",
        f"- gate_passed: `{result['gate_passed']}`",
        f"- success_claim: `{result['success_claim']}`",
        f"- daily_geometric_growth: `{result['daily_geometric_growth']:.10f}`",
        f"- pooled_nav_multiple: `{result['pooled_nav_multiple']:.10f}`",
        f"- closed_trades: `{result['closed_trades']}`",
        f"- wins / losses: `{result['wins']} / {result['losses']}`",
        f"- win_rate: `{result['win_rate']:.6f}`",
        f"- payoff_ratio: `{result['payoff_ratio']}`",
        f"- active_weeks: `{result['active_weeks']} / {result['holdout_count']}`",
        f"- maximum_weekly_closed_trade_drawdown: `{result['maximum_weekly_closed_trade_drawdown']:.10f}`",
        f"- maximum_positive_log_growth_share_from_one_week: `{result['maximum_positive_log_growth_share_from_one_week']:.10f}`",
        f"- source_commit_before_market_data: `{result['source_commit_before_market_data']}`",
        "",
        "## Precommitted gate checks",
    ]
    lines.extend(f"- {name}: `{passed}`" for name, passed in result["checks"].items())
    lines.extend(("", "## Untouched weekly evidence"))
    for record in result["weeks"]:
        lines.append(
            f"- {record['week']} ({record['start']}): "
            f"daily_geo={record['daily_geometric_growth']:.6f}, "
            f"trades={record['closed_trades']}, "
            f"W/L={record['wins']}/{record['losses']}, "
            f"plans={record['submitted_plans']}, "
            f"safety={record['safety_audit_passed']}"
        )
    combined = result["non_decisional_combined_context"]
    lines.extend(
        (
            "",
            "## Diagnostic + holdout continuity context (not used for the holdout gate)",
            f"- calendar_days: `{combined['calendar_days']}`",
            f"- nav_multiple: `{combined['nav_multiple']:.10f}`",
            f"- daily_geometric_growth: `{combined['daily_geometric_growth']:.10f}`",
            f"- trades / wins / losses: `{combined['closed_trades']} / {combined['wins']} / {combined['losses']}`",
        )
    )
    args.output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
