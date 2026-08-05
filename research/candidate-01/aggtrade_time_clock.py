"""Causal fixed-time bars built directly from verified Binance aggregate trades.

Unlike the equal-notional clock, this representation keeps market-time
resolution constant across activity regimes while preserving signed aggressive
quote flow.  A bar is observable only after its UTC-aligned interval has fully
completed.  Empty intervals are not synthesized; BTC perpetual trades
continuously in the evaluated data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

from aggtrade_clock import VolumeBar
from aggtrade_data import AggTrade


NS_PER_MINUTE = 60_000_000_000


@dataclass(slots=True)
class _TimeBuilder:
    bucket: int
    open: float
    high: float
    low: float
    close: float
    base_quantity: float
    quote_notional: float
    signed_quote_notional: float
    aggressive_buy_quote: float
    aggressive_sell_quote: float
    aggregate_trades: int
    first_agg_trade_id: int
    last_agg_trade_id: int

    @classmethod
    def from_trade(cls, *, bucket: int, trade: AggTrade) -> "_TimeBuilder":
        quote = trade.quote_notional
        signed = trade.signed_aggressive_quote
        return cls(
            bucket=bucket,
            open=trade.price,
            high=trade.price,
            low=trade.price,
            close=trade.price,
            base_quantity=trade.quantity,
            quote_notional=quote,
            signed_quote_notional=signed,
            aggressive_buy_quote=max(signed, 0.0),
            aggressive_sell_quote=max(-signed, 0.0),
            aggregate_trades=1,
            first_agg_trade_id=trade.agg_trade_id,
            last_agg_trade_id=trade.agg_trade_id,
        )

    def add(self, trade: AggTrade) -> None:
        quote = trade.quote_notional
        signed = trade.signed_aggressive_quote
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.base_quantity += trade.quantity
        self.quote_notional += quote
        self.signed_quote_notional += signed
        self.aggressive_buy_quote += max(signed, 0.0)
        self.aggressive_sell_quote += max(-signed, 0.0)
        self.aggregate_trades += 1
        self.last_agg_trade_id = trade.agg_trade_id

    def finish(self, *, index: int, interval_ns: int) -> VolumeBar:
        start = self.bucket * interval_ns
        return VolumeBar(
            index=index,
            start_time_ns=start,
            end_time_ns=start + interval_ns,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            base_quantity=self.base_quantity,
            quote_notional=self.quote_notional,
            signed_quote_notional=self.signed_quote_notional,
            aggressive_buy_quote=self.aggressive_buy_quote,
            aggressive_sell_quote=self.aggressive_sell_quote,
            aggregate_trades=self.aggregate_trades,
            first_agg_trade_id=self.first_agg_trade_id,
            last_agg_trade_id=self.last_agg_trade_id,
            # This field is meaningful only for volume bars.  The fixed-time
            # representation records zero rather than inventing a threshold.
            target_quote_notional=0.0,
        )


def iter_time_bars(
    trades: Iterable[AggTrade],
    *,
    interval_minutes: int,
    include_partial: bool = False,
) -> Iterator[VolumeBar]:
    """Yield UTC-aligned completed bars from a monotonic aggregate-trade stream."""

    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    interval_ns = interval_minutes * NS_PER_MINUTE
    builder: _TimeBuilder | None = None
    index = 0
    previous_time: int | None = None
    previous_id: int | None = None

    for trade in trades:
        if previous_time is not None and trade.ts_event_ns < previous_time:
            raise ValueError("aggregate-trade timestamps must be monotonic")
        if previous_id is not None and trade.agg_trade_id <= previous_id:
            raise ValueError("aggregate-trade IDs must be strictly increasing")
        previous_time = trade.ts_event_ns
        previous_id = trade.agg_trade_id

        bucket = trade.ts_event_ns // interval_ns
        if builder is None:
            builder = _TimeBuilder.from_trade(bucket=bucket, trade=trade)
            continue
        if bucket == builder.bucket:
            builder.add(trade)
            continue
        if bucket < builder.bucket:
            raise ValueError("time bucket moved backwards")
        # Seeing the first trade of a later bucket proves the previous UTC
        # interval is complete and therefore causally observable.
        yield builder.finish(index=index, interval_ns=interval_ns)
        index += 1
        builder = _TimeBuilder.from_trade(bucket=bucket, trade=trade)

    if include_partial and builder is not None:
        yield builder.finish(index=index, interval_ns=interval_ns)
