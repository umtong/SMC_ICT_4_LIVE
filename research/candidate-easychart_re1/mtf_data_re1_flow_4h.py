"""Binance aggressor-flow wiring for 1m/5m/15m/4h diagnostics."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.adapters.binance.common.types import BinanceBar
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import BarType

from data_re1_flow import load_range_flow, wrangler_flow_frame


def add_symbol_mtf_flow_data_4h(
    engine: BacktestEngine,
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[BarType, BarType, BarType, BarType]:
    raw = load_range_flow(symbol, start, end, cache)
    source_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    trigger_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    decision_type = BarType.from_str(f"{instrument.id}-15-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    higher_type = BarType.from_str(f"{instrument.id}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    frame = wrangler_flow_frame(raw)
    source: list[BinanceBar] = []
    for row in frame.itertuples(index=True, name=None):
        ts, open_price, high, low, close, volume, quote_volume, count, taker_buy_volume, taker_buy_quote_volume = row
        source.append(
            BinanceBar(
                bar_type=source_type,
                open=instrument.make_price(open_price),
                high=instrument.make_price(high),
                low=instrument.make_price(low),
                close=instrument.make_price(close),
                volume=instrument.make_qty(volume),
                quote_volume=Decimal(str(quote_volume)),
                count=int(count),
                taker_buy_base_volume=Decimal(str(taker_buy_volume)),
                taker_buy_quote_volume=Decimal(str(taker_buy_quote_volume)),
                ts_event=int(ts.value),
                ts_init=int(ts.value),
            )
        )
    engine.add_data(source, sort=False)
    return source_type, trigger_type, decision_type, higher_type
