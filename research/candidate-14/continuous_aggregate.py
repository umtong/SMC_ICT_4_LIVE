#!/usr/bin/env python3
"""Aggregate one frozen contiguous Candidate 14 Nautilus account path.

This evaluator deliberately rejects stitched weekly NAV evidence. One
NautilusTrader engine must own the complete interval from the first evaluation
bar through the final flatten. Calendar-week records are diagnostics sliced
from that single path; they are never independent accounts.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from math import log, sqrt
from pathlib import Path
from typing import Any, Iterable


SAFETY_KEYS = (
    "evidence_complete",
    "metric_recalculation_passed",
    "risk_budget_passed",
    "global_slot_passed",
    "partial_entry_protection_passed",
    "no_liquidation_passed",
    "engine_errors_absent",
)


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    opened: datetime
    closed: datetime
    pnl: Decimal
    instrument_id: str


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def dec(value: Any) -> Decimal:
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"none", "null", "nan", "nat"}:
        raise InvalidOperation(text)
    return Decimal(text.split()[0])


def parse_timestamp(value: Any) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def closed_trades(path: Path) -> list[ClosedTrade]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result: list[ClosedTrade] = []
    for row in rows:
        pnl_key = next(
            (key for key in ("realized_pnl", "pnl") if key in row),
            None,
        )
        if pnl_key is None:
            continue
        try:
            result.append(
                ClosedTrade(
                    opened=parse_timestamp(row["ts_opened"]),
                    closed=parse_timestamp(row["ts_closed"]),
                    pnl=dec(row[pnl_key]),
                    instrument_id=str(row.get("instrument_id", "")),
                )
            )
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
    result.sort(key=lambda item: (item.closed, item.opened, item.instrument_id))
    return result


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    proportion = wins / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (proportion + z2 / (2.0 * total)) / denominator
    half = z * sqrt(
        proportion * (1.0 - proportion) / total + z2 / (4.0 * total * total)
    ) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def realized_drawdown(starting_nav: Decimal, trades: Iterable[ClosedTrade]) -> float:
    equity = starting_nav
    peak = starting_nav
    maximum = Decimal("0")
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        if peak > 0:
            maximum = max(maximum, (peak - equity) / peak)
    return float(maximum)


def consecutive_empty_weeks(weekly: list[dict[str, Any]]) -> int:
    current = 0
    maximum = 0
    for record in weekly:
        if int(record["closed_trades"]) == 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def weekly_path(
    *,
    starting_nav: Decimal,
    start: date,
    end_exclusive: date,
    trades: list[ClosedTrade],
) -> list[dict[str, Any]]:
    days = (end_exclusive - start).days
    if days <= 0 or days % 7:
        raise ValueError("continuous evaluation length must be a positive whole number of weeks")
    count = days // 7
    buckets: list[list[ClosedTrade]] = [[] for _ in range(count)]
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(end_exclusive, datetime.min.time(), tzinfo=UTC)

    for trade in trades:
        # Realized PnL belongs to the close week. A mandatory final flatten can
        # be stamped exactly at end_exclusive, so clamp it to the final bucket.
        closed = min(max(trade.closed, start_dt), end_dt)
        index = int((closed - start_dt).total_seconds() // (7 * 86_400))
        index = min(max(index, 0), count - 1)
        buckets[index].append(trade)

    records: list[dict[str, Any]] = []
    nav = starting_nav
    for index, bucket in enumerate(buckets):
        week_start = start + timedelta(days=7 * index)
        week_end = week_start + timedelta(days=7)
        nav_before = nav
        pnl = sum((trade.pnl for trade in bucket), Decimal("0"))
        nav += pnl
        ratio = nav / nav_before if nav_before > 0 else Decimal("0")
        log_growth = log(float(ratio)) if ratio > 0 else float("-inf")
        wins = sum(trade.pnl > 0 for trade in bucket)
        losses = sum(trade.pnl < 0 for trade in bucket)
        records.append(
            {
                "week_index": index + 1,
                "start": week_start.isoformat(),
                "end_exclusive": week_end.isoformat(),
                "starting_realized_nav": str(nav_before),
                "ending_realized_nav": str(nav),
                "realized_pnl": str(pnl),
                "nav_ratio": float(ratio),
                "log_growth": log_growth,
                "closed_trades": len(bucket),
                "wins": wins,
                "losses": losses,
                "win_rate": wins / len(bucket) if bucket else 0.0,
                "symbols": sorted({trade.instrument_id for trade in bucket if trade.instrument_id}),
            }
        )
    return records


def maximum_daily_trade_share(trades: list[ClosedTrade]) -> tuple[int, float]:
    counts: dict[str, int] = {}
    for trade in trades:
        key = trade.opened.date().isoformat()
        counts[key] = counts.get(key, 0) + 1
    maximum = max(counts.values(), default=0)
    return maximum, (maximum / len(trades) if trades else 0.0)


def payoff_ratio(trades: list[ClosedTrade]) -> tuple[float | None, float]:
    positive = [trade.pnl for trade in trades if trade.pnl > 0]
    negative = [trade.pnl for trade in trades if trade.pnl < 0]
    if positive and negative:
        value = float(
            (sum(positive) / len(positive))
            / abs(sum(negative) / len(negative))
        )
        return value, value
    if positive:
        return None, float("inf")
    return 0.0, 0.0


def evaluate(result_dir: Path, protocol_path: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol.get("validation_mode") != "frozen_holdout":
        raise ValueError("continuous evidence requires validation_mode=frozen_holdout")
    holdouts = protocol["selection"]["holdouts"]
    if len(holdouts) != 1:
        raise ValueError("continuous evidence requires exactly one holdout and one Nautilus engine")
    interval, selection = next(iter(holdouts.items()))
    start = date.fromisoformat(selection["start"])
    end_exclusive = date.fromisoformat(selection["end_exclusive"])
    observed_days = (end_exclusive - start).days
    declared_days = int(protocol["selection"]["evaluation_days"])
    if observed_days != declared_days:
        raise ValueError(
            f"declared evaluation_days={declared_days} but interval has {observed_days} days"
        )

    metrics = load_object(result_dir / "metrics.json")
    audit = load_object(result_dir / "audit.json")
    run = load_object(result_dir / "run.json")
    effective = load_object(result_dir / "effective_config.json")
    positions = closed_trades(result_dir / "positions.csv")

    candidate = str(protocol["candidate"])
    schema = str(protocol["schema"])
    provenance = (
        metrics.get("candidate") == candidate
        and metrics.get("candidate14_protocol") == schema
        and metrics.get("validation_mode") == "frozen_holdout"
        and run.get("candidate") == candidate
        and run.get("candidate14_protocol") == schema
        and run.get("validation_mode") == "frozen_holdout"
        and effective.get("candidate") == candidate
        and effective.get("candidate14_protocol", {}).get("schema") == schema
        and effective.get("candidate14_protocol", {}).get("interval") == interval
    )
    safety = all(audit.get(key) is True for key in SAFETY_KEYS)

    starting_nav = dec(metrics["starting_nav"])
    final_nav = dec(metrics["final_nav"])
    ratio = final_nav / starting_nav if starting_nav > 0 else Decimal("0")
    daily_growth = (
        float(ratio ** (Decimal(1) / Decimal(observed_days)) - Decimal(1))
        if ratio > 0
        else -1.0
    )
    closed_count = len(positions)
    wins = sum(trade.pnl > 0 for trade in positions)
    losses = sum(trade.pnl < 0 for trade in positions)
    win_rate = wins / closed_count if closed_count else 0.0
    wilson_low, wilson_high = wilson_interval(wins, closed_count)
    payoff, payoff_for_gate = payoff_ratio(positions)
    drawdown = realized_drawdown(starting_nav, positions)
    weekly = weekly_path(
        starting_nav=starting_nav,
        start=start,
        end_exclusive=end_exclusive,
        trades=positions,
    )
    active_weeks = sum(int(record["closed_trades"]) > 0 for record in weekly)
    empty_streak = consecutive_empty_weeks(weekly)
    positive_logs = [
        max(0.0, float(record["log_growth"]))
        for record in weekly
        if record["log_growth"] != float("-inf")
    ]
    positive_log_total = sum(positive_logs)
    weekly_concentration = (
        max(positive_logs, default=0.0) / positive_log_total
        if positive_log_total > 0.0
        else 1.0
    )
    max_daily_trades, max_daily_share = maximum_daily_trade_share(positions)

    gate = protocol["aggregate_gate"]
    checks = {
        "single_continuous_nautilus_account": True,
        "interval_complete": observed_days == int(gate["observed_calendar_days"]),
        "provenance": provenance,
        "all_safety_audits": safety,
        "daily_geometric_growth": daily_growth >= float(gate["minimum_daily_geometric_growth"]),
        "closed_trades": closed_count >= int(gate["minimum_closed_trades"]),
        "active_calendar_weeks": active_weeks >= int(gate["minimum_active_calendar_weeks"]),
        "win_rate": win_rate >= float(gate["minimum_win_rate"]),
        "win_rate_evidence": wilson_low >= float(gate["minimum_wilson_lower_win_rate"]),
        "payoff_ratio": payoff_for_gate >= float(gate["minimum_payoff_ratio"]),
        "continuous_realized_drawdown": drawdown <= float(gate["maximum_trade_path_drawdown"]),
        "weekly_growth_concentration": (
            weekly_concentration
            <= float(gate["maximum_positive_log_growth_share_from_one_week"])
        ),
        "empty_week_streak": empty_streak <= int(gate["maximum_consecutive_empty_weeks"]),
    }
    gate_passed = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    classification = (
        "CANDIDATE14_CONTIGUOUS_HOLDOUT_PASSED"
        if gate_passed
        else "CANDIDATE14_CONTIGUOUS_HOLDOUT_FAILED"
    )

    return {
        "schema": "candidate-14-contiguous-aggregate-v1",
        "candidate": candidate,
        "candidate14_protocol": schema,
        "validation_mode": "frozen_holdout",
        "classification": classification,
        "gate_passed": gate_passed,
        "success_claim": gate_passed,
        "continuous_account_evidence": True,
        "weekly_reset_aggregation": False,
        "interval": interval,
        "evaluation_start": start.isoformat(),
        "evaluation_end_exclusive": end_exclusive.isoformat(),
        "observed_calendar_days": observed_days,
        "starting_nav": str(starting_nav),
        "final_nav": str(final_nav),
        "nav_multiple": float(ratio),
        "daily_geometric_growth": daily_growth,
        "closed_trades": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "win_rate_wilson_95_low": wilson_low,
        "win_rate_wilson_95_high": wilson_high,
        "payoff_ratio": payoff,
        "continuous_realized_max_drawdown": drawdown,
        "calendar_weeks": len(weekly),
        "active_calendar_weeks": active_weeks,
        "maximum_consecutive_empty_weeks": empty_streak,
        "maximum_positive_week_log_growth_share": weekly_concentration,
        "maximum_trades_in_one_utc_day": max_daily_trades,
        "maximum_single_day_trade_share": max_daily_share,
        "scenario_counts": metrics.get("scenario_counts", {}),
        "module_counts": metrics.get("module_counts", {}),
        "symbol_counts": metrics.get("symbol_counts", {}),
        "leadership_rejection_counts": metrics.get("leadership_rejection_counts", {}),
        "checks": checks,
        "failed_checks": failed,
        "gate": gate,
        "weeks": weekly,
    }


def write_report(output: Path, result: dict[str, Any]) -> None:
    write_json(output, result)
    lines = [
        "# Candidate 14 contiguous holdout result",
        "",
        f"**{result['classification']}**",
        "",
        f"- continuous_account_evidence: `{result['continuous_account_evidence']}`",
        f"- weekly_reset_aggregation: `{result['weekly_reset_aggregation']}`",
        f"- gate_passed: `{result['gate_passed']}`",
        f"- success_claim: `{result['success_claim']}`",
        f"- evaluation: `{result['evaluation_start']}` through `{result['evaluation_end_exclusive']}`",
        f"- observed_calendar_days: `{result['observed_calendar_days']}`",
        f"- nav_multiple: `{result['nav_multiple']:.10f}`",
        f"- daily_geometric_growth: `{result['daily_geometric_growth']:.10f}`",
        f"- closed_trades: `{result['closed_trades']}`",
        f"- wins / losses: `{result['wins']} / {result['losses']}`",
        f"- win_rate: `{result['win_rate']:.6f}`",
        f"- win_rate_wilson_95_low: `{result['win_rate_wilson_95_low']:.6f}`",
        f"- payoff_ratio: `{result['payoff_ratio']}`",
        f"- continuous_realized_max_drawdown: `{result['continuous_realized_max_drawdown']:.10f}`",
        f"- active_calendar_weeks: `{result['active_calendar_weeks']} / {result['calendar_weeks']}`",
        f"- maximum_consecutive_empty_weeks: `{result['maximum_consecutive_empty_weeks']}`",
        f"- maximum_positive_week_log_growth_share: `{result['maximum_positive_week_log_growth_share']:.10f}`",
        f"- maximum_single_day_trade_share: `{result['maximum_single_day_trade_share']:.10f}`",
        "",
        "## Gate checks",
    ]
    lines.extend(f"- {name}: `{passed}`" for name, passed in result["checks"].items())
    lines.extend(("", "## Calendar-week diagnostics"))
    for record in result["weeks"]:
        lines.append(
            f"- week {record['week_index']} ({record['start']}): "
            f"trades={record['closed_trades']}, "
            f"W/L={record['wins']}/{record['losses']}, "
            f"realized_pnl={record['realized_pnl']}, "
            f"nav={record['ending_realized_nav']}"
        )
    output.with_name("RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.result_dir.resolve(), args.protocol.resolve())
    write_report(args.output.resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
