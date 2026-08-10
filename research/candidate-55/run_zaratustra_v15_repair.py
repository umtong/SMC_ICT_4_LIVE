"""Loss-engine repair tournament for the high-capacity V15 short family.

This runner does not discard a family because its net PnL is negative.  It
measures the opportunity/profit engine and loss engine separately, compares the
unchanged source against causal repairs, and spends longer replays only when a
repair preserves gross-profit capacity while materially reducing gross loss.
"""
from __future__ import annotations

import copy
from datetime import date
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-repair"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-repair"

SHORT_WINDOWS = {
    "fresh-2026-03": ("2026-03-01", "2026-03-07"),
    "fresh-2025-02": ("2025-02-10", "2025-02-16"),
}
EXTENSION_WINDOW = ("2025-09-01", "2025-09-14")
MEDIUM_WINDOW = ("2024-10-01", "2024-10-30")
LONG_WINDOW = ("2024-03-01", "2024-05-29")

VARIANTS: dict[str, dict[str, Any]] = {
    "source": {
        "v15_repair_mode": "source",
        "v15_failure_min_age_minutes": 20,
        "v15_failure_adverse_fraction": 0.002,
        "v15_failure_mfe_cap_fraction": 0.003,
        "v15_failure_window_minutes": 10,
        "v15_failure_flow_3m_min": 0.0,
        "v15_failure_return_bps_min": 0.0,
    },
    "confirm": {
        "v15_repair_mode": "confirm",
        "v15_failure_min_age_minutes": 20,
        "v15_failure_adverse_fraction": 0.002,
        "v15_failure_mfe_cap_fraction": 0.003,
        "v15_failure_window_minutes": 10,
        "v15_failure_flow_3m_min": 0.0,
        "v15_failure_return_bps_min": 0.0,
    },
    "repair20": {
        "v15_repair_mode": "confirm_di_fail20",
        "v15_failure_min_age_minutes": 20,
        "v15_failure_adverse_fraction": 0.002,
        "v15_failure_mfe_cap_fraction": 0.003,
        "v15_failure_window_minutes": 10,
        "v15_failure_flow_3m_min": 0.0,
        "v15_failure_return_bps_min": 0.0,
    },
    "repair30": {
        "v15_repair_mode": "confirm_di_fail30",
        "v15_failure_min_age_minutes": 30,
        "v15_failure_adverse_fraction": 0.002,
        "v15_failure_mfe_cap_fraction": 0.003,
        "v15_failure_window_minutes": 5,
        "v15_failure_flow_3m_min": 0.0,
        "v15_failure_return_bps_min": 1.5,
    },
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def days(interval: tuple[str, str]) -> int:
    start = date.fromisoformat(interval[0])
    end = date.fromisoformat(interval[1])
    return (end - start).days + 1


def pnl_number(value: Any) -> float:
    text = str(value).strip().split()[0].replace("_", "").replace(",", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def create_config(variant: str) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    source = json.loads((REUSED / "config.json").read_text(encoding="utf-8"))
    config = copy.deepcopy(source)
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        config["strategy"].pop(key, None)
    config["strategy"].update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 1_000_000,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "zaratustra_variant": "edge_exact",
            "zaratustra_startup_30m_candles": 10,
            "zaratustra_rsi_period": 14,
            "zaratustra_di_period": 14,
            "zaratustra_bb_period": 20,
            "zaratustra_source_leverage": 10.0,
            "zaratustra_source_stoploss": 0.15,
            "zaratustra_trailing_positive": 0.012,
            "zaratustra_trailing_offset": 0.107,
            "zaratustra_emergency_target_fraction": 0.50,
            "v15_confirmation_delay_minutes": 1,
            "v15_entry_flow_3m_max": 0.0,
            **VARIANTS[variant],
        }
    )
    path = WORK / f"config-{variant}.json"
    dump(path, config)
    return path


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-repair-{variant}-{stage}"


def run_backtest(
    config: Path,
    variant: str,
    stage: str,
    interval: tuple[str, str],
) -> int:
    output = output_root(variant, stage)
    workspace = WORK / variant / stage
    command = [
        sys.executable,
        str(REUSED / "launch.py"),
        "--config",
        str(config),
        "--start",
        interval[0],
        "--end",
        interval[1],
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    env = dict(os.environ)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(REUSED)
        if not previous
        else str(REUSED) + os.pathsep + previous
    )
    print("RUN", variant, stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    return int(completed.returncode)


def grouped_trade_engine(root: Path) -> dict[str, Any]:
    path = root / "closed_scenarios.json"
    if not path.is_file():
        return {"closed": 0, "by_component": {}, "by_symbol": {}}
    rows = json.loads(path.read_text(encoding="utf-8"))

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        pnls = [pnl_number(item.get("realized_pnl")) for item in items]
        gross_profit = sum(value for value in pnls if value > 0.0)
        gross_loss = -sum(value for value in pnls if value < 0.0)
        return {
            "trades": len(pnls),
            "wins": sum(value > 0.0 for value in pnls),
            "losses": sum(value < 0.0 for value in pnls),
            "net_pnl": sum(pnls),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": (
                gross_profit / gross_loss
                if gross_loss > 0.0
                else (None if gross_profit > 0.0 else 0.0)
            ),
            "average_winner": (
                gross_profit / sum(value > 0.0 for value in pnls)
                if any(value > 0.0 for value in pnls)
                else 0.0
            ),
            "average_loser": (
                gross_loss / sum(value < 0.0 for value in pnls)
                if any(value < 0.0 for value in pnls)
                else 0.0
            ),
        }

    by_component: dict[str, list[dict[str, Any]]] = {}
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        diagnostics = row.get("diagnostics", {})
        bb = int(diagnostics.get("used_bb_component", 0))
        di = int(diagnostics.get("used_di_component", 0))
        component = (
            "BOTH" if bb and di else "BB" if bb else "DI" if di else "OTHER"
        )
        by_component.setdefault(component, []).append(row)
        by_symbol.setdefault(str(row.get("symbol")), []).append(row)
    return {
        "closed": len(rows),
        "all": summarize(rows),
        "by_component": {
            key: summarize(value) for key, value in sorted(by_component.items())
        },
        "by_symbol": {
            key: summarize(value) for key, value in sorted(by_symbol.items())
        },
    }


def event_counts(root: Path) -> dict[str, int]:
    path = root / "scenario_events.jsonl"
    counts: dict[str, int] = {}
    if not path.is_file():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = str(row.get("event_type"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def read_result(
    variant: str,
    stage: str,
    interval: tuple[str, str],
    returncode: int,
) -> dict[str, Any]:
    root = output_root(variant, stage)
    metrics_path = root / "metrics.json"
    diagnostics_path = root / "strategy_diagnostics.json"
    if not metrics_path.is_file() or not diagnostics_path.is_file():
        return {
            "variant": variant,
            "stage": stage,
            "interval": list(interval),
            "days": days(interval),
            "produced": False,
            "returncode": returncode,
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    keys = (
        "starting_nav",
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "max_drawdown",
        "min_equity",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "gross_profit",
        "gross_loss",
        "profit_factor",
        "expectancy_usdt",
        "largest_winner_share",
        "position_counts_by_symbol",
    )
    result: dict[str, Any] = {
        "variant": variant,
        "stage": stage,
        "interval": list(interval),
        "days": days(interval),
        "produced": True,
        "returncode": returncode,
        "artifact_root": str(root.relative_to(ROOT)),
        **{key: metrics.get(key) for key in keys},
        "trades_per_day": float(metrics.get("trades") or 0) / days(interval),
        "gross_profit_per_day": float(metrics.get("gross_profit") or 0.0)
        / days(interval),
        "gross_loss_per_day": float(metrics.get("gross_loss") or 0.0)
        / days(interval),
        "entry_submissions": diagnostics.get("entry_submissions"),
        "source_signals": diagnostics.get(
            "source_signals_before_execution_filters"
        ),
        "confirmation_candidates": diagnostics.get("confirmation_candidates"),
        "confirmation_entries": diagnostics.get("confirmation_entries"),
        "confirmation_rejections": diagnostics.get(
            "confirmation_rejections"
        ),
        "failed_auction_checks": diagnostics.get("failed_auction_checks"),
        "failed_auction_exits": diagnostics.get("failed_auction_exits"),
        "order_rejections": diagnostics.get("order_rejections"),
        "global_position_violations": diagnostics.get(
            "global_position_violations"
        ),
        "max_open_positions_observed": diagnostics.get(
            "max_open_positions_observed"
        ),
        "mechanically_valid": (
            int(diagnostics.get("order_rejections", 0)) == 0
            and int(diagnostics.get("global_position_violations", 0)) == 0
            and int(diagnostics.get("max_open_positions_observed", 0)) <= 1
            and float(metrics.get("min_equity") or 0.0) > 0.0
        ),
        "trade_engine": grouped_trade_engine(root),
        "event_counts": event_counts(root),
    }
    return result


def aggregate(
    rows: list[dict[str, Any]], variant: str
) -> dict[str, Any]:
    selected = [
        row for row in rows if row.get("variant") == variant and row.get("produced")
    ]
    total_days = sum(int(row["days"]) for row in selected)
    gross_profit = sum(float(row.get("gross_profit") or 0.0) for row in selected)
    gross_loss = sum(float(row.get("gross_loss") or 0.0) for row in selected)
    net = gross_profit - gross_loss
    trades = sum(int(row.get("trades") or 0) for row in selected)
    return {
        "variant": variant,
        "windows": len(selected),
        "days": total_days,
        "trades": trades,
        "trades_per_day": trades / total_days if total_days else 0.0,
        "positive_windows": sum(
            float(row.get("ending_nav") or 0.0)
            > float(row.get("starting_nav") or 0.0)
            for row in selected
        ),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": net,
        "gross_profit_per_day": (
            gross_profit / total_days if total_days else 0.0
        ),
        "gross_loss_per_day": gross_loss / total_days if total_days else 0.0,
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0.0
            else (None if gross_profit > 0.0 else 0.0)
        ),
        "mechanically_valid": all(
            bool(row.get("mechanically_valid")) for row in selected
        ),
    }


def comparison(
    source: dict[str, Any], repaired: dict[str, Any]
) -> dict[str, Any]:
    source_gp = float(source.get("gross_profit") or 0.0)
    source_gl = float(source.get("gross_loss") or 0.0)
    source_trades = int(source.get("trades") or 0)
    return {
        "net_improvement": float(repaired.get("net_pnl") or 0.0)
        - float(source.get("net_pnl") or 0.0),
        "gross_profit_retention": (
            float(repaired.get("gross_profit") or 0.0) / source_gp
            if source_gp > 0.0
            else 0.0
        ),
        "gross_loss_reduction": (
            1.0 - float(repaired.get("gross_loss") or 0.0) / source_gl
            if source_gl > 0.0
            else 0.0
        ),
        "trade_retention": (
            int(repaired.get("trades") or 0) / source_trades
            if source_trades > 0
            else 0.0
        ),
    }


def research_order(
    aggregates: dict[str, dict[str, Any]]
) -> list[str]:
    repairs = [name for name in VARIANTS if name != "source"]
    return sorted(
        repairs,
        key=lambda name: (
            int(aggregates[name]["positive_windows"]),
            float(aggregates[name]["net_pnl"]),
            float(aggregates[name]["profit_factor"] or 0.0),
            int(aggregates[name]["trades"]),
        ),
        reverse=True,
    )


def run_one(
    configs: dict[str, Path],
    rows: list[dict[str, Any]],
    variant: str,
    stage: str,
    interval: tuple[str, str],
) -> None:
    code = run_backtest(configs[variant], variant, stage, interval)
    rows.append(read_result(variant, stage, interval, code))


def main() -> int:
    configs = {name: create_config(name) for name in VARIANTS}
    rows: list[dict[str, Any]] = []

    # Cheap mechanism diagnosis: every variant on two unrelated seven-day tapes.
    for stage, interval in SHORT_WINDOWS.items():
        for variant in VARIANTS:
            run_one(configs, rows, variant, stage, interval)

    initial = {name: aggregate(rows, name) for name in VARIANTS}
    ordered = research_order(initial)
    extension_variants = ["source", *ordered[:2]]
    for variant in extension_variants:
        run_one(configs, rows, variant, "extension-2025-09", EXTENSION_WINDOW)

    extended = {name: aggregate(rows, name) for name in extension_variants}
    source = extended["source"]
    repair_names = [name for name in extension_variants if name != "source"]
    best_repair = max(
        repair_names,
        key=lambda name: (
            int(extended[name]["positive_windows"]),
            float(extended[name]["net_pnl"]),
            float(extended[name]["profit_factor"] or 0.0),
            int(extended[name]["trades"]),
        ),
    )
    best = extended[best_repair]
    short_comparison = comparison(source, best)

    structural_capacity = {
        "best_repair": best_repair,
        "aggregate_net_positive": float(best["net_pnl"]) > 0.0,
        "improves_source_net": float(short_comparison["net_improvement"]) > 0.0,
        "retains_at_least_55pct_gross_profit": float(
            short_comparison["gross_profit_retention"]
        )
        >= 0.55,
        "cuts_at_least_20pct_gross_loss": float(
            short_comparison["gross_loss_reduction"]
        )
        >= 0.20,
        "at_least_one_trade_per_day": float(best["trades_per_day"]) >= 1.0,
        "gross_profit_capacity_at_least_0_8pct_nav_per_day": float(
            best["gross_profit_per_day"]
        )
        >= 800.0,
        "mechanically_valid": bool(best["mechanically_valid"]),
    }
    run_medium = all(
        bool(value)
        for key, value in structural_capacity.items()
        if key != "best_repair"
    )

    medium_rows: list[dict[str, Any]] = []
    medium_comparison: dict[str, Any] = {}
    if run_medium:
        for variant in ("source", best_repair):
            run_one(
                configs,
                medium_rows,
                variant,
                "medium-2024-10",
                MEDIUM_WINDOW,
            )
        rows.extend(medium_rows)
        source_medium = aggregate(medium_rows, "source")
        repair_medium = aggregate(medium_rows, best_repair)
        medium_comparison = comparison(source_medium, repair_medium)
        medium_comparison.update(
            {
                "source": source_medium,
                "repair": repair_medium,
            }
        )

    long_capacity = {
        "medium_was_run": bool(medium_rows),
        "medium_net_positive": False,
        "medium_improves_source": False,
        "medium_pf_at_least_1_25": False,
        "medium_daily_growth_at_least_0_5pct": False,
        "medium_trades_at_least_days": False,
        "medium_gross_profit_capacity_at_least_1pct_nav_per_day": False,
        "medium_profit_retention_at_least_55pct": False,
        "medium_loss_reduction_at_least_20pct": False,
    }
    if medium_rows:
        repair_row = next(
            row
            for row in medium_rows
            if row["variant"] == best_repair and row["produced"]
        )
        long_capacity.update(
            {
                "medium_net_positive": float(
                    repair_row.get("ending_nav") or 0.0
                )
                > float(repair_row.get("starting_nav") or 0.0),
                "medium_improves_source": float(
                    medium_comparison.get("net_improvement") or 0.0
                )
                > 0.0,
                "medium_pf_at_least_1_25": float(
                    repair_row.get("profit_factor") or 0.0
                )
                >= 1.25,
                "medium_daily_growth_at_least_0_5pct": float(
                    repair_row.get("geometric_daily_growth") or 0.0
                )
                >= 0.005,
                "medium_trades_at_least_days": int(
                    repair_row.get("trades") or 0
                )
                >= days(MEDIUM_WINDOW),
                "medium_gross_profit_capacity_at_least_1pct_nav_per_day": float(
                    repair_row.get("gross_profit_per_day") or 0.0
                )
                >= 1_000.0,
                "medium_profit_retention_at_least_55pct": float(
                    medium_comparison.get("gross_profit_retention") or 0.0
                )
                >= 0.55,
                "medium_loss_reduction_at_least_20pct": float(
                    medium_comparison.get("gross_loss_reduction") or 0.0
                )
                >= 0.20,
            }
        )

    run_long = all(long_capacity.values())
    long_result: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "medium evidence did not justify long replay",
    }
    if run_long:
        run_one(
            configs,
            rows,
            best_repair,
            "long-2024-03_05",
            LONG_WINDOW,
        )
        long_result = rows[-1]

    final = {
        "candidate": "candidate-55",
        "research_question": (
            "Can the V15 short opportunity/profit engine be preserved while "
            "contradicted entries and DI failed-auction stops are removed?"
        ),
        "known_reason_for_repair": {
            "fresh_2026_04_trades": 76,
            "fresh_2026_04_gross_profit": 53875.38727796,
            "fresh_2026_04_gross_loss": 82234.05672169,
            "fresh_2026_04_di_net_loss": -22471.42731,
            "interpretation": (
                "Gross-profit capacity was target-scale, but DI-only failed "
                "auctions consumed near-full 3% risk repeatedly."
            ),
        },
        "variant_definitions": VARIANTS,
        "all_runs": rows,
        "initial_aggregates": initial,
        "extension_variants": extension_variants,
        "extended_aggregates": extended,
        "best_repair": best_repair,
        "short_comparison_to_source": short_comparison,
        "structural_capacity_for_medium": structural_capacity,
        "medium_comparison": medium_comparison,
        "long_capacity": long_capacity,
        "long_result": long_result,
        "long_replay_consumed": bool(long_result.get("produced")),
        "production_ready": bool(
            long_result.get("produced")
            and float(long_result.get("geometric_daily_growth") or 0.0) >= 0.01
            and int(long_result.get("trades") or 0) >= days(LONG_WINDOW)
            and float(long_result.get("expectancy_usdt") or 0.0) > 0.0
            and float(long_result.get("max_drawdown") or 1.0) <= 0.20
        ),
    }
    dump(ARTIFACTS / "zaratustra-v15-repair-final-result.json", final)
    print(json.dumps(final, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
