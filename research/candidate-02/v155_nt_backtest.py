#!/usr/bin/env python3
"""NautilusTrader evaluation of the locked informed-inventory specification."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.persistence.wranglers import BarDataWrangler

from core import max_drawdown
from v53_nt_backtest import (
    _WritableValuesFrame,
    _effective_commission_rates,
    _json_safe,
    _make_btc_instrument,
    _ns,
    _parse_money,
    _write_json,
    _write_records,
)
from v53_nt_core import CostConfig
from v53_nt_strategy import V53RotationStrategy, V53RotationStrategyConfig
from v155_informed_inventory_core import (
    InformedInventoryConfig,
    build_informed_inventory_signals,
    build_informed_inventory_state,
    load_metrics,
    load_raw_one_minute,
    signals_to_json,
    state_funnel,
)

UTC = timezone.utc


def _resolve_input_paths(input_root: Path) -> tuple[Path, Path]:
    raw_root = next((p for p in input_root.rglob("klines") if p.is_dir() and any(p.glob("BTCUSDT-1m-*.zip"))), None)
    metrics_root = next((p for p in input_root.rglob("metrics") if p.is_dir() and any(p.glob("BTCUSDT-metrics-*.zip"))), None)
    if raw_root is None or metrics_root is None:
        raise FileNotFoundError(f"unable to resolve V155 inputs: raw={raw_root}, metrics={metrics_root}")
    return raw_root, metrics_root


def _same_bar_ambiguities(*, raw: pd.DataFrame, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for trade in trades:
        planned = trade.get("planned_signal") or {}
        if trade.get("entry_time_ns") is None or trade.get("exit_time_ns") is None:
            continue
        stop, target = float(planned["stop_price"]), float(planned["target_price"])
        start = pd.Timestamp(int(trade["entry_time_ns"]), unit="ns", tz="UTC")
        end = pd.Timestamp(int(trade["exit_time_ns"]), unit="ns", tz="UTC")
        for observed, bar in raw.loc[(raw.index > start) & (raw.index <= end)].iterrows():
            if float(bar["low"]) <= min(stop, target) and float(bar["high"]) >= max(stop, target):
                result.append({
                    "scenario_id": planned.get("scenario_id"),
                    "bar_close_utc": observed.isoformat(),
                    "bar_low": float(bar["low"]),
                    "bar_high": float(bar["high"]),
                    "stop": stop,
                    "target": target,
                })
    return result


def run_locked_confirmation(*, config_path: Path, input_root: Path, output: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if Decimal(str(config["risk"]["risk_fraction"])) != Decimal("0.03"):
        raise ValueError("candidate-02 risk_fraction must remain exactly 0.03")
    scenario = InformedInventoryConfig.from_mapping(config["scenario"])
    costs = CostConfig.from_mapping(config["costs"])
    evaluation_start = datetime.fromisoformat(
        str(config["validation"]["evaluation_start_utc"]).replace("Z", "+00:00")
    ).astimezone(UTC)
    evaluation_end = evaluation_start + timedelta(days=7)
    data_start = evaluation_start - timedelta(days=int(config["validation"]["warmup_days"]))
    data_end = evaluation_end + timedelta(minutes=int(config["validation"]["exit_buffer_minutes"]))

    raw_directory, metrics_directory = _resolve_input_paths(input_root)
    raw_all = load_raw_one_minute(raw_directory)
    metrics_all = load_metrics(metrics_directory)
    raw = raw_all.loc[(raw_all.index >= pd.Timestamp(data_start)) & (raw_all.index <= pd.Timestamp(data_end))].copy()
    expected = int((data_end - data_start).total_seconds() // 60)
    if len(raw) < expected * 0.995:
        raise ValueError(f"insufficient raw coverage: {len(raw)}/{expected}")
    state = build_informed_inventory_state(raw_one_minute=raw_all, metrics=metrics_all, config=scenario)
    evaluation_state = state.loc[(state.index >= pd.Timestamp(evaluation_start)) & (state.index < pd.Timestamp(evaluation_end))]
    signals = build_informed_inventory_signals(
        state=state,
        raw_one_minute=raw_all,
        evaluation_start=pd.Timestamp(evaluation_start),
        evaluation_end=pd.Timestamp(evaluation_end),
        config=scenario,
        costs=costs,
    )
    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future-information guard failed")
        if not math.isclose(signal.cost_after_reward_risk, scenario.cost_after_target_r, rel_tol=1e-10, abs_tol=1e-10):
            raise AssertionError("locked target R drifted")

    effective_maker, effective_taker = _effective_commission_rates(costs)
    instrument = _make_btc_instrument(maker_fee=effective_maker, taker_fee=effective_taker)
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, instrument).process(_WritableValuesFrame(raw))
    if len(bars) != len(raw):
        raise RuntimeError(f"wrangler row mismatch: {len(raw)} -> {len(bars)}")

    starting_nav = Decimal(str(config["risk"]["starting_nav_usdt"]))
    usdt, venue = Currency.from_str("USDT"), Venue("BINANCE")
    engine = BacktestEngine(config=BacktestEngineConfig(
        logging=LoggingConfig(log_level=str(config["validation"].get("log_level", "ERROR"))),
        run_analysis=True,
    ))
    strategy = V53RotationStrategy(V53RotationStrategyConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        signals_json=signals_to_json(signals),
        risk_fraction=Decimal(str(config["risk"]["risk_fraction"])),
        entry_fee_rate=costs.entry_fee_rate,
        stop_fee_rate=costs.stop_fee_rate,
        entry_slippage_rate=costs.entry_slippage_rate,
        stop_slippage_rate=costs.stop_slippage_rate,
        market_impact_rate=costs.market_impact_rate,
        funding_rate_allowance=costs.funding_rate_allowance,
        trade_start_ns=_ns(evaluation_start),
        trade_end_ns=_ns(evaluation_end),
    ))
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "signals_schedule.json", [value.to_dict() for value in signals])
    _write_json(output / "state_funnel.json", state_funnel(evaluation_state, scenario))
    try:
        venue_kwargs = {
            "venue": venue,
            "oms_type": OmsType.NETTING,
            "account_type": AccountType.MARGIN,
            "starting_balances": [Money(starting_nav, usdt)],
            "base_currency": usdt,
            "default_leverage": Decimal("125"),
            "reject_stop_orders": False,
            "support_contingent_orders": True,
            "use_reduce_only": True,
            "bar_execution": True,
            "bar_adaptive_high_low_ordering": True,
            "trade_execution": True,
            "allow_cash_borrowing": False,
        }
        liquidation_enabled = True
        try:
            engine.add_venue(**venue_kwargs, liquidation_enabled=True, liquidation_cancel_open_orders=True)
        except TypeError as exc:
            if "liquidation_enabled" not in str(exc):
                raise
            liquidation_enabled = False
            engine.add_venue(**venue_kwargs)
        engine.add_instrument(instrument)
        engine.add_data(bars, sort=True)
        engine.add_strategy(strategy)
        engine.run()

        orders = engine.trader.generate_orders_report()
        order_fills = engine.trader.generate_order_fills_report()
        fills = engine.trader.generate_fills_report()
        positions = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(venue)
        orders.to_csv(output / "orders.csv", index=True)
        order_fills.to_csv(output / "order_fills.csv", index=True)
        fills.to_csv(output / "fills.csv", index=True)
        positions.to_csv(output / "positions.csv", index=True)
        account_report.to_csv(output / "account.csv", index=True)
        _write_records(output / "signals.jsonl", strategy.signal_records)
        _write_records(output / "risk_sizing.jsonl", strategy.sizing_records)
        _write_records(output / "trades.jsonl", strategy.trade_records)
        _write_records(output / "nav.jsonl", strategy.nav_records)

        account = engine.cache.account_for_venue(venue)
        if account is None or account.balance_total(usdt) is None:
            raise RuntimeError("backtest account or final balance missing")
        final_nav = account.balance_total(usdt).as_double()
        flat_at_end = engine.portfolio.is_flat(instrument.id)
        if not flat_at_end:
            raise RuntimeError("NautilusTrader ended with an open BTC position")
        nav_values = [float(starting_nav)] + [float(v["nav"]) for v in strategy.nav_records if float(v["nav"]) > 0]
        if not math.isclose(nav_values[-1], final_nav, rel_tol=1e-9, abs_tol=0.01):
            nav_values.append(final_nav)
        trade_pnls = [float(v["net_pnl_after_cost"]) for v in strategy.trade_records]
        wins, losses = [v for v in trade_pnls if v > 0], [v for v in trade_pnls if v < 0]
        factor = final_nav / float(starting_nav)
        days = 7.0
        growth = factor ** (1.0 / days) - 1.0 if factor > 0 else -1.0
        drawdown = max_drawdown(nav_values)
        profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else 0.0)
        planned_ratios: list[float] = []
        notionals: list[float] = []
        for value in strategy.sizing_records:
            budget, planned = Decimal(str(value["risk_budget"])), Decimal(str(value["planned_loss"]))
            if budget > 0:
                planned_ratios.append(float(planned / budget))
            if value.get("effective_notional_multiple") is not None:
                notionals.append(float(value["effective_notional_multiple"]))
        commission_total = float(sum(_parse_money(v) for v in fills.get("commission", []))) if not fills.empty else 0.0
        ambiguities = _same_bar_ambiguities(raw=raw, trades=strategy.trade_records)
        _write_json(output / "same_bar_ambiguities.json", ambiguities)
        runtime = strategy.diagnostic_snapshot()
        counts = runtime.get("runtime", {})
        execution_errors_absent = not any(int(counts.get(name, 0)) > 0 for name in (
            "ORDER_REJECTED", "ORDER_DENIED", "UNEXPECTED_POSITION_OPEN",
        ))
        thresholds = config["validation"]["pass_criteria"]
        checks = {
            "minimum_trades_per_day": len(trade_pnls) / days >= float(thresholds["minimum_trades_per_day"]),
            "minimum_win_rate": len(wins) / len(trade_pnls) >= float(thresholds["minimum_win_rate"]) if trade_pnls else False,
            "minimum_profit_factor": profit_factor >= float(thresholds["minimum_profit_factor"]),
            "minimum_geometric_daily_growth": growth >= float(thresholds["minimum_geometric_daily_growth"]),
            "maximum_drawdown": abs(drawdown) <= float(thresholds["maximum_drawdown"]),
            "planned_loss_budget": max(planned_ratios, default=0.0) <= 1.000000001,
            "flat_at_end": flat_at_end,
            "execution_errors_absent": execution_errors_absent,
            "same_bar_path_unambiguous": not ambiguities,
        }
        passed = all(checks.values())
        result_obj = engine.get_result()
        metrics = {
            "candidate": config["candidate"],
            "stage": "prospectively_selected_locked_rule_translation_nautilustrader",
            "engine": "NautilusTrader 1.230.0",
            "custom_backtest_engine": False,
            "development_only": True,
            "success_claim_allowed": False,
            "project_target_met": False,
            "translation_status": config["lineage"]["translation_status"],
            "evaluation_start_utc": evaluation_start.isoformat(),
            "evaluation_end_utc": evaluation_end.isoformat(),
            "evaluation_days": days,
            "risk_fraction": float(config["risk"]["risk_fraction"]),
            "scheduled_signals": len(signals),
            "submitted_signals": sum(1 for v in strategy.signal_records if v["status"] == "SUBMITTED"),
            "signal_rejections": [v for v in strategy.signal_records if v.get("status") == "REJECTED"],
            "trades": len(trade_pnls),
            "trades_per_day": len(trade_pnls) / days,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trade_pnls) if trade_pnls else 0.0,
            "profit_factor_after_cost": profit_factor,
            "starting_nav_usdt": float(starting_nav),
            "final_nav_usdt": final_nav,
            "nav_factor": factor,
            "geometric_daily_growth_after_cost": growth,
            "maximum_mark_to_market_drawdown": drawdown,
            "gross_profit_after_cost_usdt": sum(wins),
            "gross_loss_after_cost_usdt": sum(losses),
            "effective_commissions_usdt": commission_total,
            "maximum_planned_loss_to_budget": max(planned_ratios, default=0.0),
            "maximum_effective_notional_multiple": max(notionals, default=0.0),
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "positions_rows": int(len(positions.index)),
            "flat_at_end": flat_at_end,
            "same_bar_ambiguity_count": len(ambiguities),
            "pass_checks": checks,
            "locked_week_gate_passed": passed,
            "decision": "ADVANCE_TO_SOURCE_FREEZE_AND_NEW_UNTOUCHED_WEEK" if passed else "REJECT_OR_REDESIGN",
            "runtime_diagnostics": runtime,
            "execution_model": {
                "effective_maker_commission_rate": str(effective_maker),
                "effective_taker_commission_rate": str(effective_taker),
                "bar_execution": True,
                "bar_adaptive_high_low_ordering": True,
                "bracket_orders": True,
                "liquidation_requested": True,
                "liquidation_enabled": liquidation_enabled,
                "account_source_of_truth": True,
                "global_pending_entry_plus_position_limit": 1,
                "arbitrary_notional_cap": None,
                "score_risk_multiplier": None,
            },
            "data_integrity": {
                "raw_rows": len(raw),
                "raw_first_close_utc": raw.index[0].isoformat(),
                "raw_last_close_utc": raw.index[-1].isoformat(),
                "metrics_rows": len(metrics_all),
                "metrics_first_observation_utc": metrics_all.index[0].isoformat(),
                "metrics_last_observation_utc": metrics_all.index[-1].isoformat(),
                "joined_state_rows": len(state),
                "evaluation_state_rows": len(evaluation_state),
                "raw_directory": str(raw_directory),
                "metrics_directory": str(metrics_directory),
                "future_information_used": False,
            },
            "lineage": config["lineage"],
            "state_funnel": state_funnel(evaluation_state, scenario),
            "engine_result": {
                "summary": _json_safe(result_obj.summary),
                "stats_pnls": _json_safe(result_obj.stats_pnls),
                "stats_returns": _json_safe(result_obj.stats_returns),
                "stats_general": _json_safe(getattr(result_obj, "stats_general", {})),
                "iterations": int(result_obj.iterations),
                "total_events": int(result_obj.total_events),
                "total_orders": int(result_obj.total_orders),
                "total_positions": int(result_obj.total_positions),
            },
        }
        _write_json(output / "metrics.json", metrics)
        return metrics
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metrics = run_locked_confirmation(config_path=args.config, input_root=args.input_root, output=args.output)
    print(json.dumps(_json_safe(metrics), indent=2, sort_keys=True))
    return 0 if metrics["locked_week_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
