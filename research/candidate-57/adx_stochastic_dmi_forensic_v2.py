#!/usr/bin/env python3
"""Behaviour-identical DMI state audit for public ADXStochastic."""
from __future__ import annotations

import copy
from dataclasses import dataclass
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
WORK = ROOT / ".work" / "candidate-57-adx-dmi-forensic-v2"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-adx-dmi-forensic-v2"
EVIDENCE = HERE / "evidence" / "adx-dmi-forensic-v2"
CACHE = ROOT / ".cache" / "candidate-57-adx-dmi-forensic-v2"
BASELINE_DIR = HERE / "evidence" / "adx-stochastic-5m-v1" / "cases"


@dataclass(frozen=True)
class Stage:
    name: str
    start: date
    end: date
    baseline_name: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


STAGES = (
    Stage(
        "development",
        date(2026, 1, 15),
        date(2026, 1, 28),
        "development-corrected_exit.json",
    ),
    Stage(
        "reserved",
        date(2025, 8, 18),
        date(2025, 8, 24),
        "reserved-corrected_exit.json",
    ),
)

FEATURE_KEYS = (
    "adx_5m",
    "fastk_5m",
    "fastd_5m",
    "previous_fastk_5m",
    "previous_fastd_5m",
    "source_score",
    "source_stop_fraction",
    "forensic_elapsed_minutes",
    "forensic_mfe_r",
    "forensic_mae_r",
    "forensic_mfe_r_minute",
    "forensic_mae_r_minute",
    "forensic_entry_dmi_elapsed_minute",
    "forensic_entry_plus_di",
    "forensic_entry_minus_di",
    "forensic_entry_dmi_spread",
    "forensic_entry_bullish_dmi_state",
    "forensic_entry_adx",
    "forensic_entry_adx_slope_1",
    "forensic_entry_adx_slope_3",
    "forensic_first_bullish_dmi_state_minute",
    "forensic_first_bullish_dmi_cross_minute",
    "forensic_mark_r_at_first_bullish_dmi_state",
    "forensic_mark_r_at_first_bullish_dmi_cross",
    "forensic_mfe_r_at_first_bullish_dmi_state",
    "forensic_mae_r_at_first_bullish_dmi_state",
    "forensic_first_negative_pressure_weakening_minute",
    "forensic_mark_r_at_first_negative_pressure_weakening",
    "forensic_dmi_spread_at_first_negative_pressure_weakening",
    "forensic_time_to_mfe_0p10r",
    "forensic_time_to_mfe_0p25r",
    "forensic_time_to_mae_0p10r",
    "forensic_time_to_mae_0p25r",
    "forensic_time_to_mae_0p50r",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_config() -> Path:
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
            "picasso_source_stoploss": 0.10 / 9.0,
            "picasso_trailing_positive": 999.0,
            "picasso_trailing_offset": 999.0,
            "picasso_emergency_target_fraction": 0.05,
            "picasso_roi_0": 0.04 / 9.0,
            "picasso_roi_416": 0.01 / 9.0,
            "picasso_roi_933": 0.01 / 9.0,
            "picasso_roi_1982": 0.01 / 9.0,
            "adxstoch_risk_mode": "source_fraction",
            "adxstoch_exit_mode": "corrected",
            "adxstoch_adx_period": 14,
            "adxstoch_fastk_period": 5,
            "adxstoch_fastd_period": 3,
            "adxstoch_entry_adx": 50.0,
            "adxstoch_entry_stoch": 20.0,
            "adxstoch_exit_adx": 25.0,
            "adxstoch_exit_stoch": 75.0,
            "adxstoch_source_stop_fraction": 0.10 / 9.0,
            "adxstoch_target_fraction": 0.05,
            "adxstoch_structural_lookback_5m": 8,
            "adxstoch_atr_period_5m": 14,
            "adxstoch_stop_atr_buffer": 0.25,
            "adxstoch_min_stop_fraction": 0.0015,
        }
    )
    path = WORK / "config.json"
    dump(path, payload)
    return path


def compare_baseline(stage: Stage, metrics: dict[str, Any]) -> dict[str, Any]:
    baseline_path = BASELINE_DIR / stage.baseline_name
    if not baseline_path.is_file():
        return {"available": False, "identical": False}
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = baseline.get("metrics") or {}
    checks: dict[str, bool] = {}
    for key in (
        "trades",
        "wins",
        "losses",
        "total_return",
        "geometric_daily_growth",
        "profit_factor",
        "max_drawdown",
    ):
        if key in {"trades", "wins", "losses"}:
            checks[key] = int(expected.get(key) or 0) == int(metrics.get(key) or 0)
        else:
            checks[key] = (
                abs(number(expected.get(key), 0.0) - number(metrics.get(key), 0.0))
                <= 1e-10
            )
    return {
        "available": True,
        "identical": all(checks.values()),
        "checks": checks,
    }


def outcome(row: dict[str, Any]) -> str:
    actual_r = number(row.get("actual_r"), 0.0)
    if actual_r > 0.0:
        return "winner"
    if actual_r <= -0.90:
        return "full_stop"
    return "partial_loss"


def event_before(row: dict[str, Any], event_key: str, boundary_key: str) -> bool:
    event = number(row.get(event_key))
    boundary = number(row.get(boundary_key))
    return math.isfinite(event) and (
        not math.isfinite(boundary) or event <= boundary
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {name: [] for name in ("winner", "partial_loss", "full_stop")}
    for row in rows:
        groups[outcome(row)].append(row)
    result: dict[str, Any] = {}
    for name, items in groups.items():
        bullish_before = sum(
            event_before(
                row,
                "forensic_first_bullish_dmi_state_minute",
                "forensic_time_to_mfe_0p25r"
                if name == "winner"
                else "forensic_time_to_mae_0p50r",
            )
            for row in items
        )
        weakening_before = sum(
            event_before(
                row,
                "forensic_first_negative_pressure_weakening_minute",
                "forensic_time_to_mfe_0p25r"
                if name == "winner"
                else "forensic_time_to_mae_0p50r",
            )
            for row in items
        )
        entry_bullish = sum(
            number(row.get("forensic_entry_bullish_dmi_state"), 0.0) > 0.5
            for row in items
        )
        spreads = [
            number(row.get("forensic_entry_dmi_spread"))
            for row in items
            if math.isfinite(number(row.get("forensic_entry_dmi_spread")))
        ]
        adx_slopes = [
            number(row.get("forensic_entry_adx_slope_3"))
            for row in items
            if math.isfinite(number(row.get("forensic_entry_adx_slope_3")))
        ]
        result[name] = {
            "trades": len(items),
            "mean_r": (
                sum(number(row.get("actual_r"), 0.0) for row in items) / len(items)
                if items
                else None
            ),
            "entry_bullish_dmi_rate": entry_bullish / len(items) if items else None,
            "bullish_state_before_outcome_boundary_rate": (
                bullish_before / len(items) if items else None
            ),
            "negative_pressure_weakening_before_boundary_rate": (
                weakening_before / len(items) if items else None
            ),
            "entry_dmi_spread_mean": (
                sum(spreads) / len(spreads) if spreads else None
            ),
            "entry_adx_slope_3_mean": (
                sum(adx_slopes) / len(adx_slopes) if adx_slopes else None
            ),
        }
    return result


def run_stage(stage: Stage) -> dict[str, Any]:
    output = ARTIFACTS / stage.name
    workspace = WORK / "workspace" / stage.name
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(C51 / "launch.py"),
            "--config",
            str(build_config()),
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
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        return {
            "stage": stage.name,
            "produced": False,
            "returncode": completed.returncode,
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    forensic = analyze_trades(output, int(metrics.get("trades") or 0), FEATURE_KEYS)
    return {
        "stage": stage.name,
        "produced": True,
        "returncode": 0,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "baseline_identity": compare_baseline(stage, metrics),
        "trade_forensics": forensic,
        "directional_state": summarize(forensic["trade_ledger"]),
    }


def render(report: dict[str, Any]) -> None:
    lines = [
        "# ADXStochastic directional-state forensic v2",
        "",
        "Both accounts are behaviour-identical replays of already consumed data.",
        "",
    ]
    for stage_name in ("development", "reserved"):
        stage = report["stages"][stage_name]
        metrics = stage.get("metrics") or {}
        lines += [
            f"## {stage_name}",
            "",
            f"- baseline identical: {(stage.get('baseline_identity') or {}).get('identical')}",
            f"- trades: {metrics.get('trades')}",
            f"- wins/losses: {metrics.get('wins')}/{metrics.get('losses')}",
            f"- PF: {metrics.get('profit_factor')}",
            "",
            "| outcome | trades | mean R | entry bullish DMI | bullish state before outcome boundary | negative pressure weakening before boundary | entry DMI spread mean | ADX slope(3) mean |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for outcome_name in ("winner", "partial_loss", "full_stop"):
            row = (stage.get("directional_state") or {}).get(outcome_name) or {}
            lines.append(
                f"| {outcome_name} | {row.get('trades')} | {row.get('mean_r')} | "
                f"{row.get('entry_bullish_dmi_rate')} | "
                f"{row.get('bullish_state_before_outcome_boundary_rate')} | "
                f"{row.get('negative_pressure_weakening_before_boundary_rate')} | "
                f"{row.get('entry_dmi_spread_mean')} | "
                f"{row.get('entry_adx_slope_3_mean')} |"
            )
        lines.append("")
    lines += [
        "## Predeclared interpretation",
        "",
        f"- directional-state hypothesis supported: {report['hypothesis']['supported']}",
        f"- reason: {report['hypothesis']['reason']}",
        "",
        "A directional confirmation policy is not implemented by this workflow. It is justified only if the same temporal ordering explains both the losing development episodes and the winning reserved episodes without consuming the objective before entry.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    for stage in STAGES:
        if not (BASELINE_DIR / stage.baseline_name).is_file():
            raise RuntimeError(f"missing baseline: {stage.baseline_name}")
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    stages = {stage.name: run_stage(stage) for stage in STAGES}
    development = stages["development"].get("directional_state") or {}
    reserved = stages["reserved"].get("directional_state") or {}
    dev_winner = (development.get("winner") or {}).get(
        "bullish_state_before_outcome_boundary_rate"
    )
    dev_full = (development.get("full_stop") or {}).get(
        "bullish_state_before_outcome_boundary_rate"
    )
    reserved_winner = (reserved.get("winner") or {}).get(
        "bullish_state_before_outcome_boundary_rate"
    )
    supported = bool(
        dev_winner is not None
        and dev_full is not None
        and reserved_winner is not None
        and float(dev_winner) >= 0.75
        and float(reserved_winner) >= 0.75
        and float(dev_full) <= 0.25
    )
    reason = (
        f"development winner bullish-before={dev_winner}; "
        f"development full-stop bullish-before={dev_full}; "
        f"reserved winner bullish-before={reserved_winner}"
    )
    report = {
        "experiment": "candidate-57-adx-stochastic-dmi-forensic-v2",
        "policy_changed": False,
        "stages": stages,
        "hypothesis": {"supported": supported, "reason": reason},
    }
    dump(EVIDENCE / "directional_state_report.json", report)
    render(report)
    valid = all(
        stage.get("produced")
        and (stage.get("baseline_identity") or {}).get("identical")
        and (stage.get("trade_forensics") or {}).get("ledger_matches_metrics")
        for stage in stages.values()
    )
    return 0 if valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
