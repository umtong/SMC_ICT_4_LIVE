"""Daily causal equal-notional clock selected by execution-cost resolution.

A fixed quote-notional threshold can change effective market-time resolution by
several multiples when activity changes. This module recalibrates once per UTC
day using only the immediately preceding completed day.

For each next day, candidate equal-notional thresholds represent 5, 10, 20 and
30 minutes of the preceding day's median quote activity. The smallest
threshold whose *preceding-day* event bars have median high-low range at least
the configured round-trip execution cost is selected. If none clears cost, the
largest threshold is used. The chosen threshold then remains frozen for the
entire next day.

The selection rule is economic, causal and independent of strategy PnL. The
implementation reads each daily archive twice regardless of candidate count:
once for minute notional calibration and once to aggregate all candidate clocks
plus the selected next-day clock concurrently. This preserves exact event-bar
semantics without repeatedly decompressing the same trade file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Iterable

from aggtrade_clock import (
    VolumeBar,
    calibrate_target_from_minutes,
    minute_quote_totals,
)
from aggtrade_data import AggTrade, AggTradeDownload, iter_download


NS_PER_DAY = 86_400_000_000_000
DEFAULT_CANDIDATE_MINUTES = (5, 10, 20, 30)


@dataclass(frozen=True, slots=True)
class ClockCandidateEvidence:
    calibration_minutes: int
    target_quote_notional: float
    calibration_event_bars: int
    median_range_bps: float
    median_duration_seconds: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DailyClockCalibration:
    bar_day: str
    source_day: str
    selected_minutes: int
    selected_target_quote_notional: float
    selected_median_range_bps: float
    minimum_range_bps: float
    fallback_to_largest: bool
    candidate_evidence: tuple[ClockCandidateEvidence, ...]
    produced_event_bars: int

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["candidate_evidence"] = [item.to_dict() for item in self.candidate_evidence]
        return payload


@dataclass(slots=True)
class _Bucket:
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
    def from_trade(cls, trade: AggTrade) -> "_Bucket":
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


@dataclass(slots=True)
class _ClockAccumulator:
    target_quote_notional: float
    capture_bars: bool
    next_output_index: int
    bucket: _Bucket | None = None
    completed_bars: int = 0
    range_bps: list[float] | None = None
    duration_seconds: list[float] | None = None
    output_bars: list[VolumeBar] | None = None

    def __post_init__(self) -> None:
        if self.target_quote_notional <= 0.0:
            raise ValueError("target_quote_notional must be positive")
        if self.range_bps is None:
            self.range_bps = []
        if self.duration_seconds is None:
            self.duration_seconds = []
        if self.output_bars is None:
            self.output_bars = []

    def add(self, trade: AggTrade) -> None:
        if self.bucket is None:
            self.bucket = _Bucket.from_trade(trade)
        else:
            self.bucket.add(trade)
        if self.bucket.quote_notional < self.target_quote_notional:
            return

        index = self.next_output_index if self.capture_bars else self.completed_bars
        bar = self.bucket.finish(
            index=index,
            target_quote_notional=self.target_quote_notional,
        )
        assert self.range_bps is not None
        assert self.duration_seconds is not None
        assert self.output_bars is not None
        self.range_bps.append(bar.range_fraction * 10_000.0)
        self.duration_seconds.append(bar.duration_seconds)
        if self.capture_bars:
            self.output_bars.append(bar)
            self.next_output_index += 1
        self.completed_bars += 1
        self.bucket = None


def day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ns = int(start.timestamp() * 1_000_000_000)
    return start_ns, start_ns + NS_PER_DAY


def _targets_from_record(
    record: AggTradeDownload,
    *,
    candidate_minutes: tuple[int, ...],
) -> dict[int, float]:
    source_day = date.fromisoformat(record.day)
    start_ns, end_ns = day_bounds(source_day)
    totals = minute_quote_totals(
        iter_download(record),
        start_ns=start_ns,
        end_ns=end_ns,
    )
    return {
        minutes: calibrate_target_from_minutes(
            totals,
            minutes_per_event=minutes,
        )
        for minutes in candidate_minutes
    }


def _scan_record(
    record: AggTradeDownload,
    *,
    candidate_targets: dict[int, float],
    selected_target: float | None = None,
    selected_start_index: int = 0,
) -> tuple[tuple[ClockCandidateEvidence, ...], list[VolumeBar], int]:
    unique_targets = set(candidate_targets.values())
    if selected_target is not None:
        unique_targets.add(selected_target)
    accumulators = {
        target: _ClockAccumulator(
            target_quote_notional=target,
            capture_bars=(selected_target is not None and target == selected_target),
            next_output_index=selected_start_index,
        )
        for target in unique_targets
    }

    for trade in iter_download(record):
        for accumulator in accumulators.values():
            accumulator.add(trade)

    evidence: list[ClockCandidateEvidence] = []
    for minutes in sorted(candidate_targets):
        target = candidate_targets[minutes]
        accumulator = accumulators[target]
        assert accumulator.range_bps is not None
        assert accumulator.duration_seconds is not None
        if accumulator.completed_bars <= 0:
            raise ValueError(
                f"no calibration bars for {record.day} at {minutes} minutes",
            )
        evidence.append(
            ClockCandidateEvidence(
                calibration_minutes=minutes,
                target_quote_notional=target,
                calibration_event_bars=accumulator.completed_bars,
                median_range_bps=float(median(accumulator.range_bps)),
                median_duration_seconds=float(median(accumulator.duration_seconds)),
            ),
        )

    selected_bars: list[VolumeBar] = []
    next_index = selected_start_index
    if selected_target is not None:
        selected_accumulator = accumulators[selected_target]
        assert selected_accumulator.output_bars is not None
        selected_bars = selected_accumulator.output_bars
        next_index = selected_accumulator.next_output_index
    return tuple(evidence), selected_bars, next_index


def evidence_for_record(
    record: AggTradeDownload,
    *,
    candidate_minutes: tuple[int, ...],
) -> tuple[ClockCandidateEvidence, ...]:
    minutes = tuple(sorted(set(candidate_minutes)))
    targets = _targets_from_record(record, candidate_minutes=minutes)
    evidence, _, _ = _scan_record(
        record,
        candidate_targets=targets,
    )
    return evidence


def choose_cost_resolved_candidate(
    evidence: Iterable[ClockCandidateEvidence],
    *,
    minimum_range_bps: float,
) -> tuple[ClockCandidateEvidence, bool]:
    values = sorted(evidence, key=lambda item: item.calibration_minutes)
    if not values:
        raise ValueError("clock selection requires candidate evidence")
    if minimum_range_bps <= 0.0:
        raise ValueError("minimum_range_bps must be positive")
    for item in values:
        if item.median_range_bps >= minimum_range_bps:
            return item, False
    return values[-1], True


def build_daily_cost_resolved_bars(
    records: Iterable[AggTradeDownload],
    *,
    bar_start: datetime,
    bar_end: datetime,
    minimum_range_bps: float,
    candidate_minutes: tuple[int, ...] = DEFAULT_CANDIDATE_MINUTES,
) -> tuple[list[VolumeBar], list[DailyClockCalibration]]:
    if bar_end <= bar_start:
        raise ValueError("bar_end must be after bar_start")
    minutes = tuple(sorted(set(candidate_minutes)))
    if not minutes or any(value <= 0 for value in minutes):
        raise ValueError("candidate_minutes must contain positive values")

    by_day = {date.fromisoformat(record.day): record for record in records}
    first_day = bar_start.astimezone(timezone.utc).date()
    final_day = (bar_end - timedelta(microseconds=1)).astimezone(timezone.utc).date()
    source_day = first_day - timedelta(days=1)
    source_record = by_day.get(source_day)
    if source_record is None:
        raise ValueError(f"missing causal clock source day {source_day}")

    source_targets = _targets_from_record(
        source_record,
        candidate_minutes=minutes,
    )
    previous_evidence, _, _ = _scan_record(
        source_record,
        candidate_targets=source_targets,
    )

    bars: list[VolumeBar] = []
    calibrations: list[DailyClockCalibration] = []
    global_index = 0
    current_day = first_day
    while current_day <= final_day:
        current_record = by_day.get(current_day)
        if current_record is None:
            raise ValueError(f"missing event data day {current_day}")
        selected, fallback = choose_cost_resolved_candidate(
            previous_evidence,
            minimum_range_bps=minimum_range_bps,
        )

        current_targets = _targets_from_record(
            current_record,
            candidate_minutes=minutes,
        )
        current_evidence, day_bars, global_index = _scan_record(
            current_record,
            candidate_targets=current_targets,
            selected_target=selected.target_quote_notional,
            selected_start_index=global_index,
        )
        bars.extend(day_bars)
        calibrations.append(
            DailyClockCalibration(
                bar_day=current_day.isoformat(),
                source_day=(current_day - timedelta(days=1)).isoformat(),
                selected_minutes=selected.calibration_minutes,
                selected_target_quote_notional=selected.target_quote_notional,
                selected_median_range_bps=selected.median_range_bps,
                minimum_range_bps=minimum_range_bps,
                fallback_to_largest=fallback,
                candidate_evidence=previous_evidence,
                produced_event_bars=len(day_bars),
            ),
        )
        previous_evidence = current_evidence
        current_day += timedelta(days=1)
    return bars, calibrations
