#!/usr/bin/env python3
"""Run the two pre-frozen Winner15m one-slot arbitration policies.

This is a short causal experiment, not a promotion gate.  The two policies are
run sequentially on the same fresh-to-Candidate-57 interval with identical
signals, management, costs, risk and one-slot account constraints.  Results are
preserved at trade/episode level so aggregate NAV cannot hide the mechanism.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import copy
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
C51 = REPO / "research" / "candidate-51"
ROOT = REPO / ".work" / "candidate-57-winner-arbitration-fresh-v1"
ARTIFACT_ROOT = REPO / "artifacts" / "candidate-57-winner-arbitration-fresh-v1"
EVIDENCE = HERE / "evidence" / "winner-arbitration-fresh-v1"
CACHE = REPO / ".cache" / "candidate-57-winner-arbitration-fresh-v1"

DATA_START = date(2024, 9, 6)
ENTRY_START = date(2024, 9, 9)
ENTRY_END = date(2024, 9, 15)
DATA_END = date(2024, 9, 17)
MODES = ("current_max_climax", "least_volume_excess")


def _ns_start(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1e9)


def _ns_end(day: date) -> int:
    return _ns_start(day + timedelta(days=1)) - 1


def _json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _number(value: Any) -> float:
    if value is None:
        return math.nan
    match = re.search(r"[-+]?\d[\d_,]*(?:\.\d+)?", str(value))
    return (
        float(match.group(0).replace(",", "").replace("_", ""))
        if match is not None
        else math.nan
    )


def _quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def q(frac: float) -> float:
        position = frac * (len(clean) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return clean[lower]
        weight = position - lower
        return clean[lower] * (1.0 - weight) + clean[upper] * weight

    return {
        "min": clean[0],
        "q25": q(0.25),
        "median": q(0.5),
        "q75": q(0.75),
        "max": clean[-1],
    }


def _config(mode: str) -> Path:
    base = json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    config = copy.deepcopy(base)
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
            "max_hold_minutes": 360,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "external_family_mode": "winner",
            "winner_bucket_minutes": 15,
            "winner_ema_fast": 10,
            "winner_ema_slow": 30,
            "winner_macd_fast": 12,
            "winner_macd_slow": 26,
            "winner_macd_signal": 9,
            "winner_roc_period": 3,
            "winner_roc_threshold": 0.10,
            "winner_adx_period": 14,
            "winner_adx_threshold": 18.0,
            "winner_volume_period": 20,
            "winner_volume_ratio": 1.0,
            "winner_stop_fraction": 0.025,
            "winner_initial_target_fraction": 0.080,
            "winner_trailing_positive": 0.005,
            "winner_trailing_offset": 0.018,
            "winner_roi_0": 0.080,
            "winner_roi_480": 0.050,
            "winner_roi_1440": 0.030,
            "winner_roi_4320": 0.0,
            "winner_source_startup_candles": 200,
            "winner_entry_start_ns": _ns_start(ENTRY_START),
            "winner_entry_end_ns": _ns_end(ENTRY_END),
            "winner_force_flat_ns": _ns_end(DATA_END) - 120_000_000_000,
            "winner_bar_buffer_minutes": 50_000,
        }
    )
    path = ROOT / mode / "config.json"
    _json(path, config)
    return path


def _run(mode: str) -> int:
    output = ARTIFACT_ROOT / mode
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    config = _config(mode)
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config",
        str(config),
        "--start",
        DATA_START.isoformat(),
        "--end",
        DATA_END.isoformat(),
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(ROOT / mode / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    env["C57_ARBITRATION_MODE"] = mode
    return subprocess.run(command, cwd=REPO, env=env, check=False).returncode


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _exit_classes(events_path: Path) -> Counter[str]:
    result: Counter[str] = Counter()
    if not events_path.is_file():
        return result
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        name = str(event.get("event") or event.get("kind") or "")
        upper = name.upper()
        if "TRAIL" in upper:
            result["source_trailing"] += 1
        elif "STOP" in upper:
            result["hard_stop"] += 1
        elif "MAX_HOLD" in upper or "TIME" in upper:
            result["max_hold_or_time"] += 1
        elif "ROI" in upper or "TARGET" in upper:
            result["roi_or_target"] += 1
        elif "FILL" in upper and ("RISK" in upper or "INVALID" in upper):
            result["fill_risk"] += 1
    return result


def _summarize(mode: str, returncode: int) -> dict[str, Any]:
    source = ARTIFACT_ROOT / mode
    required = [
        source / "metrics.json",
        source / "strategy_diagnostics.json",
        source / "closed_scenarios.json",
        source / "positions.csv",
        source / "orders.csv",
    ]
    if returncode != 0 or not all(path.is_file() for path in required):
        return {
            "mode": mode,
            "produced": False,
            "returncode": returncode,
            "missing": [str(path) for path in required if not path.is_file()],
        }

    metrics = json.loads((source / "metrics.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(
        (source / "strategy_diagnostics.json").read_text(encoding="utf-8")
    )
    scenarios = json.loads(
        (source / "closed_scenarios.json").read_text(encoding="utf-8")
    )
    positions = _csv(source / "positions.csv")
    orders = _csv(source / "orders.csv")

    pnl: list[float] = []
    r_values: list[float] = []
    per_symbol: Counter[str] = Counter()
    per_side: Counter[str] = Counter()
    collision_entries = 0
    for row in scenarios:
        value = _number(row.get("realized_pnl"))
        risk = float(row.get("risk_budget") or math.nan)
        pnl.append(value)
        if math.isfinite(value) and math.isfinite(risk) and risk > 0.0:
            r_values.append(value / risk)
        per_symbol[str(row.get("symbol"))] += 1
        per_side["long" if int(row.get("side") or 0) > 0 else "short"] += 1
        candidates = int(
            (row.get("diagnostics") or {}).get("simultaneous_actionable_candidates", 1)
        )
        collision_entries += int(candidates > 1)

    hold_minutes: list[float] = []
    for row in positions:
        side = str(row.get("side", "")).strip().upper()
        closed = str(row.get("ts_closed", "")).strip().lower()
        if side != "FLAT" or closed in {"", "none", "nan", "nat"}:
            continue
        duration = _number(row.get("duration_ns"))
        if math.isfinite(duration):
            hold_minutes.append(duration / 60_000_000_000)

    active_orders = [
        row for row in orders
        if str(row.get("status", "")).strip().upper()
        not in {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "DENIED", "EXPIRED"}
    ]
    open_positions = [
        row for row in positions
        if str(row.get("side", "")).strip().upper() != "FLAT"
        or str(row.get("ts_closed", "")).strip().lower() in {"", "none", "nan", "nat"}
    ]
    entry_days = (ENTRY_END - ENTRY_START).days + 1
    gross_profit = sum(value for value in pnl if value > 0.0)
    gross_loss = -sum(value for value in pnl if value < 0.0)
    wins = sum(value > 0.0 for value in pnl)
    losses = sum(value < 0.0 for value in pnl)

    preserved = EVIDENCE / mode
    if preserved.exists():
        shutil.rmtree(preserved)
    preserved.mkdir(parents=True, exist_ok=True)
    for name in (
        "metrics.json",
        "strategy_diagnostics.json",
        "run.json",
        "data_manifest.json",
        "closed_scenarios.json",
        "scenario_events.jsonl",
        "positions.csv",
        "orders.csv",
    ):
        candidate = source / name
        if candidate.is_file():
            shutil.copy2(candidate, preserved / name)

    return {
        "mode": mode,
        "produced": True,
        "returncode": returncode,
        "engine": "NautilusTrader BacktestNode",
        "data_interval": [DATA_START.isoformat(), DATA_END.isoformat()],
        "entry_interval": [ENTRY_START.isoformat(), ENTRY_END.isoformat()],
        "entry_days": entry_days,
        "starting_nav": metrics.get("starting_nav"),
        "ending_nav": metrics.get("ending_nav"),
        "total_return": metrics.get("total_return"),
        "full_data_interval_geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "max_drawdown": metrics.get("max_drawdown"),
        "completed_trades": len(scenarios),
        "completed_trades_per_entry_day": len(scenarios) / entry_days,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(scenarios) if scenarios else 0.0,
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "mean_after_cost_r": sum(r_values) / len(r_values) if r_values else None,
        "after_cost_r_distribution": _quantiles(r_values),
        "holding_minutes": _quantiles(hold_minutes),
        "per_symbol": dict(sorted(per_symbol.items())),
        "per_side": dict(sorted(per_side.items())),
        "entries_from_simultaneous_candidate_boundaries": collision_entries,
        "exit_event_hints": dict(_exit_classes(source / "scenario_events.jsonl")),
        "end_state": {
            "open_position_rows": len(open_positions),
            "active_order_rows": len(active_orders),
            "end_flat": not open_positions and not active_orders,
            "metric_checks": {
                key: value
                for key, value in (metrics.get("checks") or {}).items()
                if key in {
                    "closed_position_rows_match_trade_count",
                    "no_open_positions_at_end",
                    "no_active_orders_at_end",
                    "single_entry_intent",
                    "single_position",
                    "no_global_position_violation",
                }
            },
        },
        "strategy_diagnostics": diagnostics,
    }


def _markdown(summary: dict[str, Any]) -> str:
    rows = []
    for mode in MODES:
        item = summary["modes"][mode]
        if not item.get("produced"):
            rows.append(f"| {mode} | implementation failure | - | - | - | - |")
            continue
        rows.append(
            "| {mode} | {trades} | {wr:.2%} | {pf} | {ret:.2%} | {mdd:.2%} |".format(
                mode=mode,
                trades=item["completed_trades"],
                wr=item["win_rate"],
                pf=(
                    f"{item['profit_factor']:.3f}"
                    if item["profit_factor"] is not None
                    else "∞"
                ),
                ret=float(item["total_return"]),
                mdd=float(item["max_drawdown"]),
            )
        )
    return """# Winner15m fresh one-slot arbitration result

This is a mechanism comparison, not a pass/fail gate.  The interval is now
development data.  Read `comparison.json`, both `closed_scenarios.json` files
and event/order evidence before changing the router.

| policy | completed trades | win rate | PF | total return | MDD |
|---|---:|---:|---:|---:|---:|
{rows}

The policy difference is interpretable only if both runs are end-flat and the
same signal/management code path was preserved.  Aggregate superiority alone
does not establish a reusable arbitration rule.
""".format(rows="\n".join(rows))


def main() -> int:
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    process_status = 0
    for mode in MODES:
        code = _run(mode)
        process_status = process_status or code
        results[mode] = _summarize(mode, code)
    comparison = {
        "experiment": "candidate-57-winner-arbitration-fresh-v1",
        "purpose": "causal one-slot arbitration mechanism comparison",
        "binary_gate": False,
        "fresh_interval_consumed": True,
        "modes": results,
    }
    _json(EVIDENCE / "comparison.json", comparison)
    (EVIDENCE / "RESULT.md").write_text(_markdown(comparison), encoding="utf-8")
    print(json.dumps(comparison, indent=2, sort_keys=True, allow_nan=False))
    if process_status != 0:
        return process_status
    if not all((results[mode].get("end_state") or {}).get("end_flat") for mode in MODES):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
