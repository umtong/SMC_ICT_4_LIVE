"""Native L1 completion snapshots for quote-resiliency execution.

The signal detector observes a completed ten-second trade/quote bucket.  The aggregate-trade bar is
therefore delivered at the bucket close first.  One nanosecond later this module re-emits the last
checksum-verified Binance ``bookTicker`` state observed inside that bucket as a NautilusTrader
``QuoteTick``.  The quote-specific strategy submits any market order from that quote callback.

This is a synchronization adapter, not fabricated market alpha:

* bid, ask, sizes, and source event time come from the actual final venue update in the bucket;
* the completion timestamp is deterministically ``bucket_end + 1 ns``;
* the source event must be strictly before the bucket end and less than one cadence old;
* the existing one-tick adverse ``FillModel`` reserve remains unchanged.

No signal, outcome, sizing, order, position, account, or PnL logic is implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import pandas as pd

from nautilus_trader.model.data import QuoteTick


NATIVE_QUOTE_REVISION = "CHECKSUM_BOOKTICKER_COMPLETION_QUOTE_TICK_V1"
COMPLETION_DELAY_NS = 1
_REQUIRED_COLUMNS = (
    "bid_close",
    "ask_close",
    "bid_qty_close",
    "ask_qty_close",
    "quote_last_event_ns",
    "native_quote_snapshot_observable",
)


@dataclass(frozen=True, slots=True)
class NativeQuoteSnapshotQuality:
    revision: str
    rows: int
    first_completion_time_ns: int | None
    last_completion_time_ns: int | None
    maximum_source_age_ns: int
    completion_delay_ns: int
    source_contract: str


def _as_float(row: pd.Series, name: str) -> float:
    value = float(row[name])
    if not isfinite(value):
        raise ValueError(f"{name} is not finite")
    return value


def completion_quote_ticks_from_frame(
    frame: pd.DataFrame,
    *,
    instrument: Any,
    cadence_seconds: int = 10,
) -> tuple[list[QuoteTick], NativeQuoteSnapshotQuality]:
    """Build one executable L1 completion snapshot for each observable completed bucket."""

    if cadence_seconds <= 0:
        raise ValueError("cadence_seconds must be positive")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise TypeError("quote feature frame must use a timezone-aware DatetimeIndex")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("quote feature frame must have unique increasing timestamps")
    missing = sorted(set(_REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        return [], NativeQuoteSnapshotQuality(
            revision=NATIVE_QUOTE_REVISION,
            rows=0,
            first_completion_time_ns=None,
            last_completion_time_ns=None,
            maximum_source_age_ns=0,
            completion_delay_ns=COMPLETION_DELAY_NS,
            source_contract="NO_NATIVE_QUOTE_COLUMNS",
        )

    cadence_ns = int(cadence_seconds) * 1_000_000_000
    ticks: list[QuoteTick] = []
    maximum_age_ns = 0
    for bucket_end, row in frame.iterrows():
        if not bool(row["native_quote_snapshot_observable"]):
            continue
        bucket_end_ns = int(bucket_end.as_unit("ns").value)
        source_event_ns = int(_as_float(row, "quote_last_event_ns"))
        source_age_ns = bucket_end_ns - source_event_ns
        if source_age_ns <= 0 or source_age_ns >= cadence_ns:
            raise ValueError(
                "source bookTicker event must be strictly before and less than one cadence old"
            )
        bid = _as_float(row, "bid_close")
        ask = _as_float(row, "ask_close")
        bid_qty = _as_float(row, "bid_qty_close")
        ask_qty = _as_float(row, "ask_qty_close")
        if bid <= 0.0 or ask <= 0.0 or bid_qty <= 0.0 or ask_qty <= 0.0:
            raise ValueError("native quote snapshot contains nonpositive state")
        if bid > ask:
            raise ValueError("native quote snapshot is crossed")
        completion_ns = bucket_end_ns + COMPLETION_DELAY_NS
        ticks.append(
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=instrument.make_price(bid),
                ask_price=instrument.make_price(ask),
                bid_size=instrument.make_qty(bid_qty),
                ask_size=instrument.make_qty(ask_qty),
                ts_event=completion_ns,
                ts_init=completion_ns,
            )
        )
        maximum_age_ns = max(maximum_age_ns, source_age_ns)

    completion_times = [int(item.ts_event) for item in ticks]
    quality = NativeQuoteSnapshotQuality(
        revision=NATIVE_QUOTE_REVISION,
        rows=len(ticks),
        first_completion_time_ns=(completion_times[0] if completion_times else None),
        last_completion_time_ns=(completion_times[-1] if completion_times else None),
        maximum_source_age_ns=maximum_age_ns,
        completion_delay_ns=COMPLETION_DELAY_NS,
        source_contract=(
            "LAST_CHECKSUM_BOOKTICKER_STATE_IN_BUCKET_REEMITTED_AT_BUCKET_END_PLUS_1NS"
        ),
    )
    return ticks, quality


__all__ = [
    "COMPLETION_DELAY_NS",
    "NATIVE_QUOTE_REVISION",
    "NativeQuoteSnapshotQuality",
    "completion_quote_ticks_from_frame",
]
