#!/usr/bin/env python3
"""Aggregate Candidate 15 V2's predeclared screening intervals."""
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


def summarize_group(
    records: list[dict[str, Any]],
    *,
    start_nav: Decimal,
    evaluation_days: int,
) -> dict[str, Any]:
    multiples = [
        decimal_value(record["final_nav"], str(start_nav)) / start_nav
        for record in records
    ]
    nav_multiple = reduce(lambda x, y: x * y, multiples, Decimal("1"))
    days = evaluation_days * len(records)
    daily_geo = (
        float(nav_multiple ** (Decimal("1") / Decimal(days)) - Decimal("1"))
        if days > 0 and nav_multiple > 0
        else None
    )
    trades = sum(record["closed_trades"] for record in records)
    wins = sum(record["wins"] for record in records)
    losses = sum(record["losses"] for record in records)
    drawdowns = [
        float(record["closed_trade_max_drawdown"])
        for record in records
        if record["closed_trade_max_drawdown"] is not None
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
            not record["engine_errors"]
            and record["liquidation_detected"] is not True
            for record in records
        ),
    }


def aggregate(root: Path) -> dict[str, Any]:
    protocol = read_object(root / "protocol.json")
    records: list[dict[str, Any]] = []
    for interval, selection in protocol["selection"]["intervals"].items():
        summary_path = root / "results" / interval / "summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(summary_path)
        summary = read_object(summary_path)
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

    development = [record for record in records if record["role"].startswith("contaminated-")]
    confirmation = [record for record in records if record["role"] == "predeclared-v2-confirmation"]
    start_nav = decimal_value(protocol["execution_lock"]["starting_nav"])
    evaluation_days = int(protocol["selection"]["evaluation_days"])
    development_stats = summarize_group(
        development,
        start_nav=start_nav,
        evaluation_days=evaluation_days,
    )
    confirmation_stats = summarize_group(
        confirmation,
        start_nav=start_nav,
        evaluation_days=evaluation_days,
    )

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
        classification = "CANDIDATE15_V2_INSUFFICIENT_ACTIVITY"
    elif all(
        checks[key]
        for key in (
            "positive_costed_growth",
            "win_rate_at_least_0_65",
            "maximum_interval_drawdown_at_most_0_20",
            "safety",
        )
    ):
        classification = "CANDIDATE15_V2_PROMISING_SCREEN"
    else:
        classification = "CANDIDATE15_V2_SCREEN_REJECTED"

    payload = {
        "schema": "candidate-15-v2-screen-aggregate-v1",
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "classification": classification,
        "success_claim": False,
        "continuous_account_evidence": False,
        "weekly_reset_screen": True,
        "development_replay": development_stats,
        "confirmation": confirmation_stats,
        "intervals": records,
        "checks": checks,
        "next_evidence_required": (
            "A frozen continuous-account interval is required before any success claim."
        ),
    }
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V2 causal decision lease",
        "",
        f"**{classification}**",
        "",
        "- success_claim: `False`",
        "- continuous_account_evidence: `False`",
        "- weekly_reset_screen: `True`",
        "",
        "## Predeclared V2 confirmation",
        f"- daily_geometric_growth: `{confirmation_stats['daily_geometric_growth']}`",
        f"- weekly_reset_nav_multiple: `{confirmation_stats['weekly_reset_nav_multiple']:.10f}`",
        f"- closed_trades: `{confirmation_stats['closed_trades']}`",
        f"- wins / losses: `{confirmation_stats['wins']} / {confirmation_stats['losses']}`",
        f"- win_rate: `{confirmation_stats['win_rate']}`",
        f"- maximum_interval_closed_trade_drawdown: `{confirmation_stats['maximum_interval_closed_trade_drawdown']}`",
        "",
        "## Contaminated mechanism replay",
        f"- closed_trades: `{development_stats['closed_trades']}`",
        f"- wins / losses: `{development_stats['wins']} / {development_stats['losses']}`",
        f"- daily_geometric_growth: `{development_stats['daily_geometric_growth']}`",
        "",
        "## Interval evidence",
    ]
    for record in records:
        router = record["router_diagnostics"].get("router_resolution_counts", {})
        lines.append(
            "- "
            f"{record['interval']} [{record['role']}] ({record['start']}): "
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
            "Classification uses only U1-U5. D1/H1/S1 are contaminated mechanism replays. "
            "The confirmation weeks do not form one continuous account path.",
        ),
    )
    (root / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    result = aggregate(args.root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
