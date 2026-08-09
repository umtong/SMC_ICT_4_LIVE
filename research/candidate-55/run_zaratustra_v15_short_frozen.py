"""Fresh validation for the frozen V15 independent-short family.

The family was selected from directional decomposition of two already observed
seven-day windows.  This runner changes no source threshold or management rule.
It first uses a new 30-day interval.  Only a component with enough post-cost
growth, opportunity density, expectancy and spare account occupancy is allowed
to consume a 90-day replay.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-short-frozen"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-short-frozen"
FRESH_30D = ("2026-04-01", "2026-04-30")
OLDER_90D = ("2025-10-01", "2025-12-29")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def create_config() -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    source = json.loads((REUSED / "config.json").read_text(encoding="utf-8"))
    config = copy.deepcopy(source)
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
            "max_hold_minutes": 1_000_000,
            "funding_flatten_minute": 60,
            "funding_blackout_before_minutes": -1,
            "funding_blackout_after_minutes": -1,
            "zaratustra_variant": "edge_exact",
            "zaratustra_startup_30m_candles": 10,
            "zaratustra_rsi_period": 14,
            "zaratustra_di_period": 14,
            "zaratustra_bb_period": 20,
            "zaratustra_source_leverage": 10.0,
            "zaratustra_source_stoploss": 0.15,
            "zaratustra_trailing_positive": 0.012,
            "zaratustra_trailing_offset": 0.107,
            "zaratustra_emergency_target_fraction": 0.50,
        }
    )
    path = WORK / "config.json"
    dump(path, config)
    manifest = {
        "candidate": "candidate-55",
        "family": "V15_EDGE_EXACT_SHORT",
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/ZaratustraV15.py",
            "blob_sha": "7f1e39e37949d732fa6b675b93fd808a73b8445c",
        },
        "selection_evidence": {
            "first_interval": {
                "dates": ["2026-07-22", "2026-07-28"],
                "closed_short_trades": 9,
                "closed_short_pnl_usdt": 5028.518333,
                "closed_short_win_rate": 0.7777777778,
                "closed_short_profit_factor": 1.809398,
            },
            "second_interval": {
                "dates": ["2026-06-22", "2026-06-28"],
                "closed_short_trades": 19,
                "closed_short_pnl_usdt": 13200.075949,
                "closed_short_win_rate": 0.7894736842,
                "closed_short_profit_factor": 2.078296,
            },
            "long_side_reason_for_removal": (
                "Long edges lost 14903.800843 USDT with PF 0.476479 in the "
                "second interval while short edges stayed positive in both."
            ),
            "threshold_search_after_decomposition": False,
        },
        "fresh_30d_interval": list(FRESH_30D),
        "conditional_older_90d_interval": list(OLDER_90D),
        "fresh_component_gate": {
            "geometric_daily_growth": 0.005,
            "completed_trades": 30,
            "profit_factor": 1.25,
            "positive_expectancy": True,
            "max_drawdown": 0.20,
            "max_occupied_fraction_for_component": 0.75,
        },
        "final_project_gate": {
            "geometric_daily_growth": 0.01,
            "completed_trades": 90,
            "max_drawdown": 0.20,
            "no_liquidation_or_execution_violation": True,
        },
        "router_sha256": hashlib.sha256((REUSED / "router.py").read_bytes()).hexdigest(),
        "strategy_sha256": hashlib.sha256((REUSED / "strategy.py").read_bytes()).hexdigest(),
    }
    dump(WORK / "manifest.json", manifest)
    return path


def output_root(stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-short-frozen-{stage}"


def run_backtest(config: Path, stage: str, interval: tuple[str, str]) -> int:
    output = output_root(stage)
    workspace = WORK / stage
    command = [
        sys.executable,
        str(REUSED / "launch.py"),
        "--config",
        str(config),
        "--start",
        interval[0],
        "--end",
        interval[1],
        "--cache",
        str(CACHE),
        "--output",
        str(output),
        "--workspace",
        str(workspace),
    ]
    env = dict(os.environ)
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REUSED) if not previous else str(REUSED) + os.pathsep + previous
    print("RUN", stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    return int(completed.returncode)


def occupancy(root: Path, total_days: int) -> dict[str, float | int]:
    path = root / "closed_scenarios.json"
    if not path.is_file():
        return {"closed_scenarios": 0, "occupied_minutes": 0.0, "occupied_fraction": 0.0}
    rows = json.loads(path.read_text(encoding="utf-8"))
    occupied = sum(
        max(0.0, (int(row["closed_ts_event"]) - int(row["episode_ts"])) / 60_000_000_000.0)
        for row in rows
    )
    total = total_days * 1_440.0
    return {
        "closed_scenarios": len(rows),
        "occupied_minutes": occupied,
        "occupied_fraction": min(1.0, occupied / total if total > 0.0 else 0.0),
    }


def read_result(stage: str, returncode: int, days: int) -> dict[str, Any]:
    root = output_root(stage)
    metrics_path = root / "metrics.json"
    diagnostics_path = root / "strategy_diagnostics.json"
    if not metrics_path.is_file() or not diagnostics_path.is_file():
        return {"produced": False, "returncode": returncode, "stage": stage}
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    row: dict[str, Any] = {
        "produced": True,
        "returncode": returncode,
        "stage": stage,
        "artifact_root": str(root.relative_to(ROOT)),
    }
    for key in (
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
        "largest_winner_share",
        "min_equity",
        "position_counts_by_symbol",
    ):
        row[key] = metrics.get(key)
    row.update(
        {
            "source_signals": diagnostics.get("source_signals_before_execution_filters"),
            "entries": diagnostics.get("entry_submissions"),
            "selected_symbols": diagnostics.get("selected_symbols"),
            "trailing_activations": diagnostics.get("zaratustra_trailing_activations"),
            "trailing_exits": diagnostics.get("zaratustra_trailing_exits"),
            "global_position_violations": diagnostics.get("global_position_violations"),
            "order_rejections": diagnostics.get("order_rejections"),
            "max_open_positions_observed": diagnostics.get("max_open_positions_observed"),
            "real_binance_ohlc_execution": diagnostics.get("real_binance_ohlc_execution"),
            "one_minute_trailing_detail": diagnostics.get("one_minute_trailing_detail"),
            "same_minute_trail_activation_and_hit_allowed": diagnostics.get(
                "same_minute_trail_activation_and_hit_allowed"
            ),
            "trades_per_day": float(metrics.get("trades") or 0) / days,
            **occupancy(root, days),
        }
    )
    return row


def mechanics(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "no_order_rejections": int(row.get("order_rejections") or 0) == 0,
        "one_global_position": int(row.get("global_position_violations") or 0) == 0,
        "max_one_observed_position": int(row.get("max_open_positions_observed") or 0) <= 1,
        "real_binance_ohlc": int(row.get("real_binance_ohlc_execution") or 0) == 1,
        "causal_one_minute_trailing": int(row.get("one_minute_trailing_detail") or 0) == 1,
        "no_same_minute_trail_hindsight": int(
            row.get("same_minute_trail_activation_and_hit_allowed") or 0
        ) == 0,
        "drawdown_lte_20pct": float(row.get("max_drawdown") or 1.0) <= 0.20,
    }


def component_checks(row: dict[str, Any], days: int) -> dict[str, bool]:
    return {
        **mechanics(row),
        "trades_at_least_days": int(row.get("trades") or 0) >= days,
        "geometric_daily_growth_at_least_0_5pct": float(
            row.get("geometric_daily_growth") or 0.0
        ) >= 0.005,
        "profit_factor_at_least_1_25": float(row.get("profit_factor") or 0.0) >= 1.25,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
        "occupied_fraction_lte_75pct": float(row.get("occupied_fraction") or 1.0) <= 0.75,
    }


def project_checks(row: dict[str, Any], days: int) -> dict[str, bool]:
    return {
        **mechanics(row),
        "trades_at_least_days": int(row.get("trades") or 0) >= days,
        "geometric_daily_growth_at_least_1pct": float(
            row.get("geometric_daily_growth") or 0.0
        ) >= 0.01,
        "positive_expectancy": float(row.get("expectancy_usdt") or 0.0) > 0.0,
    }


def main() -> int:
    config = create_config()
    fresh_code = run_backtest(config, "fresh-30d", FRESH_30D)
    fresh = read_result("fresh-30d", fresh_code, 30)
    fresh_component = component_checks(fresh, 30) if fresh.get("produced") else {}
    fresh_project = project_checks(fresh, 30) if fresh.get("produced") else {}
    fresh["component_checks"] = fresh_component
    fresh["component_gate_pass"] = bool(fresh_component) and all(fresh_component.values())
    fresh["project_checks"] = fresh_project
    fresh["project_gate_pass"] = bool(fresh_project) and all(fresh_project.values())

    older: dict[str, Any] = {"produced": False, "not_run_reason": "fresh_component_gate_failed"}
    if fresh.get("component_gate_pass"):
        older_code = run_backtest(config, "older-90d", OLDER_90D)
        older = read_result("older-90d", older_code, 90)
        older_component = component_checks(older, 90) if older.get("produced") else {}
        older_project = project_checks(older, 90) if older.get("produced") else {}
        older["component_checks"] = older_component
        older["component_gate_pass"] = bool(older_component) and all(older_component.values())
        older["project_checks"] = older_project
        older["project_gate_pass"] = bool(older_project) and all(older_project.values())

    if older.get("project_gate_pass"):
        decision = "PASS_90D_PROJECT_GATE"
        production_ready = True
        reason = "Frozen V15 short family passed the full 90-day project gate."
    elif older.get("component_gate_pass"):
        decision = "RETAIN_AS_STRUCTURAL_COMPONENT"
        production_ready = False
        reason = (
            "Frozen V15 short family remained at least 0.5%/day with adequate "
            "density, expectancy and spare slot occupancy over the older 90 days."
        )
    elif fresh.get("project_gate_pass") and not older.get("produced"):
        decision = "FRESH_PASS_BUT_LONGER_EVIDENCE_INCOMPLETE"
        production_ready = False
        reason = "Fresh 30-day project gate passed, but the required older 90-day result is absent."
    elif fresh.get("component_gate_pass"):
        decision = "REJECT_AFTER_OLDER_90D"
        production_ready = False
        reason = "Fresh structural evidence did not survive the older 90-day replay."
    else:
        decision = "STRUCTURALLY_REJECTED_ON_FRESH_30D"
        production_ready = False
        reason = (
            "The directionally frozen family did not supply at least 0.5%/day, "
            "one independent trade per day, PF 1.25, positive expectancy and "
            "sufficient spare account occupancy on the new 30-day interval."
        )

    result = {
        "candidate": "candidate-55",
        "family": "V15_EDGE_EXACT_SHORT",
        "decision": decision,
        "reason": reason,
        "fresh_30d": fresh,
        "older_90d": older,
        "entry_thresholds_changed_after_selection": False,
        "management_changed_after_selection": False,
        "long_horizon_run": bool(older.get("produced")),
        "production_ready": production_ready,
    }
    dump(ARTIFACTS / "zaratustra-v15-short-frozen-final-result.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
