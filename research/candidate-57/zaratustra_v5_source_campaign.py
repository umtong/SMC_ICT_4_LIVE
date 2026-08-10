#!/usr/bin/env python3
"""Conditional trade-forensic tournament for public ZaratustraV5."""
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
WORK = ROOT / ".work" / "candidate-57-zaratustra-v5-source-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-zaratustra-v5-source-v1"
EVIDENCE = HERE / "evidence" / "zaratustra-v5-source-v1"
CACHE = ROOT / ".cache" / "candidate-57-zaratustra-v5-source-v1"
FREEZE = HERE / "ZARATUSTRA_V5_SOURCE_V1_FREEZE.md"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


DEVELOPMENT = Stage("development", date(2026, 3, 1), date(2026, 3, 14))
RESERVED = Stage("reserved", date(2026, 5, 1), date(2026, 5, 7))
CONTINUOUS = Stage("continuous_30d", date(2026, 6, 1), date(2026, 6, 30))

# trigger, side, risk
VARIANTS: dict[str, tuple[str, str, str]] = {
    "source_level_both": ("level", "both", "source_fraction"),
    "source_level_long": ("level", "long", "source_fraction"),
    "source_level_short": ("level", "short", "source_fraction"),
    "source_edge_both": ("edge", "both", "source_fraction"),
    "structural_level_both": ("level", "both", "auction_structure"),
    "structural_level_short": ("level", "short", "auction_structure"),
}
FEATURE_KEYS = (
    "rsi_5m",
    "rsi_15m",
    "rsi_30m",
    "plus_di_5m",
    "plus_di_15m",
    "plus_di_30m",
    "minus_di_5m",
    "minus_di_15m",
    "minus_di_30m",
    "bb_middle_5m",
    "bb_middle_15m",
    "bb_middle_30m",
    "source_score",
    "source_stop_fraction",
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
    trigger, side, risk = VARIANTS[variant]
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
            "max_hold_minutes": 480,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 5,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.0296,
            "picasso_trailing_positive": 0.0013,
            "picasso_trailing_offset": 0.0071,
            "picasso_emergency_target_fraction": 0.20,
            "picasso_roi_0": 100.0,
            "picasso_roi_416": 100.0,
            "picasso_roi_933": 100.0,
            "picasso_roi_1982": 100.0,
            "zara_trigger_mode": trigger,
            "zara_side_mode": side,
            "zara_risk_mode": risk,
            "zara_rsi_period": 14,
            "zara_di_period": 14,
            "zara_bb_period": 20,
            "zara_rsi_threshold": 50.0,
            "zara_di_threshold": 25.0,
            "zara_source_stop_fraction": 0.0296,
            "zara_target_fraction": 0.20,
            "zara_structural_lookback_5m": 8,
            "zara_atr_period_5m": 14,
            "zara_stop_atr_buffer": 0.25,
            "zara_min_stop_fraction": 0.0015,
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
    trigger, side, risk = VARIANTS[variant]
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": asdict(stage) | {"days": stage.days},
            "variant": variant,
            "trigger": trigger,
            "side": side,
            "risk": risk,
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
        "picasso_trailing_activations",
        "picasso_trailing_exits",
        "picasso_roi_exits",
        "picasso_source_signal_exits",
        "zara_final_entry_blackouts",
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
        "trigger": trigger,
        "side": side,
        "risk": risk,
        "produced": True,
        "returncode": 0,
        "metrics": {key: metrics.get(key) for key in metric_keys},
        "diagnostics": {key: diagnostics.get(key) for key in diagnostic_keys},
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}-{variant}.json", row)
    return row


def mechanics(row: dict[str, Any]) -> bool:
    if not row.get("produced"):
        return False
    metrics = row.get("metrics") or {}
    diagnostics = row.get("diagnostics") or {}
    checks = metrics.get("gate_checks") or {}
    return (
        int(diagnostics.get("global_position_violations") or 0) == 0
        and int(diagnostics.get("order_rejections") or 0) == 0
        and int(diagnostics.get("max_open_positions_observed") or 0) <= 1
        and int(diagnostics.get("max_simultaneous_entry_intents") or 0) <= 1
        and int(metrics.get("open_position_rows_at_end") or 0) == 0
        and int(metrics.get("active_order_rows_at_end") or 0) == 0
        and bool(checks.get("no_liquidation", True))
        and bool((row.get("trade_forensics") or {}).get("ledger_matches_metrics"))
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
        and number((row.get("metrics") or {}).get("max_drawdown"), 1.0) <= 0.35
    ]
    quality = sorted(pool, key=quality_key)[0] if pool else None
    dense_pool = [
        row
        for row in pool
        if int((row.get("metrics") or {}).get("trades") or 0) >= DEVELOPMENT.days
    ]
    density = sorted(dense_pool, key=density_key)[0] if dense_pool else None
    selected: list[str] = []
    for row in (quality, density):
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
        "eligible": [str(row["variant"]) for row in pool],
        "quality_leader": None if quality is None else str(quality["variant"]),
        "opportunity_density_leader": None if density is None else str(density["variant"]),
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
        "# Public ZaratustraV5 source tournament",
        "",
        "Every row is the project's four-symbol, one-slot, after-cost account with one-minute trailing detail. Every case JSON contains the complete compact trade ledger.",
        "",
    ]
    for stage_name in ("development", "reserved", "continuous_30d"):
        lines += [
            f"## {stage_name}",
            "",
            "| variant | trades | W/L | PF | geo/day | return | MDD | expectancy | signals | trail exits |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for variant, row in comparison[stage_name].items():
            metrics = row.get("metrics") or {}
            diagnostics = row.get("diagnostics") or {}
            lines.append(
                f"| {variant} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
                f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth')} | "
                f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
                f"{metrics.get('expectancy_usdt')} | {diagnostics.get('source_signals_before_execution_filters')} | "
                f"{diagnostics.get('picasso_trailing_exits')} |"
            )
        lines.append("")
    lines += [
        "## Allocation",
        "",
        f"- development survivors: {comparison['development_survivors']}",
        f"- positive reserved survivors: {comparison['reserved_positive_survivors']}",
        f"- continuous winner: {comparison['continuous_winner']}",
        f"- strict project pass: {comparison['strict_project_pass']}",
        "",
        "Development allocation preserves quality and opportunity density rather than treating one threshold as a truth gate.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("ZaratustraV5 freeze missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    development = {name: run_case(DEVELOPMENT, name) for name in VARIANTS}
    survivors, allocation = allocate(development)
    reserved = {name: run_case(RESERVED, name) for name in survivors}
    positive_names = [name for name, row in reserved.items() if positive(row, RESERVED)]
    positive_names.sort(key=lambda name: quality_key(reserved[name]))
    winner = positive_names[0] if positive_names else None
    continuous = {winner: run_case(CONTINUOUS, winner)} if winner else {}
    strict_pass = bool(
        winner
        and winner in continuous
        and positive(continuous[winner], CONTINUOUS)
        and number((continuous[winner].get("metrics") or {}).get("geometric_daily_growth")) >= 0.01
    )
    comparison = {
        "experiment": "candidate-57-zaratustra-v5-source-v1",
        "external_claim_is_discovery_signal_only": True,
        "external_claim": {
            "period": ["2021-01-01", "2026-01-01"],
            "pairs": 33,
            "max_open_trades": 10,
            "trades": 175744,
            "win_rate": 0.779,
            "profit_factor": 1.34,
            "total_profit_percent": 35969.0,
            "positive_months": "60/61",
            "rolling_3m_positive": True,
            "account_drawdown": 0.4383,
            "timeframe_detail_1m_used": False,
        },
        "causality": {
            "completed_1m_input": True,
            "complete_5m_15m_30m_only": True,
            "trailing_detail": "1m_next_completed_bar_usable",
            "same_5m_bar_activation_and_fill": False,
        },
        "variants": VARIANTS,
        "development": development,
        "development_survivors": survivors,
        "development_allocation": allocation,
        "reserved": reserved,
        "reserved_positive_survivors": positive_names,
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
                "reserved_positive_survivors": positive_names,
                "continuous_winner": winner,
                "strict_project_pass": strict_pass,
            },
            indent=2,
            sort_keys=True,
        )
    )

    rows = list(development.values()) + list(reserved.values()) + list(continuous.values())
    if any(not row.get("produced") for row in rows):
        return 1
    if any(not mechanics(row) for row in rows):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
