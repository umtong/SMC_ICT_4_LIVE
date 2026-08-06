#!/usr/bin/env python3
"""Causal completed-day and completed-week external-liquidity book.

Calendar highs and lows become observable liquidity references only after the
corresponding UTC day or UTC Monday-Sunday week has fully completed.  An active
reference is removed on the first later completed equal-notional event whose
range trades that price.  The book is independent of strategy PnL and exposes
only causally available, unconsumed levels.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from aggtrade_clock import VolumeBar
from core import Side


Period = Literal["DAY", "WEEK"]
LevelSide = Literal["HIGH", "LOW"]


@dataclass(slots=True)
class CalendarPeriodBuilder:
    period: Period
    key: str
    high: float
    low: float
    start_time_ns: int
    end_time_ns: int
    days_seen: set[str] = field(default_factory=set)
    bars: int = 1

    def update(self, bar: VolumeBar, *, day_key: str) -> None:
        self.high = max(self.high, float(bar.high))
        self.low = min(self.low, float(bar.low))
        self.end_time_ns = int(bar.end_time_ns)
        self.days_seen.add(day_key)
        self.bars += 1


@dataclass(slots=True)
class CalendarLiquidityLevel:
    level_id: str
    period: Period
    period_key: str
    level_side: LevelSide
    price: float
    period_start_time_ns: int
    period_end_time_ns: int
    available_time_ns: int
    source_days: int
    source_bars: int
    consumed_time_ns: int | None = None
    consumed_bar_index: int | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["active"] = self.active
        return payload


@dataclass(frozen=True, slots=True)
class CalendarLiquidityEvent:
    event_type: str
    observed_time_ns: int
    bar_index: int
    level_id: str
    period: str
    period_key: str
    level_side: str
    price: float
    details: str


@dataclass(frozen=True, slots=True)
class CalendarTargetSelection:
    scenario_id: str
    signal_time_ns: int
    side: str
    local_internal_pivot: float
    local_intermediate_pivot: float
    target_level_id: str
    target_period: str
    target_period_key: str
    target_price: float
    target_available_time_ns: int
    target_distance_from_intermediate: float


class CausalCalendarLiquidityBook:
    """Sequential book of completed, unconsumed day/week highs and lows."""

    def __init__(self) -> None:
        self.current_day_key: str | None = None
        self.current_week_key: str | None = None
        self.day_builder: CalendarPeriodBuilder | None = None
        self.week_builder: CalendarPeriodBuilder | None = None
        self.levels: list[CalendarLiquidityLevel] = []
        self.events: list[CalendarLiquidityEvent] = []
        self.skipped_incomplete_weeks = 0

    @staticmethod
    def _keys(ts_ns: int) -> tuple[str, str]:
        instant = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        day = instant.date()
        monday = day - timedelta(days=day.weekday())
        return day.isoformat(), monday.isoformat()

    def _event(
        self,
        *,
        event_type: str,
        observed_time_ns: int,
        bar_index: int,
        level: CalendarLiquidityLevel,
        details: str,
    ) -> None:
        self.events.append(
            CalendarLiquidityEvent(
                event_type=event_type,
                observed_time_ns=observed_time_ns,
                bar_index=bar_index,
                level_id=level.level_id,
                period=level.period,
                period_key=level.period_key,
                level_side=level.level_side,
                price=level.price,
                details=details,
            ),
        )

    def _add_builder_levels(
        self,
        *,
        builder: CalendarPeriodBuilder,
        available_time_ns: int,
        bar_index: int,
    ) -> None:
        if builder.period == "WEEK" and len(builder.days_seen) != 7:
            self.skipped_incomplete_weeks += 1
            return
        for level_side, price in (
            ("HIGH", builder.high),
            ("LOW", builder.low),
        ):
            level = CalendarLiquidityLevel(
                level_id=(
                    f"calendar:{builder.period.lower()}:{builder.key}:"
                    f"{level_side.lower()}:{price:.8f}"
                ),
                period=builder.period,
                period_key=builder.key,
                level_side=level_side,
                price=float(price),
                period_start_time_ns=int(builder.start_time_ns),
                period_end_time_ns=int(builder.end_time_ns),
                available_time_ns=int(available_time_ns),
                source_days=len(builder.days_seen),
                source_bars=int(builder.bars),
            )
            self.levels.append(level)
            self._event(
                event_type="LEVEL_ACTIVATED",
                observed_time_ns=available_time_ns,
                bar_index=bar_index,
                level=level,
                details="completed calendar period became causally available",
            )

    @staticmethod
    def _new_builder(
        *,
        period: Period,
        key: str,
        day_key: str,
        bar: VolumeBar,
    ) -> CalendarPeriodBuilder:
        return CalendarPeriodBuilder(
            period=period,
            key=key,
            high=float(bar.high),
            low=float(bar.low),
            start_time_ns=int(bar.start_time_ns),
            end_time_ns=int(bar.end_time_ns),
            days_seen={day_key},
        )

    def _roll(self, bar: VolumeBar, *, day_key: str, week_key: str) -> None:
        if day_key != self.current_day_key:
            if self.day_builder is not None:
                self._add_builder_levels(
                    builder=self.day_builder,
                    available_time_ns=int(bar.start_time_ns),
                    bar_index=int(bar.index),
                )
            self.day_builder = self._new_builder(
                period="DAY",
                key=day_key,
                day_key=day_key,
                bar=bar,
            )
            self.current_day_key = day_key
        else:
            assert self.day_builder is not None
            self.day_builder.update(bar, day_key=day_key)

        if week_key != self.current_week_key:
            if self.week_builder is not None:
                self._add_builder_levels(
                    builder=self.week_builder,
                    available_time_ns=int(bar.start_time_ns),
                    bar_index=int(bar.index),
                )
            self.week_builder = self._new_builder(
                period="WEEK",
                key=week_key,
                day_key=day_key,
                bar=bar,
            )
            self.current_week_key = week_key
        else:
            assert self.week_builder is not None
            self.week_builder.update(bar, day_key=day_key)

    def _consume(self, bar: VolumeBar) -> None:
        for level in self.levels:
            if not level.active or level.available_time_ns > int(bar.end_time_ns):
                continue
            touched = (
                float(bar.high) >= level.price
                if level.level_side == "HIGH"
                else float(bar.low) <= level.price
            )
            if not touched:
                continue
            level.consumed_time_ns = int(bar.end_time_ns)
            level.consumed_bar_index = int(bar.index)
            self._event(
                event_type="LEVEL_CONSUMED",
                observed_time_ns=int(bar.end_time_ns),
                bar_index=int(bar.index),
                level=level,
                details="first later completed event traded the reference price",
            )

    def on_bar(self, bar: VolumeBar) -> None:
        day_key, week_key = self._keys(int(bar.end_time_ns))
        self._roll(bar, day_key=day_key, week_key=week_key)
        # Newly activated previous-period levels are eligible to be consumed by
        # the first event of the new day/week. Current-period builders are never
        # active before their own period completes.
        self._consume(bar)

    def select_target(
        self,
        *,
        scenario_id: str,
        signal_time_ns: int,
        side: Side,
        local_internal_pivot: float,
        local_intermediate_pivot: float,
    ) -> CalendarTargetSelection | None:
        required_side: LevelSide = "HIGH" if side is Side.LONG else "LOW"
        candidates = [
            level
            for level in self.levels
            if (
                level.active
                and level.level_side == required_side
                and level.available_time_ns <= signal_time_ns
                and (
                    level.price > local_intermediate_pivot
                    if side is Side.LONG
                    else level.price < local_intermediate_pivot
                )
            )
        ]
        if not candidates:
            return None
        ordered = sorted(
            candidates,
            key=lambda level: (
                abs(level.price - local_intermediate_pivot),
                0 if level.period == "DAY" else 1,
                level.available_time_ns,
                level.level_id,
            ),
        )
        level = ordered[0]
        return CalendarTargetSelection(
            scenario_id=scenario_id,
            signal_time_ns=int(signal_time_ns),
            side=side.value,
            local_internal_pivot=float(local_internal_pivot),
            local_intermediate_pivot=float(local_intermediate_pivot),
            target_level_id=level.level_id,
            target_period=level.period,
            target_period_key=level.period_key,
            target_price=float(level.price),
            target_available_time_ns=int(level.available_time_ns),
            target_distance_from_intermediate=abs(
                float(level.price) - float(local_intermediate_pivot)
            ),
        )
