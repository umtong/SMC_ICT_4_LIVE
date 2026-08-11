"""NautilusTrader setup, data wiring and compact evidence output."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Currency, Money

from data import load_range, wrangler_frame

STARTING_NAV = 100_000.0
VENUE = Venue("BINANCE")
USDT = Currency.from_str("USDT")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def make_engine() -> BacktestEngine:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("EASYCHART-V2-001"),
            logging=LoggingConfig(log_level="ERROR"),
            risk_engine=RiskEngineConfig(bypass=False),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(STARTING_NAV, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("100"),
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=1.0, random_seed=42),
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    return engine


def add_symbol_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[BarType, BarType]:
    raw = load_range(symbol, start, end, cache)
    source_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    signal_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    frame = wrangler_frame(raw, 1)
    source = [
        Bar(
            bar_type=source_type,
            open=instrument.make_price(row.open),
            high=instrument.make_price(row.high),
            low=instrument.make_price(row.low),
            close=instrument.make_price(row.close),
            volume=instrument.make_qty(row.volume),
            ts_event=int(row.Index.value),
            ts_init=int(row.Index.value),
        )
        for row in frame.itertuples()
    ]
    engine.add_data(source, sort=False)
    return source_type, signal_type


def final_nav(engine: BacktestEngine) -> float:
    account = engine.portfolio.account(VENUE)
    if account is None:
        raise RuntimeError("account unavailable after backtest")
    money = account.balance_total(USDT)
    if money is None:
        raise RuntimeError("USDT balance unavailable after backtest")
    return float(money.as_double())


def preserve_results(
    engine: BacktestEngine,
    strategy: Any,
    output: Path,
    *,
    symbols: tuple[str, ...],
    instruments: list[Any],
    start: date,
    end: date,
    min_prominence_atr: float,
    enable_rejection: bool,
    enable_acceptance: bool,
) -> dict[str, Any]:
    fills = engine.trader.generate_order_fills_report()
    orders = engine.trader.generate_orders_report()
    positions = engine.trader.generate_positions_report()
    account = engine.trader.generate_account_report(VENUE)
    fills.to_csv(output / "fills.csv", index=False)
    orders.to_csv(output / "orders.csv", index=False)
    positions.to_csv(output / "positions.csv", index=False)
    account.to_csv(output / "account.csv", index=False)
    with (output / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
        for event in strategy.event_log:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    nav = final_nav(engine)
    days = (end - start).days + 1
    # The framework report is authoritative. PositionClosed callbacks are not
    # guaranteed for the final on_stop flatten within the same engine run.
    closed = int(len(positions.index))
    plans = sum(event.get("kind") == "plan" for event in strategy.event_log)
    submitted = sum(event.get("kind") == "submitted" for event in strategy.event_log)
    emergency = sum(event.get("kind") == "emergency_exit_protective_failure" for event in strategy.event_log)
    metrics = {
        "candidate": "candidate-easychart-v2",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "starting_nav": STARTING_NAV,
        "final_nav": nav,
        "total_return": nav / STARTING_NAV - 1.0,
        "daily_geometric_growth": (nav / STARTING_NAV) ** (1.0 / days) - 1.0 if nav > 0 else -1.0,
        "calendar_days": days,
        "fills": int(len(fills.index)),
        "closed_positions": closed,
        "plans": plans,
        "submitted_plans": submitted,
        "emergency_protective_exits": emergency,
        "independent_trades_per_day": closed / days,
        "risk_fraction": 0.03,
        "minimum_gross_rr": 1.0,
        "min_prominence_atr": min_prominence_atr,
        "enable_rejection": enable_rejection,
        "enable_acceptance": enable_acceptance,
        "diagnostics": {
            symbol: strategy.engines[instrument.id].diagnostics
            for symbol, instrument in zip(symbols, instruments, strict=True)
        },
    }
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv2-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "candidate": "candidate-easychart-v2",
            "engine": "NautilusTrader BacktestEngine",
            "data": "Binance Vision USD-M 1m; Nautilus 5m composite signals and 1m execution",
            "contract": {
                "single_entry": True,
                "single_full_stop_market": True,
                "single_full_target": True,
                "risk_fraction_current_nav": 0.03,
                "min_pre_entry_gross_rr": 1.0,
                "global_pending_or_position_limit": 1,
                "partial_management": False,
                "daily_loss_limit": None,
                "trade_count_limit": None,
            },
        },
    )
    return metrics
