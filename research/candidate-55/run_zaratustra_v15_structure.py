"""Value-of-information validation for the V15 structural short repair.

The runner does not reduce a candidate to a pass/fail bit.  Every stage records
the opportunity engine, gross-profit engine, gross-loss engine, full-stop
concentration, scenario-family contribution and symbol contribution.

Two untouched short windows are used first.  A 30-day continuous account is
spent only when the short evidence contains a real profit engine or a clearly
isolated losing family.  A 90-day replay is spent only after the repaired
integrated policy is already strong on the short and medium evidence.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

ROOT = Path.cwd()
CANDIDATE = ROOT / "research" / "candidate-55"
REUSED = ROOT / "research" / "candidate-51"
WORK = ROOT / ".work" / "candidate-55-v15-structure"
ARTIFACTS = ROOT / "artifacts" / "candidate-55"
CACHE = ROOT / ".cache" / "candidate-55-v15-structure"

SHORT_WINDOWS = {
    "untouched-short-a": ("2026-03-09", "2026-03-15"),
    "untouched-short-b": ("2026-05-11", "2026-05-17"),
}
MID_WINDOW = ("2025-11-01", "2025-11-30")
LONG_WINDOW = ("2025-06-01", "2025-08-29")
_PNL_PATTERN = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def safe_float(value: Any, default: float = math.nan) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else default
    match = _PNL_PATTERN.search(str(value))
    if match is None:
        return default
    try:
        number = float(match.group(0))
    except ValueError:
        return default
    return number if math.isfinite(number) else default


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
        "family": "V15_STRUCTURAL_SHORT_REPAIR",
        "source": {
            "repository": "remiotore/ccxt-freqtrade",
            "path": "strategies/ZaratustraV15.py",
            "blob_sha": "7f1e39e37949d732fa6b675b93fd808a73b8445c",
        },
        "unchanged": [
            "source V15 edge entries",
            "source stop normalized by source 10x leverage",
            "source trailing activation and distance",
            "NautilusTrader matching and account engine",
            "current-NAV 3% planned-loss sizing",
            "four-symbol one-global-slot account",
            "fees, adverse slippage and funding reserve",
        ],
        "replaced": {
            "DI_state": (
                "accept only a local non-negative 15m pullback inside a "
                "negative 4h auction"
            ),
            "BB_state": (
                "accept only a downside 15m impulse >=1.5 current 5m ATR "
                "with at least 3/4 peers negative over 60m"
            ),
        },
        "development_decomposition": {
            "intervals": [
                ["2026-04-01", "2026-04-30"],
                ["2026-06-22", "2026-06-28"],
                ["2026-07-22", "2026-07-28"],
            ],
            "raw_short_trades": 103,
            "raw_gross_profit_usdt": 72103.99,
            "raw_gross_loss_usdt": 82234.06,
            "diagnosis": (
                "The opportunity engine generated substantial gross profit, "
                "but DI chases and isolated BB crosses converted favourable "
                "episodes into full planned-loss exits."
            ),
            "repaired_selected_trade_replay": {
                "trades": 45,
                "calendar_days": 44,
                "all_three_intervals_positive": True,
                "DI_pullback_resumption": {
                    "trades": 16,
                    "wins": 14,
                    "net_pnl_usdt": 14551.41,
                },
                "BB_clean_synchronized_expansion": {
                    "trades": 29,
                    "wins": 24,
                    "net_pnl_usdt": 21710.02,
                },
            },
            "warning": (
                "The selected-trade replay is development evidence only. "
                "Actual results must be rerouted through the one-slot "
                "continuous account on untouched intervals."
            ),
        },
        "untouched_short_windows": SHORT_WINDOWS,
        "mid_window": list(MID_WINDOW),
        "conditional_long_window": list(LONG_WINDOW),
        "long_resource_rule": (
            "90d is allowed only after both short evidence and the 30d "
            "continuous account show a strong after-cost profit engine."
        ),
        "router_sha256": hashlib.sha256(
            (REUSED / "router.py").read_bytes()
        ).hexdigest(),
        "strategy_sha256": hashlib.sha256(
            (REUSED / "strategy.py").read_bytes()
        ).hexdigest(),
    }
    dump(WORK / "manifest.json", manifest)
    return path


def output_root(stage: str) -> Path:
    return ARTIFACTS / f"zaratustra-v15-structure-{stage}"


def run_backtest(
    config: Path,
    stage: str,
    interval: tuple[str, str],
) -> int:
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
    env["PYTHONPATH"] = (
        str(REUSED)
        if not previous
        else str(REUSED) + os.pathsep + previous
    )
    print("RUN", stage, interval, flush=True)
    completed = subprocess.run(command, env=env, check=False)
    return int(completed.returncode)


def group_stats(
    records: Iterable[tuple[str, float, float]],
) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[tuple[float, float]]] = {}
    for key, pnl, planned in records:
        grouped.setdefault(str(key), []).append((float(pnl), float(planned)))
    result: dict[str, dict[str, float | int]] = {}
    for key, values in sorted(grouped.items()):
        pnls = [item[0] for item in values]
        wins = [value for value in pnls if value > 0.0]
        losses = [-value for value in pnls if value < 0.0]
        full_stops = sum(
            1
            for pnl, planned in values
            if pnl < 0.0
            and planned > 0.0
            and -pnl >= 0.80 * planned
        )
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        result[key] = {
            "trades": len(values),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(values) if values else 0.0,
            "gross_profit_usdt": gross_profit,
            "gross_loss_usdt": gross_loss,
            "net_pnl_usdt": gross_profit - gross_loss,
            "profit_factor": (
                gross_profit / gross_loss
                if gross_loss > 0.0
                else None
            ),
            "average_winner_usdt": (
                gross_profit / len(wins) if wins else 0.0
            ),
            "average_loser_usdt": (
                gross_loss / len(losses) if losses else 0.0
            ),
            "full_planned_loss_count": full_stops,
            "full_planned_loss_fraction_of_losses": (
                full_stops / len(losses) if losses else 0.0
            ),
        }
    return result


def closed_trade_decomposition(root: Path) -> dict[str, Any]:
    path = root / "closed_scenarios.json"
    if not path.is_file():
        return {
            "closed_trades": 0,
            "gross_profit_usdt": 0.0,
            "gross_loss_usdt": 0.0,
            "net_pnl_usdt": 0.0,
            "profit_factor": 0.0,
            "by_family": {},
            "by_symbol": {},
        }
    rows = json.loads(path.read_text(encoding="utf-8"))
    parsed: list[dict[str, Any]] = []
    for row in rows:
        pnl = safe_float(row.get("realized_pnl"))
        if not math.isfinite(pnl):
            continue
        diagnostics = dict(row.get("diagnostics") or {})
        family = str(
            row.get("candidate55_scenario_family")
            or diagnostics.get("candidate55_scenario_family")
            or "UNKNOWN"
        )
        symbol = str(row.get("symbol") or "UNKNOWN")
        planned = safe_float(
            row.get("actual_planned_account_loss"),
            safe_float(row.get("planned_account_loss"), 0.0),
        )
        parsed.append(
            {
                "pnl": pnl,
                "planned": max(0.0, planned),
                "family": family,
                "symbol": symbol,
            }
        )
    wins = [item["pnl"] for item in parsed if item["pnl"] > 0.0]
    losses = [-item["pnl"] for item in parsed if item["pnl"] < 0.0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    full_stops = sum(
        1
        for item in parsed
        if item["pnl"] < 0.0
        and item["planned"] > 0.0
        and -item["pnl"] >= 0.80 * item["planned"]
    )
    family_rows = [
        (item["family"], item["pnl"], item["planned"]) for item in parsed
    ]
    symbol_rows = [
        (item["symbol"], item["pnl"], item["planned"]) for item in parsed
    ]
    by_family = group_stats(family_rows)
    by_symbol = group_stats(symbol_rows)
    losing_family = max(
        by_family.items(),
        key=lambda item: float(item[1]["gross_loss_usdt"]),
        default=(None, {"gross_loss_usdt": 0.0}),
    )
    losing_symbol = max(
        by_symbol.items(),
        key=lambda item: float(item[1]["gross_loss_usdt"]),
        default=(None, {"gross_loss_usdt": 0.0}),
    )
    return {
        "closed_trades": len(parsed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(parsed) if parsed else 0.0,
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "net_pnl_usdt": gross_profit - gross_loss,
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0.0
            else None
        ),
        "average_winner_usdt": (
            gross_profit / len(wins) if wins else 0.0
        ),
        "average_loser_usdt": (
            gross_loss / len(losses) if losses else 0.0
        ),
        "full_planned_loss_count": full_stops,
        "full_planned_loss_fraction_of_losses": (
            full_stops / len(losses) if losses else 0.0
        ),
        "by_family": by_family,
        "by_symbol": by_symbol,
        "dominant_losing_family": losing_family[0],
        "dominant_losing_family_loss_share": (
            float(losing_family[1]["gross_loss_usdt"]) / gross_loss
            if gross_loss > 0.0
            else 0.0
        ),
        "dominant_losing_symbol": losing_symbol[0],
        "dominant_losing_symbol_loss_share": (
            float(losing_symbol[1]["gross_loss_usdt"]) / gross_loss
            if gross_loss > 0.0
            else 0.0
        ),
    }


def occupancy(root: Path, total_days: int) -> dict[str, float | int]:
    path = root / "closed_scenarios.json"
    if not path.is_file():
        return {
            "closed_scenarios": 0,
            "occupied_minutes": 0.0,
            "occupied_fraction": 0.0,
        }
    rows = json.loads(path.read_text(encoding="utf-8"))
    occupied = sum(
        max(
            0.0,
            (
                int(row["closed_ts_event"])
                - int(row["episode_ts"])
            )
            / 60_000_000_000.0,
        )
        for row in rows
        if row.get("closed_ts_event") is not None
        and row.get("episode_ts") is not None
    )
    total = total_days * 1_440.0
    return {
        "closed_scenarios": len(rows),
        "occupied_minutes": occupied,
        "occupied_fraction": min(
            1.0,
            occupied / total if total > 0.0 else 0.0,
        ),
    }


def mechanics(row: dict[str, Any]) -> dict[str, bool]:
    return {
        "positive_equity": float(row.get("min_equity") or 0.0) > 0.0,
        "no_order_rejections": int(row.get("order_rejections") or 0) == 0,
        "one_global_position": (
            int(row.get("global_position_violations") or 0) == 0
        ),
        "max_one_observed_position": (
            int(row.get("max_open_positions_observed") or 0) <= 1
        ),
        "real_binance_ohlc": (
            int(row.get("real_binance_ohlc_execution") or 0) == 1
        ),
        "causal_one_minute_trailing": (
            int(row.get("one_minute_trailing_detail") or 0) == 1
        ),
        "no_same_minute_trail_hindsight": (
            int(
                row.get(
                    "same_minute_trail_activation_and_hit_allowed"
                )
                or 0
            )
            == 0
        ),
    }


def structural_interpretation(row: dict[str, Any], days: int) -> dict[str, Any]:
    decomposition = dict(row.get("decomposition") or {})
    by_family = dict(decomposition.get("by_family") or {})
    family_profit_engines = [
        family
        for family, stats in by_family.items()
        if int(stats.get("trades") or 0) >= 4
        and float(stats.get("gross_profit_usdt") or 0.0) > 0.0
        and float(stats.get("profit_factor") or 0.0) >= 1.25
    ]
    gross_profit = float(decomposition.get("gross_profit_usdt") or 0.0)
    gross_loss = float(decomposition.get("gross_loss_usdt") or 0.0)
    pf = float(decomposition.get("profit_factor") or 0.0)
    trades = int(decomposition.get("closed_trades") or 0)
    return {
        "opportunity_density_trades_per_day": (
            trades / days if days > 0 else 0.0
        ),
        "gross_profit_engine_present": (
            gross_profit > 0.0 and trades >= max(4, days // 2)
        ),
        "family_profit_engines": family_profit_engines,
        "integrated_profit_engine_strong": (
            trades >= max(6, days // 2)
            and pf >= 1.25
            and gross_profit > gross_loss
        ),
        "loss_engine_full_stop_fraction": float(
            decomposition.get(
                "full_planned_loss_fraction_of_losses"
            )
            or 0.0
        ),
        "loss_engine_family_concentration": float(
            decomposition.get(
                "dominant_losing_family_loss_share"
            )
            or 0.0
        ),
        "loss_engine_symbol_concentration": float(
            decomposition.get(
                "dominant_losing_symbol_loss_share"
            )
            or 0.0
        ),
        "current_net_is_not_candidate_value": True,
    }


def read_result(
    stage: str,
    returncode: int,
    days: int,
) -> dict[str, Any]:
    root = output_root(stage)
    metrics_path = root / "metrics.json"
    diagnostics_path = root / "strategy_diagnostics.json"
    if not metrics_path.is_file() or not diagnostics_path.is_file():
        return {
            "produced": False,
            "returncode": returncode,
            "stage": stage,
        }
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(
        diagnostics_path.read_text(encoding="utf-8")
    )
    row: dict[str, Any] = {
        "produced": True,
        "returncode": returncode,
        "stage": stage,
        "days": days,
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
            "source_signals": diagnostics.get(
                "source_signals_before_execution_filters"
            ),
            "entries": diagnostics.get("entry_submissions"),
            "selected_symbols": diagnostics.get("selected_symbols"),
            "trailing_activations": diagnostics.get(
                "zaratustra_trailing_activations"
            ),
            "trailing_exits": diagnostics.get(
                "zaratustra_trailing_exits"
            ),
            "global_position_violations": diagnostics.get(
                "global_position_violations"
            ),
            "order_rejections": diagnostics.get("order_rejections"),
            "max_open_positions_observed": diagnostics.get(
                "max_open_positions_observed"
            ),
            "real_binance_ohlc_execution": diagnostics.get(
                "real_binance_ohlc_execution"
            ),
            "one_minute_trailing_detail": diagnostics.get(
                "one_minute_trailing_detail"
            ),
            "same_minute_trail_activation_and_hit_allowed": (
                diagnostics.get(
                    "same_minute_trail_activation_and_hit_allowed"
                )
            ),
            "unresolved_reason_counts": diagnostics.get(
                "unresolved_reason_counts"
            ),
            "actionable_family_counts": diagnostics.get(
                "actionable_family_counts"
            ),
            **occupancy(root, days),
        }
    )
    row["decomposition"] = closed_trade_decomposition(root)
    row["mechanics"] = mechanics(row)
    row["mechanics_valid"] = all(row["mechanics"].values())
    row["structural_interpretation"] = structural_interpretation(
        row,
        days,
    )
    return row


def aggregate_decompositions(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    flattened: list[tuple[str, float, float, str]] = []
    for row in rows:
        stage = str(row.get("stage") or "UNKNOWN")
        root_text = row.get("artifact_root")
        if not root_text:
            continue
        root = ROOT / str(root_text)
        path = root / "closed_scenarios.json"
        if not path.is_file():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            pnl = safe_float(item.get("realized_pnl"))
            if not math.isfinite(pnl):
                continue
            diagnostics = dict(item.get("diagnostics") or {})
            family = str(
                item.get("candidate55_scenario_family")
                or diagnostics.get("candidate55_scenario_family")
                or "UNKNOWN"
            )
            planned = safe_float(
                item.get("actual_planned_account_loss"),
                safe_float(item.get("planned_account_loss"), 0.0),
            )
            flattened.append((family, pnl, max(0.0, planned), stage))
    wins = [pnl for _, pnl, _, _ in flattened if pnl > 0.0]
    losses = [-pnl for _, pnl, _, _ in flattened if pnl < 0.0]
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    by_family = group_stats(
        (family, pnl, planned)
        for family, pnl, planned, _ in flattened
    )
    by_stage = group_stats(
        (stage, pnl, planned)
        for _, pnl, planned, stage in flattened
    )
    return {
        "trades": len(flattened),
        "wins": len(wins),
        "losses": len(losses),
        "gross_profit_usdt": gross_profit,
        "gross_loss_usdt": gross_loss,
        "net_pnl_usdt": gross_profit - gross_loss,
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0.0
            else None
        ),
        "by_family": by_family,
        "by_stage": by_stage,
    }


def short_evidence_worth_mid(rows: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    aggregate = aggregate_decompositions(rows)
    valid = all(
        row.get("produced") and row.get("mechanics_valid")
        for row in rows
    )
    family_engines = [
        family
        for family, stats in dict(
            aggregate.get("by_family") or {}
        ).items()
        if int(stats.get("trades") or 0) >= 4
        and float(stats.get("profit_factor") or 0.0) >= 1.25
    ]
    overall_pf = float(aggregate.get("profit_factor") or 0.0)
    trades = int(aggregate.get("trades") or 0)
    worth = bool(
        valid
        and trades >= 8
        and (
            overall_pf >= 1.15
            or bool(family_engines)
        )
    )
    return worth, {
        "mechanics_valid": valid,
        "aggregate": aggregate,
        "family_profit_engines": family_engines,
        "decision_basis": (
            "Continue when the two untouched windows expose either a strong "
            "integrated engine or a substantial family-level profit engine. "
            "Net sign alone is not the criterion."
        ),
        "worth_mid": worth,
    }


def long_resource_warranted(
    short_analysis: dict[str, Any],
    mid: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    short_aggregate = dict(short_analysis.get("aggregate") or {})
    mid_structural = dict(mid.get("structural_interpretation") or {})
    mid_decomposition = dict(mid.get("decomposition") or {})
    reasons = {
        "short_mechanics_valid": bool(
            short_analysis.get("mechanics_valid")
        ),
        "short_profit_factor_at_least_1_15": (
            float(short_aggregate.get("profit_factor") or 0.0) >= 1.15
        ),
        "mid_mechanics_valid": bool(mid.get("mechanics_valid")),
        "mid_integrated_profit_engine_strong": bool(
            mid_structural.get("integrated_profit_engine_strong")
        ),
        "mid_geometric_daily_growth_at_least_0_5pct": (
            float(mid.get("geometric_daily_growth") or 0.0) >= 0.005
        ),
        "mid_trades_at_least_20": (
            int(mid_decomposition.get("closed_trades") or 0) >= 20
        ),
        "mid_max_drawdown_lte_20pct": (
            float(mid.get("max_drawdown") or 1.0) <= 0.20
        ),
    }
    warranted = all(reasons.values())
    return warranted, {
        "checks": reasons,
        "warranted": warranted,
        "meaning": (
            "This controls only whether a 90d replay has enough information "
            "value. It is not a claim that a non-promoted candidate has no "
            "reusable component."
        ),
    }


def main() -> int:
    config = create_config()
    short_rows: list[dict[str, Any]] = []
    for stage, interval in SHORT_WINDOWS.items():
        code = run_backtest(config, stage, interval)
        short_rows.append(read_result(stage, code, 7))

    worth_mid, short_analysis = short_evidence_worth_mid(short_rows)
    mid: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "untouched_short_profit_engine_not_found",
    }
    long_row: dict[str, Any] = {
        "produced": False,
        "not_run_reason": "mid_not_run",
    }
    long_analysis: dict[str, Any] = {
        "warranted": False,
        "not_evaluated": True,
    }

    if worth_mid:
        mid_code = run_backtest(config, "untouched-mid-30d", MID_WINDOW)
        mid = read_result("untouched-mid-30d", mid_code, 30)
        warranted, long_analysis = long_resource_warranted(
            short_analysis,
            mid,
        )
        if warranted:
            long_code = run_backtest(
                config,
                "frozen-long-90d",
                LONG_WINDOW,
            )
            long_row = read_result(
                "frozen-long-90d",
                long_code,
                90,
            )
        else:
            long_row = {
                "produced": False,
                "not_run_reason": (
                    "short_and_mid_not_yet_strong_enough_for_90d"
                ),
            }

    completed_rows = [
        row
        for row in [*short_rows, mid, long_row]
        if row.get("produced")
    ]
    all_evidence = aggregate_decompositions(completed_rows)

    production_ready = bool(
        long_row.get("produced")
        and long_row.get("mechanics_valid")
        and float(long_row.get("geometric_daily_growth") or 0.0) >= 0.01
        and int(
            dict(long_row.get("decomposition") or {}).get(
                "closed_trades"
            )
            or 0
        )
        >= 90
        and float(long_row.get("max_drawdown") or 1.0) <= 0.20
    )
    if production_ready:
        decision = "LONG_CONTINUOUS_PROJECT_TARGET_MET"
    elif long_row.get("produced"):
        decision = "LONG_EVIDENCE_REQUIRES_LOSS_ENGINE_DIAGNOSIS"
    elif mid.get("produced"):
        decision = "MID_EVIDENCE_REQUIRES_LOSS_ENGINE_DIAGNOSIS"
    else:
        decision = "SHORT_EVIDENCE_REQUIRES_ENGINE_REDESIGN"

    result = {
        "candidate": "candidate-55",
        "family": "V15_STRUCTURAL_SHORT_REPAIR",
        "decision": decision,
        "production_ready": production_ready,
        "short_windows": short_rows,
        "short_analysis": short_analysis,
        "mid_30d": mid,
        "long_resource_analysis": long_analysis,
        "long_90d": long_row,
        "aggregate_completed_evidence": all_evidence,
        "opportunity_engine_changed": False,
        "source_management_changed": False,
        "risk_contract_changed": False,
        "result_interpretation": (
            "Candidate value is read from opportunity density, gross profit, "
            "gross loss, family contribution and fixable loss concentration. "
            "The current net sign is recorded but is not used as the sole "
            "research decision."
        ),
    }
    dump(
        ARTIFACTS / "zaratustra-v15-structure-final-result.json",
        result,
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
