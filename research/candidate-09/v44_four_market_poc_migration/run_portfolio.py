#!/usr/bin/env python3
"""Process-isolated one-account evaluation for four-market POC migration."""
from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import json
import math
from pathlib import Path
from typing import Any


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
CONFIG_FILES = {
    "BTCUSDT": "config.json",
    "ETHUSDT": "config_eth.json",
    "SOLUSDT": "config_sol.json",
    "XRPUSDT": "config_xrp.json",
}
PERIODS = {
    "development": {
        "build_start": date(2023, 12, 25),
        "build_end": date(2024, 1, 31),
        "evaluation_start": date(2024, 1, 1),
        "evaluation_end": date(2024, 1, 31),
    },
    "holdout": {
        "build_start": date(2024, 12, 25),
        "build_end": date(2025, 1, 31),
        "evaluation_start": date(2025, 1, 1),
        "evaluation_end": date(2025, 1, 31),
    },
    "long": {
        "build_start": date(2024, 1, 25),
        "build_end": date(2024, 12, 31),
        "evaluation_start": date(2024, 2, 1),
        "evaluation_end": date(2024, 12, 31),
    },
}
VARIANTS = {
    "poc-migration": True,
    "price-only-control": False,
}


def _load_v40_runner():
    path = (
        Path(__file__).resolve().parents[1]
        / "v40_btc_led_cross_impact"
        / "run_portfolio.py"
    )
    spec = importlib.util.spec_from_file_location("candidate09_v40_runner_v44", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load reusable V40 runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_V40 = _load_v40_runner()


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
    """The reused runner's boolean argument is V44's POC-ownership ablation."""
    v1 = json.loads((candidate16 / "config.json").read_text(encoding="utf-8"))
    strategy = dict(v1["strategy"])
    strategy.update(
        {
            "candidate33_require_stacked_imbalance": False,
            "candidate33_min_stacked_levels": 3,
            "candidate33_stack_boundary_tolerance_atr": 0.25,
            "candidate33_trade_failed_auction": False,
            "candidate35_include_confirmed_swings": False,
            "candidate35_enable_15m": True,
            "candidate35_enable_60m": True,
            "candidate35_enable_daily": True,
            "candidate42_require_poc_migration": bool(require_btc_leader),
            "candidate42_min_consecutive_outside_poc_bars": 2,
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
        config["execution_seed"] = 440044
        config["strategy"] = strategy
        write_json(path, config)


_V40.PERIODS = PERIODS
_V40.VARIANTS = VARIANTS
_V40.prepare_configs = prepare_configs


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
        "candidate42_diagnostics": {
            symbol: {
                key: value
                for key, value in values.items()
                if key.startswith("candidate42_") or key.startswith("shared_slot_")
            }
            for symbol, values in diagnostics.items()
        },
    }


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


def run_stage(args: argparse.Namespace) -> int:
    metrics = _V40.run_stage(
        work=args.work.resolve(),
        cache=args.cache.resolve(),
        output=args.output.resolve(),
        period_name=args.period,
        variant=args.variant,
    )
    print(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=True))
    return 0


def run_pipeline(args: argparse.Namespace) -> int:
    work = args.work.resolve()
    output = args.output.resolve()
    root = Path(__file__).resolve().parent
    output.mkdir(parents=True, exist_ok=True)
    decision: dict[str, Any] = {
        "candidate": "candidate-09-v44-four-market-poc-migration",
        "source_lineage": {
            "frozen_signal": "V42 completed-auction price acceptance plus consecutive outside POC migration",
            "change": "same state machine on BTC, ETH, SOL and XRP in one Nautilus account",
            "global_rule": "unfilled new entry plus open position never exceeds one",
            "risk": "each plan sizes from current whole-account NAV at 3% planned loss",
            "exact_control": "identical four-market price acceptance with POC ownership disabled",
            "btc_control": "V42 BTC development: 1 trade, 1 win, +2.1839%; price-only control -13.2272%",
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
            variant: _V40.run_stage_isolated(
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
        decision["status"] = (
            "IMPLEMENTATION_ERROR"
            if not all(bool(value.get("integrity_pass")) for value in development_full.values())
            else "LOGIC_ERROR_NO_STRUCTURAL_PATH"
        )
        write_json(output / "FINAL_DECISION.json", decision)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 2 if decision["status"] == "IMPLEMENTATION_ERROR" else 0

    decision["holdout_opened_once"] = True
    try:
        holdout = _V40.run_stage_isolated(
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
            long_result = _V40.run_stage_isolated(
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
    parser.add_argument("--variant", choices=tuple(VARIANTS), default="poc-migration")
    args = parser.parse_args()
    if args.mode == "stage":
        return run_stage(args)
    return run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
