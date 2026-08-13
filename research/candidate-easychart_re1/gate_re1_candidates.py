#!/usr/bin/env python3
"""Aggregate account artifacts and gate structural candidates without fitting.

The gate ranks named policy variants, not numeric parameter combinations.  Its
thresholds are project success conditions or conservative promotion criteria;
they are never fed back into entry/exit calculations.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import re
from typing import Any


VARIANT_PATTERNS = (
    ("liquidity-location", "liquidity-location"),
    ("liquidity-local", "liquidity-local"),
    ("local-alignment", "local-alignment"),
    ("complete", "complete"),
    ("impulse", "impulse"),
    ("location", "location"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--mode", choices=("short", "holdout", "long"), default="short")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, (dict, list)):
                output.update(flatten(item, path))
            else:
                output[path] = item
    elif isinstance(value, list):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            if isinstance(item, (dict, list)):
                output.update(flatten(item, path))
            else:
                output[path] = item
    return output


def first_exact(flat: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        for key, value in flat.items():
            if key.rsplit(".", 1)[-1].lower() == name and isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def first_regex(
    flat: dict[str, Any],
    required: tuple[str, ...],
    excluded: tuple[str, ...] = (),
) -> float | None:
    matches: list[tuple[int, str, float]] = []
    for key, value in flat.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        lowered = key.lower()
        if all(re.search(pattern, lowered) for pattern in required) and not any(
            re.search(pattern, lowered) for pattern in excluded
        ):
            matches.append((len(key), key, float(value)))
    return min(matches)[2] if matches else None


def normalize_fraction(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if abs(value) > 2.0 else value


def variant_from_path(path: Path) -> str | None:
    text = str(path).lower()
    for pattern, name in VARIANT_PATTERNS:
        if pattern in text:
            return name
    return None


def period_from_path(path: Path) -> str:
    text = str(path).lower()
    known = (
        "dev-february-2024",
        "dev-august-2024",
        "dev-february-2025",
        "random-aug20-sep02-2024",
        "random-sep09-sep22-2024",
        "random-mar07-mar20-2025",
        "random-jun09-jun22-2025",
        "random-sep10-sep23-2025",
        "holdout-march-2024",
        "holdout-may-2024",
        "holdout-november-2024",
        "holdout-january-2025",
        "holdout-april-2025",
        "holdout-july-2025",
        "holdout-november-2025",
        "holdout-february-2026",
        "long-continuous",
    )
    return next((item for item in known if item in text), path.parent.name)


def csv_trade_stats(root: Path) -> dict[str, float] | None:
    rows: list[float] = []
    for path in root.rglob("*.csv"):
        try:
            records = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
        except Exception:
            continue
        if not records:
            continue
        columns = {column.lower(): column for column in records[0]}
        pnl_column = next(
            (
                original
                for lowered, original in columns.items()
                if lowered in {"pnl", "net_pnl", "realized_pnl"} or "realized_pnl" in lowered
            ),
            None,
        )
        if pnl_column is None:
            continue
        local: list[float] = []
        for record in records:
            try:
                local.append(float(str(record[pnl_column]).replace(",", "")))
            except (TypeError, ValueError):
                pass
        if len(local) > len(rows):
            rows = local
    if not rows:
        return None
    return {
        "trades": float(len(rows)),
        "wins": float(sum(value > 0 for value in rows)),
        "win_rate": sum(value > 0 for value in rows) / len(rows),
        "pnl_sum": sum(rows),
    }


def parse_artifact(metrics_path: Path) -> dict[str, Any]:
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    flat = flatten(metrics)
    result: dict[str, Any] = {
        "artifact": str(metrics_path.parent),
        "variant": variant_from_path(metrics_path),
        "period": period_from_path(metrics_path),
        "metric_keys": sorted(flat),
    }
    account_return = first_exact(
        flat,
        (
            "return_after_costs_pct",
            "net_return_pct",
            "total_return_pct",
            "account_return_pct",
            "return_after_costs",
            "net_return",
            "total_return",
            "account_return",
        ),
    )
    if account_return is None:
        account_return = first_regex(flat, ("return", "cost"), ("daily", "trade"))
    if account_return is None:
        start = first_regex(flat, ("(initial|start)", "(nav|balance|equity)"))
        end = first_regex(flat, ("(final|end)", "(nav|balance|equity)"))
        if start and end:
            account_return = end / start - 1.0
    result["return"] = normalize_fraction(account_return)
    result["daily_geo"] = normalize_fraction(
        first_exact(
            flat,
            (
                "daily_geometric_growth_pct",
                "daily_geometric_growth",
                "geometric_daily_growth",
                "daily_geometric_return",
            ),
        )
        or first_regex(flat, ("daily", "geometric")),
    )
    result["trades"] = first_exact(
        flat,
        ("completed_trades", "closed_trades", "trade_count", "trades"),
    ) or first_regex(flat, ("(completed|closed)", "trade"))
    result["win_rate"] = normalize_fraction(
        first_exact(flat, ("win_rate_pct", "win_rate"))
        or first_regex(flat, ("win", "rate")),
    )
    result["max_drawdown"] = normalize_fraction(
        first_exact(flat, ("max_drawdown_pct", "max_drawdown"))
        or first_regex(flat, ("max", "drawdown")),
    )
    result["max_rr"] = first_exact(
        flat,
        ("max_planned_gross_rr", "max_gross_rr", "maximum_gross_rr"),
    ) or first_regex(flat, ("max", "(planned|gross)", "rr"))
    result["max_hold_minutes"] = first_exact(
        flat,
        ("max_holding_minutes", "maximum_holding_minutes"),
    ) or first_regex(flat, ("max", "hold"))
    csv_stats = csv_trade_stats(metrics_path.parent)
    if csv_stats:
        if result["trades"] is None:
            result["trades"] = csv_stats["trades"]
        if result["win_rate"] is None:
            result["win_rate"] = csv_stats["win_rate"]
        result["csv_trade_stats"] = csv_stats
    return result


def aggregate(rows: list[dict[str, Any]], days: int) -> dict[str, Any]:
    valid = [row for row in rows if row.get("return") is not None]
    returns = [float(row["return"]) for row in valid]
    total_trades = sum(float(row.get("trades") or 0.0) for row in valid)
    weighted_wins = sum(
        float(row.get("trades") or 0.0) * float(row.get("win_rate") or 0.0)
        for row in valid
        if row.get("trades") is not None and row.get("win_rate") is not None
    )
    weighted_total = sum(
        float(row["trades"])
        for row in valid
        if row.get("trades") is not None and row.get("win_rate") is not None
    )
    compound = math.prod(1.0 + value for value in returns) - 1.0 if returns else None
    daily_geo = (
        (1.0 + compound) ** (1.0 / days) - 1.0
        if compound is not None and compound > -1.0 and days > 0
        else None
    )
    return {
        "periods": len(valid),
        "compound_return": compound,
        "daily_geometric_growth": daily_geo,
        "positive_periods": sum(value > 0 for value in returns),
        "worst_period": min(returns) if returns else None,
        "best_period": max(returns) if returns else None,
        "trades": total_trades,
        "trades_per_day": total_trades / days if days else None,
        "weighted_win_rate": weighted_wins / weighted_total if weighted_total else None,
        "max_planned_rr": max(
            (float(row["max_rr"]) for row in valid if row.get("max_rr") is not None),
            default=None,
        ),
        "max_drawdown": min(
            (float(row["max_drawdown"]) for row in valid if row.get("max_drawdown") is not None),
            default=None,
        ),
    }


def short_score(dev: dict[str, Any], random: dict[str, Any]) -> float:
    if dev["periods"] < 3 or random["periods"] < 5:
        return -1_000_000.0
    return (
        6.0 * float(random["compound_return"] or -2.0)
        + 2.0 * float(dev["compound_return"] or -2.0)
        + 0.25 * float(random["positive_periods"])
        + 1.5 * float(random["worst_period"] or -2.0)
        + min(float(random["trades_per_day"] or 0.0), 1.5) * 0.25
        + min(float(random["weighted_win_rate"] or 0.0), 1.0) * 0.25
    )


def evaluate(root: Path, mode: str) -> dict[str, Any]:
    rows = [parse_artifact(path) for path in root.rglob("metrics.json")]
    variants = sorted({row["variant"] for row in rows if row.get("variant")})
    report: dict[str, Any] = {"mode": mode, "rows": rows, "variants": {}}
    if mode == "short":
        for variant in variants:
            own = [row for row in rows if row["variant"] == variant]
            dev_rows = [row for row in own if row["period"].startswith("dev-")]
            random_rows = [row for row in own if row["period"].startswith("random-")]
            dev = aggregate(dev_rows, 42)
            random = aggregate(random_rows, 70)
            score = short_score(dev, random)
            promising = bool(
                dev["periods"] == 3
                and random["periods"] == 5
                and float(dev["compound_return"] or -9.0) > 0.0
                and float(random["compound_return"] or -9.0) > 0.0
                and random["positive_periods"] >= 4
                and float(random["worst_period"] or -9.0) > -0.10
                and float(random["trades"] or 0.0) >= 40.0
                and float(random["weighted_win_rate"] or 0.0) >= 0.45
            )
            report["variants"][variant] = {
                "development": dev,
                "disclosed_random": random,
                "score": score,
                "promising": promising,
            }
        ranked = sorted(
            report["variants"],
            key=lambda name: report["variants"][name]["score"],
            reverse=True,
        )
        report["selected_variant"] = ranked[0] if ranked else None
        selected = report["variants"].get(report["selected_variant"], {})
        report["pass"] = bool(selected.get("promising"))
    elif mode == "holdout":
        # The root contains only one selected candidate over eight unseen windows.
        summary = aggregate(rows, 112)
        report["aggregate"] = summary
        report["selected_variant"] = variants[0] if len(variants) == 1 else None
        report["pass"] = bool(
            summary["periods"] == 8
            and float(summary["compound_return"] or -9.0) > 0.0
            and float(summary["daily_geometric_growth"] or -9.0) >= 0.01
            and summary["positive_periods"] >= 6
            and float(summary["worst_period"] or -9.0) > -0.10
            and float(summary["trades"] or 0.0) >= 80.0
            and float(summary["weighted_win_rate"] or 0.0) >= 0.50
        )
    else:
        # One true continuous account; calendar days are written by the runner when available.
        summary = aggregate(rows, 912)
        report["aggregate"] = summary
        report["selected_variant"] = variants[0] if len(variants) == 1 else None
        report["pass"] = bool(
            summary["periods"] == 1
            and float(summary["compound_return"] or -9.0) > 0.0
            and float(summary["daily_geometric_growth"] or -9.0) >= 0.01
            and float(summary["trades"] or 0.0) >= 912.0
            and float(summary["max_drawdown"] or -9.0) > -0.35
        )
    return report


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# RE1 candidate gate — {report['mode']}",
        "",
        f"Selected: **{report.get('selected_variant')}**",
        f"Pass: **{report.get('pass')}**",
        "",
        "| Variant | Period | Return | Trades | Win rate | Max DD | Max RR | Max hold |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(report["rows"], key=lambda item: (str(item.get("variant")), item["period"])):
        lines.append(
            "| {variant} | {period} | {return_} | {trades} | {win_rate} | {max_dd} | {max_rr} | {max_hold} |".format(
                variant=row.get("variant"),
                period=row.get("period"),
                return_=row.get("return"),
                trades=row.get("trades"),
                win_rate=row.get("win_rate"),
                max_dd=row.get("max_drawdown"),
                max_rr=row.get("max_rr"),
                max_hold=row.get("max_hold_minutes"),
            ),
        )
    lines += ["", "```json", json.dumps(report, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    report = evaluate(args.root, args.mode)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "gate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output / "gate.md").write_text(markdown(report), encoding="utf-8")
    if args.github_output:
        selected = report.get("selected_variant") or ""
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"variant={selected}\n")
            stream.write(f"pass={'true' if report.get('pass') else 'false'}\n")


if __name__ == "__main__":
    main()
