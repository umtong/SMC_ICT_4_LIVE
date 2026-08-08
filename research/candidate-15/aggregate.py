#!/usr/bin/env python3
"""Aggregate Candidate 15 V5's exposed mechanism-development screen."""
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def number(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return Decimal(default)
    return Decimal(text.split()[0])


def pnls(path: Path) -> list[Decimal]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        return []
    key = "realized_pnl" if "realized_pnl" in rows[0] else "pnl"
    return [number(row[key]) for row in rows if row.get(key)]


def aggregate(root: Path) -> dict[str, Any]:
    protocol = read_object(root / "protocol.json")
    start_nav = number(protocol["execution_lock"]["starting_nav"])
    records: list[dict[str, Any]] = []
    trade_pnls: list[Decimal] = []
    modules: Counter[str] = Counter()
    skips: Counter[str] = Counter()
    activations = 0
    response_rejections = 0
    for interval, selection in protocol["selection"]["intervals"].items():
        result_dir = root / "results" / interval
        summary = read_object(result_dir / "summary.json")
        interval_pnls = pnls(result_dir / "positions.csv")
        trade_pnls.extend(interval_pnls)
        modules.update({key: int(value) for key, value in summary.get("module_counts", {}).items()})
        skips.update({key: int(value) for key, value in summary.get("skip_reasons", {}).items()})
        diagnostics = summary.get("candidate15_diagnostics", {})
        event_types = diagnostics.get("event_type_counts", {})
        activations += int(event_types.get("QHI_INITIATIVE_ACTIVATED", 0))
        response_rejections += int(event_types.get("QHI_RESPONSE_REJECTED", 0))
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
                "submitted_plans": int(summary.get("submitted_plans") or 0),
                "module_counts": summary.get("module_counts", {}),
                "symbol_counts": summary.get("symbol_counts", {}),
                "engine_errors": summary.get("engine_errors", []),
                "liquidation_detected": summary.get("liquidation_detected"),
                "global_slot_overlap_count": summary.get("global_slot_overlap_count"),
                "diagnostics": diagnostics,
            },
        )

    multiples = [number(item["final_nav"], str(start_nav)) / start_nav for item in records]
    nav_multiple = reduce(lambda left, right: left * right, multiples, Decimal("1"))
    days = int(protocol["selection"]["evaluation_days"]) * len(records)
    daily_geo = (
        float(nav_multiple ** (Decimal("1") / Decimal(days)) - Decimal("1"))
        if days and nav_multiple > 0
        else None
    )
    wins = [value for value in trade_pnls if value > 0]
    losses = [value for value in trade_pnls if value < 0]
    trade_count = len(trade_pnls)
    win_rate = len(wins) / trade_count if trade_count else None
    payoff = (
        float((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)))
        if wins and losses
        else (float("inf") if wins else None)
    )

    equity = Decimal("1")
    peak = equity
    drawdown = Decimal("0")
    cursor = 0
    for record in records:
        for _ in range(record["closed_trades"]):
            equity *= Decimal("1") + trade_pnls[cursor] / start_nav
            cursor += 1
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak - equity) / peak)
    positive_logs = [log(float(value)) for value in multiples if value > 1]
    concentration = (
        max(positive_logs) / sum(positive_logs)
        if positive_logs and sum(positive_logs) > 0
        else None
    )
    active_intervals = sum(record["closed_trades"] > 0 for record in records)
    safety = all(
        not record["engine_errors"]
        and record["liquidation_detected"] is not True
        and int(record["global_slot_overlap_count"] or 0) == 0
        for record in records
    )
    gate = protocol["development_gate"]
    checks = {
        "all_intervals_present": len(records) == len(protocol["selection"]["intervals"]),
        "minimum_closed_trades": trade_count >= int(gate["minimum_closed_trades"]),
        "minimum_active_intervals": active_intervals >= int(gate["minimum_active_intervals"]),
        "positive_costed_growth": daily_geo is not None and daily_geo > float(gate["minimum_daily_geometric_growth"]),
        "minimum_win_rate": win_rate is not None and win_rate >= float(gate["minimum_win_rate"]),
        "minimum_payoff_ratio": payoff is not None and payoff >= float(gate["minimum_payoff_ratio"]),
        "maximum_closed_trade_path_drawdown": float(drawdown) <= float(gate["maximum_closed_trade_path_drawdown"]),
        "growth_not_concentrated": concentration is not None and concentration <= float(gate["maximum_positive_log_growth_share_from_one_interval"]),
        "safety": safety,
        "only_response_continuation_submitted": set(modules).issubset({"PERSISTENT_QH_MSS_FVG_CONTINUATION"}),
    }
    if not checks["minimum_closed_trades"] or not checks["minimum_active_intervals"]:
        classification = "CANDIDATE15_V5_INSUFFICIENT_ACTIVITY"
    elif all(checks.values()):
        classification = "CANDIDATE15_V5_DEVELOPMENT_PROMISING"
    else:
        classification = "CANDIDATE15_V5_DEVELOPMENT_REJECTED"

    payload = {
        "schema": "candidate-15-v5-development-aggregate-v1",
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "classification": classification,
        "development_only": True,
        "success_claim": False,
        "continuous_account_evidence": False,
        "weekly_reset_nav_multiple": float(nav_multiple),
        "daily_geometric_growth": daily_geo,
        "closed_trades": trade_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "payoff_ratio": payoff,
        "active_intervals": active_intervals,
        "closed_trade_path_max_drawdown": float(drawdown),
        "maximum_positive_log_growth_share_from_one_interval": concentration,
        "initiative_activations": activations,
        "response_rejections": response_rejections,
        "module_counts": dict(sorted(modules.items())),
        "top_skip_reasons": dict(skips.most_common(25)),
        "checks": checks,
        "intervals": records,
        "next_evidence_required": "A promising exposed result permits only a frozen newly predeclared confirmation screen.",
    }
    write_object(root / "aggregate.json", payload)

    lines = [
        "# Candidate 15 V5 timeframe-consistent response initiative",
        "",
        f"**{classification}**",
        "",
        "- development_only: `True`",
        "- success_claim: `False`",
        f"- weekly_reset_nav_multiple: `{float(nav_multiple):.10f}`",
        f"- daily_geometric_growth: `{daily_geo}`",
        f"- closed_trades: `{trade_count}`",
        f"- wins / losses: `{len(wins)} / {len(losses)}`",
        f"- win_rate: `{win_rate}`",
        f"- payoff_ratio: `{payoff}`",
        f"- active_intervals: `{active_intervals}`",
        f"- closed_trade_path_max_drawdown: `{float(drawdown)}`",
        f"- initiative_activations: `{activations}`",
        f"- response_rejections: `{response_rejections}`",
        "",
        "## Interval evidence",
    ]
    for record in records:
        event_types = record["diagnostics"].get("event_type_counts", {})
        lines.append(
            f"- {record['interval']} ({record['start']}): daily_geo={record['daily_geometric_growth']}, "
            f"trades={record['closed_trades']}, W/L={record['wins']}/{record['losses']}, "
            f"activations={event_types.get('QHI_INITIATIVE_ACTIVATED', 0)}, "
            f"response_rejections={event_types.get('QHI_RESPONSE_REJECTED', 0)}"
        )
    lines.extend(("", "## Development checks"))
    lines.extend(f"- {key}: `{value}`" for key, value in checks.items())
    lines.extend(("", "## Highest-volume diagnostic skips"))
    lines.extend(f"- {key}: `{value}`" for key, value in skips.most_common(25))
    lines.extend(("", "E01-E06 are exposed controlled-development intervals and cannot support a success claim."))
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
