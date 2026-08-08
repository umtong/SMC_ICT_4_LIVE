#!/usr/bin/env python3
"""Aggregate continuous SCDAM-core FAR development evidence.

This module never simulates fills, PnL or NAV. It reconciles recorded
NautilusTrader positions with the submitted scenario and lifecycle evidence,
forms conservative economic clusters, and applies the precommitted development
gate. A pass only authorizes a separately frozen fresh-validation candidate.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from math import exp, log
from pathlib import Path
from typing import Any

UTC = timezone.utc
MINUTE_NS = 60 * 1_000_000_000
COMMON_DISPLACEMENT_BUCKET_NS = 30 * MINUTE_NS
SAFETY_KEYS = (
    "evidence_complete",
    "metric_recalculation_passed",
    "risk_budget_passed",
    "global_slot_passed",
    "partial_entry_protection_passed",
    "no_liquidation_passed",
    "engine_errors_absent",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_object(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def decimal_value(value: Any) -> Decimal:
    text = str(value).replace(",", "").strip()
    if not text or text.lower() in {"none", "null", "nan", "nat"}:
        raise InvalidOperation(f"invalid decimal {value!r}")
    return Decimal(text.split()[0])


def timestamp_ns(value: Any) -> int:
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "nat"}:
        raise ValueError(f"invalid timestamp {value!r}")
    try:
        number = Decimal(text)
        magnitude = abs(number)
        if magnitude >= Decimal("100000000000000000"):
            return int(number)
        if magnitude >= Decimal("100000000000000"):
            return int(number * Decimal("1000"))
        if magnitude >= Decimal("100000000000"):
            return int(number * Decimal("1000000"))
        if magnitude >= Decimal("1000000000"):
            return int(number * Decimal("1000000000"))
    except InvalidOperation:
        pass
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1_000_000_000)


def first_timestamp_ns(row: dict[str, str], names: tuple[str, ...]) -> int:
    errors: list[str] = []
    for name in names:
        value = row.get(name)
        if value in (None, "", "None", "nan", "NaT"):
            continue
        try:
            return timestamp_ns(value)
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f"{name}={value!r}: {exc}")
    raise ValueError("no usable timestamp: " + "; ".join(errors))


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def plan_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("plans"), list):
        return [item for item in payload["plans"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def symbol_from_instrument(value: str) -> str:
    return value.split("-PERP", 1)[0].split(".", 1)[0]


def session_family(plan: dict[str, Any]) -> str:
    details = plan.get("details") if isinstance(plan.get("details"), dict) else {}
    source = str(details.get("pool_source", "UNSPECIFIED")).upper()
    for family in (
        "ASIA",
        "LONDON_PREMARKET",
        "LONDON_CLOSE",
        "LONDON",
        "NYAM",
        "US_LATE",
    ):
        if source.startswith(family):
            return family
    return source or "UNSPECIFIED"


def lifecycle_entry_fills(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    records: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "GLOBAL_ENTRY_FILLED":
            continue
        records.append(
            {
                "scenario_id": str(event["scenario_id"]),
                "symbol": str(event["symbol"]),
                "ts_ns": int(event["ts_event"]),
            }
        )
    return records


def match_position_to_fill(
    *,
    symbol: str,
    opened_ns: int,
    fills: list[dict[str, Any]],
    used_scenarios: set[str],
) -> dict[str, Any]:
    eligible = [
        record
        for record in fills
        if record["symbol"] == symbol
        and record["scenario_id"] not in used_scenarios
    ]
    exact = [record for record in eligible if record["ts_ns"] == opened_ns]
    if len(exact) == 1:
        return exact[0]
    nearby = sorted(
        (
            (abs(record["ts_ns"] - opened_ns), record)
            for record in eligible
            if abs(record["ts_ns"] - opened_ns) <= MINUTE_NS
        ),
        key=lambda item: item[0],
    )
    if nearby and (len(nearby) == 1 or nearby[0][0] < nearby[1][0]):
        return nearby[0][1]
    raise ValueError(
        f"cannot uniquely match {symbol} position opened at {opened_ns} "
        "to GLOBAL_ENTRY_FILLED evidence"
    )


def trade_evidence(block: str, root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    plans = plan_rows(load_json(root / "submitted_plans.json"))
    by_scenario = {str(plan.get("scenario_id")): plan for plan in plans}
    fills = lifecycle_entry_fills(load_object(root / "order_lifecycle.json"))
    positions = csv_rows(root / "positions.csv")
    used_scenarios: set[str] = set()
    trades: list[dict[str, Any]] = []

    for index, position in enumerate(positions):
        try:
            symbol = symbol_from_instrument(str(position["instrument_id"]))
            opened_ns = first_timestamp_ns(
                position,
                ("ts_opened", "ts_init", "open_time", "entry_time"),
            )
            closed_ns = first_timestamp_ns(
                position,
                ("ts_closed", "ts_last", "close_time", "exit_time"),
            )
            fill = match_position_to_fill(
                symbol=symbol,
                opened_ns=opened_ns,
                fills=fills,
                used_scenarios=used_scenarios,
            )
            scenario_id = fill["scenario_id"]
            plan = by_scenario[scenario_id]
            scenario = str(plan.get("scenario"))
            module = str(plan.get("module", "SCDAM_CORE"))
            if scenario != "FAR" or module != "SCDAM_CORE":
                raise ValueError(
                    f"unauthorized submitted domain module={module} scenario={scenario}"
                )
            direction = str(plan.get("direction", position.get("entry", ""))).upper()
            if direction not in {"LONG", "SHORT"}:
                entry_side = str(position.get("entry", "")).upper()
                direction = "LONG" if entry_side == "BUY" else "SHORT" if entry_side == "SELL" else entry_side
            if direction not in {"LONG", "SHORT"}:
                raise ValueError(f"unsupported direction {direction!r}")
            nav_before = decimal_value(plan["nav_before"])
            pnl = decimal_value(position.get("realized_pnl", position.get("pnl")))
            nav_after = nav_before + pnl
            if nav_before <= 0 or nav_after <= 0:
                raise ValueError("non-positive NAV in recorded trade path")
            trade_log_growth = log(float(nav_after / nav_before))
            details = plan.get("details") if isinstance(plan.get("details"), dict) else {}
            causal_ns = int(
                details.get(
                    "sweep_ts_ns",
                    details.get("causal_start_ts_ns", plan["observed_ts_ns"]),
                )
            )
            observed_ns = int(plan["observed_ts_ns"])
            observed_dt = datetime.fromtimestamp(observed_ns / 1_000_000_000, tz=UTC)
            cluster = {
                "utc_date": observed_dt.date().isoformat(),
                "session_family": session_family(plan),
                "direction": direction,
                "common_displacement_bucket": observed_ns // COMMON_DISPLACEMENT_BUCKET_NS,
            }
            trades.append(
                {
                    "block": block,
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "direction": direction,
                    "module": module,
                    "scenario": scenario,
                    "opened_ns": opened_ns,
                    "closed_ns": closed_ns,
                    "observed_ns": observed_ns,
                    "causal_start_ns": causal_ns,
                    "session_family": cluster["session_family"],
                    "cluster_key": "|".join(str(cluster[key]) for key in (
                        "utc_date",
                        "session_family",
                        "direction",
                        "common_displacement_bucket",
                    )),
                    "nav_before": str(nav_before),
                    "realized_pnl": str(pnl),
                    "log_growth": trade_log_growth,
                }
            )
            used_scenarios.add(scenario_id)
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            errors.append(f"position[{index}] evidence mapping failed: {exc}")

    if len(used_scenarios) != len(positions):
        errors.append(
            f"mapped scenarios {len(used_scenarios)} do not equal positions {len(positions)}"
        )
    if len(fills) != len(positions):
        errors.append(
            f"GLOBAL_ENTRY_FILLED records {len(fills)} do not equal positions {len(positions)}"
        )
    return trades, errors


def cluster_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(str(trade["cluster_key"]), []).append(trade)
    clusters: list[dict[str, Any]] = []
    for key, records in sorted(grouped.items()):
        clusters.append(
            {
                "cluster_key": key,
                "block": records[0]["block"],
                "utc_date": key.split("|", 1)[0],
                "session_family": records[0]["session_family"],
                "direction": records[0]["direction"],
                "trade_count": len(records),
                "symbols": sorted({str(item["symbol"]) for item in records}),
                "scenario_ids": [str(item["scenario_id"]) for item in records],
                "log_growth": sum(float(item["log_growth"]) for item in records),
                "realized_pnl": str(sum(decimal_value(item["realized_pnl"]) for item in records)),
            }
        )
    return clusters


def aggregate(results_root: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    gate = protocol["development_gate"]
    expected_blocks = protocol["selection"]["blocks"]
    block_records: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    mapping_errors: list[str] = []

    for block, interval in expected_blocks.items():
        root = results_root / block
        summary_path = root / "summary.json"
        metrics_path = root / "metrics.json"
        audit_path = root / "audit.json"
        if not all(path.is_file() for path in (summary_path, metrics_path, audit_path)):
            mapping_errors.append(f"{block}: missing summary, metrics or audit")
            continue
        summary = load_object(summary_path)
        metrics = load_object(metrics_path)
        audit = load_object(audit_path)
        trades, errors = trade_evidence(block, root)
        mapping_errors.extend(f"{block}: {error}" for error in errors)
        all_trades.extend(trades)
        starting_nav = decimal_value(metrics["starting_nav"])
        final_nav = decimal_value(metrics["final_nav"])
        block_log = log(float(final_nav / starting_nav)) if final_nav > 0 else float("-inf")
        mapped_log = sum(float(item["log_growth"]) for item in trades)
        reconciliation_error = abs(block_log - mapped_log)
        if reconciliation_error > 1e-8:
            mapping_errors.append(
                f"{block}: mapped trade log growth differs from account log growth by "
                f"{reconciliation_error:.12g}"
            )
        block_records.append(
            {
                "block": block,
                "start": interval["start"],
                "end_exclusive": interval["end_exclusive"],
                "calendar_days": int(protocol["selection"]["evaluation_days"]),
                "starting_nav": str(starting_nav),
                "final_nav": str(final_nav),
                "nav_ratio": float(final_nav / starting_nav),
                "log_growth": block_log,
                "daily_geometric_growth": float(metrics["daily_geometric_growth"]),
                "closed_trades": int(metrics["closed_trades"]),
                "wins": int(metrics["wins"]),
                "losses": int(metrics["losses"]),
                "scenario_max_hold_exit_count": int(
                    metrics.get("scenario_max_hold_exit_count", 0)
                ),
                "resolution_tail_unresolved_count": int(
                    metrics.get("resolution_tail_unresolved_count", 0)
                ),
                "safety_audit_passed": all(audit.get(key) is True for key in SAFETY_KEYS),
                "trade_mapping_complete": not errors and len(trades) == int(metrics["closed_trades"]),
                "nav_reconciliation_error": reconciliation_error,
                "audit_classification": audit.get("classification"),
            }
        )

    clusters = cluster_trades(all_trades)
    total_days = sum(record["calendar_days"] for record in block_records)
    total_log = sum(float(record["log_growth"]) for record in block_records)
    pooled_daily_growth = exp(total_log / total_days) - 1.0 if total_days else -1.0
    positive_logs = [float(item["log_growth"]) for item in clusters if float(item["log_growth"]) > 0.0]
    total_positive_log = sum(positive_logs)
    max_positive_share = (
        max(positive_logs) / total_positive_log if positive_logs and total_positive_log > 0.0 else 1.0
    )
    minimum_leave_one_out = (
        min(total_log - float(item["log_growth"]) for item in clusters)
        if clusters
        else float("-inf")
    )

    direction_records: dict[str, dict[str, Any]] = {}
    for direction in gate["claimed_directions"]:
        selected = [item for item in clusters if item["direction"] == direction]
        direction_records[direction] = {
            "clusters": len(selected),
            "trades": sum(int(item["trade_count"]) for item in selected),
            "log_growth": sum(float(item["log_growth"]) for item in selected),
        }

    checks = {
        "all_blocks_complete": len(block_records) == int(gate["minimum_blocks"]),
        "all_blocks_positive": bool(block_records)
        and all(float(record["log_growth"]) > 0.0 for record in block_records),
        "minimum_economic_clusters": len(clusters) >= int(gate["minimum_economic_clusters"]),
        "pooled_daily_geometric_growth": pooled_daily_growth
        >= float(gate["minimum_pooled_daily_geometric_growth"]),
        "positive_leave_one_cluster_out_growth": minimum_leave_one_out > 0.0,
        "growth_concentration": max_positive_share
        <= float(gate["maximum_positive_log_growth_share_from_one_cluster"]),
        "claimed_direction_cluster_coverage": all(
            record["clusters"] >= int(gate["minimum_clusters_per_claimed_direction"])
            for record in direction_records.values()
        ),
        "claimed_direction_positive_growth": all(
            float(record["log_growth"]) > 0.0
            for record in direction_records.values()
        ),
        "all_safety_audits": bool(block_records)
        and all(record["safety_audit_passed"] for record in block_records),
        "no_resolution_tail_forced_exit": bool(block_records)
        and all(record["resolution_tail_unresolved_count"] == 0 for record in block_records),
        "trade_mapping_and_nav_reconciliation": not mapping_errors
        and all(record["trade_mapping_complete"] for record in block_records),
    }
    gate_passed = all(checks.values())
    classification = (
        "DEVELOPMENT_GATE_PASSED_FRESH_VALIDATION_AUTHORIZED"
        if gate_passed
        else "DEVELOPMENT_GATE_FAILED"
    )
    return {
        "schema": "candidate-11-core-far-continuous-aggregate-v1",
        "candidate": protocol["candidate"],
        "protocol": protocol["schema"],
        "research_stage": "DEVELOPMENT",
        "validation_eligible": False,
        "success_claim": False,
        "fresh_validation_authorized": gate_passed,
        "classification": classification,
        "gate_passed": gate_passed,
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "mapping_errors": mapping_errors,
        "calendar_days": total_days,
        "closed_trades": len(all_trades),
        "economic_clusters": len(clusters),
        "pooled_log_growth": total_log,
        "pooled_nav_multiple": exp(total_log),
        "pooled_daily_geometric_growth": pooled_daily_growth,
        "minimum_leave_one_cluster_out_log_growth": minimum_leave_one_out,
        "maximum_positive_log_growth_share_from_one_cluster": max_positive_share,
        "direction_evidence": direction_records,
        "blocks": block_records,
        "trades": all_trades,
        "clusters": clusters,
        "decision": (
            protocol["decision"]["gate_pass"]
            if gate_passed
            else protocol["decision"]["gate_fail"]
        ),
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate 11 core FAR continuous development result",
        "",
        f"**{result['classification']}**",
        "",
        "This is development evidence. It cannot establish strategy success.",
        "",
        "## Aggregate",
        "",
        f"- calendar days: `{result['calendar_days']}`",
        f"- closed trades: `{result['closed_trades']}`",
        f"- economic clusters: `{result['economic_clusters']}`",
        f"- pooled NAV multiple: `{result['pooled_nav_multiple']:.10f}`",
        f"- pooled daily geometric growth: `{result['pooled_daily_geometric_growth']:.10%}`",
        f"- minimum leave-one-cluster-out log growth: `{result['minimum_leave_one_cluster_out_log_growth']:.10f}`",
        f"- maximum positive cluster share: `{result['maximum_positive_log_growth_share_from_one_cluster']:.10%}`",
        "",
        "## Blocks",
        "",
    ]
    for block in result["blocks"]:
        lines.append(
            f"- {block['block']} {block['start']} to {block['end_exclusive']}: "
            f"daily_geo={block['daily_geometric_growth']:.6%}, "
            f"trades={block['closed_trades']}, W/L={block['wins']}/{block['losses']}, "
            f"safety={block['safety_audit_passed']}"
        )
    lines.extend(("", "## Direction evidence", ""))
    for direction, record in result["direction_evidence"].items():
        lines.append(
            f"- {direction}: clusters={record['clusters']}, trades={record['trades']}, "
            f"log_growth={record['log_growth']:.10f}"
        )
    lines.extend(("", "## Gate checks", ""))
    lines.extend(f"- {name}: `{passed}`" for name, passed in result["checks"].items())
    if result["mapping_errors"]:
        lines.extend(("", "## Evidence mapping errors", ""))
        lines.extend(f"- {error}" for error in result["mapping_errors"])
    lines.extend(("", "## Decision", "", result["decision"], ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = load_object(args.protocol)
    result = aggregate(args.results, protocol)
    write_json(args.output, result)
    args.output.with_name("RESULT.md").write_text(
        render_markdown(result), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
