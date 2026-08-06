"""Reproducible NautilusTrader runner for completed-range FVG retest/reacceleration."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
import json
from math import exp, log
from pathlib import Path
import sys
from typing import Any, Mapping

from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data import LoadedBars, load_official_binance_bars
from range_fvg_logic import RangeFVGConfig
from range_fvg_reaccel_logic import (
    RetestReaccelConfig,
    build_range_fvg_reacceleration_signals,
    group_events_by_reason,
)
from range_fvg_strategy import RangeFVGStrategy, RangeFVGStrategyConfig
from run import (
    _build_instrument,
    _create_engine,
    _data_manifest,
    _equity_drawdown,
    _json_safe,
    _logic_events_to_research,
    _ns,
    _parse_utc,
    _position_metrics,
)
from smc_ict_4.event_log import write_events
from smc_ict_4.manifest import create_run_manifest, sha256_file, write_json_atomic


def _range_data_manifest(loaded: LoadedBars, window: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _data_manifest(loaded, window=window)
    manifest["five_minute_pattern_contract"] = {
        "aggregation": "five complete one-minute source-close bars",
        "signal_observation": "separate five-minute reacceleration close after contracted FVG retest",
        "external_levels": "only completed 4-hour/day/week periods",
        "entry": "NautilusTrader limit at observed reacceleration close; no midpoint touch entry",
    }
    return manifest


def run_window(
    *,
    config: Mapping[str, Any],
    config_path: Path,
    window: Mapping[str, Any],
    output_dir: Path,
    data_cache: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    start = _parse_utc(str(window["start"]))
    end = _parse_utc(str(window["end"]))
    if end <= start:
        raise ValueError(f"invalid evaluation window: {window}")

    # Ten warmup days provide complete previous-day/week/4-hour levels before evaluation.
    load_start = start - timedelta(days=10)
    # Post-window bars let pending entries cancel and open positions flatten causally.
    load_end = end + timedelta(hours=4, minutes=10)
    instrument = _build_instrument(config)
    bar_type = BarType.from_str(str(config["bar_type"]))
    loaded = load_official_binance_bars(
        symbol="BTCUSDT",
        interval="1m",
        load_start=load_start,
        load_end=load_end,
        bar_type=bar_type,
        instrument=instrument,
        cache_dir=data_cache,
    )
    data_manifest = _range_data_manifest(loaded, window)
    data_manifest_path = output_dir / "data_manifest.json"
    write_json_atomic(data_manifest_path, _json_safe(data_manifest))

    pattern_config = RangeFVGConfig.from_mapping(dict(config["pattern"]))
    confirmation_config = RetestReaccelConfig.from_mapping(dict(config["confirmation"]))
    bundle = build_range_fvg_reacceleration_signals(
        loaded.frame,
        pattern_config,
        confirmation_config,
    )
    signals_in_window = {
        timestamp: signals
        for timestamp, signals in bundle.signals_by_time_ns.items()
        if _ns(start) <= timestamp < _ns(end)
    }
    strategy = RangeFVGStrategy(
        RangeFVGStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trading_start_ns=_ns(start),
            trading_end_ns=_ns(end),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=Decimal(str(config["effective_fee_rate_per_fill"])),
            minimum_net_reward_risk=Decimal(str(config["minimum_net_reward_risk"])),
            entry_expiry_bars=int(config["pattern"]["entry_expiry_minutes"]),
            maximum_hold_bars=int(config["pattern"]["maximum_hold_minutes"]),
            funding_avoidance_minutes=int(config["funding_avoidance_minutes"]),
        ),
        signals_by_time_ns=signals_in_window,
    )

    engine = _create_engine(config, instrument)
    try:
        engine.add_data(loaded.bars)
        engine.add_strategy(strategy)
        engine.run()

        account = engine.cache.account_for_venue(Venue("BINANCE"))
        if account is None:
            raise RuntimeError("NautilusTrader did not retain the Binance margin account")
        cached_orders = engine.cache.orders()
        cached_positions = engine.cache.positions()
        orders = ReportProvider.generate_orders_report(cached_orders)
        fills = ReportProvider.generate_fills_report(cached_orders)
        positions = ReportProvider.generate_positions_report(cached_positions)
        account_report = ReportProvider.generate_account_report(account)
        orders.to_csv(output_dir / "orders.csv", index=True)
        fills.to_csv(output_dir / "fills.csv", index=True)
        positions.to_csv(output_dir / "positions.csv", index=True)
        account_report.to_csv(output_dir / "account.csv", index=True)

        final_money = account.balance_total(Currency.from_str("USDT"))
        if final_money is None:
            raise RuntimeError("NautilusTrader account had no total USDT balance")
        starting_nav = float(config["starting_nav_usdt"])
        final_nav = float(final_money.as_double())
        days = (end - start).total_seconds() / 86_400.0
        geometric = -1.0 if final_nav <= 0 else exp((log(final_nav) - log(starting_nav)) / days) - 1.0

        position_metrics, enriched_positions = _position_metrics(
            positions,
            strategy.trade_intents,
            strategy.position_outcomes,
        )
        max_drawdown, equity_curve = _equity_drawdown(account_report, starting_nav)
        open_positions = len(engine.cache.positions_open(instrument_id=instrument.id))
        open_orders = len(engine.cache.orders_open(instrument_id=instrument.id))
        result = engine.get_result()
        metrics: dict[str, Any] = {
            "candidate": config["candidate"],
            "window": dict(window),
            "calendar_days": days,
            "starting_nav_usdt": starting_nav,
            "final_nav_usdt": final_nav,
            "nav_multiple": final_nav / starting_nav,
            "total_return": final_nav / starting_nav - 1.0,
            "daily_geometric_growth": geometric,
            "goal_daily_geometric_growth": 0.01,
            "goal_met_in_window": geometric >= 0.01,
            "maximum_realized_equity_drawdown": max_drawdown,
            "detector_signals_in_window": sum(len(items) for items in signals_in_window.values()),
            "detector_diagnostics": bundle.diagnostics,
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "open_positions_after_run": open_positions,
            "open_orders_after_run": open_orders,
            "trade_intents": len(strategy.trade_intents),
            "skipped_setups": len(strategy.skipped_setups),
            "execution_failures": len(strategy.execution_failures),
            "unexpected_or_liquidation_closes": sum(
                item.get("close_reason") == "UNEXPECTED_CLOSE_OR_LIQUIDATION"
                for item in strategy.position_outcomes
            ),
            "logic_event_count": len(strategy.events),
            "logic_reason_counts": group_events_by_reason(strategy.events),
            "position_metrics": position_metrics,
            "nautilus_result": {
                "iterations": result.iterations,
                "total_events": result.total_events,
                "total_orders": result.total_orders,
                "total_positions": result.total_positions,
                "stats_pnls": result.stats_pnls,
                "stats_returns": result.stats_returns,
                "stats_general": getattr(result, "stats_general", {}),
            },
            "data_quality": loaded.quality,
            "cost_assumptions": config["cost_assumptions"],
        }
        if open_positions or open_orders:
            raise RuntimeError(
                f"run ended with exposure: open_positions={open_positions}, open_orders={open_orders}"
            )
        if strategy.execution_failures:
            metrics["execution_failure_details"] = strategy.execution_failures

        write_json_atomic(output_dir / "metrics.json", _json_safe(metrics))
        write_json_atomic(
            output_dir / "trade_intents.json",
            {"trade_intents": _json_safe(strategy.trade_intents)},
        )
        write_json_atomic(
            output_dir / "position_outcomes.json",
            {
                "strategy_callbacks": _json_safe(strategy.position_outcomes),
                "enriched_positions": _json_safe(enriched_positions),
            },
        )
        write_json_atomic(
            output_dir / "skipped_setups.json",
            {"skipped_setups": _json_safe(strategy.skipped_setups)},
        )
        write_json_atomic(output_dir / "equity_curve.json", {"points": equity_curve})
        write_events(
            output_dir / "scenario_events.jsonl",
            _logic_events_to_research(strategy.events, str(instrument.id)),
        )
        run_manifest = create_run_manifest(
            run_id=f"candidate-08-range-fvg-{window['name']}",
            candidate=str(config["candidate"]),
            config_path=config_path,
            data_manifest_path=data_manifest_path,
            extra={
                "window": dict(window),
                "pattern_config": asdict(pattern_config),
                "nautilus_run_config": {
                    "engine": "BacktestEngine",
                    "entry_order_type": "LIMIT",
                    "exit_contingency": "OUO",
                    "oms_type": "HEDGING",
                    "account_type": "MARGIN",
                    "bar_adaptive_high_low_ordering": config["venue"]["bar_adaptive_high_low_ordering"],
                    "liquidation_switch_supported_by_runtime": False,
                },
                "result_summary": {
                    "final_nav_usdt": final_nav,
                    "daily_geometric_growth": geometric,
                    "closed_trades": position_metrics["closed_trades"],
                },
            },
        )
        write_json_atomic(output_dir / "run.json", _json_safe(run_manifest))
        return metrics
    finally:
        engine.dispose()


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    windows: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    total_days = sum(float(item["calendar_days"]) for item in results)
    total_log = sum(log(float(item["nav_multiple"])) for item in results if item["nav_multiple"] > 0)
    combined = exp(total_log / total_days) - 1.0 if total_days else 0.0
    positive_windows = sum(float(item["total_return"]) > 0 for item in results)
    closed = [int(item["position_metrics"]["closed_trades"]) for item in results]
    gate = config["screen_gate"]
    checks = {
        "minimum_closed_trades_each_week": (
            all(value >= int(gate["minimum_closed_trades_per_week"]) for value in closed)
            if suite == "screen"
            else None
        ),
        "minimum_positive_weeks": (
            positive_windows >= int(gate["minimum_positive_weeks"])
            if suite == "screen"
            else None
        ),
        "all_windows_cost_after_positive": (
            all(float(item["total_return"]) > 0 for item in results)
            if suite == "screen"
            else None
        ),
        "no_execution_failures": all(int(item["execution_failures"]) == 0 for item in results),
        "no_residual_exposure": all(
            int(item["open_positions_after_run"]) == 0 and int(item["open_orders_after_run"]) == 0
            for item in results
        ),
    }
    applicable = [value for value in checks.values() if value is not None]
    return {
        "candidate": config["candidate"],
        "suite": suite,
        "predeclared_windows": windows,
        "window_results": [
            {
                "name": item["window"]["name"],
                "final_nav_usdt": item["final_nav_usdt"],
                "total_return": item["total_return"],
                "daily_geometric_growth": item["daily_geometric_growth"],
                "detector_signals": item["detector_signals_in_window"],
                "closed_trades": item["position_metrics"]["closed_trades"],
                "win_rate": item["position_metrics"]["win_rate"],
                "maximum_realized_equity_drawdown": item["maximum_realized_equity_drawdown"],
            }
            for item in results
        ],
        "combined_calendar_days": total_days,
        "combined_daily_geometric_growth": combined,
        "goal_daily_geometric_growth": 0.01,
        "goal_met": combined >= 0.01,
        "positive_windows": positive_windows,
        "closed_trades_by_window": closed,
        "screen_gate_checks": checks,
        "screen_gate_passed": all(applicable) if suite == "screen" and applicable else None,
    }


def run_suite(
    *,
    config_path: Path,
    suite: str,
    output: Path,
    data_cache: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    windows = list(config["suites"][suite])
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for window in windows:
        result = run_window(
            config=config,
            config_path=config_path,
            window=window,
            output_dir=output / str(window["name"]),
            data_cache=data_cache,
        )
        results.append(result)
        print(
            json.dumps(
                {
                    "window": window["name"],
                    "signals": result["detector_signals_in_window"],
                    "closed_trades": result["position_metrics"]["closed_trades"],
                    "win_rate": result["position_metrics"]["win_rate"],
                    "final_nav_usdt": result["final_nav_usdt"],
                    "daily_geometric_growth": result["daily_geometric_growth"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
    summary = _suite_summary(config, suite, windows, results)
    write_json_atomic(output / "suite_metrics.json", _json_safe(summary))
    write_json_atomic(
        output / "run.json",
        _json_safe(
            create_run_manifest(
                run_id=f"candidate-08-range-fvg-reaccel-{suite}",
                candidate=str(config["candidate"]),
                config_path=config_path,
                extra={
                    "suite": suite,
                    "config_sha256": sha256_file(config_path),
                    "summary": summary,
                },
            )
        ),
    )
    print(json.dumps(_json_safe(summary), indent=2, sort_keys=True), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("first", "screen", "long"), default="first")
    parser.add_argument("--config", type=Path, default=HERE / "config_range_fvg_reaccel.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08-range-fvg-reaccel",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        suite=args.suite,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
