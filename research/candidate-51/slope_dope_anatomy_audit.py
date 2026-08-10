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


def event_price(event: str, name: str) -> float | None:
    match = re.search(rf"{re.escape(name)}=([-+0-9_.,]+)", event)
    return number(match.group(1)) if match else None


def valid(item: dict[str, Any]) -> bool:
    marker = item.get("actual_fill_risk_valid")
    return True if marker is None else bool(marker)


def driver(item: dict[str, Any]) -> str:
    if item.get("slope_exit_driver"):
        return str(item["slope_exit_driver"])
    pnl = number(item.get("realized_pnl"))
    event = str(item.get("event") or "")
    close = event_price(event, "avg_px_close")
    stop = number(item.get("stop"), math.nan)
    target = number(item.get("target"), math.nan)
    side = int(number(item.get("side")))
    if close is not None and math.isfinite(stop) and stop > 0.0:
        if abs(close - stop) / stop <= 0.01 or (
            pnl < 0.0 and ((side > 0 and close <= stop) or (side < 0 and close >= stop))
        ):
            return "HARD_STOP"
    if close is not None and math.isfinite(target) and target > 0.0:
        if abs(close - target) / target <= 0.01 or (
            pnl > 0.0 and ((side > 0 and close >= target) or (side < 0 and close <= target))
        ):
            return "BRACKET_TARGET_OR_INITIAL_ROI"
    return "UNATTRIBUTED_POSITIVE" if pnl > 0.0 else "UNATTRIBUTED_NEGATIVE"


def trade(item: dict[str, Any]) -> dict[str, Any]:
    diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
    return {
        "key": f"{item.get('symbol')}:{item.get('episode_ts')}:{item.get('side')}",
        "symbol": str(item.get("symbol") or "UNKNOWN"),
        "side": int(number(item.get("side"))),
        "source_tag": str(diagnostics.get("source_tag") or "UNKNOWN"),
        "pnl": number(item.get("realized_pnl")),
        "exit_driver": driver(item),
        "mfe": number(item.get("slope_mfe_fraction")),
        "mae": number(item.get("slope_mae_fraction")),
        "current": number(item.get("slope_current_fraction")),
        "elapsed": int(number(item.get("slope_elapsed_minutes"))),
        "first_positive": item.get("slope_first_positive_minute"),
        "first_one_percent": item.get("slope_first_one_percent_mfe_minute"),
        "adx": number(diagnostics.get("adx"), math.nan),
        "rsi": number(diagnostics.get("rsi"), math.nan),
        "fast_slope": number(diagnostics.get("fast_slope"), math.nan),
        "slow_slope": number(diagnostics.get("slow_slope"), math.nan),
        "ma_separation": number(diagnostics.get("ma_separation_fraction"), math.nan),
    }


def group(trades: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        groups[str(row[key])].append(row)
    result = {}
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


def path(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "mfe_q25": quantile([row["mfe"] for row in rows], 0.25),
        "mfe_median": quantile([row["mfe"] for row in rows], 0.5),
        "mfe_q75": quantile([row["mfe"] for row in rows], 0.75),
        "mae_q25": quantile([row["mae"] for row in rows], 0.25),
        "mae_median": quantile([row["mae"] for row in rows], 0.5),
        "mae_q75": quantile([row["mae"] for row in rows], 0.75),
        "duration_median_minutes": quantile([float(row["elapsed"]) for row in rows], 0.5),
        "first_positive_median_minutes": quantile([
            float(row["first_positive"]) for row in rows if row["first_positive"] is not None
        ], 0.5),
        "first_one_percent_median_minutes": quantile([
            float(row["first_one_percent"]) for row in rows if row["first_one_percent"] is not None
        ], 0.5),
    }


def summarize(trades: list[dict[str, Any]], days: int, starting_nav: float) -> dict[str, Any]:
    winners = [row for row in trades if row["pnl"] > 0.0]
    losers = [row for row in trades if row["pnl"] < 0.0]
    gp = sum(row["pnl"] for row in winners)
    gl = -sum(row["pnl"] for row in losers)
    return {
        "trades": len(trades),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": len(winners) / len(trades) if trades else 0.0,
        "gross_profit_usdt": gp,
        "gross_loss_usdt": gl,
        "net_pnl_usdt": gp - gl,
        "profit_factor": gp / gl if gl else None,
        "expectancy_usdt": statistics.fmean([row["pnl"] for row in trades]) if trades else 0.0,
        "trades_per_day": len(trades) / max(days, 1),
        "gross_profit_per_day_initial_nav": gp / (max(days, 1) * max(starting_nav, 1.0)),
        "gross_loss_per_day_initial_nav": gl / (max(days, 1) * max(starting_nav, 1.0)),
        "winner_path": path(winners),
        "loser_path": path(losers),
        "by_exit_driver": group(trades, "exit_driver"),
        "by_symbol": group(trades, "symbol"),
        "by_side": group(trades, "side"),
        "by_source_tag": group(trades, "source_tag"),
    }


def load_run(root: Path, interval: str, variant: str) -> dict[str, Any] | None:
    path = root / interval / variant
    required = [path / name for name in ("metrics.json", "strategy_diagnostics.json", "closed_scenarios.json")]
    if not all(item.is_file() for item in required):
        return None
    metrics = json.loads(required[0].read_text())
    diagnostics = json.loads(required[1].read_text())
    scenarios = json.loads(required[2].read_text())
    trades = [trade(item) for item in scenarios if isinstance(item, dict) and valid(item)]
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
            "max_open_positions_observed": int(diagnostics.get("max_open_positions_observed") or 0),
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
                "slope_hourly_decisions", "slope_source_conditions", "slope_entry_candidates",
                "entry_submissions", "slope_trailing_activations", "slope_trailing_exits",
                "slope_roi_exits", "slope_source_exit_signals",
                "slope_structural_stop_submissions", "slope_exit_counts",
                "selected_symbols", "unresolved_reason_counts",
            )
        },
        "summary": summarize(trades, days, starting_nav),
        "trades": trades,
    }


def compare(control: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    c = {row["key"]: row for row in control["trades"]}
    e = {row["key"]: row for row in experiment["trades"]}
    common = set(c) & set(e)
    c_only = set(c) - set(e)
    e_only = set(e) - set(c)
    pairs = [(c[key], e[key]) for key in common]
    c_winners = [pair for pair in pairs if pair[0]["pnl"] > 0.0]
    c_losers = [pair for pair in pairs if pair[0]["pnl"] < 0.0]
    c_gp = sum(max(row["pnl"], 0.0) for row in c.values())
    e_gp = sum(max(row["pnl"], 0.0) for row in e.values())
    c_gl = -sum(min(row["pnl"], 0.0) for row in c.values())
    e_gl = -sum(min(row["pnl"], 0.0) for row in e.values())
    return {
        "interval": control["interval"],
        "control": control["variant"],
        "experiment": experiment["variant"],
        "common_episodes": len(common),
        "control_only_episodes": len(c_only),
        "experiment_only_episodes": len(e_only),
        "gross_profit_preservation": e_gp / c_gp if c_gp else None,
        "gross_loss_reduction": 1.0 - e_gl / c_gl if c_gl else None,
        "net_pnl_change_usdt": sum(row["pnl"] for row in e.values()) - sum(row["pnl"] for row in c.values()),
        "paired_control_winners": len(c_winners),
        "paired_winners_still_positive": sum(pair[1]["pnl"] > 0.0 for pair in c_winners),
        "paired_control_losers": len(c_losers),
        "paired_losers_rescued_positive": sum(pair[1]["pnl"] > 0.0 for pair in c_losers),
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
    rows = []
    for interval in manifest["intervals"]:
        for variant in manifest["variants"]:
            loaded = load_run(args.root, interval, variant)
            rows.append(loaded or {
                "interval": interval,
                "variant": variant,
                "path": str(args.root / interval / variant),
                "implementation": {"classification": "OUTPUT_MISSING"},
            })
    complete = [row for row in rows if row and "summary" in row]
    by_interval: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete:
        by_interval[row["interval"]][row["variant"]] = row
        by_variant[row["variant"]].append(row)
    comparisons = []
    for interval, lookup in sorted(by_interval.items()):
        for control, experiments in manifest["control_groups"].items():
            if control not in lookup:
                continue
            for experiment in experiments:
                if experiment in lookup:
                    comparisons.append(compare(lookup[control], lookup[experiment]))
    result = {
        "purpose": (
            "Deep non-binary anatomy of the public Slope-is-Dope system. Separate opportunity, "
            "long/short state, condition re-entry, trailing/ROI winners, asymmetric source exits, "
            "symmetric and MA-only alternatives, stop geometry, path behavior and implementation validity."
        ),
        "accounting_warning": "Independent accounts are never stitched into a continuous NAV.",
        "manifest": manifest,
        "runs": rows,
        "variant_anatomy": {
            variant: {
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
            for variant, variant_rows in sorted(by_variant.items())
        },
        "paired_mechanism_comparisons": comparisons,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Slope-is-Dope system anatomy",
        "",
        "This is not a gate. Opportunity, direction, winner engine, exit engine, risk geometry and implementation are shown separately.",
        "",
        "| interval | variant | trades | wins | GP | GL | PF | net | invalid fills | trailing | ROI | source exits |",
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
            f"{int(d.get('slope_trailing_exits') or 0)} | {int(d.get('slope_roi_exits') or 0)} | "
            f"{int(d.get('slope_source_exit_signals') or 0)} |"
        )
    lines.extend([
        "",
        "## Paired mechanism trade-offs",
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
