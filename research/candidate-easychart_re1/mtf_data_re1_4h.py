"""NautilusTrader data wiring for 1m/5m/15m/4h EasyChart diagnostics."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import Bar, BarType

from data import load_range, wrangler_frame


def add_symbol_mtf_data_4h(
    engine: BacktestEngine,
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[BarType, BarType, BarType, BarType]:
    raw = load_range(symbol, start, end, cache)
    source_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    trigger_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    decision_type = BarType.from_str(f"{instrument.id}-15-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    higher_type = BarType.from_str(f"{instrument.id}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL")
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
    return source_type, trigger_type, decision_type, higher_type
