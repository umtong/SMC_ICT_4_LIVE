#!/usr/bin/env python3
"""Aggregate Candidate 15 V6's exposed residual-laggard development screen."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from aggregate import aggregate as aggregate_v5
from aggregate import read_object, write_object


ALLOWED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"}
MODULE = "PERSISTENT_QH_MSS_FVG_CONTINUATION"


def _route_audit(root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    submitted = 0
    violations: list[dict[str, Any]] = []
    symbols: Counter[str] = Counter()
    for interval in protocol["selection"]["intervals"]:
        path = root / "results" / interval / "submitted_plans.json"
        payload = read_object(path)
        plans = payload.get("plans", [])
        if not isinstance(plans, list):
            raise TypeError(f"{path} plans must be a list")
        for plan in plans:
            submitted += 1
            symbol = str(plan.get("symbol", ""))
            symbols[symbol] += 1
            details = plan.get("details", {})
            route = details.get("candidate15_v6_route") if isinstance(details, dict) else None
            accepted = set(route.get("accepted_symbols", ())) if isinstance(route, dict) else set()
            laggard = route.get("laggard_symbol") if isinstance(route, dict) else None
            valid = (
                isinstance(route, dict)
                and route.get("policy") == "EXCLUDED_RESIDUAL_MARKET_ONLY"
                and accepted.issubset(ALLOWED)
                and len(accepted) == 3
                and symbol in ALLOWED
                and symbol not in accepted
                and laggard == symbol
                and ALLOWED - accepted == {symbol}
            )
            if not valid:
                violations.append(
                    {
                        "interval": interval,
                        "scenario_id": plan.get("scenario_id"),
                        "symbol": symbol,
                        "route": route,
                    },
                )
    return {
        "submitted_plans": submitted,
        "symbol_counts": dict(sorted(symbols.items())),
        "violation_count": len(violations),
        "violations": violations[:25],
    }


def aggregate(root: Path) -> dict[str, Any]:
    payload = aggregate_v5(root)
    protocol = read_object(root / "protocol.json")
    route_audit = _route_audit(root, protocol)

    laggard_rejections = 0
    for record in payload.get("intervals", []):
        diagnostics = record.get("diagnostics", {})
        reasons = diagnostics.get("reason_code_counts", {})
        laggard_rejections += int(reasons.get("C15_V6_NOT_RESIDUAL_LAGGARD", 0))

    checks = dict(payload.get("checks", {}))
    checks.pop("only_response_continuation_submitted", None)
    modules = payload.get("module_counts", {})
    checks["only_residual_laggard_submitted"] = (
        route_audit["violation_count"] == 0
        and set(modules).issubset({MODULE})
    )

    if not checks.get("minimum_closed_trades") or not checks.get("minimum_active_intervals"):
        classification = "CANDIDATE15_V6_INSUFFICIENT_ACTIVITY"
    elif all(checks.values()):
        classification = "CANDIDATE15_V6_DEVELOPMENT_PROMISING"
    else:
        classification = "CANDIDATE15_V6_DEVELOPMENT_REJECTED"

    payload.update(
        {
            "schema": "candidate-15-v6-residual-laggard-development-aggregate-v1",
            "candidate": protocol["candidate"],
            "protocol": protocol["schema"],
            "classification": classification,
            "checks": checks,
            "residual_laggard_route_audit": route_audit,
            "accepted_market_plan_rejections": laggard_rejections,
            "next_evidence_required": (
                "A promising exposed result permits only a frozen, newly "
                "predeclared confirmation screen."
            ),
        },
    )
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V6 residual-laggard delivery",
        "",
        f"**{classification}**",
        "",
        "- development_only: `True`",
        "- success_claim: `False`",
        f"- weekly_reset_nav_multiple: `{payload.get('weekly_reset_nav_multiple')}`",
        f"- daily_geometric_growth: `{payload.get('daily_geometric_growth')}`",
        f"- closed_trades: `{payload.get('closed_trades')}`",
        f"- wins / losses: `{payload.get('wins')} / {payload.get('losses')}`",
        f"- win_rate: `{payload.get('win_rate')}`",
        f"- payoff_ratio: `{payload.get('payoff_ratio')}`",
        f"- active_intervals: `{payload.get('active_intervals')}`",
        f"- closed_trade_path_max_drawdown: `{payload.get('closed_trade_path_max_drawdown')}`",
        f"- initiative_activations: `{payload.get('initiative_activations')}`",
        f"- response_rejections: `{payload.get('response_rejections')}`",
        f"- accepted_market_plan_rejections: `{laggard_rejections}`",
        f"- residual_route_violations: `{route_audit['violation_count']}`",
        "",
        "## Interval evidence",
    ]
    for record in payload.get("intervals", []):
        event_types = record.get("diagnostics", {}).get("event_type_counts", {})
        reasons = record.get("diagnostics", {}).get("reason_code_counts", {})
        lines.append(
            f"- {record['interval']} ({record['start']}): "
            f"daily_geo={record['daily_geometric_growth']}, "
            f"trades={record['closed_trades']}, W/L={record['wins']}/{record['losses']}, "
            f"activations={event_types.get('QHI_INITIATIVE_ACTIVATED', 0)}, "
            f"accepted_rejections={reasons.get('C15_V6_NOT_RESIDUAL_LAGGARD', 0)}"
        )
    lines.extend(("", "## Development checks"))
    lines.extend(f"- {key}: `{value}`" for key, value in checks.items())
    lines.extend(("", "## Highest-volume diagnostic skips"))
    lines.extend(
        f"- {key}: `{value}`"
        for key, value in payload.get("top_skip_reasons", {}).items()
    )
    lines.extend(
        (
            "",
            "E01-E06 are exposed controlled-development intervals. V6 may only "
            "reject or improve the residual-delivery mechanism; it cannot support "
            "a success claim.",
        ),
    )
    (root / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(aggregate(args.root.resolve()), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
