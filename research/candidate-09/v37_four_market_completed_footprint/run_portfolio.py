#!/usr/bin/env python3
"""Run frozen V35 unchanged across BTC, ETH, SOL and XRP in one account."""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
import sys
from typing import Any

import pandas as pd
from nautilus_trader.config import ImportableStrategyConfig


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
CONFIG_FILES = {
    "BTCUSDT": "config.json",
    "ETHUSDT": "config_eth.json",
    "SOLUSDT": "config_sol.json",
    "XRPUSDT": "config_xrp.json",
}
DEVELOPMENT = {
    "build_start": date(2024, 6, 24),
    "build_end": date(2024, 7, 31),
    "evaluation_start": date(2024, 7, 1),
    "evaluation_end": date(2024, 7, 31),
}
HOLDOUT = {
    "build_start": date(2025, 6, 24),
    "build_end": date(2025, 7, 31),
    "evaluation_start": date(2025, 7, 1),
    "evaluation_end": date(2025, 7, 31),
}
LONG = {
    "build_start": date(2024, 7, 25),
    "build_end": date(2025, 6, 30),
    "evaluation_start": date(2024, 8, 1),
    "evaluation_end": date(2025, 6, 30),
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def prepare_configs(candidate05: Path, candidate16: Path) -> None:
    v1 = json.loads((candidate16 / "config.json").read_text(encoding="utf-8"))
    strategy = dict(v1["strategy"])
    strategy.update(
        {
            "candidate33_require_stacked_imbalance": True,
            "candidate33_min_stacked_levels": 3,
            "candidate33_stack_boundary_tolerance_atr": 0.25,
            "candidate33_trade_failed_auction": False,
            "candidate35_include_confirmed_swings": False,
            "candidate35_enable_15m": True,
            "candidate35_enable_60m": True,
            "candidate35_enable_daily": True,
        }
    )
    invariant_keys = (
        "starting_nav",
        "risk_fraction",
        "all_in_cost_bps_each_side",
        "adverse_slippage_bps_each_side",
        "venue_leverage",
        "maintenance_margin_rate",
    )
    for symbol, filename in CONFIG_FILES.items():
        path = candidate05 / filename
        config = json.loads(path.read_text(encoding="utf-8"))
        config["symbol"] = symbol
        for key in invariant_keys:
            config[key] = v1[key]
        config["execution_seed"] = 370037
        config["strategy"] = strategy
        write_json(path, config)


def patch_shared_runner(candidate05: Path) -> Any:
    if str(candidate05) not in sys.path:
        sys.path.insert(0, str(candidate05))
    from portfolio_strategy import STRATEGY_PATHS
    import shared_account_backtest as shared

    def fake_load_validated_winner(path: Path):
        return (
            {
                "classification": "FROZEN_V35_COMPONENT_FOR_FREQUENCY_EXTENSION",
                "winner": "candidate-09-v35-completed-auction-footprint",
                "source": "v35 July 2024 development: 2 trades, +3.7249%, PF 2.921",
            },
            "candidate-09-v35-completed-auction-footprint",
        )

    def strategy_path(winner: str, symbol: str) -> str:
        del winner
        return STRATEGY_PATHS[symbol]

    def importable_strategy_configs(
        *,
        winner: str,
        configs: dict[str, dict[str, Any]],
        instrument_ids: dict[str, Any],
        bar_types: dict[str, Any],
        feature_paths: dict[str, Path],
        evaluation_start: date,
        evaluation_end: date,
        output: Path,
    ) -> list[ImportableStrategyConfig]:
        del winner
        start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
        end_ns = int(
            (
                pd.Timestamp(evaluation_end, tz="UTC")
                + pd.Timedelta(days=1)
                - pd.Timedelta(nanoseconds=1)
            ).value
        )
        result: list[ImportableStrategyConfig] = []
        for symbol in SYMBOLS:
            config = configs[symbol]
            values = dict(config["strategy"])
            values.update(
                {
                    "instrument_id": str(instrument_ids[symbol]),
                    "bar_type": str(bar_types[symbol]),
                    "output_dir": str((output / "symbols" / symbol).resolve()),
                    "features_path": str(feature_paths[symbol]),
                    "evaluation_start_ns": start_ns,
                    "evaluation_end_ns": end_ns,
                    "starting_nav": float(config["starting_nav"]),
                    "risk_fraction": float(config["risk_fraction"]),
                    "all_in_cost_bps_each_side": float(
                        config["all_in_cost_bps_each_side"]
                    ),
                    "adverse_slippage_bps_each_side": float(
                        config["adverse_slippage_bps_each_side"]
                    ),
                }
            )
            result.append(
                ImportableStrategyConfig(
                    strategy_path=STRATEGY_PATHS[symbol],
                    config_path="portfolio_strategy:Candidate16Config",
                    config=values,
                )
            )
        return result

    shared.load_validated_winner = fake_load_validated_winner
    shared.final_shared_strategy_path = strategy_path
    shared.importable_strategy_configs = importable_strategy_configs
    return shared


def gate(metrics: dict[str, Any], period: dict[str, date]) -> dict[str, bool]:
    calendar_days = (period["evaluation_end"] - period["evaluation_start"]).days + 1
    checks = {
        "integrity": bool(metrics.get("integrity_pass")),
        "daily_geometric_growth": float(metrics.get("geometric_daily_growth", -1.0))
        >= 0.01,
        "minimum_trades": int(metrics.get("trades", 0))
        >= math.ceil(0.5 * calendar_days),
        "minimum_win_rate": float(metrics.get("win_rate", 0.0)) >= 0.40,
        "minimum_active_days": int(metrics.get("active_days", 0))
        >= math.ceil(0.25 * calendar_days),
        "recoverable_drawdown": float(metrics.get("max_drawdown", 1.0)) <= 0.30,
        "profit_not_single_trade_dominated": float(
            metrics.get("largest_winner_share", 1.0)
        )
        <= 0.35,
    }
    return checks


def compact(metrics: dict[str, Any], checks: dict[str, bool]) -> dict[str, Any]:
    return {
        "gate_pass": all(checks.values()),
        "checks": checks,
        "starting_nav": metrics.get("starting_nav"),
        "ending_nav": metrics.get("ending_nav"),
        "total_return": metrics.get("total_return"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "trades": metrics.get("trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
        "active_days": metrics.get("active_days"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "symbol_metrics": metrics.get("symbol_metrics"),
        "scenario_metrics": metrics.get("scenario_metrics"),
        "global_slot_audit": metrics.get("global_slot_audit"),
        "integrity_checks": metrics.get("integrity_checks"),
    }


def run_period(
    shared: Any,
    *,
    period: dict[str, date],
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    metrics = shared.run_shared_account(
        winner_evidence_path=output / "frozen_component.json",
        build_start=period["build_start"],
        build_end=period["build_end"],
        evaluation_start=period["evaluation_start"],
        evaluation_end=period["evaluation_end"],
        cache_root=cache,
        output=output,
    )
    checks = gate(metrics, period)
    return {"full": metrics, "compact": compact(metrics, checks)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    work = args.work.resolve()
    candidate05 = work / "research" / "candidate-05"
    candidate16 = work / "research" / "candidate-16"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prepare_configs(candidate05, candidate16)
    if str(candidate16) not in sys.path:
        sys.path.insert(0, str(candidate16))
    if str(candidate05) not in sys.path:
        sys.path.insert(0, str(candidate05))
    shared = patch_shared_runner(candidate05)

    decision: dict[str, Any] = {
        "candidate": "candidate-09-v37-four-market-completed-auction-footprint",
        "source_lineage": {
            "frozen_signal": "v35 completed 15m/60m/daily auction + v33 stacked-footprint acceptance",
            "change": "same state machine on BTC/ETH/SOL/XRP in one Nautilus account",
            "global_rule": "unfilled new entry plus open position never exceeds one",
            "risk": "each approved plan sizes from current whole-account NAV at 3% planned loss",
            "btc_control": "v35: 2 trades, 1 win, +3.7249%, PF 2.921, daily 0.1178%",
        },
        "development_period": {key: str(value) for key, value in DEVELOPMENT.items()},
        "holdout_period_reserved": {key: str(value) for key, value in HOLDOUT.items()},
        "long_period_reserved": {key: str(value) for key, value in LONG.items()},
        "holdout_opened_once": False,
        "long_opened_once": False,
    }
    try:
        development = run_period(
            shared,
            period=DEVELOPMENT,
            cache=args.cache / "development",
            output=output / "development",
        )
        decision["development"] = development["compact"]
        if not development["compact"]["checks"]["integrity"]:
            decision["status"] = "IMPLEMENTATION_ERROR"
        elif not development["compact"]["gate_pass"]:
            decision["status"] = "LOGIC_OR_FREQUENCY_FAIL"
        else:
            decision["holdout_opened_once"] = True
            holdout = run_period(
                shared,
                period=HOLDOUT,
                cache=args.cache / "holdout",
                output=output / "holdout",
            )
            decision["holdout"] = holdout["compact"]
            if not holdout["compact"]["checks"]["integrity"]:
                decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
            elif not holdout["compact"]["gate_pass"]:
                decision["status"] = "HOLDOUT_LOGIC_FAIL_FAMILY_RETIRED"
            else:
                decision["long_opened_once"] = True
                long_result = run_period(
                    shared,
                    period=LONG,
                    cache=args.cache / "long",
                    output=output / "long",
                )
                decision["long"] = long_result["compact"]
                if not long_result["compact"]["checks"]["integrity"]:
                    decision["status"] = "LONG_IMPLEMENTATION_ERROR"
                elif long_result["compact"]["gate_pass"]:
                    decision["status"] = "TARGET_VALIDATED_LONG_CONTINUOUS"
                else:
                    decision["status"] = "LONG_LOGIC_FAIL_FAMILY_RETIRED"
    except Exception as exc:
        decision["status"] = "IMPLEMENTATION_ERROR"
        decision["error"] = f"{type(exc).__name__}: {exc}"
    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if "IMPLEMENTATION_ERROR" not in decision["status"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
