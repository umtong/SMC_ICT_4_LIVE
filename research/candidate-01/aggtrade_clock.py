"""Causal quote-notional clock for Binance aggregate-trade events."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Iterable, Iterator

from aggtrade_data import AggTrade


NS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class VolumeBar:
    index: int
    start_time_ns: int
    end_time_ns: int
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
    target_quote_notional: float

    @property
    def duration_seconds(self) -> float:
        return max(self.end_time_ns - self.start_time_ns, 0) / 1_000_000_000.0

    @property
    def imbalance(self) -> float:
        if self.quote_notional <= 0.0:
            return 0.0
        return self.signed_quote_notional / self.quote_notional

    @property
    def return_fraction(self) -> float:
        return self.close / self.open - 1.0 if self.open > 0.0 else 0.0

    @property
    def range_fraction(self) -> float:
        return (self.high - self.low) / self.open if self.open > 0.0 else 0.0

    @property
    def close_location(self) -> float:
        width = self.high - self.low
        return (self.close - self.low) / width if width > 0.0 else 0.5

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            {
                "duration_seconds": self.duration_seconds,
                "imbalance": self.imbalance,
                "return_fraction": self.return_fraction,
                "range_fraction": self.range_fraction,
                "close_location": self.close_location,
            },
        )
        return payload


@dataclass(slots=True)
class _Builder:
    start_time_ns: int
    end_time_ns: int
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
    def from_trade(cls, trade: AggTrade) -> "_Builder":
        quote = trade.quote_notional
        signed = trade.signed_aggressive_quote
        return cls(
            start_time_ns=trade.ts_event_ns,
            end_time_ns=trade.ts_event_ns,
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
        self.end_time_ns = trade.ts_event_ns
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

    def finish(self, *, index: int, target_quote_notional: float) -> VolumeBar:
        return VolumeBar(
            index=index,
            start_time_ns=self.start_time_ns,
            end_time_ns=self.end_time_ns,
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
            target_quote_notional=target_quote_notional,
        )


def minute_quote_totals(
    trades: Iterable[AggTrade],
    *,
    start_ns: int,
    end_ns: int,
) -> dict[int, float]:
    result: dict[int, float] = {}
    for trade in trades:
        if trade.ts_event_ns < start_ns:
            continue
        if trade.ts_event_ns >= end_ns:
            break
        minute = trade.ts_event_ns // NS_PER_MINUTE
        result[minute] = result.get(minute, 0.0) + trade.quote_notional
    return result


def calibrate_target_from_minutes(
    minute_totals: dict[int, float],
    *,
    minutes_per_event: int,
) -> float:
    if minutes_per_event <= 0:
        raise ValueError("minutes_per_event must be positive")
    if not minute_totals:
        raise ValueError("cannot calibrate volume clock from empty warmup")
    first = min(minute_totals)
    last = max(minute_totals)
    bucket_totals: list[float] = []
    bucket_start = first - (first % minutes_per_event)
    while bucket_start <= last:
        total = sum(
            minute_totals.get(bucket_start + offset, 0.0)
            for offset in range(minutes_per_event)
        )
        if total > 0.0:
            bucket_totals.append(total)
        bucket_start += minutes_per_event
    if not bucket_totals:
        raise ValueError("warmup minute buckets contain no notional")
    target = float(median(bucket_totals))
    if target <= 0.0:
        raise ValueError(f"invalid calibrated target: {target}")
    return target


def iter_volume_bars(
    trades: Iterable[AggTrade],
    *,
    target_quote_notional: float,
    include_partial: bool = False,
) -> Iterator[VolumeBar]:
    if target_quote_notional <= 0.0:
        raise ValueError("target_quote_notional must be positive")
    builder: _Builder | None = None
    index = 0
    for trade in trades:
        if builder is None:
            builder = _Builder.from_trade(trade)
        else:
            builder.add(trade)
        if builder.quote_notional >= target_quote_notional:
            yield builder.finish(index=index, target_quote_notional=target_quote_notional)
            index += 1
            builder = None
    if include_partial and builder is not None:
        yield builder.finish(index=index, target_quote_notional=target_quote_notional)
