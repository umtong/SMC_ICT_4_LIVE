#!/usr/bin/env python3
"""Development audit of the public EdgeBot 4σ VWAP description."""
from __future__ import annotations

import copy
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
WORK = ROOT / ".work" / "candidate-57-edgebot-vwap-4sigma-v1"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-edgebot-vwap-4sigma-v1"
EVIDENCE = HERE / "evidence" / "edgebot-vwap-4sigma-v1"
CACHE = ROOT / ".cache" / "candidate-57-edgebot-vwap-4sigma-v1"
FREEZE = HERE / "EDGEBOT_VWAP_4SIGMA_V1_FREEZE.md"
START = date(2026, 1, 1)
END = date(2026, 1, 14)

# signal mode, scope, mean exit, risk
VARIANTS = {
    "btc_weighted_6sigma_static": ("weighted_band", "btc", "static_entry_mean", "six_sigma"),
    "all_weighted_6sigma_static": ("weighted_band", "all", "static_entry_mean", "six_sigma"),
    "all_weighted_impulse_static": ("weighted_band", "all", "static_entry_mean", "impulse_extreme"),
    "all_prior_residual_impulse_static": ("prior_residual", "all", "static_entry_mean", "impulse_extreme"),
    "all_weighted_impulse_dynamic": ("weighted_band", "all", "dynamic_mean", "impulse_extreme"),
}
FEATURE_KEYS = (
    "source_z", "source_sigma", "source_residual", "source_vwap",
    "source_score", "source_stop_fraction", "source_target_fraction",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_config(name: str) -> Path:
    signal, scope, mean_exit, risk = VARIANTS[name]
    payload = copy.deepcopy(json.loads((C51 / "config.json").read_text(encoding="utf-8")))
    strategy = payload["strategy"]
    for key in (
        "sma_offset_low", "sma_offset_high", "sma_stop_min_fraction",
        "sma_stop_max_fraction", "sma_stop_atr_buffer",
    ):
        strategy.pop(key, None)
    strategy.update(
        {
            "cooldown_minutes": 0,
            "max_hold_minutes": 720,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "picasso_bucket_minutes": 15,
            "picasso_precedence_mode": "corrected_level",
            "picasso_source_effective_leverage": 1.0,
            "picasso_source_stoploss": 0.10,
            "picasso_trailing_positive": 999.0,
            "picasso_trailing_offset": 999.0,
            "picasso_emergency_target_fraction": 0.20,
            "picasso_roi_0": 100.0,
            "picasso_roi_416": 100.0,
            "picasso_roi_933": 100.0,
            "picasso_roi_1982": 100.0,
            "edge_signal_mode": signal,
            "edge_scope": scope,
            "edge_mean_exit_mode": mean_exit,
            "edge_risk_mode": risk,
            "edge_vwap_period": 20,
            "edge_entry_sigma": 4.0,
            "edge_stop_sigma": 6.0,
            "edge_residual_window": 20,
            "edge_atr_period": 14,
            "edge_stop_atr_buffer": 0.25,
            "edge_min_stop_fraction": 0.0015,
            "edge_dynamic_emergency_target_fraction": 0.20,
        }
    )
    path = WORK / "configs" / f"{name}.json"
    dump(path, payload)
    return path


def run_case(name: str) -> dict[str, Any]:
    output = ARTIFACTS / name
    workspace = WORK / "workspace" / name
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable, str(C51 / "launch.py"), "--config", str(build_config(name)),
            "--start", START.isoformat(), "--end", END.isoformat(),
            "--cache", str(CACHE), "--output", str(output),
            "--workspace", str(workspace),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        return {"variant": name, "produced": False, "returncode": completed.returncode}
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    expected = int(metrics.get("trades") or 0)
    return {
        "variant": name,
        "policy": VARIANTS[name],
        "produced": True,
        "returncode": 0,
        "metrics": {
            key: metrics.get(key)
            for key in (
                "starting_nav", "ending_nav", "total_return",
                "geometric_daily_growth", "max_drawdown", "min_equity",
                "trades", "wins", "losses", "win_rate", "profit_factor",
                "expectancy_usdt", "open_position_rows_at_end",
                "active_order_rows_at_end", "gate_checks",
            )
        },
        "diagnostics": {
            key: diagnostics.get(key)
            for key in (
                "source_signals_before_execution_filters", "entry_submissions",
                "selected_symbols", "edge_dynamic_mean_exits",
                "max_open_positions_observed", "max_simultaneous_entry_intents",
                "global_position_violations", "order_rejections",
            )
        },
        "trade_forensics": analyze_trades(output, expected, FEATURE_KEYS),
    }


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


def main() -> int:
    if not FREEZE.is_file():
        raise RuntimeError("EdgeBot freeze missing")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    rows = {name: run_case(name) for name in VARIANTS}
    for name, row in rows.items():
        dump(EVIDENCE / "cases" / f"development-{name}.json", row)
    quality = sorted(
        [row for row in rows.values() if mechanics(row)],
        key=lambda row: (
            -number((row.get("metrics") or {}).get("geometric_daily_growth"), -math.inf),
            -number((row.get("metrics") or {}).get("expectancy_usdt"), -math.inf),
            -int((row.get("metrics") or {}).get("trades") or 0),
        ),
    )
    comparison = {
        "experiment": "candidate-57-edgebot-vwap-4sigma-v1",
        "development_only": True,
        "period": [START.isoformat(), END.isoformat()],
        "external_claim_is_discovery_signal_only": True,
        "external_claim": {
            "symbol": "BTCUSDT", "timeframe": "15m", "periods": 20,
            "entry_sigma": 4.0, "exit": "mean", "live_months": 14,
            "trades": 1847, "win_rate": 0.623, "sharpe": 1.84,
            "max_drawdown": 0.082,
        },
        "cells": rows,
        "best_valid_development": None if not quality else quality[0]["variant"],
        "next_step": (
            "run predeclared comparison account for up to two informative cells"
            if quality and int((quality[0].get("metrics") or {}).get("trades") or 0) >= 7
            else "external 4sigma definition does not reproduce claimed opportunity density; redesign or retire"
        ),
    }
    dump(EVIDENCE / "comparison.json", comparison)
    lines = [
        "# EdgeBot 4σ rolling-VWAP development audit", "",
        "| variant | trades | W/L | PF | geo/day | return | MDD | signals |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, row in rows.items():
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        lines.append(
            f"| {name} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | "
            f"{metrics.get('profit_factor')} | {metrics.get('geometric_daily_growth')} | "
            f"{metrics.get('total_return')} | {metrics.get('max_drawdown')} | "
            f"{diagnostics.get('source_signals_before_execution_filters')} |"
        )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({name: row.get("metrics") for name, row in rows.items()}, indent=2, sort_keys=True))
    if any(not row.get("produced") for row in rows.values()):
        return 1
    if any(not mechanics(row) for row in rows.values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
