"""Reproduce and anatomize the public ichiV1 claim on its own 2025 regime.

This is not a gate screen.  Four predeclared policies isolate the source gross
profit engine, the public EMA-cross exit, and the planned invalidation geometry.
Each is replayed on two disjoint short intervals inside the reported claim
period.  All fills use the common Nautilus one-slot account and current-NAV 3%
planned-loss sizing.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
C51 = ROOT / "research" / "candidate-51"
C55 = ROOT / "research" / "candidate-55"
WORK = ROOT / ".work" / "candidate-55-ichi-claim"
OUT = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-ichi-claim"

_SPEC = importlib.util.spec_from_file_location(
    "candidate55_inherited_ichi_runner", C51 / "ichi_v9_runner.py"
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("inherited Ichi runner was not materialized")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

WINDOWS = {
    "claim-a-2025-01": ("2025-01-08", "2025-01-14"),
    "claim-b-2025-03": ("2025-03-01", "2025-03-07"),
}
VARIANTS = {
    "source_deep_stop_source_exit": {
        "source_stop": True,
        "exit_mode": "source",
    },
    "source_deep_stop_no_exit": {
        "source_stop": True,
        "exit_mode": "disabled",
    },
    "ema24_invalidation_source_exit": {
        "source_stop": False,
        "exit_mode": "source",
    },
    "ema24_invalidation_no_exit": {
        "source_stop": False,
        "exit_mode": "disabled",
    },
}


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def config_for(name: str) -> Path:
    values = VARIANTS[name]
    base = json.loads((C51 / "config.json").read_text(encoding="utf-8"))
    config = _BASE._strategy_config(
        base,
        {
            "short": False,
            "source_stop": values["source_stop"],
            "exit_ema": 24,
            "gain": 1.002,
        },
    )
    config["strategy"].update(
        {
            "max_hold_minutes": 1_000_000,
            "ichi_exit_signal_mode": values["exit_mode"],
        }
    )
    path = WORK / f"{name}.json"
    dump(path, config)
    return path


def run_one(name: str, stage: str, interval: tuple[str, str], config: Path) -> int:
    output = OUT / f"ichi-claim-{name}-{stage}"
    workspace = WORK / name / stage
    command = [
        sys.executable,
        str(C51 / "launch.py"),
        "--config", str(config),
        "--start", interval[0],
        "--end", interval[1],
        "--cache", str(CACHE),
        "--output", str(output),
        "--workspace", str(workspace),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    print("RUN", name, stage, interval, flush=True)
    return int(subprocess.run(command, env=env, check=False).returncode)


def pnl_value(text: Any) -> float:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(text))
    return float(match.group(0)) if match else 0.0


def exit_events(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    path = root / "scenario_events.jsonl"
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        event_type = str(event.get("event_type", ""))
        if event_type in {
            "PUBLIC_ICHI_ROI_EXIT",
            "PUBLIC_ICHI_EMA_CROSS_EXIT",
            "STOP_HIT",
            "TIME_EXIT",
            "EVALUATION_END_EXIT",
            "FUNDING_FLATTEN_EXIT",
        }:
            result[str(event.get("scenario_id"))] = event_type
    return result


def read_one(name: str, stage: str, interval: tuple[str, str], code: int) -> dict[str, Any]:
    root = OUT / f"ichi-claim-{name}-{stage}"
    mp = root / "metrics.json"
    dp = root / "strategy_diagnostics.json"
    cp = root / "closed_scenarios.json"
    if code != 0 or not mp.is_file() or not dp.is_file() or not cp.is_file():
        return {
            "variant": name,
            "stage": stage,
            "interval": interval,
            "return_code": code,
            "produced": False,
        }
    metrics = json.loads(mp.read_text(encoding="utf-8"))
    diagnostics = json.loads(dp.read_text(encoding="utf-8"))
    trades = json.loads(cp.read_text(encoding="utf-8"))
    reasons = exit_events(root)
    enriched = []
    for trade in trades:
        item = dict(trade)
        pnl = pnl_value(item.get("realized_pnl"))
        item["realized_pnl_value"] = pnl
        item["exit_type"] = reasons.get(str(item.get("scenario_id")), "POSITION_CLOSE_UNCLASSIFIED")
        enriched.append(item)
    gross_profit = sum(max(0.0, item["realized_pnl_value"]) for item in enriched)
    gross_loss = -sum(min(0.0, item["realized_pnl_value"]) for item in enriched)
    exit_buckets: dict[str, dict[str, Any]] = {}
    for item in enriched:
        bucket = exit_buckets.setdefault(
            item["exit_type"],
            {"trades": 0, "wins": 0, "losses": 0, "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0},
        )
        value = item["realized_pnl_value"]
        bucket["trades"] += 1
        bucket["wins"] += int(value > 0.0)
        bucket["losses"] += int(value < 0.0)
        bucket["gross_profit"] += max(0.0, value)
        bucket["gross_loss"] += max(0.0, -value)
        bucket["net_pnl"] += value
    return {
        "variant": name,
        "stage": stage,
        "interval": interval,
        "return_code": code,
        "produced": True,
        "ending_nav": metrics.get("ending_nav"),
        "total_return": metrics.get("total_return"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "max_drawdown": metrics.get("max_drawdown"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": gross_profit - gross_loss,
        "source_signals": diagnostics.get("source_signals_before_execution_filters"),
        "entry_submissions": diagnostics.get("entry_submissions"),
        "used_episode_rejections": diagnostics.get("used_episode_rejections"),
        "source_exit_submissions": diagnostics.get("ichi_source_exit_submissions"),
        "roi_exits": diagnostics.get("ichi_roi_exits"),
        "selected_symbols": diagnostics.get("selected_symbols"),
        "order_rejections": diagnostics.get("order_rejections"),
        "global_position_violations": diagnostics.get("global_position_violations"),
        "max_open_positions": diagnostics.get("max_open_positions_observed"),
        "exit_buckets": exit_buckets,
        "closed_trades": enriched,
    }


def aggregate(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    selected = [row for row in rows if row["variant"] == name and row.get("produced")]
    gp = sum(float(row["gross_profit"]) for row in selected)
    gl = sum(float(row["gross_loss"]) for row in selected)
    trades = sum(int(row["trades"] or 0) for row in selected)
    wins = sum(int(row["wins"] or 0) for row in selected)
    return {
        "windows": len(selected),
        "positive_windows": sum(float(row["net_pnl"]) > 0.0 for row in selected),
        "trades": trades,
        "wins": wins,
        "losses": trades - wins,
        "win_rate": wins / trades if trades else 0.0,
        "gross_profit": gp,
        "gross_loss": gl,
        "net_pnl": gp - gl,
        "profit_factor": gp / gl if gl > 0.0 else None,
        "source_signals": sum(int(row.get("source_signals") or 0) for row in selected),
        "entries": sum(int(row.get("entry_submissions") or 0) for row in selected),
    }


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    configs = {name: config_for(name) for name in VARIANTS}
    rows: list[dict[str, Any]] = []
    for stage, interval in WINDOWS.items():
        for name, config in configs.items():
            code = run_one(name, stage, interval, config)
            rows.append(read_one(name, stage, interval, code))
    aggregates = {name: aggregate(rows, name) for name in VARIANTS}
    result = {
        "candidate": "candidate-55",
        "family": "PUBLIC_ICHI_V1_CLOUD_HA_EMA_FAN",
        "external_claim_interval": ["2025-01-01", "2025-04-03"],
        "external_claim": {
            "trades_18_pairs": 1056,
            "max_open_trades": 3,
            "profit_factor": 6.51,
            "roi_exit_trades": 822,
            "roi_exit_net_claim_pct": 4116.1,
            "exit_signal_trades": 234,
            "exit_signal_net_claim_pct": -583.14,
        },
        "our_contract": {
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "one_global_slot": True,
            "current_nav_risk_fraction": 0.03,
            "real_costs_and_adverse_slippage": True,
            "continuous_account_per_run": True,
        },
        "windows": WINDOWS,
        "variants": VARIANTS,
        "runs": rows,
        "aggregates": aggregates,
        "interpretation_required": [
            "Do not rank by net PnL alone.",
            "Inspect every ROI winner, EMA-exit loser, stop, rejected repeated episode and symbol arbitration conflict.",
            "Deep-stop and EMA-invalidation variants answer different geometry questions and are not interchangeable parameter tweaks.",
            "No long evaluation is justified until the profit engine and loss mechanism repeat in both short windows.",
        ],
        "long_evaluation_run": False,
        "production_ready": False,
    }
    dump(OUT / "ichi-claim-final-result.json", result)
    dump(C55 / "evidence" / "ichi-claim" / "RESULT.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
