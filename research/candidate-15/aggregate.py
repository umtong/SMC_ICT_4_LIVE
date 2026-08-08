#!/usr/bin/env python3
"""Aggregate Candidate 15's predeclared screening intervals.

The intervals run as separate Nautilus accounts for parallel information gain.
The aggregate is therefore explicitly a weekly-reset screen, not continuous
account evidence and never a final success claim.
"""
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
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def decimal_value(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def aggregate(root: Path) -> dict[str, Any]:
    protocol = read_object(root / "protocol.json")
    interval_records: list[dict[str, Any]] = []
    for interval, selection in protocol["selection"]["intervals"].items():
        summary_path = root / "results" / interval / "summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(summary_path)
        summary = read_object(summary_path)
        interval_records.append(
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
                "promising_gate_passed": bool(summary.get("promising_gate_passed")),
                "complete_gate_passed": bool(summary.get("complete_gate_passed")),
                "liquidation_detected": summary.get("liquidation_detected"),
                "engine_errors": summary.get("engine_errors", []),
                "router_diagnostics": summary.get("router_diagnostics", {}),
            },
        )

    start_nav = decimal_value(protocol["execution_lock"]["starting_nav"])
    nav_multiples = [
        decimal_value(record["final_nav"], str(start_nav)) / start_nav
        for record in interval_records
    ]
    weekly_reset_nav_multiple = reduce(lambda x, y: x * y, nav_multiples, Decimal("1"))
    days = protocol["selection"]["evaluation_days"] * len(interval_records)
    daily_geo = (
        float(weekly_reset_nav_multiple ** (Decimal("1") / Decimal(days)) - Decimal("1"))
        if days > 0 and weekly_reset_nav_multiple > 0
        else None
    )
    trades = sum(record["closed_trades"] for record in interval_records)
    wins = sum(record["wins"] for record in interval_records)
    losses = sum(record["losses"] for record in interval_records)
    win_rate = wins / trades if trades else None
    drawdowns = [
        float(record["closed_trade_max_drawdown"])
        for record in interval_records
        if record["closed_trade_max_drawdown"] is not None
    ]
    maximum_interval_drawdown = max(drawdowns, default=0.0)
    safety = all(
        not record["engine_errors"]
        and record["liquidation_detected"] is not True
        for record in interval_records
    )

    checks = {
        "all_intervals_present": len(interval_records)
        == len(protocol["selection"]["intervals"]),
        "screening_activity": trades >= 5,
        "positive_costed_growth": daily_geo is not None and daily_geo > 0.0,
        "project_growth_threshold": daily_geo is not None and daily_geo >= 0.01,
        "win_rate_at_least_0_65": win_rate is not None and win_rate >= 0.65,
        "maximum_interval_drawdown_at_most_0_20": maximum_interval_drawdown <= 0.20,
        "safety": safety,
    }
    if not checks["screening_activity"]:
        classification = "CANDIDATE15_INSUFFICIENT_ACTIVITY"
    elif all(
        checks[key]
        for key in (
            "positive_costed_growth",
            "win_rate_at_least_0_65",
            "maximum_interval_drawdown_at_most_0_20",
            "safety",
        )
    ):
        classification = "CANDIDATE15_PROMISING_SCREEN"
    else:
        classification = "CANDIDATE15_SCREEN_REJECTED"

    payload = {
        "schema": "candidate-15-screen-aggregate-v1",
        "candidate": protocol["candidate"],
        "classification": classification,
        "success_claim": False,
        "continuous_account_evidence": False,
        "weekly_reset_screen": True,
        "intervals": interval_records,
        "weekly_reset_nav_multiple": float(weekly_reset_nav_multiple),
        "daily_geometric_growth": daily_geo,
        "closed_trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "maximum_interval_closed_trade_drawdown": maximum_interval_drawdown,
        "checks": checks,
        "next_evidence_required": (
            "A frozen continuous-account interval is required before any success claim."
        ),
    }
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 sequential response router",
        "",
        f"**{classification}**",
        "",
        "- success_claim: `False`",
        "- continuous_account_evidence: `False`",
        "- weekly_reset_screen: `True`",
        f"- daily_geometric_growth: `{daily_geo}`",
        f"- weekly_reset_nav_multiple: `{float(weekly_reset_nav_multiple):.10f}`",
        f"- closed_trades: `{trades}`",
        f"- wins / losses: `{wins} / {losses}`",
        f"- win_rate: `{win_rate}`",
        f"- maximum_interval_closed_trade_drawdown: `{maximum_interval_drawdown}`",
        "",
        "## Interval evidence",
    ]
    for record in interval_records:
        router = record["router_diagnostics"].get("router_resolution_counts", {})
        lines.append(
            "- "
            f"{record['interval']} ({record['start']}): "
            f"daily_geo={record['daily_geometric_growth']}, "
            f"trades={record['closed_trades']}, "
            f"W/L={record['wins']}/{record['losses']}, "
            f"router={router}"
        )
    lines.extend(("", "## Checks"))
    lines.extend(f"- {key}: `{value}`" for key, value in checks.items())
    lines.extend(
        (
            "",
            "The three intervals are a parallel information-value screen. "
            "They do not form one continuous account path.",
        ),
    )
    (root / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
    )
    args = parser.parse_args()
    result = aggregate(args.root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
