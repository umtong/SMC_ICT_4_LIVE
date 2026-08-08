#!/usr/bin/env python3
"""Aggregate Candidate 15 V4's exposed mechanism-development screen."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from decimal import Decimal
from functools import reduce
import json
from math import log
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
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return Decimal(default)
    return Decimal(text.split()[0])


def position_pnls(path: Path) -> list[Decimal]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        return []
    column = "realized_pnl" if "realized_pnl" in rows[0] else "pnl"
    return [decimal_value(row.get(column)) for row in rows if row.get(column)]


def aggregate(root: Path) -> dict[str, Any]:
    protocol = read_object(root / "protocol.json")
    intervals: list[dict[str, Any]] = []
    all_pnls: list[Decimal] = []
    module_counts: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    for interval, selection in protocol["selection"]["intervals"].items():
        result_dir = root / "results" / interval
        summary = read_object(result_dir / "summary.json")
        pnls = position_pnls(result_dir / "positions.csv")
        all_pnls.extend(pnls)
        module_counts.update(
            {key: int(value) for key, value in summary.get("module_counts", {}).items()},
        )
        skip_reasons.update(
            {key: int(value) for key, value in summary.get("skip_reasons", {}).items()},
        )
        intervals.append(
            {
                "interval": interval,
                "role": selection["role"],
                "start": selection["start"],
                "end_exclusive": selection["end_exclusive"],
                "daily_geometric_growth": summary.get("daily_geometric_growth"),
                "final_nav": summary.get("final_nav"),
                "net_return": summary.get("net_return"),
                "closed_trades": int(summary.get("closed_trades") or 0),
                "wins": int(summary.get("wins") or 0),
                "losses": int(summary.get("losses") or 0),
                "win_rate": summary.get("win_rate"),
                "payoff_ratio": summary.get("payoff_ratio"),
                "closed_trade_max_drawdown": summary.get("closed_trade_max_drawdown"),
                "submitted_plans": int(summary.get("submitted_plans") or 0),
                "module_counts": summary.get("module_counts", {}),
                "symbol_counts": summary.get("symbol_counts", {}),
                "engine_errors": summary.get("engine_errors", []),
                "liquidation_detected": summary.get("liquidation_detected"),
                "global_slot_overlap_count": summary.get("global_slot_overlap_count"),
                "diagnostics": summary.get("candidate15_diagnostics", {}),
            },
        )

    start_nav = decimal_value(protocol["execution_lock"]["starting_nav"])
    nav_multiples = [
        decimal_value(item["final_nav"], str(start_nav)) / start_nav
        for item in intervals
    ]
    aggregate_multiple = reduce(lambda left, right: left * right, nav_multiples, Decimal("1"))
    total_days = int(protocol["selection"]["evaluation_days"]) * len(intervals)
    daily_geo = (
        float(aggregate_multiple ** (Decimal("1") / Decimal(total_days)) - Decimal("1"))
        if total_days and aggregate_multiple > 0
        else None
    )
    wins = [pnl for pnl in all_pnls if pnl > 0]
    losses = [pnl for pnl in all_pnls if pnl < 0]
    closed_trades = len(all_pnls)
    win_rate = len(wins) / closed_trades if closed_trades else None
    payoff_ratio = (
        float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
        if wins and losses
        else (None if not wins else float("inf"))
    )

    # Weekly-reset compounded trade path. Each interval contributes its relative
    # per-trade change from the same 100k base to a single comparable screen path.
    equity = Decimal("1")
    peak = equity
    max_drawdown = Decimal("0")
    pnl_cursor = 0
    for interval in intervals:
        for _ in range(interval["closed_trades"]):
            pnl = all_pnls[pnl_cursor]
            pnl_cursor += 1
            equity *= Decimal("1") + pnl / start_nav
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)

    positive_logs = [
        log(float(multiple))
        for multiple in nav_multiples
        if multiple > 1
    ]
    positive_log_share = (
        max(positive_logs) / sum(positive_logs)
        if positive_logs and sum(positive_logs) > 0
        else None
    )
    active_intervals = sum(item["closed_trades"] > 0 for item in intervals)
    safety = all(
        not item["engine_errors"]
        and item["liquidation_detected"] is not True
        and int(item["global_slot_overlap_count"] or 0) == 0
        for item in intervals
    )
    gate = protocol["development_gate"]
    checks = {
        "all_intervals_present": len(intervals) == len(protocol["selection"]["intervals"]),
        "minimum_closed_trades": closed_trades >= int(gate["minimum_closed_trades"]),
        "minimum_active_intervals": active_intervals >= int(gate["minimum_active_intervals"]),
        "positive_costed_growth": daily_geo is not None and daily_geo > float(gate["minimum_daily_geometric_growth"]),
        "minimum_win_rate": win_rate is not None and win_rate >= float(gate["minimum_win_rate"]),
        "minimum_payoff_ratio": payoff_ratio is not None and payoff_ratio >= float(gate["minimum_payoff_ratio"]),
        "maximum_closed_trade_path_drawdown": float(max_drawdown) <= float(gate["maximum_closed_trade_path_drawdown"]),
        "growth_not_concentrated": (
            positive_log_share is not None
            and positive_log_share <= float(gate["maximum_positive_log_growth_share_from_one_interval"])
        ),
        "safety": safety,
        "only_v4_module_submitted": set(module_counts).issubset({"PERSISTENT_QH_MSS_FVG_CONTINUATION"}),
    }
    if not checks["minimum_closed_trades"] or not checks["minimum_active_intervals"]:
        classification = "CANDIDATE15_V4_INSUFFICIENT_ACTIVITY"
    elif all(checks.values()):
        classification = "CANDIDATE15_V4_DEVELOPMENT_PROMISING"
    else:
        classification = "CANDIDATE15_V4_DEVELOPMENT_REJECTED"

    top_skips = dict(skip_reasons.most_common(25))
    payload = {
        "schema": "candidate-15-v4-development-aggregate-v1",
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "classification": classification,
        "development_only": True,
        "success_claim": False,
        "continuous_account_evidence": False,
        "weekly_reset_screen": True,
        "weekly_reset_nav_multiple": float(aggregate_multiple),
        "daily_geometric_growth": daily_geo,
        "closed_trades": closed_trades,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": payoff_ratio,
        "active_intervals": active_intervals,
        "closed_trade_path_max_drawdown": float(max_drawdown),
        "maximum_positive_log_growth_share_from_one_interval": positive_log_share,
        "module_counts": dict(sorted(module_counts.items())),
        "top_skip_reasons": top_skips,
        "checks": checks,
        "intervals": intervals,
        "next_evidence_required": (
            "Only a promising exposed-development result may advance to a source freeze and newly predeclared confirmation intervals."
        ),
    }
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V4 persistent cross-market initiative",
        "",
        f"**{classification}**",
        "",
        "- development_only: `True`",
        "- success_claim: `False`",
        "- continuous_account_evidence: `False`",
        f"- weekly_reset_nav_multiple: `{float(aggregate_multiple):.10f}`",
        f"- daily_geometric_growth: `{daily_geo}`",
        f"- closed_trades: `{closed_trades}`",
        f"- wins / losses: `{len(wins)} / {len(losses)}`",
        f"- win_rate: `{win_rate}`",
        f"- payoff_ratio: `{payoff_ratio}`",
        f"- active_intervals: `{active_intervals}`",
        f"- closed_trade_path_max_drawdown: `{float(max_drawdown)}`",
        f"- maximum_positive_log_growth_share_from_one_interval: `{positive_log_share}`",
        f"- module_counts: `{dict(sorted(module_counts.items()))}`",
        "",
        "## Interval evidence",
    ]
    for item in intervals:
        diagnostics = item["diagnostics"]
        lines.append(
            f"- {item['interval']} ({item['start']}): daily_geo={item['daily_geometric_growth']}, "
            f"trades={item['closed_trades']}, W/L={item['wins']}/{item['losses']}, "
            f"initiative_activations={diagnostics.get('initiative_activations')}, "
            f"continuation_plans={diagnostics.get('continuation_plans')}"
        )
    lines.extend(("", "## Development checks"))
    lines.extend(f"- {key}: `{value}`" for key, value in checks.items())
    lines.extend(("", "## Highest-volume diagnostic skips"))
    lines.extend(f"- {key}: `{value}`" for key, value in top_skips.items())
    lines.extend(
        (
            "",
            "E01-E06 are exposed mechanism-development intervals. This result cannot support a success claim.",
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
