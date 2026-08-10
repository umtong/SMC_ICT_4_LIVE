"""Short-first tournament for the V15 cross-sectional auction router."""
from __future__ import annotations

import copy
from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-relative"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-relative"

_SPEC = importlib.util.spec_from_file_location(
    "candidate55_v15_relative_helpers",
    CANDIDATE / "run_zaratustra_v15_repair.py",
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load shared Candidate 55 result helpers")
_HELPER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _HELPER
_SPEC.loader.exec_module(_HELPER)


def output_root(variant: str, stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-relative-{variant}-{stage}"


_HELPER.output_root = output_root
SHORT_WINDOWS = {
    "fresh-2026-03": ("2026-03-01", "2026-03-07"),
    "fresh-2025-02": ("2025-02-10", "2025-02-16"),
}
EXTENSION_WINDOW = ("2025-09-01", "2025-09-14")
MEDIUM_WINDOW = ("2024-10-01", "2024-10-30")
LONG_WINDOW = ("2024-03-01", "2024-05-29")
VARIANTS: dict[str, dict[str, Any]] = {
    "source_both": {
        "v15_relative_mode": "source_both",
        "v15_relative_min_fraction": 0.001,
        "v15_relative_max_fraction": 0.004,
    },
    "bb_relative_short": {
        "v15_relative_mode": "bb_relative_short",
        "v15_relative_min_fraction": 0.001,
        "v15_relative_max_fraction": 0.004,
    },
    "bb_relative_both": {
        "v15_relative_mode": "bb_relative_both",
        "v15_relative_min_fraction": 0.001,
        "v15_relative_max_fraction": 0.004,
    },
    "bb_relative_basis_both": {
        "v15_relative_mode": "bb_relative_basis_both",
        "v15_relative_min_fraction": 0.001,
        "v15_relative_max_fraction": 0.004,
        "v15_short_min_premium_change_5m": -0.00005,
        "v15_long_max_premium_change_5m": 0.00005,
    },
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def days(interval: tuple[str, str]) -> int:
    return (
        date.fromisoformat(interval[1]) - date.fromisoformat(interval[0])
    ).days + 1


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
            "v15_relative_lookback_minutes": 60,
            "v15_short_min_premium_change_5m": -0.00005,
            "v15_long_max_premium_change_5m": 0.00005,
            **VARIANTS[variant],
        }
    )
    path = WORK / f"config-{variant}.json"
    dump(path, config)
    return path


def run_backtest(
    config: Path,
    variant: str,
    stage: str,
    interval: tuple[str, str],
) -> int:
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
        str(output_root(variant, stage)),
        "--workspace",
        str(WORK / variant / stage),
    ]
    env = dict(os.environ)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(REUSED)
        if not previous
        else str(REUSED) + os.pathsep + previous
    )
    print("RUN", variant, stage, interval, flush=True)
    return int(subprocess.run(command, env=env, check=False).returncode)


def read_result(
    variant: str,
    stage: str,
    interval: tuple[str, str],
    code: int,
) -> dict[str, Any]:
    result = _HELPER.read_result(variant, stage, interval, code)
    path = output_root(variant, stage) / "strategy_diagnostics.json"
    if result.get("produced") and path.is_file():
        diagnostics = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "relative_source_actionable",
            "relative_component_rejections",
            "relative_direction_rejections",
            "relative_feature_stale",
            "relative_band_rejections",
            "relative_basis_rejections",
            "relative_eligible",
            "relative_alternative_symbol_selected",
        ):
            result[key] = diagnostics.get(key)
    return result


def run_one(
    configs: dict[str, Path],
    rows: list[dict[str, Any]],
    variant: str,
    stage: str,
    interval: tuple[str, str],
) -> None:
    code = run_backtest(configs[variant], variant, stage, interval)
    rows.append(read_result(variant, stage, interval, code))


def aggregate(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    return _HELPER.aggregate(rows, variant)


def compare(source: dict[str, Any], repair: dict[str, Any]) -> dict[str, Any]:
    return _HELPER.comparison(source, repair)


def rank_variants(aggregates: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        [name for name in aggregates if name != "source_both"],
        key=lambda name: (
            int(aggregates[name]["positive_windows"]),
            float(aggregates[name]["net_pnl"]),
            float(aggregates[name]["profit_factor"] or 0.0),
            int(aggregates[name]["trades"]),
        ),
        reverse=True,
    )


def main() -> int:
    configs = {name: create_config(name) for name in VARIANTS}
    rows: list[dict[str, Any]] = []
    for stage, interval in SHORT_WINDOWS.items():
        for variant in VARIANTS:
            run_one(configs, rows, variant, stage, interval)

    initial = {name: aggregate(rows, name) for name in VARIANTS}
    order = rank_variants(initial)
    extension_variants = ["source_both", *order[:2]]
    for variant in extension_variants:
        run_one(configs, rows, variant, "extension-2025-09", EXTENSION_WINDOW)

    extended = {name: aggregate(rows, name) for name in extension_variants}
    source = extended["source_both"]
    best_name = max(
        [name for name in extension_variants if name != "source_both"],
        key=lambda name: (
            int(extended[name]["positive_windows"]),
            float(extended[name]["net_pnl"]),
            float(extended[name]["profit_factor"] or 0.0),
            int(extended[name]["trades"]),
        ),
    )
    best = extended[best_name]
    short_compare = compare(source, best)
    medium_capacity = {
        "best_variant": best_name,
        "aggregate_net_positive": float(best["net_pnl"]) > 0.0,
        "improves_source_net": float(short_compare["net_improvement"]) > 0.0,
        "gross_profit_retention_at_least_45pct": float(
            short_compare["gross_profit_retention"]
        )
        >= 0.45,
        "gross_loss_reduction_at_least_40pct": float(
            short_compare["gross_loss_reduction"]
        )
        >= 0.40,
        "at_least_0_6_trade_per_day": float(best["trades_per_day"]) >= 0.6,
        "gross_profit_capacity_at_least_0_6pct_nav_per_day": float(
            best["gross_profit_per_day"]
        )
        >= 600.0,
        "mechanically_valid": bool(best["mechanically_valid"]),
    }
    run_medium = all(
        bool(value)
        for key, value in medium_capacity.items()
        if key != "best_variant"
    )

    medium_rows: list[dict[str, Any]] = []
    medium_compare: dict[str, Any] = {}
    if run_medium:
        for variant in ("source_both", best_name):
            run_one(
                configs,
                medium_rows,
                variant,
                "medium-2024-10",
                MEDIUM_WINDOW,
            )
        rows.extend(medium_rows)
        source_medium = aggregate(medium_rows, "source_both")
        repair_medium = aggregate(medium_rows, best_name)
        medium_compare = compare(source_medium, repair_medium)
        medium_compare.update(
            {"source": source_medium, "repair": repair_medium}
        )

    long_capacity = {
        "medium_was_run": bool(medium_rows),
        "medium_net_positive": False,
        "medium_improves_source": False,
        "medium_pf_at_least_1_25": False,
        "medium_daily_growth_at_least_0_5pct": False,
        "medium_trades_at_least_0_6_days": False,
        "medium_gross_profit_capacity_at_least_0_8pct_nav_per_day": False,
    }
    if medium_rows:
        repair_row = next(
            row
            for row in medium_rows
            if row["variant"] == best_name and row["produced"]
        )
        long_capacity.update(
            {
                "medium_net_positive": float(
                    repair_row.get("ending_nav") or 0.0
                )
                > float(repair_row.get("starting_nav") or 0.0),
                "medium_improves_source": float(
                    medium_compare.get("net_improvement") or 0.0
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
                "medium_trades_at_least_0_6_days": float(
                    repair_row.get("trades_per_day") or 0.0
                )
                >= 0.6,
                "medium_gross_profit_capacity_at_least_0_8pct_nav_per_day": float(
                    repair_row.get("gross_profit_per_day") or 0.0
                )
                >= 800.0,
            }
        )

    long_result: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "medium evidence did not justify long replay",
    }
    if all(long_capacity.values()):
        run_one(
            configs,
            rows,
            best_name,
            "long-2024-03_05",
            LONG_WINDOW,
        )
        long_result = rows[-1]

    final = {
        "candidate": "candidate-55",
        "research_question": (
            "Can the V15 Bollinger gross-profit engine be preserved by routing "
            "only moderate cross-sectional followers/leaders and rejecting DI "
            "and exhausted moves?"
        ),
        "known_tape_diagnosis": {
            "source_short_periods": 6,
            "source_short_trades": 187,
            "bb_or_both_residual_band_trades": 44,
            "bb_or_both_residual_band_wins": 35,
            "bb_or_both_residual_band_gross_profit": 51257.181288,
            "bb_or_both_residual_band_gross_loss": 23323.490912,
            "bb_or_both_residual_band_profit_factor": 2.197663,
            "bb_or_both_residual_band_net_pnl": 27933.690376,
            "positive_known_periods": 6,
            "band_plateau": {
                "lower_bounds": [-0.004, -0.0035, -0.003],
                "upper_bounds": [-0.0015, -0.001],
                "all_six_periods_positive": True,
            },
        },
        "mechanism": {
            "component": "BOLLINGER_ORIGIN_OR_BB_PLUS_DI",
            "lookback_minutes": 60,
            "relative_band_fraction": [0.001, 0.004],
            "lower_bound_role": "REQUIRE_ACTUAL_RELATIVE_LEADERSHIP_OR_FOLLOWERSHIP",
            "upper_bound_role": "REJECT_EXHAUSTED_CHASE",
            "source_stop_trailing_unchanged": True,
            "all_symbols_arbitrated_before_selection": True,
        },
        "variant_definitions": VARIANTS,
        "all_runs": rows,
        "initial_aggregates": initial,
        "extension_variants": extension_variants,
        "extended_aggregates": extended,
        "best_variant": best_name,
        "short_comparison_to_source": short_compare,
        "structural_capacity_for_medium": medium_capacity,
        "medium_comparison": medium_compare,
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
    dump(ARTIFACTS / "zaratustra-v15-relative-final-result.json", final)
    print(json.dumps(final, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
