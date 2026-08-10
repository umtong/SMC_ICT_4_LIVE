from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

_NUMBER = re.compile(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?")


def number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else default
    match = _NUMBER.search(str(value).replace("_", ""))
    if not match:
        return default
    try:
        result = float(match.group().replace(",", ""))
    except ValueError:
        return default
    return result if math.isfinite(result) else default


def quantile(values: list[float], q: float) -> float | None:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    location = q * (len(clean) - 1)
    lo = int(math.floor(location))
    hi = int(math.ceil(location))
    if lo == hi:
        return clean[lo]
    weight = location - lo
    return clean[lo] * (1.0 - weight) + clean[hi] * weight


def parse_close(event: str) -> float | None:
    match = re.search(r"avg_px_close=([-+0-9_.,]+)", event)
    return number(match.group(1)) if match else None


def valid_trade(item: dict[str, Any]) -> bool:
    marker = item.get("actual_fill_risk_valid")
    return True if marker is None else bool(marker)


def exit_driver(item: dict[str, Any]) -> str:
    explicit = item.get("picasso_repair_exit_driver")
    if explicit:
        return str(explicit)
    pnl = number(item.get("realized_pnl"))
    close = parse_close(str(item.get("event") or ""))
    stop = number(item.get("stop"), math.nan)
    target = number(item.get("target"), math.nan)
    side = int(number(item.get("side")))
    if close is not None and math.isfinite(stop) and stop > 0.0:
        stop_gap = abs(close - stop) / stop
        if stop_gap <= 0.01 or (pnl < 0.0 and ((side > 0 and close <= stop) or (side < 0 and close >= stop))):
            return "HARD_STOP"
    if close is not None and math.isfinite(target) and target > 0.0:
        target_gap = abs(close - target) / target
        if target_gap <= 0.01:
            return "BRACKET_TARGET"
    return "UNATTRIBUTED_POSITIVE" if pnl > 0.0 else "UNATTRIBUTED_NEGATIVE"


def trade_record(item: dict[str, Any]) -> dict[str, Any]:
    diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
    return {
        "key": f"{item.get('symbol')}:{item.get('episode_ts')}",
        "symbol": str(item.get("symbol") or "UNKNOWN"),
        "side": int(number(item.get("side"))),
        "source_tag": str(diagnostics.get("source_tag") or "UNKNOWN"),
        "pnl": number(item.get("realized_pnl")),
        "exit_driver": exit_driver(item),
        "mfe": number(item.get("picasso_repair_mfe_fraction")),
        "mae": number(item.get("picasso_repair_mae_fraction")),
        "current": number(item.get("picasso_repair_current_fraction")),
        "elapsed_minutes": int(number(item.get("picasso_repair_elapsed_minutes"))),
        "trail_activation_minutes": item.get("picasso_repair_trail_activation_minutes"),
        "adx": number(diagnostics.get("adx"), math.nan),
        "volume_ratio": (
            number(diagnostics.get("volume")) / max(number(
                diagnostics.get("volume_mean_long") if int(number(item.get("side"))) > 0
                else diagnostics.get("volume_mean_short")
            ), 1e-12)
        ),
        "macd_gap_bps": (
            abs(number(diagnostics.get("macd")) - number(diagnostics.get("macd_signal")))
            / max(number(item.get("entry_reference")), 1e-12) * 10_000.0
        ),
        "stop_distance_fraction": (
            abs(number(item.get("entry_reference")) - number(item.get("stop")))
            / max(number(item.get("entry_reference")), 1e-12)
        ),
    }


def summarize(trades: list[dict[str, Any]], days: int, starting_nav: float) -> dict[str, Any]:
    pnls = [trade["pnl"] for trade in trades]
    winners = [trade for trade in trades if trade["pnl"] > 0.0]
    losers = [trade for trade in trades if trade["pnl"] < 0.0]
    gp = sum(trade["pnl"] for trade in winners)
    gl = -sum(trade["pnl"] for trade in losers)

    def path(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(rows),
            "mfe_median": quantile([row["mfe"] for row in rows], 0.5),
            "mfe_q25": quantile([row["mfe"] for row in rows], 0.25),
            "mfe_q75": quantile([row["mfe"] for row in rows], 0.75),
            "mae_median": quantile([row["mae"] for row in rows], 0.5),
            "elapsed_median_minutes": quantile([float(row["elapsed_minutes"]) for row in rows], 0.5),
        }

    def grouped(key: str) -> dict[str, Any]:
        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            groups[str(trade[key])].append(trade)
        result = {}
        for name, rows in sorted(groups.items()):
            group_gp = sum(max(row["pnl"], 0.0) for row in rows)
            group_gl = -sum(min(row["pnl"], 0.0) for row in rows)
            result[name] = {
                "trades": len(rows),
                "wins": sum(row["pnl"] > 0.0 for row in rows),
                "gross_profit_usdt": group_gp,
                "gross_loss_usdt": group_gl,
                "net_pnl_usdt": group_gp - group_gl,
                "profit_factor": group_gp / group_gl if group_gl else None,
            }
        return result

    return {
        "trades": len(trades),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": len(winners) / len(trades) if trades else 0.0,
        "gross_profit_usdt": gp,
        "gross_loss_usdt": gl,
        "net_pnl_usdt": gp - gl,
        "profit_factor": gp / gl if gl else None,
        "expectancy_usdt": statistics.fmean(pnls) if pnls else 0.0,
        "trades_per_day": len(trades) / max(days, 1),
        "gross_profit_per_day_initial_nav": gp / (max(days, 1) * max(starting_nav, 1.0)),
        "gross_loss_per_day_initial_nav": gl / (max(days, 1) * max(starting_nav, 1.0)),
        "winner_path": path(winners),
        "loser_path": path(losers),
        "by_exit_driver": grouped("exit_driver"),
        "by_symbol": grouped("symbol"),
        "by_side": grouped("side"),
        "by_source_tag": grouped("source_tag"),
    }


def load_run(path: Path, interval: str, variant: str) -> dict[str, Any]:
    metrics = json.loads((path / "metrics.json").read_text())
    diagnostics = json.loads((path / "strategy_diagnostics.json").read_text())
    scenarios = json.loads((path / "closed_scenarios.json").read_text())
    valid = [trade_record(item) for item in scenarios if isinstance(item, dict) and valid_trade(item)]
    invalid = [item for item in scenarios if isinstance(item, dict) and not valid_trade(item)]
    days = int(metrics.get("calendar_days") or 7)
    starting_nav = number(metrics.get("starting_nav"), 100_000.0)
    return {
        "interval": interval,
        "variant": variant,
        "path": str(path),
        "implementation": {
            "scenario_records": len(scenarios),
            "economically_valid_trades": len(valid),
            "invalid_actual_fill_trades": len(invalid),
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
                "source_signals_before_execution_filters", "entry_submissions", "selected_symbols",
                "picasso_trailing_activations", "picasso_trailing_exits", "picasso_roi_exits",
                "picasso_source_signal_exits", "picasso_repair_stop_submissions",
                "picasso_repair_lifecycle_exits", "picasso_repair_progress_exits",
                "picasso_repair_exit_counts", "unresolved_reason_counts",
            )
        },
        "summary": summarize(valid, days, starting_nav),
        "trades": valid,
    }


def compare(control: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    c = {trade["key"]: trade for trade in control["trades"]}
    e = {trade["key"]: trade for trade in experiment["trades"]}
    common = sorted(set(c) & set(e))
    c_only = sorted(set(c) - set(e))
    e_only = sorted(set(e) - set(c))
    pairs = [(c[key], e[key]) for key in common]
    control_winners = [pair for pair in pairs if pair[0]["pnl"] > 0.0]
    control_losers = [pair for pair in pairs if pair[0]["pnl"] < 0.0]
    control_gp = sum(max(trade["pnl"], 0.0) for trade in c.values())
    experiment_gp = sum(max(trade["pnl"], 0.0) for trade in e.values())
    control_gl = -sum(min(trade["pnl"], 0.0) for trade in c.values())
    experiment_gl = -sum(min(trade["pnl"], 0.0) for trade in e.values())
    return {
        "control": control["variant"],
        "experiment": experiment["variant"],
        "common_episode_count": len(common),
        "control_only_episode_count": len(c_only),
        "experiment_only_episode_count": len(e_only),
        "common_episode_share_of_control": len(common) / len(c) if c else 0.0,
        "gross_profit_preservation_all_trades": experiment_gp / control_gp if control_gp else None,
        "gross_loss_reduction_all_trades": 1.0 - experiment_gl / control_gl if control_gl else None,
        "net_pnl_change_usdt": sum(trade["pnl"] for trade in e.values()) - sum(trade["pnl"] for trade in c.values()),
        "paired_control_winners": len(control_winners),
        "paired_winners_still_positive": sum(pair[1]["pnl"] > 0.0 for pair in control_winners),
        "paired_winner_pnl_preservation": (
            sum(max(pair[1]["pnl"], 0.0) for pair in control_winners)
            / sum(pair[0]["pnl"] for pair in control_winners)
            if control_winners else None
        ),
        "paired_control_losers": len(control_losers),
        "paired_losers_rescued_positive": sum(pair[1]["pnl"] > 0.0 for pair in control_losers),
        "paired_loser_loss_reduction": (
            1.0 - (-sum(min(pair[1]["pnl"], 0.0) for pair in control_losers))
            / (-sum(pair[0]["pnl"] for pair in control_losers))
            if control_losers and -sum(pair[0]["pnl"] for pair in control_losers) > 0.0 else None
        ),
        "paired_mean_pnl_change_usdt": (
            statistics.fmean(pair[1]["pnl"] - pair[0]["pnl"] for pair in pairs)
            if pairs else 0.0
        ),
        "control_only_keys": c_only,
        "experiment_only_keys": e_only,
    }


def aggregate_variant(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [trade for row in rows for trade in row["trades"]]
    total_days = sum(int(row["reported_metrics"].get("trades") is not None) * 7 for row in rows)
    # Intervals in this workflow are seven days; retain per-run summaries too so
    # future audits do not silently treat a stitched account as continuous.
    summary = summarize(trades, max(total_days, 1), 100_000.0)
    return {
        "independent_account_count": len(rows),
        "interval_summaries": [
            {
                "interval": row["interval"],
                "reported_metrics": row["reported_metrics"],
                "implementation": row["implementation"],
                "summary": row["summary"],
            }
            for row in rows
        ],
        "descriptive_trade_pool_only_not_a_continuous_account": summary,
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
    by_interval: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete:
        by_interval[row["interval"]].append(row)
        by_variant[row["variant"]].append(row)

    comparisons = []
    for interval, interval_rows in sorted(by_interval.items()):
        lookup = {row["variant"]: row for row in interval_rows}
        for control, experiments in manifest["control_groups"].items():
            if control not in lookup:
                continue
            for experiment in experiments:
                if experiment in lookup:
                    comparisons.append({
                        "interval": interval,
                        **compare(lookup[control], lookup[experiment]),
                    })

    result = {
        "purpose": (
            "Deep system anatomy, not a binary gate. Preserve and measure both sparse/high-quality "
            "and dense/repairable mechanisms. Compare winner preservation, loss reduction, new/missed "
            "episodes, execution validity, exit engines and path behavior."
        ),
        "accounting_warning": (
            "Cross-interval pooled trade summaries are descriptive only. No NAV, return or PnL from "
            "independent accounts is summed or accepted as a continuous system result."
        ),
        "manifest": manifest,
        "runs": rows,
        "variant_anatomy": {
            variant: aggregate_variant(variant_rows)
            for variant, variant_rows in sorted(by_variant.items())
        },
        "paired_repair_comparisons": comparisons,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Picasso mechanism anatomy",
        "",
        "This is not a pass/fail table. It separates entry opportunity, winner management, loss management, risk geometry, and implementation validity.",
        "",
        "Independent weekly accounts are never stitched into a claimed continuous NAV.",
        "",
        "## Per-account anatomy",
        "",
        "| interval | variant | trades | wins | gross profit | gross loss | PF | net | invalid fills | trail exits | source exits | repair exits |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in complete:
        summary = row["summary"]
        diagnostics = row["diagnostics"]
        repair_exits = int(diagnostics.get("picasso_repair_lifecycle_exits") or 0) + int(
            diagnostics.get("picasso_repair_progress_exits") or 0
        )
        lines.append(
            f"| {row['interval']} | `{row['variant']}` | {summary['trades']} | {summary['wins']} | "
            f"{summary['gross_profit_usdt']:.2f} | {summary['gross_loss_usdt']:.2f} | "
            f"{(summary['profit_factor'] or 0.0):.3f} | {summary['net_pnl_usdt']:.2f} | "
            f"{row['implementation']['invalid_actual_fill_trades']} | "
            f"{int(diagnostics.get('picasso_trailing_exits') or 0)} | "
            f"{int(diagnostics.get('picasso_source_signal_exits') or 0)} | {repair_exits} |"
        )
    lines.extend([
        "",
        "## Paired episode trade-offs",
        "",
        "| interval | control | experiment | common | GP preservation | GL reduction | control losers rescued | control winners still positive | net change |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in comparisons:
        gp = row["gross_profit_preservation_all_trades"]
        gl = row["gross_loss_reduction_all_trades"]
        lines.append(
            f"| {row['interval']} | `{row['control']}` | `{row['experiment']}` | "
            f"{row['common_episode_count']} | {0.0 if gp is None else gp:.3f} | "
            f"{0.0 if gl is None else gl:.3f} | {row['paired_losers_rescued_positive']} | "
            f"{row['paired_winners_still_positive']} | {row['net_pnl_change_usdt']:.2f} |"
        )
    args.output_md.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
