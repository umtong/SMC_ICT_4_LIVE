"""Causal UTC-aligned four-hour bars from completed one-hour candles.

The canonical RE1 runner supplies 1m/5m/15m/60m bars.  H4 auction engines must
not be silently inactive merely because the execution configuration exposes one
``higher_bar_type`` slot.  Four completed, contiguous UTC-aligned hourly bars
are therefore aggregated inside the scenario layer and emitted at the exact
four-hour close before the same-timestamp 60m decision is processed.

No partial H4 candle is exposed.  An incomplete, duplicate or non-contiguous
bucket is discarded rather than repaired with future data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain import Candle


HOUR_NS = 60 * 60 * 1_000_000_000
FOUR_HOUR_NS = 4 * HOUR_NS

COMPLETED_H4_FROM_HOURLY_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:A_UTC_ALIGNED_H4_CANDLE_BECOMES_AVAILABLE_"
    "ONLY_AFTER_FOUR_CONTIGUOUS_COMPLETED_HOURLY_CANDLES_AND_IS_PROCESSED_"
    "BEFORE_THE_SAME_TIMESTAMP_LOWER_TIMEFRAME_DECISION"
)


@dataclass(slots=True)
class CompletedH4FromHourly:
    """Stateful, no-lookahead 60m -> 240m closed-candle aggregator."""

    symbol: str
    _bucket_end_ns: int | None = None
    _bars: list[Candle] = field(default_factory=list)
    _counts: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def bucket_end_ns(ts_close_ns: int) -> int:
        # A close exactly on 04:00 belongs to the bucket ending at 04:00,
        # whereas 04:00 < close <= 08:00 belongs to the 08:00 bucket.
        return ((int(ts_close_ns) - 1) // FOUR_HOUR_NS + 1) * FOUR_HOUR_NS

    def _inc(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def _reset(self) -> None:
        self._bucket_end_ns = None
        self._bars = []

    @staticmethod
    def _contiguous(bars: list[Candle]) -> bool:
        return all(
            bars[index].ts_close_ns - bars[index - 1].ts_close_ns == HOUR_NS
            for index in range(1, len(bars))
        )

    def update(self, bar: Candle) -> Candle | None:
        bucket_end = self.bucket_end_ns(bar.ts_close_ns)
        if self._bucket_end_ns is None:
            self._bucket_end_ns = bucket_end
        elif bucket_end != self._bucket_end_ns:
            if self._bars:
                self._inc("incomplete_bucket_discarded_on_roll")
            self._bucket_end_ns = bucket_end
            self._bars = []

        if self._bars and bar.ts_close_ns <= self._bars[-1].ts_close_ns:
            self._inc("duplicate_or_out_of_order_hour_discarded")
            return None
        self._bars.append(bar)

        if bar.ts_close_ns != bucket_end:
            if len(self._bars) > 4:
                self._inc("overfull_bucket_discarded")
                self._reset()
            return None

        bars = list(self._bars)
        self._reset()
        if len(bars) != 4:
            self._inc("boundary_bucket_without_four_hours")
            return None
        if not self._contiguous(bars):
            self._inc("non_contiguous_boundary_bucket_discarded")
            return None
        if bars[0].ts_close_ns != bucket_end - 3 * HOUR_NS:
            self._inc("misaligned_boundary_bucket_discarded")
            return None

        self._inc("completed_h4_emitted")
        return Candle(
            ts_close_ns=bucket_end,
            open=float(bars[0].open),
            high=max(float(item.high) for item in bars),
            low=min(float(item.low) for item in bars),
            close=float(bars[-1].close),
            volume=sum(float(item.volume) for item in bars),
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._counts.items())),
            "pending_hours": len(self._bars),
            "pending_bucket_end_ns": self._bucket_end_ns,
            "rule_provenance": COMPLETED_H4_FROM_HOURLY_RULE,
        }
