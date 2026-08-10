#!/usr/bin/env python3
"""Conditional tournament for the public Slope-is-Dope one-hour strategy."""
from __future__ import annotations

import copy
from dataclasses import dataclass, asdict
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-slope-is-dope-1h-source-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-slope-is-dope-1h-source-v1"
EVIDENCE = HERE / "evidence" / "slope-is-dope-1h-source-v1"
CACHE = ROOT / ".cache" / "candidate-57-slope-is-dope-1h-source-v1"
FREEZE = HERE / "SLOPE_IS_DOPE_1H_SOURCE_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2026, 4, 15), date(2026, 4, 28))
UNTOUCHED = Stage("untouched", date(2025, 10, 13), date(2025, 10, 19))
CONTINUOUS = Stage("continuous_30d", date(2025, 8, 1), date(2025, 8, 30))

PROFILES: dict[str, dict[str, Any]] = {
    "claim": {
        "adx_long": 39.0,
        "adx_short": 20.0,
        "close_shift_long": 6,
        "close_shift_short": 9,
        "market_ma": 97,
        "fast_ma": 16,
        "slow_ma": 57,
        "rsi": 10,
        "exit_rolling_long": 9,
        "exit_rolling_short": 9,
        "roi_0": 0.283,
        "roi_t1_minutes": 132,
        "roi_t1": 0.160,
        "roi_t2_minutes": 548,
        "roi_t2": 0.071,
        "roi_t3_minutes": 961,
        "roi_t3": 0.0,
        "stoploss": 0.289,
        "trailing_positive": 0.010,
        "trailing_offset": 0.021,
        "trailing_only_offset_is_reached": True,
    },
    "json": {
        "adx_long": 24.0,
        "adx_short": 23.0,
        "close_shift_long": 7,
        "close_shift_short": 10,
        "market_ma": 120,
        "fast_ma": 15,
        "slow_ma": 46,
        "rsi": 12,
        "exit_rolling_long": 8,
        "exit_rolling_short": 9,
        "roi_0": 0.581,
        "roi_t1_minutes": 262,
        "roi_t1": 0.130,
        "roi_t2_minutes": 580,
        "roi_t2": 0.069,
        "roi_t3_minutes": 1923,
        "roi_t3": 0.0,
        "stoploss": 0.187,
        "trailing_positive": 0.025,
        "trailing_offset": 0.048,
        "trailing_only_offset_is_reached": False,
    },
}

VARIANTS: dict[str, tuple[str, str]] = {
    "claim_level_both": ("claim", "both"),
    "claim_level_long": ("claim", "long"),
    "claim_level_short": ("claim", "short"),
    "json_level_both": ("json", "both"),
    "json_level_long": ("json", "long"),
    "json_level_short": ("json", "short"),
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_config(variant: str) -> Path:
    profile_name, side = VARIANTS[variant]
    profile = PROFILES[profile_name]
    payload = copy.deepcopy(
        json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    )
    strategy = payload["strategy"]
    for key in (
        "sma_offset_low",
        "sma_offset_high",
        "sma_stop_min_fraction",
        "sma_stop_max_fraction",
        "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    leverage = 2.0
    stoploss = float(profile["stoploss"])
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 2400,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            # Existing public-strategy shell fields.
            "picasso_bucket_minutes": 60,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": leverage,
            "picasso_source_stoploss": stoploss,
            "picasso_trailing_positive": float(profile["trailing_positive"]),
            "picasso_trailing_offset": float(profile["trailing_offset"]),
            "picasso_emergency_target_fraction": 0.20,
            "picasso_roi_0": float(profile["roi_0"]),
            "picasso_roi_416": float(profile["roi_t1"]),
            "picasso_roi_933": float(profile["roi_t2"]),
            "picasso_roi_1982": float(profile["roi_t3"]),
            # Public Slope-is-Dope source fields.
            "slope_trigger_mode": "level",
            "slope_side_mode": side,
            "slope_adx_period": 14,
            "slope_rsi_period": int(profile["rsi"]),
            "slope_market_ma_period": int(profile["market_ma"]),
            "slope_fast_ma_period": int(profile["fast_ma"]),
            "slope_slow_ma_period": int(profile["slow_ma"]),
            "slope_adx_long": float(profile["adx_long"]),
            "slope_adx_short": float(profile["adx_short"]),
            "slope_close_shift_long": int(profile["close_shift_long"]),
            "slope_close_shift_short": int(profile["close_shift_short"]),
            "slope_exit_rolling_long": int(profile["exit_rolling_long"]),
            "slope_exit_rolling_short": int(profile["exit_rolling_short"]),
            "slope_source_effective_leverage": leverage,
            "slope_source_stoploss": stoploss,
            "slope_trailing_positive": float(profile["trailing_positive"]),
            "slope_trailing_offset": float(profile["trailing_offset"]),
            "slope_trailing_only_offset_is_reached": bool(
                profile["trailing_only_offset_is_reached"]
            ),
            "slope_roi_0": float(profile["roi_0"]),
            "slope_roi_t1_minutes": int(profile["roi_t1_minutes"]),
            "slope_roi_t1": float(profile["roi_t1"]),
            "slope_roi_t2_minutes": int(profile["roi_t2_minutes"]),
            "slope_roi_t2": float(profile["roi_t2"]),
            "slope_roi_t3_minutes": int(profile["roi_t3_minutes"]),
            "slope_roi_t3": float(profile["roi_t3"]),
            "slope_emergency_target_fraction": 0.20,
        }
    )
    path = WORK / "configs" / f"{variant}.json"
    dump(path, payload)
    return path


def run_case(stage: Stage, variant: str) -> dict[str, Any]:
    output = ARTIFACTS / stage.name / variant
    workspace = WORK / "workspace" / stage.name / variant
    if output.exists():
        shutil.rmtree(output)
    if workspace.exists():
        shutil.rmtree(workspace)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(variant)),
        "--start",
        stage.start.isoformat(),
        "--end",
        stage.end.isoformat(),
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    run_path = output / "run.json"
    if (
        completed.returncode != 0
        or not metrics_path.is_file()
        or not diagnostics_path.is_file()
    ):
        row = {
            "stage": stage.name,
            "variant": variant,
            "produced": False,
            "returncode": completed.returncode,
        }
        dump(EVIDENCE / "cases" / f"{stage.name}-{variant}.json", row)
        return row
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    metric_keys = (
        "starting_nav",
        "ending_nav",
        "total_return",
        "geometric_daily_growth",
        "max_drawdown",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "expectancy_usdt",
        "expectancy_r",
        "average_hold_minutes",
        "largest_winner_share",
        "largest_loser_share",
        "max_consecutive_losses",
    )
    diagnostic_keys = (
        "source_signals_before_execution_filters",
        "entry_submissions",
        "entry_expirations",
        "selected_symbols",
        "route_counts",
        "unresolved_reason_counts",
        "slope_trailing_activations",
        "slope_default_trailing_exits",
        "slope_positive_trailing_exits",
        "slope_roi_exits",
        "slope_zero_roi_exits",
        "slope_source_signal_exits",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    row = {
        "stage": asdict(stage) | {"days": stage.days},
        "variant": variant,
        "profile": VARIANTS[variant][0],
        "side": VARIANTS[variant][1],
        "produced": True,
        "returncode": completed.returncode,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "run_contract": (
            json.loads(run_path.read_text(encoding="utf-8"))
            if run_path.is_file()
            else None
        ),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}-{variant}.json", row)
    return row


def account_ok(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    diagnostics = row.get("diagnostics") or {}
    return (
        int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
    )


def expectancy_positive(metrics: dict[str, Any]) -> bool:
    value = metrics.get("expectancy_r")
    if value is None:
        value = metrics.get("expectancy_usdt")
    return number(value, -math.inf) > 0.0


def pf_positive(metrics: dict[str, Any]) -> bool:
    value = metrics.get("profit_factor")
    if value is None:
        return int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    return number(value) > 1.0


def stage_pass(row: dict[str, Any], stage: Stage, daily_target: float) -> bool:
    metrics = row.get("metrics") or {}
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= stage.days
        and expectancy_positive(metrics)
        and pf_positive(metrics)
        and number(metrics.get("geometric_daily_growth")) >= daily_target
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def rank(row: dict[str, Any]) -> tuple[float, float, int, str]:
    metrics = row.get("metrics") or {}
    expectancy = metrics.get("expectancy_r")
    if expectancy is None:
        expectancy = metrics.get("expectancy_usdt")
    return (
        -number(metrics.get("geometric_daily_growth"), -math.inf),
        -number(expectancy, -math.inf),
        -int(metrics.get("trades") or 0),
        str(row.get("variant")),
    )


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen Slope-is-Dope tournament specification missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {
        variant: run_case(DEVELOPMENT, variant) for variant in VARIANTS
    }
    survivors = sorted(
        [
            row
            for row in development.values()
            if stage_pass(row, DEVELOPMENT, 0.0)
        ],
        key=rank,
    )[:2]
    untouched: dict[str, dict[str, Any]] = {}
    for row in survivors:
        variant = str(row["variant"])
        untouched[variant] = run_case(UNTOUCHED, variant)
    winners = sorted(
        [
            row
            for row in untouched.values()
            if stage_pass(row, UNTOUCHED, 0.0)
        ],
        key=rank,
    )[:1]
    continuous: dict[str, dict[str, Any]] = {}
    for row in winners:
        variant = str(row["variant"])
        continuous[variant] = run_case(CONTINUOUS, variant)

    comparison = {
        "experiment": "candidate-57-slope-is-dope-1h-source-v1",
        "source_claims_are_discovery_signals_only": True,
        "source_claims": {
            "embedded_current": {
                "total_profit": 4.8459,
                "profit_factor": 1.53,
                "trades_per_day": 11.18,
                "average_daily_profit": 0.0142,
            },
            "embedded_prior": {
                "trades": 1373,
                "trades_per_day": 10.98,
                "win_rate": 0.7225,
                "profit_factor": 2.28,
                "total_profit": 1.7816,
                "max_drawdown": 0.0567,
            },
        },
        "profiles": PROFILES,
        "variants": VARIANTS,
        "stages": {
            "development": asdict(DEVELOPMENT) | {"days": DEVELOPMENT.days},
            "untouched": asdict(UNTOUCHED) | {"days": UNTOUCHED.days},
            "continuous_30d": asdict(CONTINUOUS) | {"days": CONTINUOUS.days},
        },
        "development": development,
        "development_survivors": [row["variant"] for row in survivors],
        "untouched": untouched,
        "untouched_winners": [row["variant"] for row in winners],
        "continuous_30d": continuous,
        "continuous_pass": {
            variant: stage_pass(row, CONTINUOUS, 0.01)
            for variant, row in continuous.items()
        },
        "selection_was_predeclared": True,
    }
    dump(EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Public Slope-is-Dope 1h source tournament",
        "",
        "Public reports are discovery signals only. Results below use the "
        "project's four-symbol, one-slot, cost-after continuous account.",
        "",
        "## Development — 2026-04-15 to 2026-04-28",
        "",
        "| variant | trades | W/L | PF | geo/day | total return | MDD | avg hold | signals | entries | survivor |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    survivor_names = {str(row["variant"]) for row in survivors}
    for variant, row in development.items():
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    variant,
                    str(metrics.get("trades")),
                    f"{metrics.get('wins')}/{metrics.get('losses')}",
                    str(metrics.get("profit_factor")),
                    str(metrics.get("geometric_daily_growth")),
                    str(metrics.get("total_return")),
                    str(metrics.get("max_drawdown")),
                    str(metrics.get("average_hold_minutes")),
                    str(diagnostics.get("source_signals_before_execution_filters")),
                    str(diagnostics.get("entry_submissions")),
                    str(variant in survivor_names),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Untouched — 2025-10-13 to 2025-10-19",
            "",
            "| variant | trades | W/L | PF | geo/day | total return | MDD | winner |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    winner_names = {str(row["variant"]) for row in winners}
    for variant, row in untouched.items():
        metrics = row.get("metrics") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    variant,
                    str(metrics.get("trades")),
                    f"{metrics.get('wins')}/{metrics.get('losses')}",
                    str(metrics.get("profit_factor")),
                    str(metrics.get("geometric_daily_growth")),
                    str(metrics.get("total_return")),
                    str(metrics.get("max_drawdown")),
                    str(variant in winner_names),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Conditional continuous — 2025-08-01 to 2025-08-30",
            "",
            "| variant | trades | W/L | PF | geo/day | total return | MDD | project pass |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for variant, row in continuous.items():
        metrics = row.get("metrics") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    variant,
                    str(metrics.get("trades")),
                    f"{metrics.get('wins')}/{metrics.get('losses')}",
                    str(metrics.get("profit_factor")),
                    str(metrics.get("geometric_daily_growth")),
                    str(metrics.get("total_return")),
                    str(metrics.get("max_drawdown")),
                    str(stage_pass(row, CONTINUOUS, 0.01)),
                ]
            )
            + " |"
        )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "development_survivors": comparison[
                    "development_survivors"
                ],
                "untouched_winners": comparison["untouched_winners"],
                "continuous_pass": comparison["continuous_pass"],
                "development_produced": {
                    variant: row.get("produced")
                    for variant, row in development.items()
                },
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        flush=True,
    )
    all_rows = list(development.values()) + list(untouched.values()) + list(continuous.values())
    if any(not row.get("produced") for row in all_rows):
        return 1
    if any(not account_ok(row) for row in all_rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
