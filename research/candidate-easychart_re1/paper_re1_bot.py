"""Warm-started paper strategy for the canonical liquidity-episode policy."""
from __future__ import annotations

from collections import defaultdict

from nautilus_trader.model.identifiers import InstrumentId

from easychart_re1_bot import EasyChartRE1BotStrategy
from paper_re1_fixed import EasyChartRE1CoherentPaperStrategy


class EasyChartRE1BotPaperStrategy(
    EasyChartRE1CoherentPaperStrategy,
    EasyChartRE1BotStrategy,
):
    """Use the same flow candles, decisions and account lifecycle as replay."""

    def _preload_warmup(self) -> None:
        """Warm the four-market factor and every local episode in replay order."""
        buckets: dict[int, list[tuple[InstrumentId, int, object]]] = defaultdict(list)
        for instrument_id in self.config.instrument_ids:
            by_timeframe = self._warmup.get(str(instrument_id))
            if not by_timeframe:
                raise RuntimeError(f"missing warmup series for {instrument_id}")
            for timeframe, candles in by_timeframe.items():
                for candle in candles:
                    buckets[candle.ts_close_ns].append(
                        (instrument_id, int(timeframe), candle),
                    )

        count = 0
        for timestamp in sorted(buckets):
            events = buckets[timestamp]
            one_minute = [item for item in events if item[1] == 1]
            if len(one_minute) == len(self.config.instrument_ids):
                self.bar_bucket_ts = timestamp
                self.bar_bucket = list(one_minute)
                self._observe_common_factor()
                self.bar_bucket = []
                self.bar_bucket_seen = set()
                self.bar_bucket_ts = None
            for instrument_id, timeframe, candle in sorted(
                events,
                key=lambda item: (-item[1], str(item[0])),
            ):
                engine = self.scenario_engines[instrument_id]
                engine.on_bar(timeframe, candle)
                engine.drain_trace()
                self._warmup_last_ts[(instrument_id, timeframe)] = candle.ts_close_ns
                count += 1

        self._record(
            "paper_warmup_complete",
            candles=count,
            instruments=len(self.config.instrument_ids),
            earliest_ts_ns=min(buckets),
            latest_ts_ns=max(buckets),
            common_factor_warmed=True,
        )
        self._warmup = {}


__all__ = ["EasyChartRE1BotPaperStrategy"]
