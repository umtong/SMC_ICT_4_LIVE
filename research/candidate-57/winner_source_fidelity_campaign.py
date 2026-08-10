#!/usr/bin/env python3
"""Run and anatomize a source-fidelity Winner15m one-slot adaptation.

This is not a long validation.  It repairs three confounds in the prior short
result before drawing strategic conclusions:

1. the public source requires 200 completed 15-minute startup candles;
2. the source condition is true on every qualifying candle, not only on the
   false->true transition;
3. entries near the end need a run-off interval so an open position is not
   counted as a completed loss or silently left in the account.

Raw trades and independent continuous source-condition episodes are reported
separately.  Performance is descriptive; the purpose is to discover whether
the public alpha engine survives the project's one-slot/risk/cost constraints
and which code adaptation changed its behavior.
"""
from __future__ import annotations

from collections import Counter, defaultdict
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
WORK = REPO / ".work" / "candidate-57-winner-source-fidelity-v1"
OUTPUT = REPO / "artifacts" / "candidate-57-winner-source-fidelity-v1"
EVIDENCE = HERE / "evidence" / "winner-source-fidelity-v1"
CACHE = REPO / ".cache" / "candidate-57-winner-source-fidelity-v1"

DATA_START = date.fromisoformat(os.environ.get("C57_DATA_START", "2025-02-27"))
ENTRY_START = date.fromisoformat(os.environ.get("C57_ENTRY_START", "2025-03-03"))
ENTRY_END = date.fromisoformat(os.environ.get("C57_ENTRY_END", "2025-03-09"))
DATA_END = date.fromisoformat(os.environ.get("C57_DATA_END", "2025-03-17"))


def ns_start(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1e9)


def ns_end(day: date) -> int:
    return ns_start(day + timedelta(days=1)) - 1


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def parse_number(value: Any) -> float:
    if value is None:
        return math.nan
    match = re.search(r"[-+]?\d[\d_,]*(?:\.\d+)?", str(value))
    if match is None:
        return math.nan
    return float(match.group(0).replace("_", "").replace(",", ""))


def quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return {"min": None, "q25": None, "median": None, "q75": None, "max": None}

    def q(fraction: float) -> float:
        position = fraction * (len(clean) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return clean[lower]
        weight = position - lower
        return clean[lower] * (1.0 - weight) + clean[upper] * weight

    return {
        "min": clean[0],
        "q25": q(0.25),
        "median": q(0.50),
        "q75": q(0.75),
        "max": clean[-1],
    }


def frozen_config() -> Path:
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
            "max_hold_minutes": 10_080,
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
            "winner_entry_start_ns": ns_start(ENTRY_START),
            "winner_entry_end_ns": ns_end(ENTRY_END),
            # Two minutes before the final day closes, leaving a subsequent
            # minute for a forced flatten market order to fill.
            "winner_force_flat_ns": ns_end(DATA_END) - 120_000_000_000,
            "winner_bar_buffer_minutes": 50_000,
        }
    )
    path = WORK / "config.json"
    write_json(path, config)
    return path


def run_source() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    config = frozen_config()
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
        str(OUTPUT),
        "--workspace",
        str(WORK / "workspace"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C51)
    return subprocess.run(command, cwd=REPO, env=env, check=False).returncode


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def episode_key(record: dict[str, Any]) -> tuple[str, int, int]:
    diagnostics = record.get("diagnostics") or {}
    start = diagnostics.get("causal_episode_start_ts")
    if start is None:
        start = record.get("episode_ts", 0)
    return str(record.get("symbol")), int(record.get("side") or 0), int(start)


def analyze(returncode: int) -> dict[str, Any]:
    required = [
        OUTPUT / "metrics.json",
        OUTPUT / "strategy_diagnostics.json",
        OUTPUT / "closed_scenarios.json",
        OUTPUT / "positions.csv",
        OUTPUT / "orders.csv",
    ]
    if returncode != 0 or not all(path.is_file() for path in required):
        return {
            "produced": False,
            "returncode": returncode,
            "missing": [str(path) for path in required if not path.is_file()],
        }

    metrics = json.loads((OUTPUT / "metrics.json").read_text(encoding="utf-8"))
    diagnostics = json.loads(
        (OUTPUT / "strategy_diagnostics.json").read_text(encoding="utf-8")
    )
    scenarios = json.loads(
        (OUTPUT / "closed_scenarios.json").read_text(encoding="utf-8")
    )
    positions = load_csv(OUTPUT / "positions.csv")
    orders = load_csv(OUTPUT / "orders.csv")

    closed_positions = [
        row for row in positions
        if str(row.get("side", "")).upper() == "FLAT"
        and str(row.get("ts_closed", "")).strip()
        and str(row.get("ts_closed", "")).lower() != "nan"
    ]
    open_positions = [
        row for row in positions
        if str(row.get("side", "")).upper() != "FLAT"
    ]
    open_orders = [
        row for row in orders
        if str(row.get("status", "")).upper() not in {
            "FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"
        }
    ]

    scenario_by_id = {
        str(item.get("scenario_id")): item
        for item in scenarios
        if item.get("scenario_id") is not None
    }
    pnls: list[float] = []
    r_values: list[float] = []
    holds_minutes: list[float] = []
    per_symbol: Counter[str] = Counter()
    per_side: Counter[str] = Counter()
    episode_rows: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    persistent_entries = 0
    fresh_entries = 0

    for item in scenarios:
        pnl = parse_number(item.get("realized_pnl"))
        risk = float(item.get("risk_budget") or math.nan)
        pnls.append(pnl)
        if math.isfinite(pnl) and math.isfinite(risk) and risk > 0.0:
            r_values.append(pnl / risk)
        per_symbol[str(item.get("symbol"))] += 1
        per_side["long" if int(item.get("side") or 0) > 0 else "short"] += 1
        row_diagnostics = item.get("diagnostics") or {}
        if int(row_diagnostics.get("persistent_source_condition", 0)):
            persistent_entries += 1
        else:
            fresh_entries += 1
        episode_rows[episode_key(item)].append(item)

    for row in closed_positions:
        duration_ns = parse_number(row.get("duration_ns"))
        if math.isfinite(duration_ns):
            holds_minutes.append(duration_ns / 60_000_000_000)

    episode_pnls: list[float] = []
    episode_trade_counts: list[int] = []
    episode_r: list[float] = []
    for rows in episode_rows.values():
        total_pnl = sum(parse_number(row.get("realized_pnl")) for row in rows)
        total_risk = sum(float(row.get("risk_budget") or 0.0) for row in rows)
        episode_pnls.append(total_pnl)
        episode_trade_counts.append(len(rows))
        if total_risk > 0.0:
            episode_r.append(total_pnl / total_risk)

    gross_profit = sum(value for value in pnls if value > 0.0)
    gross_loss = -sum(value for value in pnls if value < 0.0)
    wins = sum(value > 0.0 for value in pnls)
    losses = sum(value < 0.0 for value in pnls)
    episode_wins = sum(value > 0.0 for value in episode_pnls)
    episode_losses = sum(value < 0.0 for value in episode_pnls)

    starting_nav = float(metrics.get("starting_nav") or 100_000.0)
    ending_nav = float(metrics.get("ending_nav") or math.nan)
    entry_days = (ENTRY_END - ENTRY_START).days + 1
    optimistic_entry_window_geo = (
        (ending_nav / starting_nav) ** (1.0 / entry_days) - 1.0
        if starting_nav > 0.0 and ending_nav > 0.0
        else math.nan
    )

    first_open = min(
        (str(row.get("ts_opened")) for row in closed_positions),
        default=None,
    )
    last_close = max(
        (str(row.get("ts_closed")) for row in closed_positions),
        default=None,
    )
    occupation_days = None
    occupation_geo = None
    if first_open and last_close:
        first_dt = datetime.fromisoformat(first_open.replace("Z", "+00:00"))
        last_dt = datetime.fromisoformat(last_close.replace("Z", "+00:00"))
        occupation_days = max((last_dt - first_dt).total_seconds() / 86_400.0, 1.0 / 1440.0)
        if starting_nav > 0.0 and ending_nav > 0.0:
            occupation_geo = (ending_nav / starting_nav) ** (1.0 / occupation_days) - 1.0

    raw_trades = len(scenarios)
    independent_episodes = len(episode_rows)
    raw_frequency = raw_trades / entry_days
    independent_frequency = independent_episodes / entry_days

    return {
        "produced": True,
        "returncode": returncode,
        "engine": "NautilusTrader BacktestNode",
        "source": "win-boom/BTCquant user_data/strategies/winner_strat.py",
        "source_semantics_repaired": {
            "startup_candles": 200,
            "signal": "true on every completed 15m candle",
            "raw_trade_vs_independent_episode_separated": True,
            "runoff_data_used": True,
            "project_global_slot": 1,
            "project_risk_fraction": 0.03,
        },
        "data_interval": [DATA_START.isoformat(), DATA_END.isoformat()],
        "entry_interval": [ENTRY_START.isoformat(), ENTRY_END.isoformat()],
        "entry_window_days": entry_days,
        "starting_nav": starting_nav,
        "ending_nav": ending_nav,
        "total_return": ending_nav / starting_nav - 1.0,
        "optimistic_entry_window_geometric_daily_growth": optimistic_entry_window_geo,
        "capital_occupation_days": occupation_days,
        "capital_occupation_geometric_daily_growth": occupation_geo,
        "raw_trades": raw_trades,
        "independent_causal_episodes": independent_episodes,
        "raw_trades_per_entry_day": raw_frequency,
        "independent_episodes_per_entry_day": independent_frequency,
        "raw_wins": wins,
        "raw_losses": losses,
        "raw_win_rate": wins / raw_trades if raw_trades else 0.0,
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "mean_after_cost_r": sum(r_values) / len(r_values) if r_values else None,
        "r_distribution": quantiles(r_values),
        "holding_minutes": quantiles(holds_minutes),
        "episode_wins": episode_wins,
        "episode_losses": episode_losses,
        "episode_win_rate": episode_wins / independent_episodes if independent_episodes else 0.0,
        "episode_mean_after_cost_r": sum(episode_r) / len(episode_r) if episode_r else None,
        "trades_per_episode": quantiles([float(value) for value in episode_trade_counts]),
        "fresh_condition_entries": fresh_entries,
        "persistent_condition_reentries": persistent_entries,
        "per_symbol": dict(sorted(per_symbol.items())),
        "per_side": dict(sorted(per_side.items())),
        "end_state": {
            "closed_position_rows": len(closed_positions),
            "open_position_rows": len(open_positions),
            "apparently_open_order_rows": len(open_orders),
            "end_flat": not open_positions and not open_orders,
            "closed_scenarios": len(scenarios),
            "closed_scenarios_match_closed_positions": len(scenarios) == len(closed_positions),
        },
        "strategy_diagnostics": diagnostics,
        "runner_metrics": {
            key: metrics.get(key)
            for key in (
                "calendar_days",
                "geometric_daily_growth",
                "max_drawdown",
                "min_equity",
                "trades",
                "wins",
                "losses",
                "profit_factor",
                "largest_winner_share",
            )
        },
        "interpretation": {
            "do_not_treat_raw_reentries_as_independent_frequency": True,
            "do_not_compare_runner_daily_growth_to_seven_day_entry_window_without_adjustment": True,
            "source_fidelity_before_repair": "transition-only, ~39-candle warmup, 360m cap",
            "source_fidelity_now": "200-candle warmup, every true source candle, up to 7d source anatomy with forced runoff flatten",
        },
    }


def preserve() -> None:
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
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
        source = OUTPUT / name
        if source.is_file():
            shutil.copy2(source, EVIDENCE / name)


def main() -> int:
    if not (DATA_START < ENTRY_START <= ENTRY_END < DATA_END):
        raise RuntimeError("invalid warmup/entry/runoff interval ordering")
    WORK.mkdir(parents=True, exist_ok=True)
    returncode = run_source()
    preserve()
    summary = analyze(returncode)
    write_json(EVIDENCE / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    if returncode != 0 or not summary.get("produced"):
        return returncode or 1
    if not (summary.get("end_state") or {}).get("end_flat"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
