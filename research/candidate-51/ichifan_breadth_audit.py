from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

NUM = re.compile(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?")


def num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
        return value if math.isfinite(value) else default
    match = NUM.search(str(value).replace("_", ""))
    if not match:
        return default
    try:
        value = float(match.group().replace(",", ""))
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def valid(item: dict[str, Any]) -> bool:
    marker = item.get("actual_fill_risk_valid")
    return True if marker is None else bool(marker)


def trade(item: dict[str, Any]) -> dict[str, Any]:
    diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
    active_count = int(
        num(
            item.get("ichifan_active_source_count"),
            num(diagnostics.get("ichifan_active_source_count"), 1.0),
        )
    )
    episode = str(
        item.get("causal_episode_id")
        or diagnostics.get("causal_episode_id")
        or f"{item.get('symbol')}:{item.get('episode_ts')}:{item.get('side')}"
    )
    return {
        "key": episode,
        "symbol": str(item.get("symbol") or "UNKNOWN"),
        "pnl": num(item.get("realized_pnl")),
        "active_count": active_count,
        "active_symbols": item.get("ichifan_active_source_symbols") or [],
    }


def grouped(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    output = {}
    for name, items in sorted(groups.items()):
        gp = sum(max(item["pnl"], 0.0) for item in items)
        gl = -sum(min(item["pnl"], 0.0) for item in items)
        output[name] = {
            "trades": len(items),
            "wins": sum(item["pnl"] > 0.0 for item in items),
            "gross_profit_usdt": gp,
            "gross_loss_usdt": gl,
            "net_pnl_usdt": gp - gl,
            "profit_factor": gp / gl if gl else None,
        }
    return output


def summarize(rows: list[dict[str, Any]], days: int) -> dict[str, Any]:
    gp = sum(max(row["pnl"], 0.0) for row in rows)
    gl = -sum(min(row["pnl"], 0.0) for row in rows)
    return {
        "trades": len(rows),
        "wins": sum(row["pnl"] > 0.0 for row in rows),
        "losses": sum(row["pnl"] < 0.0 for row in rows),
        "gross_profit_usdt": gp,
        "gross_loss_usdt": gl,
        "net_pnl_usdt": gp - gl,
        "profit_factor": gp / gl if gl else None,
        "expectancy_usdt": statistics.fmean(row["pnl"] for row in rows) if rows else 0.0,
        "trades_per_day": len(rows) / max(days, 1),
        "by_active_source_count": grouped(rows, "active_count"),
        "by_symbol": grouped(rows, "symbol"),
    }


def load(root: Path, interval: str, variant: str) -> dict[str, Any] | None:
    path = root / interval / variant
    files = [path / name for name in ("metrics.json", "strategy_diagnostics.json", "closed_scenarios.json")]
    if not all(item.is_file() for item in files):
        return None
    metrics = json.loads(files[0].read_text())
    diagnostics = json.loads(files[1].read_text())
    scenarios = json.loads(files[2].read_text())
    trades = [trade(item) for item in scenarios if isinstance(item, dict) and valid(item)]
    invalid = sum(isinstance(item, dict) and not valid(item) for item in scenarios)
    return {
        "interval": interval,
        "variant": variant,
        "implementation": {
            "closed_records": len(scenarios),
            "valid_trades": len(trades),
            "invalid_fill_trades": invalid,
            "order_rejections": int(diagnostics.get("order_rejections") or 0),
            "global_position_violations": int(diagnostics.get("global_position_violations") or 0),
            "structural_stop_failures": int(diagnostics.get("ichifan_structural_stop_failures") or 0),
        },
        "reported": {key: metrics.get(key) for key in (
            "ending_nav", "total_return", "geometric_daily_growth", "max_drawdown",
            "trades", "wins", "losses", "profit_factor", "expectancy_usdt",
        )},
        "diagnostics": {key: diagnostics.get(key) for key in (
            "ichifan_entry_candidates", "ichifan_rising_edge_candidates", "entry_submissions",
            "ichifan_breadth_evaluations", "ichifan_breadth_rejections",
            "ichifan_breadth_passes", "ichifan_selected_breadth_counts",
            "ichifan_selected_active_peers", "selected_symbols",
        )},
        "summary": summarize(trades, int(metrics.get("calendar_days") or 7)),
        "trades": trades,
    }


def compare(control: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    c = {row["key"]: row for row in control["trades"]}
    e = {row["key"]: row for row in experiment["trades"]}
    common = set(c) & set(e)
    c_only = set(c) - set(e)
    e_only = set(e) - set(c)
    c_gp = sum(max(row["pnl"], 0) for row in c.values())
    e_gp = sum(max(row["pnl"], 0) for row in e.values())
    c_gl = -sum(min(row["pnl"], 0) for row in c.values())
    e_gl = -sum(min(row["pnl"], 0) for row in e.values())
    removed = [c[key] for key in c_only]
    return {
        "interval": control["interval"],
        "common_episodes": len(common),
        "control_only_episodes": len(c_only),
        "experiment_only_episodes": len(e_only),
        "gross_profit_preservation": e_gp / c_gp if c_gp else None,
        "gross_loss_reduction": 1 - e_gl / c_gl if c_gl else None,
        "net_pnl_change_usdt": sum(row["pnl"] for row in e.values()) - sum(row["pnl"] for row in c.values()),
        "removed_episode_net_pnl_usdt": sum(row["pnl"] for row in removed),
        "removed_winners": sum(row["pnl"] > 0 for row in removed),
        "removed_losers": sum(row["pnl"] < 0 for row in removed),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [trade for row in rows for trade in row["trades"]]
    result = summarize(trades, 7 * len(rows))
    returns = [num(row["reported"].get("total_return"), math.nan) for row in rows]
    gdg = [num(row["reported"].get("geometric_daily_growth"), math.nan) for row in rows]
    drawdown = [num(row["reported"].get("max_drawdown"), math.nan) for row in rows]
    result.update(
        {
            "independent_account_count": len(rows),
            "positive_account_count": sum(value > 0 for value in returns),
            "median_account_return": statistics.median(returns) if returns else None,
            "median_account_geometric_daily_growth": statistics.median(gdg) if gdg else None,
            "worst_account_return": min(returns) if returns else None,
            "best_account_return": max(returns) if returns else None,
            "worst_account_drawdown": max(drawdown) if drawdown else None,
            "zero_global_position_violations": all(row["implementation"]["global_position_violations"] == 0 for row in rows),
            "zero_order_rejections": all(row["implementation"]["order_rejections"] == 0 for row in rows),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    runs = []
    for interval in manifest["intervals"]:
        for variant in manifest["variants"]:
            runs.append(load(args.root, interval, variant) or {
                "interval": interval,
                "variant": variant,
                "implementation": {"classification": "OUTPUT_MISSING"},
            })
    complete = [row for row in runs if "summary" in row]
    by_interval: defaultdict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in complete:
        by_interval[row["interval"]][row["variant"]] = row
        by_variant[row["variant"]].append(row)
    comparisons = []
    for interval, lookup in sorted(by_interval.items()):
        if "breadth1_source_control" in lookup and "breadth2_static_context" in lookup:
            comparisons.append(compare(lookup["breadth1_source_control"], lookup["breadth2_static_context"]))
    result = {
        "purpose": "Non-binary anatomy of a fixed static same-direction entry context.",
        "accounting_warning": manifest["accounting_warning"],
        "manifest": manifest,
        "all_runs_present": len(complete) == len(runs),
        "runs": runs,
        "variant_aggregate": {name: aggregate(rows) for name, rows in sorted(by_variant.items())},
        "paired_mechanism_comparisons": comparisons,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Candidate 51 IchiFan static breadth anatomy",
        "",
        "Independent accounts are not stitched into a continuous NAV.",
        "",
        "| interval | variant | trades | wins | GP | GL | PF | net | return | GDG | DD | rejected |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in runs:
        if "summary" not in row:
            lines.append(f"| {row['interval']} | `{row['variant']}` | MISSING | | | | | | | | | |")
            continue
        s, r, d = row["summary"], row["reported"], row["diagnostics"]
        pf = s["profit_factor"]
        pf_text = "∞" if pf is None and s["gross_profit_usdt"] > 0 else (f"{pf:.3f}" if pf is not None else "0.000")
        lines.append(
            f"| {row['interval']} | `{row['variant']}` | {s['trades']} | {s['wins']} | "
            f"{s['gross_profit_usdt']:.2f} | {s['gross_loss_usdt']:.2f} | {pf_text} | "
            f"{s['net_pnl_usdt']:.2f} | {num(r.get('total_return')):.4f} | "
            f"{num(r.get('geometric_daily_growth')):.4f} | {num(r.get('max_drawdown')):.4f} | "
            f"{int(d.get('ichifan_breadth_rejections') or 0)} |"
        )
    lines += [
        "",
        "## Variant aggregate",
        "",
        "| variant | positive accounts | trades | trades/day | wins | PF | net | median return | median GDG | worst return | worst DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in sorted(result["variant_aggregate"].items()):
        pf = s["profit_factor"]
        pf_text = "∞" if pf is None and s["gross_profit_usdt"] > 0 else (f"{pf:.3f}" if pf is not None else "0.000")
        lines.append(
            f"| `{name}` | {s['positive_account_count']}/{s['independent_account_count']} | "
            f"{s['trades']} | {s['trades_per_day']:.3f} | {s['wins']} | {pf_text} | "
            f"{s['net_pnl_usdt']:.2f} | {s['median_account_return'] or 0:.4f} | "
            f"{s['median_account_geometric_daily_growth'] or 0:.4f} | "
            f"{s['worst_account_return'] or 0:.4f} | {s['worst_account_drawdown'] or 0:.4f} |"
        )
    lines += [
        "",
        "## Paired breadth trade-offs",
        "",
        "| interval | common | removed | added | GP preservation | GL reduction | removed winners | removed losers | removed PnL | net change |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparisons:
        lines.append(
            f"| {row['interval']} | {row['common_episodes']} | {row['control_only_episodes']} | "
            f"{row['experiment_only_episodes']} | {row['gross_profit_preservation'] or 0:.3f} | "
            f"{row['gross_loss_reduction'] or 0:.3f} | {row['removed_winners']} | "
            f"{row['removed_losers']} | {row['removed_episode_net_pnl_usdt']:.2f} | "
            f"{row['net_pnl_change_usdt']:.2f} |"
        )
    args.output_md.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
