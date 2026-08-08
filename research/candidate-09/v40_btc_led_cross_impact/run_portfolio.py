#!/usr/bin/env python3
"""Process-isolated one-account evaluation for BTC-led lagged cross-impact."""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
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
PERIODS = {
    "development": {
        "build_start": date(2024, 6, 24),
        "build_end": date(2024, 7, 31),
        "evaluation_start": date(2024, 7, 1),
        "evaluation_end": date(2024, 7, 31),
    },
    "holdout": {
        "build_start": date(2025, 6, 24),
        "build_end": date(2025, 7, 31),
        "evaluation_start": date(2025, 7, 1),
        "evaluation_end": date(2025, 7, 31),
    },
    "long": {
        "build_start": date(2024, 7, 25),
        "build_end": date(2025, 6, 30),
        "evaluation_start": date(2024, 8, 1),
        "evaluation_end": date(2025, 6, 30),
    },
}
VARIANTS = {
    "btc-leader": True,
    "no-leader-control": False,
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def prepare_configs(
    candidate05: Path,
    candidate16: Path,
    *,
    require_btc_leader: bool,
) -> None:
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
            "candidate40_require_btc_leader": bool(require_btc_leader),
            "candidate40_leader_max_age_bars": 3,
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
        config["execution_seed"] = 400040
        config["strategy"] = strategy
        write_json(path, config)


def patch_shared_runner(candidate05: Path) -> Any:
    if str(candidate05) not in sys.path:
        sys.path.insert(0, str(candidate05))
    from portfolio_strategy import STRATEGY_PATHS
    import shared_account_backtest as shared

    def fake_load_validated_winner(path: Path):
        del path
        return (
            {
                "classification": "V40_PRE_REGISTERED_COMPONENT",
                "winner": "candidate-09-v40-btc-led-cross-impact",
                "source": (
                    "BTC V35 retained; alt V35 acceptance requires prior completed BTC lead"
                ),
            },
            "candidate-09-v40-btc-led-cross-impact",
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
    return {
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


def compact(metrics: dict[str, Any], checks: dict[str, bool]) -> dict[str, Any]:
    diagnostics = metrics.get("strategy_diagnostics", {})
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
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "max_drawdown": metrics.get("max_drawdown"),
        "active_days": metrics.get("active_days"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "symbol_metrics": metrics.get("symbol_metrics"),
        "scenario_metrics": metrics.get("scenario_metrics"),
        "global_slot_audit": metrics.get("global_slot_audit"),
        "integrity_checks": metrics.get("integrity_checks"),
        "candidate40_diagnostics": {
            symbol: {
                key: value
                for key, value in values.items()
                if key.startswith("candidate40_") or key.startswith("shared_slot_")
            }
            for symbol, values in diagnostics.items()
        },
    }


def run_stage(
    *,
    work: Path,
    cache: Path,
    output: Path,
    period_name: str,
    variant: str,
) -> dict[str, Any]:
    period = PERIODS[period_name]
    candidate05 = work / "research" / "candidate-05"
    candidate16 = work / "research" / "candidate-16"
    prepare_configs(
        candidate05,
        candidate16,
        require_btc_leader=VARIANTS[variant],
    )
    if str(candidate16) not in sys.path:
        sys.path.insert(0, str(candidate16))
    if str(candidate05) not in sys.path:
        sys.path.insert(0, str(candidate05))
    from portfolio_strategy import reset_shared_btc_leader_context

    reset_shared_btc_leader_context()
    shared = patch_shared_runner(candidate05)
    return shared.run_shared_account(
        winner_evidence_path=output / "pre_registered_component.json",
        build_start=period["build_start"],
        build_end=period["build_end"],
        evaluation_start=period["evaluation_start"],
        evaluation_end=period["evaluation_end"],
        cache_root=cache,
        output=output,
    )


def run_stage_isolated(
    *,
    root: Path,
    work: Path,
    cache: Path,
    output: Path,
    period_name: str,
    variant: str,
) -> dict[str, Any]:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(root / "run_bootstrapped.py"),
        "--work",
        str(work),
        "--cache",
        str(cache),
        "--output",
        str(output),
        "--mode",
        "stage",
        "--period",
        period_name,
        "--variant",
        variant,
    ]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    (output / "stage_stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (output / "stage_stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"{period_name}/{variant} failed with code {completed.returncode}: "
            f"{completed.stderr[-5000:]}"
        )
    metrics_path = output / "metrics.json"
    if not metrics_path.exists():
        raise RuntimeError(f"missing shared metrics: {metrics_path}")
    return json.loads(metrics_path.read_text(encoding="utf-8"))


def select(results: dict[str, dict[str, Any]], period: dict[str, date]) -> str | None:
    eligible = [
        name
        for name, metrics in results.items()
        if all(gate(metrics, period).values())
    ]
    return max(
        eligible,
        key=lambda name: (
            float(results[name].get("geometric_daily_growth", -1.0)),
            -float(results[name].get("max_drawdown", 1.0)),
        ),
        default=None,
    )


def run_pipeline(args: argparse.Namespace) -> int:
    work = args.work.resolve()
    output = args.output.resolve()
    root = Path(__file__).resolve().parent
    output.mkdir(parents=True, exist_ok=True)
    decision: dict[str, Any] = {
        "candidate": "candidate-09-v40-btc-led-lagged-cross-impact",
        "source_lineage": {
            "preserved": "positive BTC V35 completed-auction footprint component",
            "external_mechanism": "lagged cross-asset order flow improves short-horizon return forecasts",
            "alt_context": "strictly prior completed BTC repricing stronger than lagging alt progress",
            "local_state": "alt completed-auction true acceptance plus footprint stack",
            "execution": "first defended retest with V35 invalidation, natural target, costs and 3% NAV risk",
            "exact_control": "identical four-market portfolio with BTC-leader requirement disabled",
        },
        "development_period": {
            key: str(value) for key, value in PERIODS["development"].items()
        },
        "holdout_period_reserved": {
            key: str(value) for key, value in PERIODS["holdout"].items()
        },
        "long_period_reserved": {
            key: str(value) for key, value in PERIODS["long"].items()
        },
        "holdout_opened_once": False,
        "long_opened_once": False,
    }
    try:
        development_full = {
            variant: run_stage_isolated(
                root=root,
                work=work,
                cache=args.cache.resolve() / "development",
                output=output / "development" / variant,
                period_name="development",
                variant=variant,
            )
            for variant in VARIANTS
        }
    except Exception as exc:
        decision["status"] = "IMPLEMENTATION_ERROR"
        decision["error"] = f"{type(exc).__name__}: {exc}"
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2

    decision["development"] = {
        name: compact(metrics, gate(metrics, PERIODS["development"]))
        for name, metrics in development_full.items()
    }
    selected = select(development_full, PERIODS["development"])
    decision["selected_variant"] = selected
    if selected is None:
        if not all(
            bool(metrics.get("integrity_pass")) for metrics in development_full.values()
        ):
            decision["status"] = "IMPLEMENTATION_ERROR"
        else:
            decision["status"] = "LOGIC_ERROR_NO_STRUCTURAL_PATH"
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 0 if decision["status"] != "IMPLEMENTATION_ERROR" else 2

    decision["holdout_opened_once"] = True
    try:
        holdout = run_stage_isolated(
            root=root,
            work=work,
            cache=args.cache.resolve() / "holdout",
            output=output / "holdout" / selected,
            period_name="holdout",
            variant=selected,
        )
    except Exception as exc:
        decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
        decision["holdout_error"] = f"{type(exc).__name__}: {exc}"
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2
    holdout_checks = gate(holdout, PERIODS["holdout"])
    decision["holdout"] = compact(holdout, holdout_checks)
    if not holdout_checks["integrity"]:
        decision["status"] = "HOLDOUT_IMPLEMENTATION_ERROR"
    elif not all(holdout_checks.values()):
        decision["status"] = "HOLDOUT_LOGIC_FAIL_FAMILY_RETIRED"
    else:
        decision["long_opened_once"] = True
        try:
            long_result = run_stage_isolated(
                root=root,
                work=work,
                cache=args.cache.resolve() / "long",
                output=output / "long" / selected,
                period_name="long",
                variant=selected,
            )
            long_checks = gate(long_result, PERIODS["long"])
            decision["long"] = compact(long_result, long_checks)
            if not long_checks["integrity"]:
                decision["status"] = "LONG_IMPLEMENTATION_ERROR"
            elif all(long_checks.values()):
                decision["status"] = "TARGET_VALIDATED_LONG_CONTINUOUS"
            else:
                decision["status"] = "LONG_LOGIC_FAIL_FAMILY_RETIRED"
        except Exception as exc:
            decision["status"] = "LONG_IMPLEMENTATION_ERROR"
            decision["long_error"] = f"{type(exc).__name__}: {exc}"

    write_json(output / "FINAL_DECISION.json", decision)
    print(json.dumps(decision, indent=2, sort_keys=True))
    return 0 if "IMPLEMENTATION_ERROR" not in decision["status"] else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("pipeline", "stage"), default="pipeline")
    parser.add_argument("--period", choices=tuple(PERIODS), default="development")
    parser.add_argument("--variant", choices=tuple(VARIANTS), default="btc-leader")
    args = parser.parse_args()
    if args.mode == "stage":
        metrics = run_stage(
            work=args.work.resolve(),
            cache=args.cache.resolve(),
            output=args.output.resolve(),
            period_name=args.period,
            variant=args.variant,
        )
        print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=True))
        return 0
    return run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
