"""Daily causal equal-notional clock selected by execution-cost resolution.

A fixed quote-notional threshold can change effective market-time resolution by
several multiples when activity changes.  This module recalibrates once per UTC
day using only the immediately preceding completed day.

For each next day, candidate equal-notional thresholds represent 5, 10, 20 and
30 minutes of the preceding day's median quote activity.  The smallest
threshold whose *preceding-day* event bars have median high-low range at least
the configured round-trip execution cost is selected.  If none clears cost,
the largest threshold is used.  The chosen threshold then remains frozen for
the entire next day.

The selection rule is economic, causal and independent of strategy PnL.  It
preserves opportunity resolution while preventing sub-cost event clocks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Iterable

from aggtrade_clock import (
    VolumeBar,
    calibrate_target_from_minutes,
    iter_volume_bars,
    minute_quote_totals,
)
from aggtrade_data import AggTradeDownload, iter_download


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


def day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start_ns = int(start.timestamp() * 1_000_000_000)
    return start_ns, start_ns + NS_PER_DAY


def evidence_for_record(
    record: AggTradeDownload,
    *,
    candidate_minutes: tuple[int, ...],
) -> tuple[ClockCandidateEvidence, ...]:
    source_day = date.fromisoformat(record.day)
    start_ns, end_ns = day_bounds(source_day)
    minute_totals = minute_quote_totals(
        iter_download(record),
        start_ns=start_ns,
        end_ns=end_ns,
    )
    result: list[ClockCandidateEvidence] = []
    for minutes in candidate_minutes:
        target = calibrate_target_from_minutes(
            minute_totals,
            minutes_per_event=minutes,
        )
        bars = list(
            iter_volume_bars(
                iter_download(record),
                target_quote_notional=target,
                include_partial=False,
            ),
        )
        if not bars:
            raise ValueError(
                f"no calibration bars for {record.day} at {minutes} minutes",
            )
        ranges = [bar.range_fraction * 10_000.0 for bar in bars]
        durations = [bar.duration_seconds for bar in bars]
        result.append(
            ClockCandidateEvidence(
                calibration_minutes=minutes,
                target_quote_notional=target,
                calibration_event_bars=len(bars),
                median_range_bps=float(median(ranges)),
                median_duration_seconds=float(median(durations)),
            ),
        )
    return tuple(result)


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
    if not candidate_minutes or any(value <= 0 for value in candidate_minutes):
        raise ValueError("candidate_minutes must contain positive values")
    by_day = {
        date.fromisoformat(record.day): record
        for record in records
    }
    first_day = bar_start.astimezone(timezone.utc).date()
    final_day = (bar_end - timedelta(microseconds=1)).astimezone(timezone.utc).date()
    bars: list[VolumeBar] = []
    calibrations: list[DailyClockCalibration] = []
    global_index = 0
    current_day = first_day
    while current_day <= final_day:
        source_day = current_day - timedelta(days=1)
        source_record = by_day.get(source_day)
        current_record = by_day.get(current_day)
        if source_record is None:
            raise ValueError(f"missing causal clock source day {source_day}")
        if current_record is None:
            raise ValueError(f"missing event data day {current_day}")
        evidence = evidence_for_record(
            source_record,
            candidate_minutes=tuple(sorted(set(candidate_minutes))),
        )
        selected, fallback = choose_cost_resolved_candidate(
            evidence,
            minimum_range_bps=minimum_range_bps,
        )
        day_bars: list[VolumeBar] = []
        for bar in iter_volume_bars(
            iter_download(current_record),
            target_quote_notional=selected.target_quote_notional,
            include_partial=False,
        ):
            day_bars.append(replace(bar, index=global_index))
            global_index += 1
        bars.extend(day_bars)
        calibrations.append(
            DailyClockCalibration(
                bar_day=current_day.isoformat(),
                source_day=source_day.isoformat(),
                selected_minutes=selected.calibration_minutes,
                selected_target_quote_notional=selected.target_quote_notional,
                selected_median_range_bps=selected.median_range_bps,
                minimum_range_bps=minimum_range_bps,
                fallback_to_largest=fallback,
                candidate_evidence=evidence,
                produced_event_bars=len(day_bars),
            ),
        )
        current_day += timedelta(days=1)
    return bars, calibrations
