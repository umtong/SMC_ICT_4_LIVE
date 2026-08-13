#!/usr/bin/env python3
"""Diagnose whether a candidate fails at entry, geometry, or management.

The script reads preserved trade windows only.  It never changes strategy rules
or feeds future bars into a decision.  Post-trade MFE/MAE are diagnostic labels
used to decide which causal component deserves the next experiment.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def timestamp(record: dict[str, Any]) -> int | None:
    for key in ("event_ts_ns", "bar_ts_ns", "ts_event", "ts_close_ns", "confirmation_time_ns"):
        value = record.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return None


def side_name(value: Any) -> str:
    text = str(value).upper()
    return "LONG" if "LONG" in text or "BUY" in text else "SHORT"


def family(plan: dict[str, Any]) -> str:
    text = "|".join(
        str(plan.get(key, ""))
        for key in (
            "plan_id",
            "setup_id",
            "higher_zone_id",
            "lower_zone_id",
            "trigger_zone_id",
            "target_zone_id",
        )
    ).upper()
    if "DECISION_OB" in text or "DECISION_AREA_OB" in text:
        return "DECISION_AREA_OB"
    if "MAJOR_LIQUIDITY" in text:
        return "MAJOR_LIQUIDITY"
    if "REPEATED_DEFENSE" in text or "HORIZONTAL" in text:
        return "HORIZONTAL"
    return "DIAGONAL"


def variant(path: Path) -> str:
    names = (
        "displacement-invalidation",
        "displacement-5m",
        "decision-area-invalidation",
        "geometry-invalidation",
        "zone-invalidation",
        "geometry-static",
        "geometry-5m",
        "validated-static",
        "validated-5m",
        "zone-static",
        "zone-5m",
        "zone-1m",
        "geometry-btc",
        "validated-btc",
        "complete-breadth",
        "validated-structure",
        "decision-area",
        "geometry",
        "adjacent",
        "complete",
        "impulse",
    )
    lowered = str(path).lower()
    return next((name for name in names if name in lowered), path.parent.name)


def filled_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("kind") == "order_filled"]


def choose_fills(events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    fills = sorted(filled_events(events), key=lambda item: timestamp(item) or 0)
    entry = next((item for item in fills if item.get("role") == "ENTRY"), None)
    exits = [item for item in fills if item.get("role") != "ENTRY"]
    return entry, exits[-1] if exits else None


def bar_series(record: dict[str, Any]) -> list[dict[str, Any]]:
    bars = record.get("bars") or {}
    values = bars.get("1") or bars.get(1) or []
    return sorted(values, key=lambda item: timestamp(item) or 0)


def diagnose_trade(record: dict[str, Any], source: Path) -> dict[str, Any] | None:
    plan = record.get("plan") or {}
    events = record.get("events") or []
    entry_event, exit_event = choose_fills(events)
    planned_entry = number(plan.get("entry"))
    planned_stop = number(plan.get("stop"))
    planned_target = number(plan.get("target"))
    if planned_entry is None or planned_stop is None or planned_target is None:
        return None
    risk = abs(planned_entry - planned_stop)
    if risk <= 0:
        return None
    actual_entry = number(None if entry_event is None else entry_event.get("last_px")) or planned_entry
    actual_exit = number(None if exit_event is None else exit_event.get("last_px"))
    entry_ts = None if entry_event is None else timestamp(entry_event)
    exit_ts = None if exit_event is None else timestamp(exit_event)
    bars = bar_series(record)
    during = [
        bar
        for bar in bars
        if (entry_ts is None or (timestamp(bar) or 0) >= entry_ts)
        and (exit_ts is None or (timestamp(bar) or 0) <= exit_ts)
    ]
    if not during:
        during = [bar for bar in bars if int(bar.get("relative_to_trigger", 0)) >= 0]
    highs = [number(bar.get("high")) for bar in during]
    lows = [number(bar.get("low")) for bar in during]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    side = side_name(plan.get("side"))
    if highs and lows:
        if side == "LONG":
            mfe = (max(highs) - actual_entry) / risk
            mae = (actual_entry - min(lows)) / risk
        else:
            mfe = (actual_entry - min(lows)) / risk
            mae = (max(highs) - actual_entry) / risk
    else:
        mfe = mae = None
    if actual_exit is not None:
        realized = (
            (actual_exit - actual_entry) / risk
            if side == "LONG"
            else (actual_entry - actual_exit) / risk
        )
    else:
        realized = None
    planned_rr = abs(planned_target - planned_entry) / risk
    role = None if exit_event is None else exit_event.get("role")
    return {
        "variant": variant(source),
        "source": str(source),
        "symbol": plan.get("symbol"),
        "family": family(plan),
        "path": plan.get("scenario_path"),
        "side": side,
        "plan_id": plan.get("plan_id"),
        "planned_rr": planned_rr,
        "realized_price_r": realized,
        "mfe_r": mfe,
        "mae_r": mae,
        "exit_role": role,
        "entry_ts_ns": entry_ts,
        "exit_ts_ns": exit_ts,
        "hold_minutes": (
            None
            if entry_ts is None or exit_ts is None
            else max(0.0, (exit_ts - entry_ts) / 60_000_000_000)
        ),
        "immediate_failure": bool(mfe is not None and mfe < 0.25 and mae is not None and mae >= 0.75),
        "management_leak": bool(
            mfe is not None
            and realized is not None
            and mfe >= 1.0
            and realized < 0.30
        ),
        "remote_target_without_path": bool(planned_rr >= 8.0 and (mfe is None or mfe < 2.0)),
    }


def finite(values: list[Any]) -> list[float]:
    output = []
    for value in values:
        parsed = number(value)
        if parsed is not None and math.isfinite(parsed):
            output.append(parsed)
    return output


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    realized = finite([row["realized_price_r"] for row in rows])
    mfe = finite([row["mfe_r"] for row in rows])
    mae = finite([row["mae_r"] for row in rows])
    rr = finite([row["planned_rr"] for row in rows])
    holds = finite([row["hold_minutes"] for row in rows])
    return {
        "trades": len(rows),
        "wins": sum(value > 0 for value in realized),
        "win_rate": None if not realized else sum(value > 0 for value in realized) / len(realized),
        "mean_realized_price_r": None if not realized else mean(realized),
        "median_realized_price_r": None if not realized else median(realized),
        "mean_mfe_r": None if not mfe else mean(mfe),
        "median_mfe_r": None if not mfe else median(mfe),
        "mean_mae_r": None if not mae else mean(mae),
        "median_planned_rr": None if not rr else median(rr),
        "max_planned_rr": None if not rr else max(rr),
        "median_hold_minutes": None if not holds else median(holds),
        "immediate_failure_rate": None if not rows else sum(row["immediate_failure"] for row in rows) / len(rows),
        "management_leak_rate": None if not rows else sum(row["management_leak"] for row in rows) / len(rows),
        "remote_target_without_path_rate": (
            None if not rows else sum(row["remote_target_without_path"] for row in rows) / len(rows)
        ),
        "exit_roles": {
            str(role): sum(row["exit_role"] == role for row in rows)
            for role in sorted({row["exit_role"] for row in rows}, key=str)
        },
    }


def main() -> None:
    args = parse_args()
    rows: list[dict[str, Any]] = []
    for path in args.root.rglob("mtf_trade_windows.jsonl"):
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    record = json.loads(line)
                    diagnosed = diagnose_trade(record, path)
                    if diagnosed is not None:
                        rows.append(diagnosed)
        except Exception:
            continue
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["variant"], []).append(row)
    report = {
        "overall": aggregate(rows),
        "by_variant": {
            name: aggregate(group)
            for name, group in sorted(grouped.items())
        },
        "by_variant_family": {
            f"{name}|{family_name}": aggregate(
                [row for row in group if row["family"] == family_name],
            )
            for name, group in sorted(grouped.items())
            for family_name in sorted({row["family"] for row in group})
        },
        "trades": rows,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "diagnosis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if rows:
        with (args.output / "trades.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# RE1 trade-path diagnosis", "", "```json", json.dumps(report["by_variant"], indent=2), "```"]
    (args.output / "diagnosis.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
