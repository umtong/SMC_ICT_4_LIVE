"""Ledger-only Binance funding events for the v13 external cash ledger."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import FundingRateUpdate, MarkPriceUpdate

from funding_data_v10 import (
    FUNDING_PROVENANCE,
    align_preceding_marks,
    load_funding_observations,
    load_mark_observations,
)


LEDGER_EVENT_TRANSLATION = (
    "EXTERNAL_METHOD:FUNDING_RATE_UPDATE_HAS_NO_NATIVE_BOUNDARY_TO_PREVENT_DOUBLE_SETTLEMENT"
)


def add_symbol_funding_ledger_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: object,
    start: date,
    end: date,
    cache: Path,
) -> dict[str, object]:
    """Add preceding marks and realized rates without a native settlement boundary."""
    funding = load_funding_observations(symbol, start, end, cache)
    marks = load_mark_observations(symbol, start, end, cache)
    pairs = align_preceding_marks(funding, marks)
    mark_events = [
        MarkPriceUpdate(
            instrument_id=instrument.id,
            value=instrument.make_price(pair.mark.value),
            ts_event=pair.mark.timestamp_ns,
            ts_init=pair.mark.timestamp_ns,
        )
        for pair in pairs
    ]
    # ``interval=None`` and ``next_funding_ns=None`` make these reference-data
    # observations non-settling to the native exchange. The strategy ledger
    # applies the cash flow exactly once at ts_event.
    funding_events = [
        FundingRateUpdate(
            instrument_id=instrument.id,
            rate=pair.funding.rate,
            interval=None,
            next_funding_ns=None,
            ts_event=pair.funding.timestamp_ns,
            ts_init=pair.funding.timestamp_ns,
        )
        for pair in pairs
    ]
    engine.add_data(mark_events, sort=False)
    engine.add_data(funding_events, sort=False)
    return {
        "symbol": symbol,
        "funding_updates": len(funding_events),
        "mark_updates": len(mark_events),
        "first_funding_ns": None if not pairs else pairs[0].funding.timestamp_ns,
        "last_funding_ns": None if not pairs else pairs[-1].funding.timestamp_ns,
        "actual_intervals_minutes": [pair.funding.interval_minutes for pair in pairs],
        "archive_provenance": FUNDING_PROVENANCE,
        "event_translation": LEDGER_EVENT_TRANSLATION,
    }
