"""Materialize prelocked v69 variant configs without using market outcomes."""
from __future__ import annotations

from pathlib import Path
import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if lock["status"] != "LOCKED_REVEALED_WEEK_FAMILY_BEFORE_NAUTILUSTRADER_RUN":
        raise ValueError("unexpected v69 family status")
    if lock["custom_backtest_engine"] is not False:
        raise ValueError("custom engine is forbidden")
    args.output.mkdir(parents=True, exist_ok=True)

    for variant in lock["prelocked_variants"]:
        scenario = dict(lock["base_scenario"])
        scenario["mode"] = variant["mode"]
        scenario["recovery_window_minutes"] = variant["recovery_window_minutes"]
        config = {
            "candidate": f"candidate-02-v69-{variant['label'].replace('_', '-')}",
            "scenario": scenario,
            "risk": {
                "starting_nav_usdt": lock["fixed_risk"]["starting_nav_usdt"],
                "risk_fraction": lock["fixed_risk"]["risk_fraction"],
                "quantity_rule": "current NautilusTrader account NAV times 3% divided by expected per-unit stop loss including entry and stop fees, slippage, impact and funding",
                "maximum_notional_cap": lock["fixed_risk"]["maximum_notional_cap"],
                "score_risk_multiplier": lock["fixed_risk"]["score_risk_multiplier"],
            },
            "costs": dict(lock["fixed_costs"]),
            "validation": {
                "first_week_start": "2024-10-28",
                "selection_seed": 2026080669,
                "selection_stage": "revealed v66 week; prelocked v69 adjacent recovery-window family",
                "warmup_days": 2,
                "exit_buffer_minutes": 60,
                "log_level": "ERROR",
                "pass_criteria": {
                    "minimum_trades_per_day": lock["pass_criteria"]["minimum_trades_per_day"],
                    "minimum_win_rate": lock["pass_criteria"]["minimum_win_rate"],
                    "minimum_profit_factor": lock["pass_criteria"]["minimum_profit_factor_after_cost"],
                    "minimum_geometric_daily_growth": lock["pass_criteria"]["minimum_geometric_daily_growth_after_cost"],
                    "maximum_drawdown": lock["pass_criteria"]["maximum_mark_to_market_drawdown"],
                },
            },
        }
        path = args.output / f"{variant['label']}.json"
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
