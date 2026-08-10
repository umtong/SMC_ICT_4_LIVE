from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

_NUM = re.compile(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?")


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else default
    match = _NUM.search(str(value).replace("_", ""))
    if not match:
        return default
    try:
        result = float(match.group().replace(",", ""))
    except ValueError:
        return default
    return result if math.isfinite(result) else default


def parse_close(event: str) -> float | None:
    match = re.search(r"avg_px_close=([-+0-9_.,]+)", event)
    return number(match.group(1)) if match else None


def quantile(values: list[float], q: float) -> float | None:
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    location = q * (len(values) - 1)
    lo = int(math.floor(location))
    hi = int(math.ceil(location))
    if lo == hi:
        return values[lo]
    weight = location - lo
    return values[lo] * (1.0 - weight) + values[hi] * weight


def valid(item: dict[str, Any]) -> bool:
    marker = item.get("actual_fill_risk_valid")
    return True if marker is None else bool(marker)


def driver(item: dict[str, Any]) -> str:
    explicit = item.get("edtma_exit_driver")
    if explicit:
        return str(explicit)
    pnl = number(item.get("realized_pnl"))
    close = parse_close(str(item.get("event") or ""))
    stop = number(item.get("stop"), math.nan)
    target = number(item.get("target"), math.nan)
    side = int(number(item.get("side")))
    if close is not None and math.isfinite(stop) and stop > 0.0:
        if abs(close - stop) / stop <= 0.01 or (
            pnl < 0.0 and ((side > 0 and close <= stop) or (side < 0 and close >= stop))
        ):
            return "HARD_STOP"
    if close is not None and math.isfinite(target) and target > 0.0:
        if abs(close - target) / target <= 0.01:
            return "BRACKET_TARGET"
    return "UNATTRIBUTED_POSITIVE" if pnl > 0.0 else "UNATTRIBUTED_NEGATIVE"


def record(item: dict[str, Any]) -> dict[str, Any]:
    diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
    return {
        "key": f"{item.get('symbol')}:{item.get('episode_ts')}",
        "symbol": str(item.get("symbol") or "UNKNOWN"),
        "side": int(number(item.get("side"))),
        "source_tag": str(diagnostics.get("source_tag") or "UNKNOWN"),
        "pnl": number(item.get("realized_pnl")),
        "exit_driver": driver(item),
        "mfe": number(item.get("edtma_mfe_fraction")),
        "mae": number(item.get("edtma_mae_fraction")),
        "elapsed": int(number(item.get("edtma_elapsed_minutes"))),
        "adx": number(diagnostics.get("adx"), math.nan),
        "volume_ratio": number(diagnostics.get("volume_ratio"), math.nan),
        "trend_separation": number(diagnostics.get("trend_separation_fraction"), math.nan),
    }


def grouped(trades: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade[key])].append(trade)
    result: dict[str, Any] = {}
    for name, rows in sorted(groups.items()):
        gp = sum(max(row["pnl"], 0.0) for row in rows)
        gl = -sum(min(row["pnl"], 0.0) for row in rows)
        result[name] = {
            "trades": len(rows),
            "wins": sum(row["pnl"] > 0.0 for row in rows),
            "gross_profit_usdt": gp,
            "gross_loss_usdt": gl,
            "net_pnl_usdt": gp - gl,
            "profit_factor": gp / gl if gl else None,
        }
    return result


def summary(trades: list[dict[str, Any]], days: int, starting_nav: float) -> dict[str, Any]:
    winners = [trade for trade in trades if trade["pnl"] > 0.0]
    losers = [trade for trade in trades if trade["pnl"] < 0.0]
    gp = sum(trade["pnl"] for trade in winners)
    gl = -sum(trade["pnl"] for trade in losers)
    return {
        "trades": len(trades),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": len(winners) / len(trades) if trades else 0.0,
        "gross_profit_usdt": gp,
        "gross_loss_usdt": gl,
        "net_pnl_usdt": gp - gl,
        "profit_factor": gp / gl if gl else None,
        "expectancy_usdt": statistics.fmean([trade["pnl"] for trade in trades]) if trades else 0.0,
        "trades_per_day": len(trades) / max(days, 1),
        "gross_profit_per_day_initial_nav": gp / (max(days, 1) * max(starting_nav, 1.0)),
        "gross_loss_per_day_initial_nav": gl / (max(days, 1) * max(starting_nav, 1.0)),
        "winner_mfe_median": quantile([trade["mfe"] for trade in winners], 0.5),
        "loser_mfe_median": quantile([trade["mfe"] for trade in losers], 0.5),
        "winner_mae_median": quantile([trade["mae"] for trade in winners], 0.5),
        "loser_mae_median": quantile([trade["mae"] for trade in losers], 0.5),
        "winner_duration_median_minutes": quantile([float(trade["elapsed"]) for trade in winners], 0.5),
        "loser_duration_median_minutes": quantile([float(trade["elapsed"]) for trade in losers], 0.5),
        "by_exit_driver": grouped(trades, "exit_driver"),
        "by_symbol": grouped(trades, "symbol"),
        "by_side": grouped(trades, "side"),
        "by_source_tag": grouped(trades, "source_tag"),
    }


def load_run(path: Path, interval: str, variant: str) -> dict[str, Any]:
    metrics = json.loads((path / "metrics.json").read_text())
    diagnostics = json.loads((path / "strategy_diagnostics.json").read_text())
    scenarios = json.loads((path / "closed_scenarios.json").read_text())
    trades = [record(item) for item in scenarios if isinstance(item, dict) and valid(item)]
    invalid = sum(isinstance(item, dict) and not valid(item) for item in scenarios)
    days = int(metrics.get("calendar_days") or 14)
    starting_nav = number(metrics.get("starting_nav"), 100_000.0)
    return {
        "interval": interval,
        "variant": variant,
        "path": str(path),
        "implementation": {
            "closed_records": len(scenarios),
            "valid_trades": len(trades),
            "invalid_fill_trades": invalid,
            "order_rejections": int(diagnostics.get("order_rejections") or 0),
            "global_position_violations": int(diagnostics.get("global_position_violations") or 0),
            "fill_invalidations": int(diagnostics.get("fill_invalidations") or 0),
        },
        "reported_metrics": {
            key: metrics.get(key)
            for key in (
                "ending_nav", "total_return", "geometric_daily_growth", "max_drawdown",
                "trades", "wins", "losses", "win_rate", "gross_profit", "gross_loss",
                "profit_factor", "expectancy_usdt", "largest_winner_share",
            )
        },
        "diagnostics": {
            key: diagnostics.get(key)
            for key in (
                "edtma_hourly_decisions", "edtma_source_conditions", "edtma_entry_candidates",
                "entry_submissions", "edtma_trailing_activations", "edtma_trailing_exits",
                "edtma_roi_exits", "edtma_source_exit_signals",
                "edtma_rolling_chandelier_exits", "edtma_structural_stop_submissions",
                "edtma_exit_counts", "selected_symbols", "unresolved_reason_counts",
            )
        },
        "summary": summary(trades, days, starting_nav),
        "trades": trades,
    }


def compare(control: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    c = {trade["key"]: trade for trade in control["trades"]}
    e = {trade["key"]: trade for trade in experiment["trades"]}
    common = set(c) & set(e)
    c_only = set(c) - set(e)
    e_only = set(e) - set(c)
    pairs = [(c[key], e[key]) for key in common]
    c_gp = sum(max(trade["pnl"], 0.0) for trade in c.values())
    e_gp = sum(max(trade["pnl"], 0.0) for trade in e.values())
    c_gl = -sum(min(trade["pnl"], 0.0) for trade in c.values())
    e_gl = -sum(min(trade["pnl"], 0.0) for trade in e.values())
    c_winners = [pair for pair in pairs if pair[0]["pnl"] > 0.0]
    c_losers = [pair for pair in pairs if pair[0]["pnl"] < 0.0]
    return {
        "interval": control["interval"],
        "control": control["variant"],
        "experiment": experiment["variant"],
        "common_episodes": len(common),
        "control_only_episodes": len(c_only),
        "experiment_only_episodes": len(e_only),
        "gross_profit_preservation": e_gp / c_gp if c_gp else None,
        "gross_loss_reduction": 1.0 - e_gl / c_gl if c_gl else None,
        "net_pnl_change_usdt": sum(trade["pnl"] for trade in e.values()) - sum(trade["pnl"] for trade in c.values()),
        "control_winners_still_positive": sum(pair[1]["pnl"] > 0.0 for pair in c_winners),
        "control_winner_count": len(c_winners),
        "control_losers_rescued_positive": sum(pair[1]["pnl"] > 0.0 for pair in c_losers),
        "control_loser_count": len(c_losers),
        "extra_episode_net_pnl_usdt": sum(e[key]["pnl"] for key in e_only),
        "removed_episode_net_pnl_usdt": sum(c[key]["pnl"] for key in c_only),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())

    rows: list[dict[str, Any]] = []
    for interval in manifest["intervals"]:
        for variant in manifest["variants"]:
            path = args.root / interval / variant
            required = [path / name for name in ("metrics.json", "strategy_diagnostics.json", "closed_scenarios.json")]
            if all(item.is_file() for item in required):
                rows.append(load_run(path, interval, variant))
            else:
                rows.append({
                    "interval": interval,
                    "variant": variant,
                    "path": str(path),
                    "implementation": {"classification": "OUTPUT_MISSING"},
                })

    complete = [row for row in rows if "summary" in row]
    by_interval: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete:
        by_interval[row["interval"]][row["variant"]] = row
        by_variant[row["variant"]].append(row)

    comparisons: list[dict[str, Any]] = []
    for interval, lookup in sorted(by_interval.items()):
        for control, experiments in manifest["control_groups"].items():
            if control not in lookup:
                continue
            for experiment in experiments:
                if experiment in lookup:
                    comparisons.append(compare(lookup[control], lookup[experiment]))

    variant_anatomy = {}
    for variant, variant_rows in sorted(by_variant.items()):
        variant_anatomy[variant] = {
            "independent_account_count": len(variant_rows),
            "accounts": [
                {
                    "interval": row["interval"],
                    "implementation": row["implementation"],
                    "reported_metrics": row["reported_metrics"],
                    "summary": row["summary"],
                    "diagnostics": row["diagnostics"],
                }
                for row in variant_rows
            ],
        }

    result = {
        "purpose": (
            "Decompose the EDTMA system without reducing it to a pass/fail gate. Measure condition "
            "reentry versus independent episodes, source versus corrected chandelier exits, source "
            "versus structural risk geometry, winner preservation, loss concentration and implementation validity."
        ),
        "accounting_warning": "Independent interval accounts are not summed into a continuous NAV.",
        "manifest": manifest,
        "runs": rows,
        "variant_anatomy": variant_anatomy,
        "paired_mechanism_comparisons": comparisons,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# EDTMA system anatomy",
        "",
        "The tables separate opportunity generation, re-entry policy, exit engine and stop geometry. They are not a binary promotion gate.",
        "",
        "| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source/rolling exits |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in complete:
        s = row["summary"]
        d = row["diagnostics"]
        lines.append(
            f"| {row['interval']} | `{row['variant']}` | {s['trades']} | {s['wins']} | "
            f"{s['gross_profit_usdt']:.2f} | {s['gross_loss_usdt']:.2f} | "
            f"{(s['profit_factor'] or 0.0):.3f} | {s['net_pnl_usdt']:.2f} | "
            f"{row['implementation']['invalid_fill_trades']} | "
            f"{int(d.get('edtma_trailing_exits') or 0)} | {int(d.get('edtma_roi_exits') or 0)} | "
            f"{int(d.get('edtma_source_exit_signals') or 0) + int(d.get('edtma_rolling_chandelier_exits') or 0)} |"
        )
    lines.extend([
        "",
        "## Paired mechanism comparisons",
        "",
        "| interval | control | experiment | common | control-only | experiment-only | GP preservation | GL reduction | extra-episode PnL | net change |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in comparisons:
        gp = row["gross_profit_preservation"]
        gl = row["gross_loss_reduction"]
        lines.append(
            f"| {row['interval']} | `{row['control']}` | `{row['experiment']}` | "
            f"{row['common_episodes']} | {row['control_only_episodes']} | {row['experiment_only_episodes']} | "
            f"{0.0 if gp is None else gp:.3f} | {0.0 if gl is None else gl:.3f} | "
            f"{row['extra_episode_net_pnl_usdt']:.2f} | {row['net_pnl_change_usdt']:.2f} |"
        )
    args.output_md.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
