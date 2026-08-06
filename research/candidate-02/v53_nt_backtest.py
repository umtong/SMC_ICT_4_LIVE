#!/usr/bin/env python3
"""NautilusTrader-only weekly screen for full-auction rotation v53."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler

from core import max_drawdown
from v53_nt_core import (
    CostConfig,
    RotationConfig,
    build_rotation_signals,
    build_state,
    load_feature_matrix,
    load_raw_one_minute,
    signals_to_json,
)
from v53_nt_strategy import V53RotationStrategy, V53RotationStrategyConfig

UTC = timezone.utc


class _WritableValuesFrame(pd.DataFrame):
    """Pandas 3 copy-on-write compatibility for NT 1.230.0 wranglers."""

    @property
    def _constructor(self):
        return _WritableValuesFrame

    @property
    def values(self):
        array = self.to_numpy(dtype="float64", copy=True)
        array.setflags(write=True)
        return array


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(value), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_records(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(_json_safe(record), sort_keys=True, ensure_ascii=False))
            stream.write("\n")


def _parse_money(value: Any) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else 0.0


def _make_btc_instrument(*, maker_fee: Decimal, taker_fee: Decimal) -> CryptoPerpetual:
    usdt = Currency.from_str("USDT")
    btc = Currency.from_str("BTC")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=btc,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        min_notional=Money(5.0, usdt),
        max_price=Price.from_str("1000000.0"),
        min_price=Price.from_str("100.0"),
        margin_init=Decimal("0.008"),
        margin_maint=Decimal("0.004"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )


def _effective_commission_rates(costs: CostConfig) -> tuple[Decimal, Decimal]:
    effective_taker = (
        costs.entry_fee_rate
        + max(costs.entry_slippage_rate, costs.stop_slippage_rate)
        + costs.market_impact_rate
        + costs.funding_rate_allowance / 2
    )
    effective_maker = (
        costs.target_fee_rate + costs.market_impact_rate + costs.funding_rate_allowance / 2
    )
    return effective_maker, effective_taker


def _resolve_input_paths(input_root: Path) -> tuple[Path, Path, Path]:
    candidates = [input_root]
    candidates.extend(path for path in input_root.rglob("candidate-02-v48-first-week") if path.is_dir())
    feature_root = next(
        (
            path
            for path in candidates
            if (path / "v48_features.npz").is_file() and (path / "columns.json").is_file()
        ),
        None,
    )
    raw_root = next(
        (
            path
            for path in input_root.rglob("binance_1m")
            if path.is_dir() and any(path.glob("BTCUSDT-1m-*.zip"))
        ),
        None,
    )
    if feature_root is None or raw_root is None:
        raise FileNotFoundError(
            f"unable to resolve v53 inputs under {input_root}: feature_root={feature_root}, raw_root={raw_root}"
        )
    return feature_root / "v48_features.npz", feature_root / "columns.json", raw_root


def _ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def run_first_week(*, config_path: Path, input_root: Path, output: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if Decimal(str(config["risk"]["risk_fraction"])) != Decimal("0.03"):
        raise ValueError("candidate-02 risk_fraction must remain exactly 0.03")
    scenario = RotationConfig.from_mapping(config["scenario"])
    costs = CostConfig.from_mapping(config["costs"])
    evaluation_start = datetime.fromisoformat(config["validation"]["first_week_start"]).replace(tzinfo=UTC)
    evaluation_end = evaluation_start + timedelta(days=7)
    data_start = evaluation_start - timedelta(days=int(config["validation"]["warmup_days"]))
    data_end = evaluation_end + timedelta(minutes=int(config["validation"]["exit_buffer_minutes"]))

    npz_path, columns_path, raw_directory = _resolve_input_paths(input_root)
    features = load_feature_matrix(npz_path, columns_path)
    raw_all = load_raw_one_minute(raw_directory)
    raw = raw_all.loc[
        (raw_all.index >= pd.Timestamp(data_start)) & (raw_all.index <= pd.Timestamp(data_end))
    ].copy()
    expected_minutes = int((data_end - data_start).total_seconds() // 60)
    if len(raw) < expected_minutes * 0.995:
        raise ValueError(f"insufficient raw one-minute coverage: {len(raw)}/{expected_minutes}")
    state = build_state(features, scenario)
    signals = build_rotation_signals(
        state=state,
        raw=raw_all,
        evaluation_start=pd.Timestamp(evaluation_start),
        evaluation_end=pd.Timestamp(evaluation_end),
        config=scenario,
        costs=costs,
    )
    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future-information guard failed")

    effective_maker, effective_taker = _effective_commission_rates(costs)
    instrument = _make_btc_instrument(maker_fee=effective_maker, taker_fee=effective_taker)
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, instrument).process(_WritableValuesFrame(raw))
    if len(bars) != len(raw):
        raise RuntimeError(f"wrangler row mismatch: {len(raw)} -> {len(bars)}")

    starting_nav = Decimal(str(config["risk"]["starting_nav_usdt"]))
    usdt = Currency.from_str("USDT")
    venue = Venue("BINANCE")
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level=str(config["validation"].get("log_level", "ERROR"))),
            run_analysis=True,
        )
    )
    strategy = V53RotationStrategy(
        V53RotationStrategyConfig(
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
        )
    )

    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "signals_schedule.json", [value.to_dict() for value in signals])
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
            engine.add_venue(
                **venue_kwargs,
                liquidation_enabled=True,
                liquidation_cancel_open_orders=True,
            )
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
        if account is None:
            raise RuntimeError("backtest account missing")
        final_balance = account.balance_total(usdt)
        if final_balance is None:
            raise RuntimeError("final USDT balance missing")
        final_nav = final_balance.as_double()
        flat_at_end = engine.portfolio.is_flat(instrument.id)
        if not flat_at_end:
            raise RuntimeError("NautilusTrader ended with an open BTC position")

        nav_values = [float(starting_nav)]
        nav_values.extend(float(value["nav"]) for value in strategy.nav_records if value["nav"] > 0)
        if not math.isclose(nav_values[-1], final_nav, rel_tol=1e-9, abs_tol=0.01):
            nav_values.append(final_nav)
        trade_pnls = [float(value["net_pnl_after_cost"]) for value in strategy.trade_records]
        wins = [value for value in trade_pnls if value > 0]
        losses = [value for value in trade_pnls if value < 0]
        factor = final_nav / float(starting_nav)
        days = 7.0
        growth = factor ** (1.0 / days) - 1.0
        drawdown = max_drawdown(nav_values)
        profit_factor = sum(wins) / abs(sum(losses)) if losses else (math.inf if wins else 0.0)
        planned_loss_ratios = []
        notional_multiples = []
        for value in strategy.sizing_records:
            budget = Decimal(str(value["risk_budget"]))
            planned = Decimal(str(value["planned_loss"]))
            if budget > 0:
                planned_loss_ratios.append(float(planned / budget))
            multiple = value.get("effective_notional_multiple")
            if multiple is not None:
                notional_multiples.append(float(multiple))
        commission_total = (
            float(sum(_parse_money(value) for value in fills.get("commission", [])))
            if not fills.empty
            else 0.0
        )
        thresholds = config["validation"]["pass_criteria"]
        checks = {
            "minimum_trades_per_day": len(trade_pnls) / days >= float(thresholds["minimum_trades_per_day"]),
            "minimum_win_rate": len(wins) / len(trade_pnls) >= float(thresholds["minimum_win_rate"]) if trade_pnls else False,
            "minimum_profit_factor": profit_factor >= float(thresholds["minimum_profit_factor"]),
            "minimum_geometric_daily_growth": growth >= float(thresholds["minimum_geometric_daily_growth"]),
            "maximum_drawdown": abs(drawdown) <= float(thresholds["maximum_drawdown"]),
            "planned_loss_budget": max(planned_loss_ratios, default=0.0) <= 1.000000001,
            "flat_at_end": flat_at_end,
        }
        result_obj = engine.get_result()
        metrics = {
            "candidate": config["candidate"],
            "stage": "first_random_btc_week_nautilustrader",
            "engine": "NautilusTrader 1.230.0",
            "custom_backtest_engine": False,
            "evaluation_start_utc": evaluation_start.isoformat(),
            "evaluation_end_utc": evaluation_end.isoformat(),
            "evaluation_days": days,
            "risk_fraction": float(config["risk"]["risk_fraction"]),
            "scheduled_signals": len(signals),
            "submitted_signals": sum(1 for value in strategy.signal_records if value["status"] == "SUBMITTED"),
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
            "maximum_planned_loss_to_budget": max(planned_loss_ratios, default=0.0),
            "maximum_effective_notional_multiple": max(notional_multiples, default=0.0),
            "orders": int(len(orders.index)),
            "fills": int(len(fills.index)),
            "positions_rows": int(len(positions.index)),
            "flat_at_end": flat_at_end,
            "pass_checks": checks,
            "target_met": all(checks.values()),
            "decision": "ADVANCE_TO_SECOND_WEEK" if all(checks.values()) else "REJECT_OR_REDESIGN",
            "runtime_diagnostics": strategy.diagnostic_snapshot(),
            "execution_model": {
                "effective_maker_commission_rate": str(effective_maker),
                "effective_taker_commission_rate": str(effective_taker),
                "bar_execution": True,
                "bar_adaptive_high_low_ordering": True,
                "bracket_orders": True,
                "liquidation_requested": True,
                "liquidation_enabled": liquidation_enabled,
                "account_source_of_truth": True,
            },
            "data_integrity": {
                "raw_rows": len(raw),
                "raw_first_close_utc": raw.index[0].isoformat(),
                "raw_last_close_utc": raw.index[-1].isoformat(),
                "feature_rows": len(features),
                "feature_first_open_utc": features.index[0].isoformat(),
                "feature_last_open_utc": features.index[-1].isoformat(),
                "npz_path": str(npz_path),
                "columns_path": str(columns_path),
                "raw_directory": str(raw_directory),
                "forward_feature_columns_used": False,
            },
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
    metrics = run_first_week(config_path=args.config, input_root=args.input_root, output=args.output)
    print(json.dumps(_json_safe(metrics), indent=2, sort_keys=True))
    return 0 if metrics["target_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
