"""Regional liquidity handoff map for Candidate 11.

The session detector creates causal range endpoints only after each source
auction has completed.  Endpoints become triggerable solely during the next
predefined regional handoff window.  Higher-timeframe and round-number levels
remain target-only liquidity; they never create an entry by themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import ceil, floor, log10
from zoneinfo import ZoneInfo

try:
    from .logic import BarObs, CausalAuctionEngine, LogicConfig, Pool, Side, StructuralBar
except ImportError:  # direct execution/unit tests from this directory
    from logic import BarObs, CausalAuctionEngine, LogicConfig, Pool, Side, StructuralBar


NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class SessionSpec:
    label: str
    range_start_minute: int
    range_end_minute: int
    decision_start_minute: int
    decision_end_minute: int
    strength: int


SESSION_SPECS = (
    # Crypto trades continuously.  The previous US late auction supplies the
    # first causal range for the Asia handoff rather than treating 00:00 UTC as
    # an economic session boundary.
    SessionSpec("US_LATE_1600_2000_NY", 16 * 60, 20 * 60, 20 * 60, 24 * 60, 3),
    SessionSpec("ASIA_2000_0000_NY", 20 * 60, 0, 2 * 60, 5 * 60, 3),
    SessionSpec("LONDON_PREMARKET_0000_0200_NY", 0, 2 * 60, 2 * 60, 5 * 60, 2),
    SessionSpec("LONDON_0200_0500_NY", 2 * 60, 5 * 60, 7 * 60, 10 * 60, 3),
    SessionSpec("NY_PREMARKET_0500_0700_NY", 5 * 60, 7 * 60, 7 * 60, 10 * 60, 2),
    SessionSpec("NYAM_0700_1000_NY", 7 * 60, 10 * 60, 10 * 60, 12 * 60, 2),
    SessionSpec("LONDON_CLOSE_1000_1200_NY", 10 * 60, 12 * 60, 13 * 60, 16 * 60, 2),
)


class RegionalHandoffAuctionEngine(CausalAuctionEngine):
    """SCDAM core plus completed regional ranges and target-only HTF levels."""

    def __init__(self, config: LogicConfig, instrument_id: str) -> None:
        super().__init__(config, instrument_id)
        self._sessions: dict[tuple[str, date], StructuralBar] = {}
        self._round_day: date | None = None

    @staticmethod
    def _local(ts_ns: int) -> datetime:
        return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).astimezone(NEW_YORK)

    @staticmethod
    def _minute_ns(day: date, minute: int) -> int:
        if minute >= 24 * 60:
            day += timedelta(days=1)
            minute -= 24 * 60
        local = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=NEW_YORK)
        return int(local.astimezone(timezone.utc).timestamp() * 1_000_000_000)

    def _finish_session(self, spec: SessionSpec, key: date) -> None:
        structural = self._sessions.pop((spec.label, key), None)
        if structural is None:
            return
        before = len(self.pools)
        self._new_range(structural, spec.label, spec.strength, self.config.range_expiry_bars)
        decision_day = key + timedelta(days=1) if spec.range_start_minute > spec.range_end_minute else key
        start_ns = self._minute_ns(decision_day, spec.decision_start_minute)
        end_ns = self._minute_ns(decision_day, spec.decision_end_minute)
        for pool in self.pools[before:]:
            pool.triggerable = True
            pool.trigger_start_ts_ns = start_ns
            pool.trigger_end_ts_ns = end_ns

    def _update_session(self, bar: BarObs, spec: SessionSpec) -> None:
        local = self._local(bar.ts_ns)
        minute = local.hour * 60 + local.minute
        crosses_midnight = spec.range_start_minute > spec.range_end_minute
        if crosses_midnight:
            inside = minute > spec.range_start_minute or minute <= spec.range_end_minute
            key = local.date() if minute > spec.range_start_minute else local.date() - timedelta(days=1)
        else:
            inside = spec.range_start_minute < minute <= spec.range_end_minute
            key = local.date()
        if not inside:
            return

        session_key = (spec.label, key)
        current = self._sessions.get(session_key)
        if current is None:
            current = StructuralBar(
                start_ts_ns=bar.ts_ns,
                end_ts_ns=bar.ts_ns,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                taker_buy_volume=bar.taker_buy_volume,
                high_ts_ns=bar.ts_ns,
                low_ts_ns=bar.ts_ns,
            )
            self._sessions[session_key] = current
        else:
            current.end_ts_ns = bar.ts_ns
            current.close = bar.close
            current.volume += bar.volume
            current.taker_buy_volume += bar.taker_buy_volume
            if bar.high > current.high:
                current.high = bar.high
                current.high_ts_ns = bar.ts_ns
            if bar.low < current.low:
                current.low = bar.low
                current.low_ts_ns = bar.ts_ns

        if minute == spec.range_end_minute:
            self._finish_session(spec, key)

    @staticmethod
    def _round_step(price: float) -> float:
        # Dimensionally stable scale: BTC~500, ETH~50, SOL~5, XRP~0.005.
        return 5.0 * 10.0 ** (floor(log10(max(price, 1e-12))) - 2)

    def _update_round_targets(self, bar: BarObs) -> None:
        local_day = self._local(bar.ts_ns).date()
        if local_day == self._round_day:
            return
        self._round_day = local_day
        step = self._round_step(bar.close)
        low = floor(bar.close / step - 6) * step
        high = ceil(bar.close / step + 6) * step
        count = int(round((high - low) / step))
        for i in range(count + 1):
            level = low + i * step
            if abs(level - bar.close) < 1e-12:
                continue
            side = Side.HIGH if level > bar.close else Side.LOW
            self._pool_seq += 1
            pool = Pool(
                scenario_id=f"{self.instrument_id}-ROUND-{local_day}-R{self._pool_seq:06d}-{side.value}",
                side=side,
                level=float(level),
                source="ROUND_NUMBER",
                candidate_ts_ns=bar.ts_ns,
                confirmed_ts_ns=bar.ts_ns,
                confirmed_index=self._index,
                expiry_index=self._index + 2 * 1440,
                strength=1,
                external=True,
                triggerable=False,
            )
            self._merge_or_add(pool)

    def _update_structure(self, bar: BarObs) -> None:
        internal = self._internal_agg.update(bar)
        if internal is not None:
            self.internal_bars.append(internal)
            self._confirm_internal_pivots(bar.ts_ns)

        context = self._context_agg.update(bar)
        if context is not None:
            self.context_bars.append(context)
            before = len(self.pools)
            self._new_range(context, "COMPLETED_4H_AUCTION", 2, self.config.range_expiry_bars)
            for pool in self.pools[before:]:
                pool.triggerable = False

        daily = self._day_agg.update(bar)
        if daily is not None:
            before = len(self.pools)
            self._new_range(daily, "PREVIOUS_UTC_DAY", 4, self.config.daily_range_expiry_bars)
            for pool in self.pools[before:]:
                pool.triggerable = False

        self._update_round_targets(bar)
        for spec in SESSION_SPECS:
            self._update_session(bar, spec)
