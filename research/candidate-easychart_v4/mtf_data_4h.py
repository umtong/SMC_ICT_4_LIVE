"""Five-timeframe bar wiring for a 4h->1h->15m/5m/1m hierarchy."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import BarType

from mtf_data import add_symbol_mtf_data


def add_symbol_4h_mtf_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[BarType, BarType, BarType, BarType, BarType]:
    """Add 1m source data and return 1m/5m/15m/1h/4h bar types.

    Every higher bar is internally aggregated by NautilusTrader from the same
    one-minute external stream.  No separately sampled 4h history can drift
    from the execution data or become available early.
    """
    source, trigger, decision, higher = add_symbol_mtf_data(
        engine,
        symbol,
        instrument,
        start,
        end,
        cache,
    )
    context_4h = BarType.from_str(
        f"{instrument.id}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL",
    )
    return source, trigger, decision, higher, context_4h


__all__ = ["add_symbol_4h_mtf_data"]
