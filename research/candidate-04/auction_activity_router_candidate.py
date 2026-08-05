#!/usr/bin/env python3
"""Candidate-04 v11: causal auction-activity router.

A failed high-impact move and persistent leveraged inventory are opposite
mechanisms. Applying both everywhere caused V8 and V10 to fail in complementary
weeks. V11 makes the mechanisms mutually exclusive:

* normal basis: use the frozen V8 failed-auction and inventory states;
* persistent negative basis with a 240-minute absolute price path >= 600 bps:
  use the frozen V8 stress acceptance/inventory states;
* persistent negative basis with a 240-minute path < 600 bps: suppress V8
  stress continuation and allow only V10 failed price discovery when the
  current trade-index basis is also negative.

The path is scale-free in basis points and uses only closes already observed at
the signal. Costs, 3% current-NAV loss budget, funding, same-bar stop priority,
break-even handling and the global one-position constraint are unchanged.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_ROOT = Path(__file__).parent
v8 = _load_module(
    "candidate04_v11_v8",
    _ROOT / "mesoscale_acceptance_candidate.py",
)
v10 = _load_module(
    "candidate04_v11_v10",
    _ROOT / "impact_exhaustion_candidate.py",
)

Config = v8.Config
CandidateError = v8.CandidateError
Intent = v8.Intent
Trade = v8.Trade
base = v8.v7.v6.v5


@dataclass(frozen=True, slots=True)
class RouterParameters:
    activity_lookback_minutes: int
    low_activity_path_bps_max: float
    impact_requires_negative_basis: bool

    @classmethod
    def load(cls, path: Path) -> "RouterParameters":
        result = cls(**json.loads(path.read_text(encoding="utf-8")))
        result.validate()
        return result

    def validate(self) -> None:
        if self.activity_lookback_minutes < 60:
            raise CandidateError("activity lookback must be at least 60 minutes")
        if self.low_activity_path_bps_max <= 0.0:
            raise CandidateError("low-activity path ceiling must be positive")


def auction_path_bps(
    data: pd.DataFrame,
    parameters: RouterParameters,
) -> pd.Series:
    """Past/current absolute minute path, knowable at each signal close."""

    path = data["close"].astype(float).pct_change(fill_method=None).abs()
    return (
        path.rolling(
            parameters.activity_lookback_minutes,
            min_periods=parameters.activity_lookback_minutes,
        ).sum()
        * 10_000.0
    )


def _with_route_details(
    intent: Intent,
    *,
    route: str,
    path_bps: float,
    basis_bps: float,
) -> Intent:
    return Intent(
        scenario=intent.scenario,
        side=intent.side,
        signal_index=intent.signal_index,
        entry_index=intent.entry_index,
        stop_level=intent.stop_level,
        event_indices=intent.event_indices,
        details={
            **intent.details,
            "auction_route": route,
            "auction_path_240m_bps": path_bps,
            "trade_index_basis_bps": basis_bps,
        },
    )


def collect_routed_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
    impact_parameters: v10.ImpactParameters,
    router: RouterParameters,
) -> tuple[list[Intent], list[dict[str, Any]]]:
    """Collect mutually exclusive V8 and V10 intents by causal auction state."""

    path = auction_path_bps(data, router)

    swing, swing_diagnostics = v8.v7.v6.detect_swing_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    v8.detect_mesoscale_inventory_intents.original_detector = (
        v8.v7.v6.detect_trend_intents
    )
    trend, trend_diagnostics = v8.detect_mesoscale_inventory_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )

    normal_swing = [
        intent
        for intent in swing
        if v8.v7.v6.basis_regime(data, intent.signal_index, config)
        >= config.basis_stress_threshold_bps
    ]
    normal_trend = [
        intent
        for intent in trend
        if v8.v7.v6.basis_regime(data, intent.signal_index, config)
        >= config.basis_stress_threshold_bps
    ]
    stress_failure, stress_failure_diagnostics = (
        v8.v7.v6.detect_stress_failure_intents(data, swing, config)
    )
    stress_shock, stress_shock_diagnostics = (
        v8.v7.detect_stress_inventory_transfer_intents(
            data,
            evaluation_start,
            evaluation_end,
            config,
        )
    )
    impact, impact_diagnostics = v10.detect_impact_exhaustion_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )

    routed: list[Intent] = []
    route_diagnostics: list[dict[str, Any]] = []

    for intent in [*normal_swing, *normal_trend]:
        activity = float(path.iloc[intent.signal_index])
        basis = float(data["trade_index_basis_bps"].iloc[intent.signal_index])
        routed.append(
            _with_route_details(
                intent,
                route="NORMAL_BASIS_V8",
                path_bps=activity,
                basis_bps=basis,
            ),
        )

    for intent in [*stress_failure, *stress_shock]:
        activity = float(path.iloc[intent.signal_index])
        basis = float(data["trade_index_basis_bps"].iloc[intent.signal_index])
        passed = (
            math.isfinite(activity)
            and activity >= router.low_activity_path_bps_max
        )
        route_diagnostics.append(
            {
                "time": data.index[intent.signal_index],
                "signal_index": intent.signal_index,
                "scenario": intent.scenario,
                "auction_route": "HIGH_ACTIVITY_V8_STRESS",
                "auction_path_240m_bps": activity,
                "trade_index_basis_bps": basis,
                "router_passed": passed,
            },
        )
        if passed:
            routed.append(
                _with_route_details(
                    intent,
                    route="HIGH_ACTIVITY_V8_STRESS",
                    path_bps=activity,
                    basis_bps=basis,
                ),
            )

    for intent in impact:
        activity = float(path.iloc[intent.signal_index])
        basis = float(data["trade_index_basis_bps"].iloc[intent.signal_index])
        passed = (
            math.isfinite(activity)
            and activity < router.low_activity_path_bps_max
            and (
                not router.impact_requires_negative_basis
                or basis < 0.0
            )
        )
        route_diagnostics.append(
            {
                "time": data.index[intent.signal_index],
                "signal_index": intent.signal_index,
                "scenario": intent.scenario,
                "auction_route": "LOW_ACTIVITY_NEGATIVE_BASIS_V10",
                "auction_path_240m_bps": activity,
                "trade_index_basis_bps": basis,
                "router_passed": passed,
            },
        )
        if passed:
            routed.append(
                _with_route_details(
                    intent,
                    route="LOW_ACTIVITY_NEGATIVE_BASIS_V10",
                    path_bps=activity,
                    basis_bps=basis,
                ),
            )

    priority = {
        "SWING_FAILED_AUCTION_RESUMPTION": 0,
        "IMPACT_EXHAUSTION_FAILED_PRICE_DISCOVERY": 1,
        "STRESS_INVENTORY_SHOCK_DISPLACEMENT": 2,
        "STRESS_REVERSAL_FAILURE_CONTINUATION": 3,
        "INVENTORY_BACKED_DISPLACEMENT": 4,
    }
    routed.sort(
        key=lambda item: (
            item.entry_index,
            priority.get(item.scenario, 99),
        ),
    )
    diagnostics = [
        *swing_diagnostics,
        *trend_diagnostics,
        *stress_failure_diagnostics,
        *stress_shock_diagnostics,
        *impact_diagnostics,
        *route_diagnostics,
    ]
    return routed, diagnostics


def _execution_config(
    config: Config,
    intent: Intent,
    impact_parameters: v10.ImpactParameters,
) -> Config:
    if intent.scenario != "IMPACT_EXHAUSTION_FAILED_PRICE_DISCOVERY":
        return config
    impact_base = replace(
        config.base,
        target_net_r=impact_parameters.target_net_r,
        trend_max_hold_minutes=impact_parameters.maximum_hold_minutes,
    )
    return replace(config, base=impact_base)


def evaluate_intents(
    data: pd.DataFrame,
    intents: list[Intent],
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
    impact_parameters: v10.ImpactParameters,
) -> tuple[list[Trade], dict[str, Any]]:
    """Execute routed intents with the audited one-position NAV accounting."""

    trades: list[Trade] = []
    nav = config.starting_nav
    occupied_until = -1
    last_inventory_entry = -10**12
    inventory_scenarios = {
        "INVENTORY_BACKED_DISPLACEMENT",
        "STRESS_INVENTORY_SHOCK_DISPLACEMENT",
    }
    for intent in intents:
        if intent.entry_index <= occupied_until:
            continue
        if (
            intent.scenario in inventory_scenarios
            and intent.entry_index - last_inventory_entry
            < config.trend_cooldown_minutes
        ):
            continue
        execution_config = _execution_config(
            config,
            intent,
            impact_parameters,
        )
        trade = base.execute_intent(data, intent, nav, execution_config)
        if trade is None:
            continue
        trades.append(trade)
        nav = trade.nav_after
        occupied_until = trade.exit_index
        if intent.scenario in inventory_scenarios:
            last_inventory_entry = intent.entry_index

    start_day = evaluation_start.normalize()
    end_day = evaluation_end.normalize()
    calendar_days = int((end_day - start_day).days) + 1
    daily_returns: dict[str, float] = {}
    nav_cursor = config.starting_nav
    peak = nav_cursor
    max_drawdown = 0.0
    for day in pd.date_range(start_day, end_day, freq="1D"):
        before = nav_cursor
        day_date = day.date()
        for trade in (item for item in trades if item.exit_time.date() == day_date):
            nav_cursor += trade.net_pnl
            peak = max(peak, nav_cursor)
            max_drawdown = max(max_drawdown, 1.0 - nav_cursor / peak)
        daily_returns[str(day_date)] = nav_cursor / before - 1.0

    ending_nav = trades[-1].nav_after if trades else config.starting_nav
    geometric_daily = (
        (ending_nav / config.starting_nav) ** (1.0 / calendar_days) - 1.0
    )
    positive = [trade.net_pnl for trade in trades if trade.net_pnl > 0.0]
    largest_winner_share = max(positive) / sum(positive) if positive else 1.0
    scenarios = sorted({trade.scenario for trade in trades})
    scenario_metrics: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        selected = [trade for trade in trades if trade.scenario == scenario]
        scenario_metrics[scenario] = {
            "trades": len(selected),
            "wins": sum(trade.net_pnl > 0.0 for trade in selected),
            "net_pnl": sum(trade.net_pnl for trade in selected),
            "mean_net_r": float(np.mean([trade.net_r for trade in selected])),
        }

    checks = {
        "geometric_daily": geometric_daily >= config.gate_min_geom_daily,
        "trades": len(trades) >= config.gate_min_trades,
        "active_days": sum(abs(value) > 1e-12 for value in daily_returns.values())
        >= config.gate_min_active_days,
        "win_rate": (
            sum(trade.net_pnl > 0.0 for trade in trades) / len(trades)
            if trades
            else 0.0
        )
        >= config.gate_min_win_rate,
        "max_drawdown": max_drawdown <= config.gate_max_drawdown,
        "largest_winner_share": largest_winner_share
        <= config.gate_max_largest_winner_share,
        "positive_nav": ending_nav > 0.0,
        "single_position": True,
    }
    metrics = {
        "candidate": "candidate-04-v11-auction-activity-router",
        "calendar_days": calendar_days,
        "starting_nav": config.starting_nav,
        "ending_nav": ending_nav,
        "total_return": ending_nav / config.starting_nav - 1.0,
        "geometric_daily_growth": geometric_daily,
        "trades": len(trades),
        "wins": sum(trade.net_pnl > 0.0 for trade in trades),
        "losses": sum(trade.net_pnl < 0.0 for trade in trades),
        "targets": sum(trade.reason == "TARGET" for trade in trades),
        "win_rate": (
            sum(trade.net_pnl > 0.0 for trade in trades) / len(trades)
            if trades
            else 0.0
        ),
        "sum_net_r": sum(trade.net_r for trade in trades),
        "mean_net_r": float(np.mean([trade.net_r for trade in trades]))
        if trades
        else 0.0,
        "max_drawdown_realized": max_drawdown,
        "active_days": sum(
            abs(value) > 1e-12 for value in daily_returns.values()
        ),
        "largest_winner_share": largest_winner_share,
        "daily_returns": daily_returns,
        "scenario_metrics": scenario_metrics,
        "gate_checks": checks,
        "gate_pass": all(checks.values()),
    }
    return trades, metrics


def run_candidate(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Config,
    impact_parameters: v10.ImpactParameters,
    router: RouterParameters,
) -> tuple[list[Trade], dict[str, Any], list[dict[str, Any]]]:
    intents, diagnostics = collect_routed_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    trades, metrics = evaluate_intents(
        data,
        intents,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    metrics["router_parameters"] = asdict(router)
    metrics["impact_parameters"] = asdict(impact_parameters)
    return trades, metrics, diagnostics


def write_outputs(
    output: Path,
    trades: list[Trade],
    metrics: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    base_config_path: Path,
    impact_config_path: Path,
    router_config_path: Path,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
) -> None:
    base.write_outputs(
        output,
        trades,
        metrics,
        diagnostics,
        router_config_path,
        evaluation_start,
        evaluation_end,
    )
    metrics_path = output / "metrics.json"
    metrics_path.write_text(
        json.dumps(base.serializable(metrics), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_path = output / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["candidate"] = "candidate-04-v11-auction-activity-router"
    extra = dict(run.get("extra", {}))
    extra.update(
        {
            "candidate": "candidate-04-v11-auction-activity-router",
            "base_config": {
                "path": str(base_config_path),
                "sha256": base.sha256_file(base_config_path),
            },
            "impact_config": {
                "path": str(impact_config_path),
                "sha256": base.sha256_file(impact_config_path),
            },
            "router_config": {
                "path": str(router_config_path),
                "sha256": base.sha256_file(router_config_path),
            },
        },
    )
    run["extra"] = extra
    run_path.write_text(
        json.dumps(base.serializable(run), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--impact-config", type=Path, required=True)
    parser.add_argument("--router-config", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--kline-dir", type=Path, required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download-klines", action="store_true")
    args = parser.parse_args()

    config = Config.load(args.base_config)
    impact_parameters = v10.ImpactParameters.load(args.impact_config)
    router = RouterParameters.load(args.router_config)
    evaluation_start = pd.Timestamp(args.evaluation_start, tz="UTC")
    evaluation_end = (
        pd.Timestamp(args.evaluation_end, tz="UTC")
        + pd.Timedelta(hours=23, minutes=59)
    )
    if args.download_klines:
        kline_paths = base.ensure_klines(
            config.symbol,
            evaluation_start.date(),
            evaluation_end.date(),
            args.kline_dir,
        )
    else:
        kline_paths = sorted(args.kline_dir.glob(f"{config.symbol}-1m-*.zip"))
    rich = base.load_rich(args.rich_dir)
    klines = base.load_klines(kline_paths)
    required = pd.date_range(
        evaluation_start.normalize(),
        evaluation_end.normalize(),
        freq="1D",
    ).date
    present = set(klines.index.normalize().date)
    missing = [str(day) for day in required if day not in present]
    if missing:
        raise CandidateError(f"missing evaluation kline days: {missing}")

    data = base.prepare_data(rich, klines, config)
    trades, metrics, diagnostics = run_candidate(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
        router,
    )
    write_outputs(
        args.output,
        trades,
        metrics,
        diagnostics,
        args.base_config,
        args.impact_config,
        args.router_config,
        evaluation_start,
        evaluation_end,
    )
    print(json.dumps(base.serializable(metrics), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
