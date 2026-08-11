#!/usr/bin/env python3
"""Correct startup/end-flat replay for the frozen TrendRider v1 policy.

This is implementation repair only: ten unscored calendar days supply the
public 210 completed 1-hour startup candles, the original 14-day signal window
is frozen, and two runoff days permit the unchanged <=24h lifecycle to flatten.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import trendrider_pullback_long_v1_campaign as base
import trendrider_pullback_long_v1_endflat_rerun as prior

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
C51 = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-57-trendrider-pullback-long-v1-warmup-endflat-v2"
ARTIFACTS = ROOT / "artifacts" / "candidate-57-trendrider-pullback-long-v1-warmup-endflat-v2"
EVIDENCE = HERE / "evidence" / "trendrider-pullback-long-v1-warmup-endflat-v2"
CACHE = ROOT / ".cache" / "candidate-57-trendrider-pullback-long-v1-warmup-endflat-v2"
WARMUP_DAYS = 10
RUNOFF_DAYS = 2


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(base.safe(value), indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def boundary_ns(day, *, end: bool) -> int:
    moment = datetime.combine(day, time.min, tzinfo=timezone.utc)
    if end:
        moment += timedelta(days=1)
        moment -= timedelta(microseconds=1)
    return int(moment.timestamp() * 1_000_000_000)


def build_config(stage: base.Stage) -> Path:
    seed = prior.build_config(stage)
    payload = json.loads(seed.read_text(encoding="utf-8"))
    payload["strategy"].update(
        {
            "trendrider_signal_start_ns": boundary_ns(stage.start, end=False),
            "trendrider_signal_end_ns": boundary_ns(stage.end, end=True),
        }
    )
    path = WORK / "configs" / f"{stage.name}.json"
    dump(path, payload)
    return path


def run_stage(stage: base.Stage) -> dict[str, Any]:
    data_start = stage.start - timedelta(days=WARMUP_DAYS)
    data_end = stage.end + timedelta(days=RUNOFF_DAYS)
    output = ARTIFACTS / stage.name
    workspace = WORK / "workspace" / stage.name
    for path in (output, workspace):
        if path.exists():
            shutil.rmtree(path)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(build_config(stage)),
        "--start",
        data_start.isoformat(),
        "--end",
        data_end.isoformat(),
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(C51)},
        check=False,
    )
    metrics_path = output / "metrics.json"
    diagnostics_path = output / "strategy_diagnostics.json"
    stage_record = asdict(stage) | {
        "days": stage.days,
        "data_start": str(data_start),
        "data_end": str(data_end),
        "warmup_days": WARMUP_DAYS,
        "runoff_days": RUNOFF_DAYS,
    }
    if completed.returncode != 0 or not metrics_path.is_file() or not diagnostics_path.is_file():
        row = {
            "stage": stage_record,
            "produced": False,
            "returncode": int(completed.returncode),
        }
        dump(EVIDENCE / "cases" / f"{stage.name}.json", row)
        return row

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    starting = float(metrics.get("starting_nav") or 0.0)
    ending = float(metrics.get("ending_nav") or 0.0)
    metrics["signal_window_calendar_days"] = stage.days
    metrics["warmup_days"] = WARMUP_DAYS
    metrics["runoff_days"] = RUNOFF_DAYS
    metrics["data_start"] = str(data_start)
    metrics["data_end"] = str(data_end)
    metrics["geometric_daily_growth_signal_window"] = (
        (ending / starting) ** (1.0 / stage.days) - 1.0
        if starting > 0.0 and ending > 0.0
        else math.nan
    )
    expected = int(metrics.get("trades") or 0)
    row = {
        "stage": stage_record,
        "produced": True,
        "returncode": 0,
        "metrics": metrics,
        "diagnostics": diagnostics,
        "trade_forensics": base.analyze_trades(output, expected, base.FEATURE_KEYS),
    }
    dump(EVIDENCE / "cases" / f"{stage.name}.json", row)
    return row


def render(result: dict[str, Any]) -> None:
    lines = [
        "# TrendRider pullback-long v1 warmup/end-flat v2",
        "",
        "The source policy is unchanged.  Ten days are unscored startup and two days are close runoff; entries are allowed only inside the original frozen 14-day windows.",
        "",
        f"- mechanically valid: {result.get('mechanically_valid')}",
        f"- decision: `{result.get('decision')}`",
        f"- thresholds searched: {result.get('thresholds_searched')}",
        f"- policy-fresh authorized: {result.get('policy_fresh_authorized')}",
        f"- integration authorized: {result.get('integration_authorized')}",
        f"- long evaluation authorized: {result.get('long_evaluation_authorized')}",
        "",
        "| stage | trades | W/L | PF | expectancy USDT | signal-window geo/day | return | MDD | signals | end open |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage in base.STAGES:
        row = (result.get("cases") or {}).get(stage.name) or {}
        metrics = row.get("metrics") or {}
        diagnostics = row.get("diagnostics") or {}
        lines.append(
            f"| {stage.name} | {metrics.get('trades')} | {metrics.get('wins')}/{metrics.get('losses')} | {metrics.get('profit_factor')} | {metrics.get('expectancy_usdt')} | {metrics.get('geometric_daily_growth_signal_window')} | {metrics.get('total_return')} | {metrics.get('max_drawdown')} | {diagnostics.get('source_signals_before_execution_filters')} | {metrics.get('open_position_rows_at_end')} |"
        )
    lines.extend(
        [
            "",
            "This remains development evidence.  A coherent positive intended-regime mechanism authorizes at most one separately frozen policy-fresh interval.",
        ]
    )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    for path in (WORK, ARTIFACTS, CACHE):
        path.mkdir(parents=True, exist_ok=True)
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    rows = {stage.name: run_stage(stage) for stage in base.STAGES}
    result = base.classify(rows)
    result.update(
        {
            "implementation_only_change": "unscored 210-candle-capable startup plus close runoff",
            "warmup_days": WARMUP_DAYS,
            "runoff_days": RUNOFF_DAYS,
        }
    )
    dump(EVIDENCE / "comparison.json", result)
    render(result)
    return 0 if result.get("mechanically_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
