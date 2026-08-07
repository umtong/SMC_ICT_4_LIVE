#!/usr/bin/env python3
"""Reproducible Candidate 12 evaluation through NautilusTrader."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bar_adapter import build_bars
from data_loader import load_binance_bars
from logic import LogicConfig
from metrics import calculate_metrics, decimal_value
from strategy_adapter import build_candidate_strategy

from smc_ict_4.event_log import EventLogError, write_events
from smc_ict_4.manifest import create_run_manifest, write_json_atomic


def _write_raw_events(path: Path, events: list[Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False) + "\n")
    temporary.replace(path)
    return path


def run(config_path: Path, symbol: str, week_id: str, output_dir: Path) -> dict[str, Any]:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if symbol not in config["symbols"]:
        raise ValueError(f"unsupported symbol {symbol!r}")
    if week_id not in config["selection"]["weeks"]:
        raise ValueError(f"unknown frozen week {week_id!r}")
    selected = config["selection"]["weeks"][week_id]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    frame, source_files = load_binance_bars(symbol, warmup_start, evaluation_end, output_dir / "data")
    data_manifest_path = output_dir / "data_manifest.json"
    write_json_atomic(
        data_manifest_path,
        {
            "schema": "candidate-12-source-manifest-v1",
            "dataset": f"Binance USD-M {symbol} one-minute daily klines",
            "bar_visibility": "archive open_time plus one minute",
            "selection_seed": config["selection"]["seed"],
            "selection_rule": config["selection"]["selection_rule"],
            "warmup_start": warmup_start.isoformat(),
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "files": source_files,
        },
    )

    symbol_spec = config["symbols"][symbol]
    account_config = config["account"]
    execution_config = config["execution"]
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    base_currency = Currency.from_str(symbol_spec["base_currency"])
    instrument_id = InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=venue)
    instrument = CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=int(symbol_spec["price_precision"]),
        price_increment=Price.from_str(symbol_spec["price_increment"]),
        size_precision=int(symbol_spec["size_precision"]),
        size_increment=Quantity.from_str(symbol_spec["size_increment"]),
        max_quantity=Quantity.from_str("999999999"),
        min_quantity=Quantity.from_str(symbol_spec["min_quantity"]),
        max_notional=None,
        min_notional=Money(float(symbol_spec["min_notional"]), usdt),
        max_price=Price.from_str(format(10_000_000.0, f".{int(symbol_spec['price_precision'])}f")),
        min_price=Price.from_str(format(float(symbol_spec["price_increment"]), f".{int(symbol_spec['price_precision'])}f")),
        margin_init=Decimal(account_config["margin_init"]),
        margin_maint=Decimal(account_config["margin_maint"]),
        maker_fee=Decimal(execution_config["effective_maker_rate"]),
        taker_fee=Decimal(execution_config["effective_taker_rate"]),
        ts_event=0,
        ts_init=0,
    )
    bar_type = BarType.from_str(f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    bars = build_bars(frame[["open", "high", "low", "close", "volume"]], bar_type, instrument)
    source_volumes = frame["volume"].astype(float).tolist()
    taker_buy_volumes = frame["taker_buy_volume"].astype(float).tolist()
    if len(bars) != len(source_volumes) or len(bars) != len(taker_buy_volumes):
        raise RuntimeError("bar and aggressor-flow streams are not aligned")

    logic_values = dict(config["logic"])
    logic_values["price_increment"] = float(symbol_spec["price_increment"])
    logic_values["risk_fraction"] = float(account_config["risk_fraction"])
    logic_values["effective_maker_rate"] = float(execution_config["effective_maker_rate"])
    logic_values["effective_taker_rate"] = float(execution_config["effective_taker_rate"])
    logic_values["tick_slippage_units"] = float(execution_config["tick_slippage_units_in_risk_budget"])
    logic_config = LogicConfig(**logic_values)
    evaluation_start_ns = int(pd.Timestamp(evaluation_start, tz="UTC").value)
    evaluation_end_ns = int(pd.Timestamp(evaluation_end, tz="UTC").value)
    starting_nav = Decimal(account_config["starting_nav"])

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    fill_model = FillModel(
        prob_fill_on_limit=float(execution_config["prob_fill_on_limit"]),
        prob_slippage=float(execution_config["prob_slippage"]),
        random_seed=int(execution_config["random_seed"]),
    )
    strategy = build_candidate_strategy(
        logic_config=logic_config,
        instrument=instrument,
        settlement_currency=usdt,
        bar_type=bar_type,
        source_volumes=source_volumes,
        taker_buy_volumes=taker_buy_volumes,
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
        starting_nav=starting_nav,
    )
    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(starting_nav, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(account_config["default_leverage"]),
            fill_model=fill_model,
            bar_adaptive_high_low_ordering=bool(execution_config["bar_adaptive_high_low_ordering"]),
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()

        orders = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account_report = engine.trader.generate_account_report(venue)
        orders.to_csv(output_dir / "orders.csv", index=False)
        positions.to_csv(output_dir / "positions.csv", index=False)
        account_report.to_csv(output_dir / "account.csv", index=False)
        account = engine.cache.account_for_venue(venue)
        if account is None:
            raise RuntimeError("Nautilus margin account is unavailable after the run")
        final_nav = decimal_value(account.balance_total(usdt))
        metrics = calculate_metrics(
            config=config,
            symbol=symbol,
            week_id=week_id,
            starting_nav=starting_nav,
            final_nav=final_nav,
            positions=positions,
            orders=orders,
            plans=strategy.plans,
            logic=strategy.logic,
            errors=strategy.errors,
            lifecycle=strategy.lifecycle,
            slot_rejections=strategy.slot_rejections,
        )
        result = engine.get_result() if hasattr(engine, "get_result") else None
        metrics.update(
            {
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "warmup_start": warmup_start.isoformat(),
                "bars": len(bars),
                "instrument": str(instrument.id),
                "effective_maker_rate": str(instrument.maker_fee),
                "effective_taker_rate": str(instrument.taker_fee),
                "nautilus_result": {
                    name: None if result is None else getattr(result, name, None)
                    for name in ("iterations", "total_orders", "total_positions", "total_events")
                },
            },
        )
        _write_raw_events(output_dir / "scenario_events.raw.jsonl", strategy.logic.events)
        write_json_atomic(output_dir / "submitted_plans.json", {"plans": strategy.plans})
        write_json_atomic(output_dir / "order_lifecycle.json", {"events": strategy.lifecycle})
        event_log_error: str | None = None
        try:
            write_events(output_dir / "scenario_events.jsonl", strategy.logic.events)
            metrics["event_log_valid"] = True
            metrics["event_log_error"] = None
        except EventLogError as exc:
            event_log_error = str(exc)
            metrics["event_log_valid"] = False
            metrics["event_log_error"] = event_log_error
            metrics["diagnostic_promise_gate_passed"] = False
            metrics["project_target_gate_passed"] = False
            metrics["success_claim"] = False
        write_json_atomic(output_dir / "metrics.json", metrics)
        manifest = create_run_manifest(
            run_id=f"candidate-12-{symbol.lower()}-{week_id.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            candidate=config["candidate"],
            config_path=config_path,
            data_manifest_path=data_manifest_path,
            extra={
                "symbol": symbol,
                "week_id": week_id,
                "bar_type": str(bar_type),
                "evaluation_start": evaluation_start.isoformat(),
                "evaluation_end_exclusive": evaluation_end.isoformat(),
                "logic": logic_values,
                "execution": config["execution"],
                "metrics_path": str(output_dir / "metrics.json"),
                "event_log_valid": metrics["event_log_valid"],
            },
        )
        write_json_atomic(output_dir / "run.json", manifest)
        if event_log_error is not None:
            raise EventLogError(event_log_error)
        return metrics
    finally:
        engine.dispose()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--symbol", choices=("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"), default="BTCUSDT")
    parser.add_argument("--week", choices=("W1", "W2", "W3", "W4", "W5", "W6"), default="W1")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "BTCUSDT-W1")
    args = parser.parse_args()
    metrics = run(args.config.resolve(), args.symbol, args.week, args.output.resolve())
    print(json.dumps(metrics, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
