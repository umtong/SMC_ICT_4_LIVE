#!/usr/bin/env python3
"""Source-faithful public RSI/BB/MACD 1h tournament under project execution."""
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
WORK = ROOT / ".work" / "candidate-57-picasso-1h-source-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-picasso-1h-source-v1"
EVIDENCE = HERE / "evidence" / "picasso-1h-source-v1"
CACHE = ROOT / ".cache" / "candidate-57-picasso-1h-source-v1"
FREEZE = HERE / "PICASSO_1H_SOURCE_TOURNAMENT_V1_FREEZE.md"

VARIANTS = (
    "exact_level",
    "exact_level_short",
    "exact_edge",
    "corrected_level",
    "corrected_edge",
)


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2026, 5, 15), date(2026, 5, 28))
UNTOUCHED = Stage("untouched", date(2025, 11, 3), date(2025, 11, 9))
CONTINUOUS = Stage("continuous_30d", date(2025, 9, 1), date(2025, 9, 30))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_config(variant: str) -> Path:
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
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 2400,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 60,
            "picasso_precedence_mode": variant,
            "picasso_adx_period": 14,
            "picasso_rsi_long_period": 22,
            "picasso_rsi_short_period": 17,
            "picasso_bb_long_period": 16,
            "picasso_bb_short_period": 20,
            "picasso_volume_long_period": 38,
            "picasso_volume_short_period": 20,
            "picasso_adx_long_min_1": 5.7,
            "picasso_adx_long_max_1": 6.5,
            "picasso_adx_long_min_2": 20.9,
            "picasso_adx_long_max_2": 50.7,
            "picasso_adx_short_min_1": 9.9,
            "picasso_adx_short_max_1": 21.4,
            "picasso_adx_short_min_2": 30.3,
            "picasso_adx_short_max_2": 50.8,
            "picasso_source_effective_leverage": 5.0,
            "picasso_source_stoploss": 0.317,
            "picasso_trailing_positive": 0.010,
            "picasso_trailing_offset": 0.022,
            "picasso_emergency_target_fraction": 0.10,
            "picasso_roi_0": 0.184,
            "picasso_roi_416": 0.140,
            "picasso_roi_933": 0.073,
            "picasso_roi_1982": 0.0,
            "picasso_atr_period": 20,
            "picasso_ema_long_exit": 91,
            "picasso_ema_short_exit": 147,
            "picasso_atr_long_multiple": 3.8,
            "picasso_atr_short_multiple": 5.0,
            "picasso_volume_long_exit": 19,
            "picasso_volume_short_exit": 41,
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
    run_payload = (
        json.loads(run_path.read_text(encoding="utf-8"))
        if run_path.is_file()
        else None
    )
    keys = (
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
    row = {
        "stage": asdict(stage) | {"days": stage.days},
        "variant": variant,
        "produced": True,
        "returncode": completed.returncode,
        "metrics": {key: metrics.get(key) for key in keys},
        "diagnostics": {
            "source_signals_before_execution_filters": diagnostics.get(
                "source_signals_before_execution_filters"
            ),
            "entry_submissions": diagnostics.get("entry_submissions"),
            "entry_expirations": diagnostics.get("entry_expirations"),
            "selected_symbols": diagnostics.get("selected_symbols"),
            "route_counts": diagnostics.get("route_counts"),
            "unresolved_reason_counts": diagnostics.get(
                "unresolved_reason_counts"
            ),
            "picasso_trailing_activations": diagnostics.get(
                "picasso_trailing_activations"
            ),
            "picasso_trailing_exits": diagnostics.get(
                "picasso_trailing_exits"
            ),
            "picasso_roi_exits": diagnostics.get("picasso_roi_exits"),
            "picasso_source_signal_exits": diagnostics.get(
                "picasso_source_signal_exits"
            ),
            "max_open_positions_observed": diagnostics.get(
                "max_open_positions_observed"
            ),
            "max_simultaneous_entry_intents": diagnostics.get(
                "max_simultaneous_entry_intents"
            ),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
        },
        "run_contract": run_payload,
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


def positive_expectancy(metrics: dict[str, Any]) -> bool:
    if metrics.get("expectancy_r") is not None:
        return finite(metrics.get("expectancy_r"), -math.inf) > 0.0
    return finite(metrics.get("expectancy_usdt"), -math.inf) > 0.0


def development_pass(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= DEVELOPMENT.days
        and positive_expectancy(metrics)
        and finite(metrics.get("geometric_daily_growth")) > 0.0
        and finite(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def untouched_pass(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= UNTOUCHED.days
        and positive_expectancy(metrics)
        and finite(metrics.get("geometric_daily_growth")) > 0.0
        and finite(metrics.get("profit_factor")) > 1.0
        and finite(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def continuous_pass(row: dict[str, Any]) -> bool:
    metrics = row.get("metrics") or {}
    return (
        account_ok(row)
        and int(metrics.get("trades") or 0) >= CONTINUOUS.days
        and positive_expectancy(metrics)
        and finite(metrics.get("geometric_daily_growth")) >= 0.01
        and finite(metrics.get("profit_factor")) > 1.0
        and finite(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def rank(row: dict[str, Any]) -> tuple[float, float, int, str]:
    metrics = row.get("metrics") or {}
    expectancy = metrics.get("expectancy_r")
    if expectancy is None:
        expectancy = metrics.get("expectancy_usdt")
    return (
        -finite(metrics.get("geometric_daily_growth"), -math.inf),
        -finite(expectancy, -math.inf),
        -int(metrics.get("trades") or 0),
        str(row.get("variant")),
    )


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("frozen public source tournament specification missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {variant: run_case(DEVELOPMENT, variant) for variant in VARIANTS}
    survivors = sorted(
        [row for row in development.values() if development_pass(row)], key=rank
    )[:2]

    untouched: dict[str, dict[str, Any]] = {}
    for row in survivors:
        variant = str(row["variant"])
        untouched[variant] = run_case(UNTOUCHED, variant)
    untouched_winners = sorted(
        [row for row in untouched.values() if untouched_pass(row)], key=rank
    )[:1]

    continuous: dict[str, dict[str, Any]] = {}
    for row in untouched_winners:
        variant = str(row["variant"])
        continuous[variant] = run_case(CONTINUOUS, variant)

    comparison = {
        "experiment": "candidate-57-picasso-1h-source-v1",
        "source_claim": {
            "repository": "syuraj/freq-test",
            "strategy": "RSI_BB_MACD_Nov_2023_1h_2_Dec.py",
            "period_days": 1027,
            "reported_unlimited_trades": 330583,
            "reported_unlimited_trades_per_day": 321.89,
            "reported_unlimited_total_profit": 55.6249,
            "reported_four_slot_trades": 24973,
            "reported_four_slot_trades_per_day": 24.32,
            "reported_four_slot_total_profit": 95.1306,
            "claim_is_project_evidence": False,
        },
        "stages": {
            "development": asdict(DEVELOPMENT) | {"days": DEVELOPMENT.days},
            "untouched": asdict(UNTOUCHED) | {"days": UNTOUCHED.days},
            "continuous": asdict(CONTINUOUS) | {"days": CONTINUOUS.days},
        },
        "variants": list(VARIANTS),
        "development": development,
        "development_survivors": [row["variant"] for row in survivors],
        "untouched": untouched,
        "untouched_winners": [row["variant"] for row in untouched_winners],
        "continuous_30d": continuous,
        "continuous_pass": {
            variant: continuous_pass(row) for variant, row in continuous.items()
        },
        "selection_was_predeclared": True,
        "source_semantics_separated": {
            "operator_precedence": True,
            "level_vs_edge": True,
            "side": True,
        },
    }
    dump(EVIDENCE / "comparison.json", comparison)

    lines = [
        "# Public RSI/BB/MACD 1h source tournament",
        "",
        "The public reports are discovery signals only. Every table below is "
        "the project's four-symbol, one-slot, cost-after continuous account.",
        "",
        "## Development — 2026-05-15 to 2026-05-28",
        "",
        "| variant | trades | W/L | PF | geo/day | total return | MDD | avg hold min | signals | entries | survivor |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    survivor_names = {str(row["variant"]) for row in survivors}
    for variant in VARIANTS:
        row = development[variant]
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
            "## Untouched — 2025-11-03 to 2025-11-09",
            "",
            "| variant | trades | W/L | PF | geo/day | total return | MDD | pass |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    untouched_names = {str(row["variant"]) for row in untouched_winners}
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
                    str(variant in untouched_names),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Conditional continuous — 2025-09-01 to 2025-09-30",
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
                    str(continuous_pass(row)),
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
    produced_rows = list(development.values()) + list(untouched.values()) + list(continuous.values())
    if any(not row.get("produced") for row in produced_rows):
        return 1
    if any(not account_ok(row) for row in produced_rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
