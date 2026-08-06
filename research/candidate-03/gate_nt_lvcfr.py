#!/usr/bin/env python3
"""Fail-fast gate for one frozen NautilusTrader week; never mutates parameters."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metrics", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    config = json.loads(args.config.read_text(encoding="utf-8"))
    checks = {
        "nautilus_engine": metrics.get("engine") == "NautilusTrader 1.230.0 BacktestNode",
        "enough_independent_episodes": metrics["independent_episodes"] >= config["minimum_episodes"],
        "positive_after_cost_expectancy": metrics["mean_episode_pnl"] > 0,
        "win_rate": metrics["win_rate"] >= config["minimum_win_rate"],
        "after_cost_daily_growth": metrics["daily_geometric_growth"] >= config["minimum_daily_geometric_growth"],
        "recoverable_drawdown": metrics["max_drawdown"] < config["maximum_mark_to_market_drawdown"],
        "account_flat_at_end": metrics["incomplete_at_end"] == 0,
        "native_positions_exist": metrics["native_positions"] >= 1,
        "single_slot": bool(metrics["single_slot_enforced"]),
        "risk_fraction": metrics["risk_fraction"] == config["risk_fraction"] and metrics["risk_fraction"] <= 0.03,
    }
    payload = {
        "passed": all(checks.values()),
        "checks": checks,
        "metrics": {key: metrics[key] for key in (
            "signals", "independent_episodes", "win_rate", "mean_episode_pnl",
            "daily_geometric_growth", "max_drawdown", "native_orders", "native_positions",
            "entry_rejections", "incomplete_at_end", "target_met",
        )},
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
