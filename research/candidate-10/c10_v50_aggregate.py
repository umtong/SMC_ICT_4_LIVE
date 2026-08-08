#!/usr/bin/env python3
"""Aggregate v50 continuous-panel evidence without selecting periods by PnL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def row(path: Path, metrics: dict[str, Any]) -> dict[str, Any]:
    nav = finite(first(
        metrics,
        "impact_adjusted_ending_nav",
        "impact_adjusted_final_nav",
        "ending_nav",
        "final_nav",
    ))
    growth = finite(first(
        metrics,
        "impact_adjusted_geometric_daily_growth",
        "impact_adjusted_daily_geometric_growth",
        "daily_geometric_growth",
        "geometric_daily_growth",
    ))
    drawdown = finite(first(
        metrics,
        "impact_adjusted_intraday_max_drawdown",
        "impact_adjusted_max_drawdown",
        "intraday_max_drawdown",
        "max_drawdown",
    ))
    trades = int(first(metrics, "closed_trades", "trades") or 0)
    wins = int(metrics.get("wins") or 0)
    losses = int(metrics.get("losses") or 0)
    win_rate = finite(metrics.get("win_rate"))
    if win_rate is None:
        win_rate = wins / trades if trades else 0.0
    payoff = finite(first(metrics, "payoff_ratio", "impact_adjusted_payoff_ratio"))
    errors = list(metrics.get("errors", []) or [])
    target_pass = bool(
        trades >= 8
        and win_rate >= 0.90
        and payoff is not None
        and payoff >= 1.20
        and growth is not None
        and growth >= 0.01
        and (drawdown is None or drawdown <= 0.20)
        and not errors
        and not bool(metrics.get("liquidation_detected"))
        and int(metrics.get("global_overlap_count") or 0) == 0
    )
    return {
        "path": str(path),
        "candidate": metrics.get("candidate_generation"),
        "variant": metrics.get("variant"),
        "evidence_class": metrics.get("v50_evidence_class"),
        "start": metrics.get("period_start", metrics.get("evaluation_start")),
        "end_exclusive": metrics.get(
            "period_end_exclusive",
            metrics.get("evaluation_end_exclusive"),
        ),
        "evaluation_days": int(metrics.get("evaluation_days") or 0),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "nav": nav,
        "daily_geometric_growth": growth,
        "max_drawdown": drawdown,
        "errors": len(errors),
        "liquidation": bool(metrics.get("liquidation_detected")),
        "global_overlap_count": int(metrics.get("global_overlap_count") or 0),
        "target_pass": target_pass,
        "accepted_rank_counts": metrics.get("v50_accepted_event_rank_counts", {}),
        "rejection_reasons": metrics.get("v50_rejection_reasons", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for path in sorted(args.root.rglob("metrics.json")):
        try:
            metrics = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metrics.get("candidate_generation") != (
            "candidate-10-v50-independent-external-event-leader"
        ):
            continue
        rows.append(row(path, metrics))

    event_rows = [
        item for item in rows
        if item.get("variant") == "independent-external-far-event-leader"
    ]
    baseline_rows = [
        item for item in rows
        if item.get("variant") == "independent-external-far-baseline"
    ]
    positive_event = [
        item for item in event_rows
        if item.get("nav") is not None and float(item["nav"]) > 100000.0
    ]
    result = {
        "schema": "candidate-10-v50-panel-v1",
        "candidate": "candidate-10-v50-independent-external-event-leader",
        "rows": rows,
        "baseline_rows": baseline_rows,
        "event_leader_rows": event_rows,
        "positive_event_leader_rows": positive_event,
        "target_passing_rows": [item for item in rows if item["target_pass"]],
        "event_leader_total_trades": sum(item["trades"] for item in event_rows),
        "event_leader_total_wins": sum(item["wins"] for item in event_rows),
        "event_leader_total_losses": sum(item["losses"] for item in event_rows),
        "event_leader_mean_daily_growth": (
            mean(
                item["daily_geometric_growth"]
                for item in event_rows
                if item["daily_geometric_growth"] is not None
            )
            if any(item["daily_geometric_growth"] is not None for item in event_rows)
            else None
        ),
        "success_claim": False,
    }
    (args.output / "v50_panel_metrics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Candidate 10 v50 continuous panel",
        "",
        "No success claim is made by the aggregator.",
        "",
        "| period | variant | trades | W/L | win rate | payoff | cost NAV | daily geom | max DD | target |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in sorted(rows, key=lambda value: (str(value["start"]), str(value["variant"]))):
        def fmt(value: Any, digits: int = 4) -> str:
            return "NA" if value is None else f"{float(value):.{digits}f}"
        lines.append(
            "| {start}..{end} | {variant} | {trades} | {wins}/{losses} | "
            "{wr} | {payoff} | {nav} | {growth} | {dd} | {target} |".format(
                start=item["start"],
                end=item["end_exclusive"],
                variant=item["variant"],
                trades=item["trades"],
                wins=item["wins"],
                losses=item["losses"],
                wr=fmt(item["win_rate"]),
                payoff=fmt(item["payoff_ratio"]),
                nav=fmt(item["nav"], 2),
                growth=fmt(item["daily_geometric_growth"], 6),
                dd=fmt(item["max_drawdown"], 6),
                target=item["target_pass"],
            ),
        )
    (args.output / "V50_PANEL_RESULT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
