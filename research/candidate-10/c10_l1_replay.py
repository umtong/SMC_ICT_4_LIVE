"""Bounded official-L1 replay batches for NautilusTrader candidate 10."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    from nautilus_trader.model import QuoteTick
    from nautilus_trader.model import TradeTick
except ImportError:  # pragma: no cover
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity

from c10_l1_cache import iter_alignment_records
from c10_l1_data import REPLAY_BATCH_EVENTS
from c10_model import NS_PER_MINUTE

def _bars_by_open_day(bars: Iterable[Bar]) -> dict[str, list[Bar]]:
    result: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        open_ns = int(bar.ts_event) - NS_PER_MINUTE
        day = datetime.fromtimestamp(open_ns / 1_000_000_000, tz=timezone.utc)
        result[day.date().isoformat()].append(bar)
    return result


def _record_market_events(
    record: tuple[Any, ...],
    *,
    instrument: Any,
    replay_state: dict[str, Any],
) -> list[Any]:
    (
        trade_id,
        trade_ts_ns,
        trade_price,
        trade_quantity,
        aggressor,
        quote_update_id,
        quote_event_ts_ns,
        bid_price,
        bid_quantity,
        ask_price,
        ask_quantity,
    ) = record
    events: list[Any] = []
    if quote_update_id >= 0 and quote_update_id != replay_state.get("last_quote_id"):
        events.append(
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=Price(
                    bid_price,
                    precision=instrument.price_precision,
                ),
                ask_price=Price(
                    ask_price,
                    precision=instrument.price_precision,
                ),
                bid_size=Quantity(
                    bid_quantity,
                    precision=instrument.size_precision,
                ),
                ask_size=Quantity(
                    ask_quantity,
                    precision=instrument.size_precision,
                ),
                ts_event=int(quote_event_ts_ns),
                # The adapter observes this already-known quote when the trade is
                # replayed. Equal ts_init keeps quote and trade in one timestamp
                # group; list order places the quote first.
                ts_init=int(trade_ts_ns),
            ),
        )
        replay_state["last_quote_id"] = int(quote_update_id)
        replay_state["quote_events_emitted"] = (
            int(replay_state.get("quote_events_emitted", 0)) + 1
        )
    events.append(
        TradeTick(
            instrument_id=instrument.id,
            price=Price(
                trade_price,
                precision=instrument.price_precision,
            ),
            size=Quantity(
                trade_quantity,
                precision=instrument.size_precision,
            ),
            aggressor_side=(
                AggressorSide.BUYER if aggressor > 0 else AggressorSide.SELLER
            ),
            trade_id=TradeId(str(trade_id)),
            ts_event=int(trade_ts_ns),
            ts_init=int(trade_ts_ns),
        ),
    )
    replay_state["trade_events_emitted"] = (
        int(replay_state.get("trade_events_emitted", 0)) + 1
    )
    return events


def iter_day_events(
    *,
    cache_path: Path,
    bars: list[Bar],
    instrument: Any,
    replay_state: dict[str, Any],
) -> Iterator[Any]:
    bar_index = 0
    for record in iter_alignment_records(cache_path):
        trade_ts_ns = int(record[1])
        while bar_index < len(bars) and int(bars[bar_index].ts_init) < trade_ts_ns:
            yield bars[bar_index]
            bar_index += 1
        for event in _record_market_events(
            record,
            instrument=instrument,
            replay_state=replay_state,
        ):
            yield event
        while bar_index < len(bars) and int(bars[bar_index].ts_init) == trade_ts_ns:
            # Trade/quote state at an exact close timestamp is known first,
            # matching the prior TradeTick-only ordering contract.
            yield bars[bar_index]
            bar_index += 1
    while bar_index < len(bars):
        yield bars[bar_index]
        bar_index += 1


def chunk_events_by_timestamp(
    events: Iterable[Any],
    *,
    maximum_events: int = REPLAY_BATCH_EVENTS,
) -> Iterator[list[Any]]:
    if maximum_events <= 0:
        raise ValueError("maximum_events must be positive")
    batch: list[Any] = []
    group: list[Any] = []
    group_ts: int | None = None
    for event in events:
        event_ts = int(event.ts_init)
        if group_ts is None or event_ts == group_ts:
            group.append(event)
            group_ts = event_ts
            continue
        if batch and len(batch) + len(group) > maximum_events:
            yield batch
            batch = []
        batch.extend(group)
        group = [event]
        group_ts = event_ts
    if group:
        if batch and len(batch) + len(group) > maximum_events:
            yield batch
            batch = []
        batch.extend(group)
    if batch:
        yield batch

__all__ = [
    "_bars_by_open_day",
    "_record_market_events",
    "chunk_events_by_timestamp",
    "iter_day_events",
]
