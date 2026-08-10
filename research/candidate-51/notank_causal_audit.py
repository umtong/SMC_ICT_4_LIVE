from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict
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


def valid(item: dict[str, Any]) -> bool:
    marker = item.get("actual_fill_risk_valid")
    return True if marker is None else bool(marker)


def exit_driver(item: dict[str, Any]) -> str:
    if item.get("notank_exit_driver"):
        return str(item["notank_exit_driver"])
    event = str(item.get("event") or "")
    pnl = number(item.get("realized_pnl"))
    if "STOP_LOSS" in event.upper() or "STOPLOSS" in event.upper():
        return "HARD_STOP"
    if "TAKE_PROFIT" in event.upper() or "TAKEPROFIT" in event.upper():
        return "BRACKET_TARGET"
    return "UNATTRIBUTED_POSITIVE" if pnl > 0.0 else "UNATTRIBUTED_NEGATIVE"


def trade(item: dict[str, Any]) -> dict[str, Any]:
    diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
    return {
        "key": f"{item.get('symbol')}:{item.get('episode_ts')}:{item.get('side')}",
        "symbol": str(item.get("symbol") or "UNKNOWN"),
        "side": int(number(item.get("side"))),
        "episode_ts": int(number(item.get("episode_ts"))),
        "pnl": number(item.get("realized_pnl")),
        "exit_driver": exit_driver(item),
        "entry_mode": str(item.get("notank_entry_mode") or diagnostics.get("entry_mode") or "UNKNOWN"),
        "management": str(item.get("notank_management_mode") or "UNKNOWN"),
        "mfe_r": number(item.get("notank_mfe_r"), math.nan),
        "mae_r": number(item.get("notank_mae_r"), math.nan),
        "current_r": number(item.get("notank_current_r"), math.nan),
        "elapsed": int(number(item.get("notank_elapsed_minutes"))),
        "pivot_rsi": number(diagnostics.get("pivot_rsi"), math.nan),
        "current_rsi": number(diagnostics.get("current_rsi"), math.nan),
        "pivot_di": number(diagnostics.get("pivot_di"), math.nan),
        "current_di": number(diagnostics.get("current_di"), math.nan),
        "confirmation_move_atr": number(
            diagnostics.get("signed_confirmation_move_atr", diagnostics.get("confirmation_move_atr")),
            math.nan,
        ),
        "reclaim_fraction": number(diagnostics.get("reclaim_fraction"), math.nan),
        "lower_wick_fraction": number(diagnostics.get("lower_wick_fraction"), math.nan),
        "upper_wick_fraction": number(diagnostics.get("upper_wick_fraction"), math.nan),
        "planned_account_loss": number(item.get("planned_account_loss"), math.nan),
    }


def group(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    output = {}
    for name, members in sorted(grouped.items()):
        gp = sum(max(row["pnl"], 0.0) for row in members)
        gl = -sum(min(row["pnl"], 0.0) for row in members)
        output[name] = {
            "trades": len(members),
            "wins": sum(row["pnl"] > 0.0 for row in members),
            "gross_profit_usdt": gp,
            "gross_loss_usdt": gl,
            "net_pnl_usdt": gp - gl,
            "profit_factor": gp / gl if gl else None,
        }
    return output


def path(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "mfe_r_q25": quantile([row["mfe_r"] for row in rows], 0.25),
        "mfe_r_median": quantile([row["mfe_r"] for row in rows], 0.5),
        "mfe_r_q75": quantile([row["mfe_r"] for row in rows], 0.75),
        "mae_r_q25": quantile([row["mae_r"] for row in rows], 0.25),
        "mae_r_median": quantile([row["mae_r"] for row in rows], 0.5),
        "mae_r_q75": quantile([row["mae_r"] for row in rows], 0.75),
        "duration_median_minutes": quantile([float(row["elapsed"]) for row in rows], 0.5),
        "confirmation_move_atr_median": quantile(
            [row["confirmation_move_atr"] for row in rows], 0.5
        ),
        "pivot_rsi_median": quantile([row["pivot_rsi"] for row in rows], 0.5),
        "current_rsi_median": quantile([row["current_rsi"] for row in rows], 0.5),
        "pivot_di_median": quantile([row["pivot_di"] for row in rows], 0.5),
    }


def summarize(rows: list[dict[str, Any]], days: int, starting_nav: float) -> dict[str, Any]:
    winners = [row for row in rows if row["pnl"] > 0.0]
    losers = [row for row in rows if row["pnl"] < 0.0]
    gp = sum(row["pnl"] for row in winners)
    gl = -sum(row["pnl"] for row in losers)
    return {
        "trades": len(rows),
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": len(winners) / len(rows) if rows else 0.0,
        "gross_profit_usdt": gp,
        "gross_loss_usdt": gl,
        "net_pnl_usdt": gp - gl,
        "profit_factor": gp / gl if gl else None,
        "expectancy_usdt": statistics.fmean([row["pnl"] for row in rows]) if rows else 0.0,
        "trades_per_day": len(rows) / max(days, 1),
        "gross_profit_per_day_initial_nav": gp / (max(days, 1) * max(starting_nav, 1.0)),
        "gross_loss_per_day_initial_nav": gl / (max(days, 1) * max(starting_nav, 1.0)),
        "winner_path": path(winners),
        "loser_path": path(losers),
        "by_exit_driver": group(rows, "exit_driver"),
        "by_symbol": group(rows, "symbol"),
        "by_side": group(rows, "side"),
        "by_entry_mode": group(rows, "entry_mode"),
    }


def trace_summary(diagnostics: dict[str, Any]) -> dict[str, Any]:
    trace = diagnostics.get("notank_decision_trace") or []
    reasons = Counter()
    actionable = 0
    selected = 0
    future_rejected = 0
    for episode in trace:
        if not isinstance(episode, dict):
            continue
        for candidate in episode.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            reason = str(candidate.get("reason") or "UNKNOWN")
            reasons[reason] += 1
            actionable += int(bool(candidate.get("actionable")))
            selected += int(bool(candidate.get("allow")) and candidate.get("symbol") == episode.get("selected_symbol"))
            future_rejected += int("FUTURE" in reason)
    return {
        "decision_rows": len(trace),
        "candidate_rows": sum(
            len(item.get("candidates") or []) for item in trace if isinstance(item, dict)
        ),
        "actionable_candidates": actionable,
        "selected_candidates": selected,
        "reason_counts": dict(reasons),
        "future_rejections": future_rejected,
        "duplicate_episode_rejections": int(
            diagnostics.get("notank_duplicate_episode_rejections") or 0
        ),
        "contiguous_signal_rejections": int(
            diagnostics.get("notank_contiguous_signal_rejections") or 0
        ),
    }


def load_run(root: Path, interval: str, variant: str) -> dict[str, Any] | None:
    path = root / interval / variant
    metrics_path = path / "metrics.json"
    diagnostics_path = path / "strategy_diagnostics.json"
    scenarios_path = path / "closed_scenarios.json"
    if not all(item.is_file() for item in (metrics_path, diagnostics_path, scenarios_path)):
        return None
    metrics = json.loads(metrics_path.read_text())
    diagnostics = json.loads(diagnostics_path.read_text())
    scenarios = json.loads(scenarios_path.read_text())
    rows = [trade(item) for item in scenarios if isinstance(item, dict) and valid(item)]
    days = int(metrics.get("calendar_days") or 14)
    starting_nav = number(metrics.get("starting_nav"), 100_000.0)
    return {
        "interval": interval,
        "variant": variant,
        "path": str(path),
        "implementation": {
            "closed_records": len(scenarios),
            "valid_trades": len(rows),
            "invalid_fill_trades": sum(
                isinstance(item, dict) and not valid(item) for item in scenarios
            ),
            "order_rejections": int(diagnostics.get("order_rejections") or 0),
            "global_position_violations": int(
                diagnostics.get("global_position_violations") or 0
            ),
            "max_open_positions_observed": int(
                diagnostics.get("max_open_positions_observed") or 0
            ),
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
                "notank_decisions", "notank_source_candidates", "notank_entry_candidates",
                "entry_submissions", "notank_trailing_activations", "notank_trailing_exits",
                "notank_opposite_pivot_exits", "notank_progress_exits", "notank_exit_counts",
                "selected_symbols", "unresolved_reason_counts",
            )
        },
        "trace": trace_summary(diagnostics),
        "summary": summarize(rows, days, starting_nav),
        "trades": rows,
    }


def compare(control: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    c = {row["key"]: row for row in control["trades"]}
    e = {row["key"]: row for row in experiment["trades"]}
    common = set(c) & set(e)
    c_only = set(c) - set(e)
    e_only = set(e) - set(c)
    c_gp = sum(max(row["pnl"], 0.0) for row in c.values())
    e_gp = sum(max(row["pnl"], 0.0) for row in e.values())
    c_gl = -sum(min(row["pnl"], 0.0) for row in c.values())
    e_gl = -sum(min(row["pnl"], 0.0) for row in e.values())
    pairs = [(c[key], e[key]) for key in common]
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
        "control_winners": sum(row[0]["pnl"] > 0.0 for row in pairs),
        "control_winners_still_positive": sum(
            left["pnl"] > 0.0 and right["pnl"] > 0.0 for left, right in pairs
        ),
        "control_losers": sum(row[0]["pnl"] < 0.0 for row in pairs),
        "control_losers_rescued_positive": sum(
            left["pnl"] < 0.0 and right["pnl"] > 0.0 for left, right in pairs
        ),
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
    by_interval: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for interval in manifest["intervals"]:
        for variant in manifest["variants"]:
            loaded = load_run(args.root, interval, variant)
            if loaded is None:
                rows.append({
                    "interval": interval,
                    "variant": variant,
                    "implementation": {"classification": "OUTPUT_MISSING"},
                })
                continue
            rows.append(loaded)
            by_interval[interval][variant] = loaded
            by_variant[variant].append(loaded)
    comparisons = []
    for interval, lookup in sorted(by_interval.items()):
        for control, experiments in manifest["control_groups"].items():
            if control not in lookup:
                continue
            for experiment in experiments:
                if experiment in lookup:
                    comparisons.append(compare(lookup[control], lookup[experiment]))
    output = {
        "purpose": (
            "Causal reconstruction of the public NOTank extrema system. The retrospective "
            "future-labelled source result is not evidence; confirmation delay, rolling "
            "rejection, loss geometry, no-trades, arbitration and management are decomposed."
        ),
        "accounting_warning": "Independent accounts are not stitched into a continuous NAV.",
        "manifest": manifest,
        "runs": rows,
        "variant_anatomy": {
            variant: {
                "independent_account_count": len(members),
                "accounts": [
                    {
                        "interval": row["interval"],
                        "implementation": row["implementation"],
                        "reported_metrics": row["reported_metrics"],
                        "summary": row["summary"],
                        "diagnostics": row["diagnostics"],
                        "trace": row["trace"],
                    }
                    for row in members
                ],
            }
            for variant, members in sorted(by_variant.items())
        },
        "paired_mechanism_comparisons": comparisons,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Causal NOTank extrema anatomy",
        "",
        "The public argrelextrema label needs five future 15-minute candles. These rows contain only decisions available at the actual timestamp.",
        "",
        "| interval | variant | trades | wins | GP | GL | PF | ending NAV | daily geom |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if "summary" not in row:
            lines.append(f"| {row['interval']} | {row['variant']} | missing | | | | | | |")
            continue
        summary = row["summary"]
        metrics = row["reported_metrics"]
        lines.append(
            f"| {row['interval']} | {row['variant']} | {summary['trades']} | "
            f"{summary['wins']} | {summary['gross_profit_usdt']:.2f} | "
            f"{summary['gross_loss_usdt']:.2f} | "
            f"{summary['profit_factor'] if summary['profit_factor'] is not None else 'inf'} | "
            f"{metrics.get('ending_nav')} | {metrics.get('geometric_daily_growth')} |"
        )
    args.output_md.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
