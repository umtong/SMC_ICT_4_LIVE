#!/usr/bin/env python3
"""Trade-forensic conditional campaign for the private ichi5m reconstruction."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from trade_ledger_forensics import analyze as analyze_trades

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-ichi5m-private-recon-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-ichi5m-private-recon-v1"
EVIDENCE = HERE / "evidence" / "ichi5m-private-recon-v1"
CACHE = ROOT / ".cache" / "candidate-57-ichi5m-private-recon-v1"
FREEZE = HERE / "ICHI5M_PRIVATE_RECON_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2026, 2, 15), date(2026, 2, 28))
UNTOUCHED = Stage("untouched", date(2025, 9, 8), date(2025, 9, 14))
CONTINUOUS = Stage("continuous_30d", date(2025, 8, 1), date(2025, 8, 30))

# entry mode, side mode, risk mode, tight trailing
VARIANTS: dict[str, tuple[str, str, str, bool]] = {
    "source_anchor_long": ("anchor_cloud", "long", "source_fraction", False),
    "structural_anchor_long": ("anchor_cloud", "long", "auction_structure", False),
    "structural_ordered_long": ("ordered_cloud", "long", "auction_structure", False),
    "structural_fast_long": ("fast_cloud", "long", "auction_structure", False),
    "structural_ordered_no_cloud_long": ("ordered_no_cloud", "long", "auction_structure", False),
    "structural_anchor_both": ("anchor_cloud", "both", "auction_structure", False),
    "structural_anchor_long_tight_trail": ("anchor_cloud", "long", "auction_structure", True),
}
FEATURE_KEYS = (
    "fan_magnitude",
    "fan_magnitude_gain",
    "source_score",
    "source_stop_fraction",
    "ema_close_12",
    "ema_close_24",
    "ema_close_48",
    "ema_close_96",
    "ema_open_48",
    "cloud_top",
    "cloud_bottom",
    "atr",
)


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
    entry_mode, side_mode, risk_mode, tight_trail = VARIANTS[variant]
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
            "max_hold_minutes": 180,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 1,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.10,
            "picasso_trailing_positive": 0.003 if tight_trail else 999.0,
            "picasso_trailing_offset": 0.008 if tight_trail else 999.0,
            "picasso_emergency_target_fraction": 0.010,
            "picasso_roi_0": 0.010,
            "picasso_roi_416": 0.010,
            "picasso_roi_933": 0.010,
            "picasso_roi_1982": 0.010,
            "ichi5_entry_mode": entry_mode,
            "ichi5_side_mode": side_mode,
            "ichi5_trigger_mode": "level",
            "ichi5_risk_mode": risk_mode,
            "ichi5_min_fan_gain": 1.001,
            "ichi5_fan_shift_value": 2,
            "ichi5_target_fraction": 0.010,
            "ichi5_source_stop_fraction": 0.100,
            "ichi5_structural_lookback": 12,
            "ichi5_atr_period": 14,
            "ichi5_stop_atr_buffer": 0.25,
            "ichi5_min_stop_fraction": 0.0015,
            "ichi5_conversion_period": 9,
            "ichi5_base_period": 26,
            "ichi5_lagging_span_period": 52,
            "ichi5_displacement": 26,
            "ichi5_source_exit_mode": "anchor_or_fan_cross",
            "ichi5_hidden_source_available": False,
        }
    )
    path = WORK / "configs" / f"{variant}.json"
    dump(path, payload)
    return path


def run_case(stage: Stage, variant: str) -> dict[str, Any]:
    output = ARTIFACTS / stage.name / variant
    workspace = WORK / "workspace" / stage.name / variant
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
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
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    entry_mode, side_mode, risk_mode, tight_trail = VARIANTS[variant]
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": asdict(stage) | {"days": stage.days},
            "variant": variant,
            "entry_mode": entry_mode,
            "side_mode": side_mode,
            "risk_mode": risk_mode,
            "tight_trail": tight_trail,
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
        "min_equity",
        "trades",
        "wins",
        "losses",
        "win_rate",
        "profit_factor",
        "expectancy_usdt",
        "active_days",
        "largest_winner_share",
        "position_counts_by_symbol",
        "open_position_rows_at_end",
        "active_order_rows_at_end",
        "gate_checks",
    )
    diagnostic_keys = (
        "source_signals_before_execution_filters",
        "entry_submissions",
        "entry_expirations",
        "selected_symbols",
        "route_counts",
        "unresolved_reason_counts",
        "picasso_roi_exits",
        "picasso_source_signal_exits",
        "picasso_trailing_activations",
        "picasso_trailing_exits",
        "ichi5_source_signal_exits",
        "ichi5_final_entry_blackouts",
        "exchange_max_quantity_bounds",
        "max_open_positions_observed",
        "max_simultaneous_entry_intents",
        "global_position_violations",
        "order_rejections",
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": asdict(stage) | {"days": stage.days},
        "variant": variant,
        "entry_mode": entry_mode,
        "side_mode": side_mode,
        "risk_mode": risk_mode,
        "tight_trail": tight_trail,
        "produced": True,
        "returncode": 0,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}-{variant}.json", row)
    return row


def mechanics(row: dict[str, Any], require_end_flat: bool = True) -> bool:
    if not row.get("produced"):
        return False
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    checks = metrics.get("gate_checks") or {}
    end_ok = (
        int(metrics.get("open_position_rows_at_end") or 0) == 0
        and int(metrics.get("active_order_rows_at_end") or 0) == 0
    )
    return (
        int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
        and bool(checks.get("no_liquidation", True))
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
        and (end_ok or not require_end_flat)
    )


def quality_key(row: dict[str, Any]) -> tuple[float, float, float, int, str]:
    metrics = row.get("metrics") or {}
    return (
        -number(metrics.get("geometric_daily_growth"), -math.inf),
        -number(metrics.get("expectancy_usdt"), -math.inf),
        -number(metrics.get("profit_factor"), -math.inf),
        -int(metrics.get("trades") or 0),
        str(row.get("variant")),
    )


def density_key(row: dict[str, Any]) -> tuple[int, float, float, str]:
    metrics = row.get("metrics") or {}
    return (
        -int(metrics.get("trades") or 0),
        -number(metrics.get("total_return"), -math.inf),
        -number(metrics.get("expectancy_usdt"), -math.inf),
        str(row.get("variant")),
    )


def allocate(rows: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    pool = [
        row
        for row in rows.values()
        if mechanics(row)
        and int((row.get("metrics") or {}).get("trades") or 0) >= 7
        and number((row.get("metrics") or {}).get("max_drawdown"), 1.0) <= 0.30
    ]
    quality = sorted(pool, key=quality_key)[0] if pool else None
    dense = sorted(pool, key=density_key)[0] if pool else None
    selected: list[str] = []
    for row in (quality, dense):
        if row is not None and str(row["variant"]) not in selected:
            selected.append(str(row["variant"]))
    for row in sorted(pool, key=quality_key):
        if str(row["variant"]) not in selected:
            selected.append(str(row["variant"]))
        if len(selected) >= 2:
            break
    return selected[:2], {
        "binary_gate": False,
        "selection_is_information_value_allocation": True,
        "eligible_cases": [str(row["variant"]) for row in pool],
        "quality_leader": None if quality is None else str(quality["variant"]),
        "opportunity_density_leader": None if dense is None else str(dense["variant"]),
    }


def positive(row: dict[str, Any], stage: Stage) -> bool:
    metrics = row.get("metrics") or {}
    pf = metrics.get("profit_factor")
    pf_ok = (
        number(pf) > 1.0
        if pf is not None
        else int(metrics.get("wins") or 0) > 0 and int(metrics.get("losses") or 0) == 0
    )
    return (
        mechanics(row)
        and int(metrics.get("trades") or 0) >= stage.days
        and number(metrics.get("geometric_daily_growth")) > 0.0
        and number(metrics.get("expectancy_usdt")) > 0.0
        and pf_ok
        and number(metrics.get("max_drawdown"), 1.0) <= 0.20
    )


def render(comparison: dict[str, Any]) -> None:
    lines = [
        "# Private ichi_5m one-minute reconstruction v1",
        "",
        "The hidden source was unavailable. Each cell is a declared reconstruction and every case JSON contains every completed trade and entry-state diagnostics.",
        "",
    ]
    for stage_name in ("development", "untouched", "continuous_30d"):
        lines += [
            f"## {stage_name}",
            "",
            "| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals | exits |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for variant, row in comparison[stage_name].items():
            metrics = row.get("metrics") or {}
            diagnostics = row.get("diagnostics") or {}
            exits = (row.get("trade_forensics") or {}).get("by_exit_reason")
            lines.append(
                f"| {variant} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth')} | "
                f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
                f"{metrics.get('expectancy_usdt')} | {diagnostics.get('source_signals_before_execution_filters')} | "
                f"{json.dumps(exits, sort_keys=True)} |"
            )
        lines.append("")
    lines += [
        "## Allocation",
        "",
        f"- development survivors: {comparison['development_survivors']}",
        f"- positive untouched survivors: {comparison['untouched_positive_survivors']}",
        f"- continuous winner: {comparison['continuous_winner']}",
        f"- strict project pass: {comparison['strict_project_pass']}",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("private ichi5m reconstruction freeze missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {name: run_case(DEVELOPMENT, name) for name in VARIANTS}
    survivors, allocation = allocate(development)
    untouched = {name: run_case(UNTOUCHED, name) for name in survivors}
    positive_names = [name for name, row in untouched.items() if positive(row, UNTOUCHED)]
    positive_names.sort(key=lambda name: quality_key(untouched[name]))
    winner = positive_names[0] if positive_names else None
    continuous = {winner: run_case(CONTINUOUS, winner)} if winner else {}
    strict_pass = bool(
        winner
        and winner in continuous
        and positive(continuous[winner], CONTINUOUS)
        and number((continuous[winner].get("metrics") or {}).get("geometric_daily_growth")) >= 0.01
    )
    comparison = {
        "experiment": "candidate-57-ichi5m-private-recon-v1",
        "external_claim_is_discovery_signal_only": True,
        "hidden_source_available": False,
        "external_clues": {
            "timeframe": "1m",
            "mode": "spot",
            "stoploss": -0.10,
            "startup_candles": 96,
            "monthly_positive_binance_and_kucoin": True,
            "displayed_binance_win_rate_range": [0.844, 0.894],
            "displayed_average_profit_range": [0.0068, 0.0075],
            "displayed_average_duration_minutes": [2, 4],
            "indicator_footprint": [
                "ema_close_12",
                "ema_close_24",
                "ema_close_48",
                "ema_close_96",
                "ema_open_48",
                "fan_magnitude",
                "fan_magnitude_gain",
                "senkou_a",
                "senkou_b",
            ],
        },
        "causality": {
            "completed_one_minute_only": True,
            "chikou_used": False,
            "displaced_cloud_only_from_past_inputs": True,
            "trailing_floor_usable_next_completed_minute": True,
        },
        "variants": VARIANTS,
        "development": development,
        "development_survivors": survivors,
        "development_allocation": allocation,
        "untouched": untouched,
        "untouched_positive_survivors": positive_names,
        "continuous_30d": continuous,
        "continuous_winner": winner,
        "strict_project_pass": strict_pass,
    }
    dump(EVIDENCE / "comparison.json", comparison)
    render(comparison)
    print(
        json.dumps(
            {
                "development_survivors": survivors,
                "untouched_positive_survivors": positive_names,
                "continuous_winner": winner,
                "strict_project_pass": strict_pass,
            },
            indent=2,
            sort_keys=True,
        )
    )

    rows = list(development.values()) + list(untouched.values()) + list(continuous.values())
    if any(not row.get("produced") for row in rows):
        return 1
    if any(not mechanics(row) for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
