#!/usr/bin/env python3
"""Run the frozen peer-taker state filter and preserve all source episodes."""
from __future__ import annotations

from collections import Counter, defaultdict
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
C51 = REPO / "research" / "candidate-51"
WORK = REPO / ".work" / "candidate-57-jump-taker-alignment-fresh-v1"
ARTIFACTS = REPO / "artifacts" / "candidate-57-jump-taker-alignment-fresh-v1"
EVIDENCE = HERE / "evidence" / "jump-taker-alignment-fresh-v1"
CACHE = REPO / ".cache" / "candidate-57-jump-taker-alignment-fresh-v1"
METRICS = WORK / "binance_metrics_2026-03-28_2026-04-14.json"
START = date(2026, 4, 1)
END = date(2026, 4, 14)
DAYS = (END - START).days + 1
CELLS = (
    "source_without_taker_filter",
    "peer_taker_alignment_3of4",
)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [
        float(row["exit_net_r"])
        for row in rows
        if row.get("exit_net_r") is not None
        and math.isfinite(float(row["exit_net_r"]))
    ]
    positive = [value for value in values if value > 0.0]
    negative = [value for value in values if value < 0.0]
    return {
        "candidate_rows": len(rows),
        "resolved_rows": len(values),
        "positive_rows": len(positive),
        "negative_rows": len(negative),
        "positive_share": len(positive) / len(values) if values else 0.0,
        "sum_shadow_r": sum(values),
        "mean_shadow_r": sum(values) / len(values) if values else None,
        "shadow_profit_factor_r": (
            sum(positive) / -sum(negative) if negative else None
        ),
    }


def config(cell: str) -> Path:
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
            "max_hold_minutes": 240,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "jump_timeframe_minutes": 240,
            "jump_threshold_sigma": 2.0,
            "jump_volatility_window": 18,
            "jump_min_absolute_return": 0.0,
            "jump_terminal_atr_period": 14,
            "jump_stop_atr_multiple": 1.0,
            "jump_min_stop_fraction": 0.0015,
            "jump_emergency_target_fraction": 0.20,
            "jump_stop_mode": "impulse",
            "jump_selection_mode": "source",
            "jump_min_residual_share": 0.50,
            "jump_min_residual_z": 0.75,
            "jump_confirmation_minutes": 0,
            "jump_confirmation_bucket_minutes": 5,
            "jump_protection_mode": "transient_be",
            "jump_protection_activation_r": 0.4,
            "jump_protection_floor_r": 0.0,
            "jump_protection_trail_gap_r": 999.0,
            "jump_protection_escape_r": 1.0,
            "jump_audit_enabled": True,
        }
    )
    path = WORK / cell / "config.json"
    dump(path, payload)
    return path


def download_sidecar() -> int:
    command = [
        sys.executable,
        str(HERE / "download_binance_metrics_sidecar.py"),
        "--start",
        "2026-03-28",
        "--end",
        END.isoformat(),
        "--output",
        str(METRICS),
        "--cache",
        str(CACHE / "metrics"),
    ]
    return subprocess.run(command, cwd=REPO, check=False).returncode


def run_cell(cell: str) -> int:
    output = ARTIFACTS / cell
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config(cell)),
        "--start",
        START.isoformat(),
        "--end",
        END.isoformat(),
        "--cache",
        str(CACHE / "bars"),
        "--output",
        str(output),
        "--workspace",
        str(WORK / cell / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    env["C57_JUMP_TAKER_FILTER_MODE"] = cell
    env["C57_JUMP_TAKER_METRICS_PATH"] = str(METRICS)
    return subprocess.run(command, cwd=REPO, env=env, check=False).returncode


def analyze_cell(cell: str, returncode: int) -> dict[str, Any]:
    source = ARTIFACTS / cell
    out = EVIDENCE / cell
    analyzer = [
        sys.executable,
        str(HERE / "jump_episode_forensic_analyze.py"),
        "--source",
        str(source),
        "--out",
        str(out),
    ]
    analyzer_status = subprocess.run(analyzer, cwd=REPO, check=False).returncode
    summary_path = out / "summary.json"
    rows_path = out / "episode_rows.json"
    if returncode != 0 or analyzer_status != 0 or not summary_path.is_file() or not rows_path.is_file():
        return {
            "cell": cell,
            "produced": False,
            "returncode": returncode,
            "analyzer_status": analyzer_status,
        }
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = json.loads(rows_path.read_text(encoding="utf-8"))
    accepted = [
        row
        for row in rows
        if int((row.get("diagnostics") or {}).get("taker_filter_accepted", 0)) == 1
    ]
    rejected = [row for row in rows if row not in accepted]
    source_boundaries = {int(row["episode_ts"]) for row in rows}
    accepted_boundaries = {int(row["episode_ts"]) for row in accepted}
    rejected_boundaries = {int(row["episode_ts"]) for row in rejected}
    reasons: Counter[str] = Counter()
    alignment_histogram: Counter[str] = Counter()
    for row in rows:
        diagnostics = row.get("diagnostics") or {}
        for reason in diagnostics.get("taker_filter_reasons", []):
            reasons[str(reason)] += 1
        aligned = diagnostics.get("jump_taker_aligned_peers")
        if aligned is not None:
            alignment_histogram[str(int(aligned))] += 1
    actual_metrics = summary.get("actual_metrics") or {}
    return {
        "cell": cell,
        "produced": True,
        "returncode": returncode,
        "analyzer_status": analyzer_status,
        "actual_account": actual_metrics,
        "actual_completed_trades": summary.get("actual_completed_trades"),
        "source_symbol_candidates": len(rows),
        "source_independent_boundaries": len(source_boundaries),
        "source_boundaries_per_day": len(source_boundaries) / DAYS,
        "accepted_symbol_candidates": len(accepted),
        "accepted_independent_boundaries": len(accepted_boundaries),
        "rejected_symbol_candidates": len(rejected),
        "boundaries_with_at_least_one_rejected_candidate": len(rejected_boundaries),
        "all_source_candidates_shadow": stats(rows),
        "accepted_candidates_shadow": stats(accepted),
        "rejected_candidates_shadow": stats(rejected),
        "alignment_peer_count_histogram": dict(sorted(alignment_histogram.items())),
        "filter_reason_counts": dict(sorted(reasons.items())),
        "blocked_by_account_slot": summary.get("blocked_by_account_slot"),
        "collision_boundaries": summary.get("collision_boundaries"),
        "end_validity": summary.get("end_validity"),
    }


def trade_map(cell: str) -> dict[tuple[str, int], dict[str, Any]]:
    path = EVIDENCE / cell / "episode_rows.json"
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        (str(row["symbol"]), int(row["episode_ts"])): row
        for row in rows
        if bool(row.get("actual_executed"))
    }


def main() -> int:
    freeze = HERE / "JUMP_TAKER_ALIGNMENT_FRESH_V1_FREEZE.md"
    if not freeze.is_file():
        raise RuntimeError("frozen taker experiment specification missing")
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    data_status = download_sidecar()
    results: dict[str, Any] = {}
    process_status = data_status
    for cell in CELLS:
        code = run_cell(cell) if data_status == 0 else 1
        process_status = process_status or code
        results[cell] = analyze_cell(cell, code)

    baseline = trade_map("source_without_taker_filter")
    filtered = trade_map("peer_taker_alignment_3of4")
    shared = sorted(set(baseline) & set(filtered))
    baseline_only = sorted(set(baseline) - set(filtered))
    filtered_only = sorted(set(filtered) - set(baseline))
    path_diff = {
        "shared_actual_trade_keys": len(shared),
        "baseline_only_actual_trades": [baseline[key] for key in baseline_only],
        "filtered_only_actual_trades": [filtered[key] for key in filtered_only],
        "shared_actual_outcome_differences": [
            {
                "symbol": key[0],
                "episode_ts": key[1],
                "baseline_actual_r": baseline[key].get("actual_after_cost_r"),
                "filtered_actual_r": filtered[key].get("actual_after_cost_r"),
            }
            for key in shared
            if baseline[key].get("actual_after_cost_r")
            != filtered[key].get("actual_after_cost_r")
        ],
    }
    comparison = {
        "experiment": "candidate-57-jump-taker-alignment-fresh-v1",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "interval": [START.isoformat(), END.isoformat()],
        "metrics_sidecar": {
            "source": "Binance Vision futures/um daily metrics",
            "path": str(METRICS),
            "bytes": METRICS.stat().st_size if METRICS.is_file() else None,
            "strict_asof_max_age_minutes": 10,
        },
        "cells": results,
        "continuous_account_path_difference": path_diff,
    }
    dump(EVIDENCE / "comparison.json", comparison)
    lines = [
        "# 4h jump peer-taker state result",
        "",
        "This is a causal state experiment, not a pass/fail gate.  The interval "
        "is now development data.  Raw simultaneous symbols at one 4h boundary "
        "are not independent opportunities.",
        "",
    ]
    for cell in CELLS:
        result = results[cell]
        account = result.get("actual_account") or {}
        lines.append(
            f"- `{cell}`: trades={result.get('actual_completed_trades')}, "
            f"return={account.get('total_return')}, "
            f"geo/day={account.get('geometric_daily_growth')}, "
            f"MDD={account.get('max_drawdown')}"
        )
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))

    if process_status != 0:
        return process_status
    for cell in CELLS:
        if not results[cell].get("produced"):
            return 1
        validity = results[cell].get("end_validity") or {}
        if validity.get("no_open_positions_at_end") is False:
            return 2
        if validity.get("no_active_orders_at_end") is False:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
