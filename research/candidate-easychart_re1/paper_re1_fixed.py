"""Runtime fixes for the RE1 warm-started paper wrapper.

The original paper wrapper was written before the final v3 multi-timeframe
router API was inspected.  This small subclass uses the actual ``route_by_key``
map and ``expected_composite_count`` contract, and is the class used by the
paper runner.  It intentionally changes no trading decision or order logic.
"""
from __future__ import annotations

from nautilus_trader.model.data import Bar

from paper_re1 import (
    LIVE_REORDER_GRACE_NS,
    EasyChartRE1PaperStrategy,
    WarmupMap,
    build_warmup_map,
    load_warmup_frame,
)


class EasyChartRE1CoherentPaperStrategy(EasyChartRE1PaperStrategy):
    """Warm-started paper strategy with deterministic external-bar routing."""

    def _expected_bucket_count(self, ts_event: int) -> int:
        return self.expected_composite_count(ts_event, len(self.config.instrument_ids))

    def _halt_for_incomplete_market_view(self, earliest_ts: int, latest_ts: int) -> None:
        if self._live_data_halted:
            return
        self._live_data_halted = True
        expected = self._expected_bucket_count(earliest_ts)
        actual = len(self._pending_live.get(earliest_ts, ()))
        self._record(
            "live_bar_coherence_fault",
            earliest_incomplete_ts_ns=earliest_ts,
            latest_seen_ts_ns=latest_ts,
            expected_bars=expected,
            actual_bars=actual,
        )
        for instrument_id in self.config.instrument_ids:
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)

    def _drain_live_buckets(self) -> None:
        while self._pending_live:
            earliest_ts = min(self._pending_live)
            expected = self._expected_bucket_count(earliest_ts)
            bucket = self._pending_live[earliest_ts]
            if len(bucket) < expected:
                latest_ts = max(self._pending_live)
                if latest_ts - earliest_ts > LIVE_REORDER_GRACE_NS:
                    self._halt_for_incomplete_market_view(earliest_ts, latest_ts)
                return
            if len(bucket) > expected:
                self._halt_for_incomplete_market_view(earliest_ts, earliest_ts)
                return
            self.bar_bucket_ts = earliest_ts
            self.bar_bucket = bucket
            self.bar_bucket_seen = self._pending_live_seen[earliest_ts]
            del self._pending_live[earliest_ts]
            del self._pending_live_seen[earliest_ts]
            self._flush_bar_bucket()
            self._last_live_processed_ts = earliest_ts

    def on_bar(self, bar: Bar) -> None:
        if self._live_data_halted:
            return
        route = self.route_by_key.get(bar.bar_type.id_spec_key())
        if route is None:
            return
        instrument_id, timeframe = route
        if bar.ts_event <= self._warmup_last_ts.get((instrument_id, timeframe), 0):
            self._record(
                "live_bar_skipped_warmup_duplicate",
                instrument_id=str(instrument_id),
                timeframe_minutes=timeframe,
                bar_ts_ns=bar.ts_event,
            )
            return
        if bar.ts_event <= self._last_live_processed_ts:
            self._halt_for_incomplete_market_view(bar.ts_event, self._last_live_processed_ts)
            return
        key = (instrument_id, timeframe)
        if key in self._pending_live_seen[bar.ts_event]:
            self._record(
                "live_bar_duplicate",
                instrument_id=str(instrument_id),
                timeframe_minutes=timeframe,
                bar_ts_ns=bar.ts_event,
            )
            return
        self._pending_live_seen[bar.ts_event].add(key)
        self._pending_live[bar.ts_event].append((instrument_id, timeframe, bar))
        self._drain_live_buckets()


__all__ = [
    "EasyChartRE1CoherentPaperStrategy",
    "WarmupMap",
    "build_warmup_map",
    "load_warmup_frame",
]
