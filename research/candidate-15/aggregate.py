#!/usr/bin/env python3
"""Aggregate Candidate 15 V3 screening and contaminated diagnostics."""
from __future__ import annotations

import argparse
from decimal import Decimal
from functools import reduce
import json
from pathlib import Path
from typing import Any


def read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def write_object(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def decimal_value(value: Any, default: str = "0") -> Decimal:
    return Decimal(default if value is None else str(value))


def summarize(records: list[dict[str, Any]], start_nav: Decimal, days_per_interval: int) -> dict[str, Any]:
    multiples = [decimal_value(item["final_nav"], str(start_nav)) / start_nav for item in records]
    nav_multiple = reduce(lambda left, right: left * right, multiples, Decimal("1"))
    days = days_per_interval * len(records)
    daily_geo = (
        float(nav_multiple ** (Decimal("1") / Decimal(days)) - Decimal("1"))
        if days and nav_multiple > 0
        else None
    )
    trades = sum(item["closed_trades"] for item in records)
    wins = sum(item["wins"] for item in records)
    losses = sum(item["losses"] for item in records)
    drawdowns = [
        float(item["closed_trade_max_drawdown"])
        for item in records
        if item["closed_trade_max_drawdown"] is not None
    ]
    return {
        "interval_count": len(records),
        "weekly_reset_nav_multiple": float(nav_multiple),
        "daily_geometric_growth": daily_geo,
        "closed_trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades if trades else None,
        "maximum_interval_closed_trade_drawdown": max(drawdowns, default=0.0),
        "safety": all(
            not item["engine_errors"] and item["liquidation_detected"] is not True
            for item in records
        ),
    }


def aggregate(root: Path) -> dict[str, Any]:
    protocol = read_object(root / "protocol.json")
    records: list[dict[str, Any]] = []
    for interval, selection in protocol["selection"]["intervals"].items():
        summary = read_object(root / "results" / interval / "summary.json")
        records.append(
            {
                "interval": interval,
                "role": selection["role"],
                "start": selection["start"],
                "end_exclusive": selection["end_exclusive"],
                "daily_geometric_growth": summary.get("daily_geometric_growth"),
                "final_nav": summary.get("final_nav"),
                "closed_trades": int(summary.get("closed_trades") or 0),
                "wins": int(summary.get("wins") or 0),
                "losses": int(summary.get("losses") or 0),
                "win_rate": summary.get("win_rate"),
                "payoff_ratio": summary.get("payoff_ratio"),
                "closed_trade_max_drawdown": summary.get("closed_trade_max_drawdown"),
                "liquidation_detected": summary.get("liquidation_detected"),
                "engine_errors": summary.get("engine_errors", []),
                "router_diagnostics": summary.get("router_diagnostics", {}),
            },
        )

    mechanism = [item for item in records if item["role"] == "contaminated-v2-mechanism-replay"]
    reference = [item for item in records if item["role"] == "contaminated-candidate13-reference-replay"]
    confirmation = [item for item in records if item["role"] == "predeclared-v3-confirmation"]
    start_nav = decimal_value(protocol["execution_lock"]["starting_nav"])
    days = int(protocol["selection"]["evaluation_days"])
    mechanism_stats = summarize(mechanism, start_nav, days)
    reference_stats = summarize(reference, start_nav, days)
    confirmation_stats = summarize(confirmation, start_nav, days)

    checks = {
        "all_intervals_present": len(records) == len(protocol["selection"]["intervals"]),
        "five_predeclared_confirmation_intervals": len(confirmation) == 5,
        "confirmation_activity": confirmation_stats["closed_trades"] >= 5,
        "positive_costed_growth": (
            confirmation_stats["daily_geometric_growth"] is not None
            and confirmation_stats["daily_geometric_growth"] > 0.0
        ),
        "project_growth_threshold": (
            confirmation_stats["daily_geometric_growth"] is not None
            and confirmation_stats["daily_geometric_growth"] >= 0.01
        ),
        "win_rate_at_least_0_65": (
            confirmation_stats["win_rate"] is not None
            and confirmation_stats["win_rate"] >= 0.65
        ),
        "maximum_interval_drawdown_at_most_0_20": (
            confirmation_stats["maximum_interval_closed_trade_drawdown"] <= 0.20
        ),
        "safety": confirmation_stats["safety"],
    }
    if not checks["confirmation_activity"]:
        classification = "CANDIDATE15_V3_INSUFFICIENT_ACTIVITY"
    elif all(
        checks[key]
        for key in (
            "positive_costed_growth",
            "win_rate_at_least_0_65",
            "maximum_interval_drawdown_at_most_0_20",
            "safety",
        )
    ):
        classification = "CANDIDATE15_V3_PROMISING_SCREEN"
    else:
        classification = "CANDIDATE15_V3_SCREEN_REJECTED"

    payload = {
        "schema": "candidate-15-v3-screen-aggregate-v1",
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "classification": classification,
        "success_claim": False,
        "continuous_account_evidence": False,
        "weekly_reset_screen": True,
        "mechanism_replay": mechanism_stats,
        "candidate13_reference_replay": reference_stats,
        "confirmation": confirmation_stats,
        "intervals": records,
        "checks": checks,
        "next_evidence_required": "A frozen continuous-account interval is required before any success claim.",
    }
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V3 scenario-terminal invalidation",
        "",
        f"**{classification}**",
        "",
        "- success_claim: `False`",
        "- continuous_account_evidence: `False`",
        "- weekly_reset_screen: `True`",
        "",
        "## Predeclared V3 confirmation",
        f"- daily_geometric_growth: `{confirmation_stats['daily_geometric_growth']}`",
        f"- weekly_reset_nav_multiple: `{confirmation_stats['weekly_reset_nav_multiple']:.10f}`",
        f"- closed_trades: `{confirmation_stats['closed_trades']}`",
        f"- wins / losses: `{confirmation_stats['wins']} / {confirmation_stats['losses']}`",
        f"- win_rate: `{confirmation_stats['win_rate']}`",
        f"- maximum_interval_closed_trade_drawdown: `{confirmation_stats['maximum_interval_closed_trade_drawdown']}`",
        "",
        "## Contaminated diagnostic replays",
        f"- V2 mechanism replay trades W/L: `{mechanism_stats['closed_trades']}` / `{mechanism_stats['wins']}/{mechanism_stats['losses']}`",
        f"- Candidate 13 reference replay trades W/L: `{reference_stats['closed_trades']}` / `{reference_stats['wins']}/{reference_stats['losses']}`",
        f"- Candidate 13 reference replay daily_geo: `{reference_stats['daily_geometric_growth']}`",
        "",
        "## Interval evidence",
    ]
    for item in records:
        transitions = item["router_diagnostics"].get("router_transition_counts", {})
        lines.append(
            f"- {item['interval']} [{item['role']}] ({item['start']}): "
            f"daily_geo={item['daily_geometric_growth']}, trades={item['closed_trades']}, "
            f"W/L={item['wins']}/{item['losses']}, router={transitions}"
        )
    lines.extend(("", "## Checks"))
    lines.extend(f"- {key}: `{value}`" for key, value in checks.items())
    lines.extend(
        (
            "",
            "Classification uses only V1-V5. M1/M2 and C1-C5 are contaminated diagnostics. "
            "The confirmation weeks do not form one continuous account path.",
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
