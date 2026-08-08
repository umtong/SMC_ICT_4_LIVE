"""Causal top-of-book resilience features from Binance bookTicker updates.

The feature builder is observational only.  It neither creates orders nor
simulates fills.  Candidate 03's official archive, checksum, and timestamp
contracts are reused; this module adds event-sequence aggregation at the actual
best bid and ask.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Iterator, Sequence

NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND
BookTickerRecord = tuple[int, float, float, float, float, int, int]


def _imbalance(bid_qty: float, ask_qty: float) -> float:
    total = bid_qty + ask_qty
    return (bid_qty - ask_qty) / total if total > 0.0 else float("nan")


@dataclass(slots=True)
class _MinuteAccumulator:
    minute_start_ns: int
    first_observed_ns: int
    last_observed_ns: int
    start_bid: float
    start_bid_qty: float
    start_ask: float
    start_ask_qty: float
    end_bid: float
    end_bid_qty: float
    end_ask: float
    end_ask_qty: float
    previous_mid: float
    quote_updates: int = 1
    mid_path_bps: float = 0.0
    max_spread_bps: float = 0.0
    min_spread_bps: float = float("inf")
    bid_same_price_add_qty: float = 0.0
    bid_same_price_remove_qty: float = 0.0
    ask_same_price_add_qty: float = 0.0
    ask_same_price_remove_qty: float = 0.0
    bid_improve_count: int = 0
    bid_retreat_count: int = 0
    ask_improve_count: int = 0
    ask_retreat_count: int = 0
    bid_episode_min_qty: float = 0.0
    ask_episode_min_qty: float = 0.0
    bid_episode_had_depletion: bool = False
    ask_episode_had_depletion: bool = False
    bid_episode_refilled: bool = False
    ask_episode_refilled: bool = False

    @classmethod
    def from_record(cls, record: BookTickerRecord) -> "_MinuteAccumulator":
        _, bid, bid_qty, ask, ask_qty, _, observed_ns = record
        if bid <= 0.0 or ask <= bid or bid_qty <= 0.0 or ask_qty <= 0.0:
            raise ValueError("invalid bookTicker quote")
        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10_000.0
        minute_start = observed_ns // NS_PER_MINUTE * NS_PER_MINUTE
        return cls(
            minute_start_ns=minute_start,
            first_observed_ns=observed_ns,
            last_observed_ns=observed_ns,
            start_bid=bid,
            start_bid_qty=bid_qty,
            start_ask=ask,
            start_ask_qty=ask_qty,
            end_bid=bid,
            end_bid_qty=bid_qty,
            end_ask=ask,
            end_ask_qty=ask_qty,
            previous_mid=mid,
            max_spread_bps=spread_bps,
            min_spread_bps=spread_bps,
            bid_episode_min_qty=bid_qty,
            ask_episode_min_qty=ask_qty,
        )

    def update(self, record: BookTickerRecord) -> None:
        _, bid, bid_qty, ask, ask_qty, _, observed_ns = record
        if observed_ns < self.last_observed_ns:
            raise ValueError("bookTicker observed time moved backwards")
        if observed_ns // NS_PER_MINUTE * NS_PER_MINUTE != self.minute_start_ns:
            raise ValueError("record belongs to a different minute")
        if bid <= 0.0 or ask <= bid or bid_qty <= 0.0 or ask_qty <= 0.0:
            raise ValueError("invalid bookTicker quote")

        previous_bid = self.end_bid
        previous_bid_qty = self.end_bid_qty
        previous_ask = self.end_ask
        previous_ask_qty = self.end_ask_qty

        if bid == previous_bid:
            delta = bid_qty - previous_bid_qty
            if delta > 0.0:
                self.bid_same_price_add_qty += delta
                if self.bid_episode_had_depletion and bid_qty > self.bid_episode_min_qty:
                    self.bid_episode_refilled = True
            elif delta < 0.0:
                self.bid_same_price_remove_qty += -delta
                self.bid_episode_had_depletion = True
                self.bid_episode_min_qty = min(self.bid_episode_min_qty, bid_qty)
        else:
            if bid > previous_bid:
                self.bid_improve_count += 1
            else:
                self.bid_retreat_count += 1
            self.bid_episode_min_qty = bid_qty
            self.bid_episode_had_depletion = False
            self.bid_episode_refilled = False

        if ask == previous_ask:
            delta = ask_qty - previous_ask_qty
            if delta > 0.0:
                self.ask_same_price_add_qty += delta
                if self.ask_episode_had_depletion and ask_qty > self.ask_episode_min_qty:
                    self.ask_episode_refilled = True
            elif delta < 0.0:
                self.ask_same_price_remove_qty += -delta
                self.ask_episode_had_depletion = True
                self.ask_episode_min_qty = min(self.ask_episode_min_qty, ask_qty)
        else:
            if ask < previous_ask:
                self.ask_improve_count += 1
            else:
                self.ask_retreat_count += 1
            self.ask_episode_min_qty = ask_qty
            self.ask_episode_had_depletion = False
            self.ask_episode_refilled = False

        mid = (bid + ask) / 2.0
        self.mid_path_bps += abs(math.log(mid / self.previous_mid)) * 10_000.0
        spread_bps = (ask - bid) / mid * 10_000.0
        self.max_spread_bps = max(self.max_spread_bps, spread_bps)
        self.min_spread_bps = min(self.min_spread_bps, spread_bps)
        self.previous_mid = mid
        self.end_bid = bid
        self.end_bid_qty = bid_qty
        self.end_ask = ask
        self.end_ask_qty = ask_qty
        self.last_observed_ns = observed_ns
        self.quote_updates += 1

    def finalize(self) -> dict[str, float | int | bool]:
        start_mid = (self.start_bid + self.start_ask) / 2.0
        end_mid = (self.end_bid + self.end_ask) / 2.0
        start_spread_bps = (self.start_ask - self.start_bid) / start_mid * 10_000.0
        end_spread_bps = (self.end_ask - self.end_bid) / end_mid * 10_000.0
        mid_ret_bps = math.log(end_mid / start_mid) * 10_000.0
        mid_efficiency = (
            min(1.0, abs(mid_ret_bps) / self.mid_path_bps)
            if self.mid_path_bps > 0.0
            else 0.0
        )
        spread_recovered = end_spread_bps <= start_spread_bps
        bid_persistent_refill = (
            self.bid_episode_had_depletion
            and self.bid_episode_refilled
            and self.end_bid_qty > self.bid_episode_min_qty
        )
        ask_persistent_refill = (
            self.ask_episode_had_depletion
            and self.ask_episode_refilled
            and self.end_ask_qty > self.ask_episode_min_qty
        )

        # Positive means best-quote resilience; negative means withdrawal ahead.
        # No magnitude threshold is fitted: the state follows temporal order and
        # strict dominance of observed add/remove and best-price transitions.
        bid_defense = spread_recovered and (
            bid_persistent_refill or self.end_bid > self.start_bid
        )
        bid_withdrawal = (
            self.end_bid < self.start_bid
            and self.bid_same_price_remove_qty > self.bid_same_price_add_qty
        )
        ask_defense = spread_recovered and (
            ask_persistent_refill or self.end_ask < self.start_ask
        )
        ask_withdrawal = (
            self.end_ask > self.start_ask
            and self.ask_same_price_remove_qty > self.ask_same_price_add_qty
        )
        bid_queue_response = (
            1
            if bid_defense and not bid_withdrawal
            else (-1 if bid_withdrawal and not bid_defense else 0)
        )
        ask_queue_response = (
            1
            if ask_defense and not ask_withdrawal
            else (-1 if ask_withdrawal and not ask_defense else 0)
        )
        minute_end_ns = self.minute_start_ns + NS_PER_MINUTE

        return {
            "minute_start_ns": self.minute_start_ns,
            "topbook_feature_ready": self.quote_updates >= 2,
            "topbook_quote_updates": self.quote_updates,
            "topbook_first_quote_delay_seconds": max(
                0.0,
                (self.first_observed_ns - self.minute_start_ns) / NS_PER_SECOND,
            ),
            "topbook_last_quote_age_seconds": max(
                0.0,
                (minute_end_ns - self.last_observed_ns) / NS_PER_SECOND,
            ),
            "topbook_mid_ret_60s_bps": mid_ret_bps,
            "topbook_mid_path_60s_bps": self.mid_path_bps,
            "topbook_mid_efficiency_60s": mid_efficiency,
            "topbook_spread_start_bps": start_spread_bps,
            "topbook_spread_end_bps": end_spread_bps,
            "topbook_spread_max_bps": self.max_spread_bps,
            "topbook_spread_min_bps": self.min_spread_bps,
            "topbook_spread_recovered": spread_recovered,
            "topbook_quote_imbalance_end": _imbalance(
                self.end_bid_qty,
                self.end_ask_qty,
            ),
            "topbook_bid_queue_response": bid_queue_response,
            "topbook_ask_queue_response": ask_queue_response,
            "topbook_bid_persistent_refill": bid_persistent_refill,
            "topbook_ask_persistent_refill": ask_persistent_refill,
            "topbook_bid_same_price_add_qty": self.bid_same_price_add_qty,
            "topbook_bid_same_price_remove_qty": self.bid_same_price_remove_qty,
            "topbook_ask_same_price_add_qty": self.ask_same_price_add_qty,
            "topbook_ask_same_price_remove_qty": self.ask_same_price_remove_qty,
            "topbook_bid_improve_count": self.bid_improve_count,
            "topbook_bid_retreat_count": self.bid_retreat_count,
            "topbook_ask_improve_count": self.ask_improve_count,
            "topbook_ask_retreat_count": self.ask_retreat_count,
            "topbook_bid_start": self.start_bid,
            "topbook_bid_end": self.end_bid,
            "topbook_ask_start": self.start_ask,
            "topbook_ask_end": self.end_ask,
            "topbook_bid_qty_start": self.start_bid_qty,
            "topbook_bid_qty_end": self.end_bid_qty,
            "topbook_ask_qty_start": self.start_ask_qty,
            "topbook_ask_qty_end": self.end_ask_qty,
        }


def aggregate_records(
    records: Iterable[BookTickerRecord],
) -> list[dict[str, float | int | bool]]:
    result: list[dict[str, float | int | bool]] = []
    current: _MinuteAccumulator | None = None
    previous_observed_ns = -1
    for record in records:
        observed_ns = int(record[6])
        if observed_ns < previous_observed_ns:
            raise ValueError("bookTicker records are not monotonic")
        previous_observed_ns = observed_ns
        minute_start = observed_ns // NS_PER_MINUTE * NS_PER_MINUTE
        if current is None or minute_start != current.minute_start_ns:
            if current is not None:
                result.append(current.finalize())
            current = _MinuteAccumulator.from_record(record)
        else:
            current.update(record)
    if current is not None:
        result.append(current.finalize())
    return result


def iter_book_ticker_paths(
    paths: Sequence[Path],
) -> Iterator[BookTickerRecord]:
    """Reuse Candidate 03's checksum/timestamp contract to stream bookTicker."""
    import nt_lvcfr_data as source

    previous_observed_ns = -1
    for path in sorted(paths, key=lambda item: item.name):
        archive, reader = source._one_csv_reader(path)  # noqa: SLF001 frozen reuse
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                if len(row) < 7:
                    raise ValueError(f"bookTicker row too short in {path}")
                transaction_ns = source.normalize_timestamp_ns(int(row[5]))
                observed_ns = max(
                    source.normalize_timestamp_ns(int(row[6])),
                    transaction_ns,
                )
                if observed_ns < previous_observed_ns:
                    raise ValueError("bookTicker observed time moved backwards")
                previous_observed_ns = observed_ns
                yield (
                    int(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    transaction_ns,
                    observed_ns,
                )
        finally:
            archive.close()


def aggregate_book_ticker_paths(paths: Sequence[Path]):
    import pandas as pd

    frame = pd.DataFrame(aggregate_records(iter_book_ticker_paths(paths)))
    if frame.empty:
        raise ValueError("no bookTicker features produced")
    if frame["minute_start_ns"].duplicated().any():
        raise ValueError("duplicate top-of-book minute")
    return frame.sort_values("minute_start_ns", kind="stable").reset_index(drop=True)


__all__ = [
    "BookTickerRecord",
    "aggregate_book_ticker_paths",
    "aggregate_records",
    "iter_book_ticker_paths",
]
