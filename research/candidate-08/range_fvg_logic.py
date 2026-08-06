"""Causal completed-range and fair-value-gap pattern detector for candidate-08.

The detector converts official one-minute Binance bars into completed five-minute bars,
maintains only already-completed 4-hour/day/week external levels, and emits scenario
signals after a three-bar fair-value gap is observable. It never places orders, sizes
positions, or simulates fills; those responsibilities remain in NautilusTrader.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ScenarioFamily(str, Enum):
    ACCEPTANCE = "RANGE_ACCEPTANCE_FVG"
    REJECTION = "RANGE_REJECTION_FVG"


class LevelKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class LevelSource(str, Enum):
    FOUR_HOUR = "FOUR_HOUR"
    DAY = "DAY"
    WEEK = "WEEK"


SOURCE_RANK = {
    LevelSource.FOUR_HOUR: 1,
    LevelSource.DAY: 2,
    LevelSource.WEEK: 3,
}


@dataclass(frozen=True, slots=True)
class LogicEvent:
    scenario_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExternalLevel:
    level_id: str
    kind: LevelKind
    source: LevelSource
    level: float
    formed_index: int
    formed_time_ns: int
    period_key: str


@dataclass(frozen=True, slots=True)
class FiveMinuteBar:
    index: int
    ts_event_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: float
    taker_buy_volume: float
    imbalance: float
    atr: float
    volume_ratio: float
    trade_ratio: float
    efficiency_60m: float
    direction_60m: float
    session_key: str
    day_key: str
    week_key: str

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def spread(self) -> float:
        return self.high - self.low

    @property
    def close_location(self) -> float:
        return (self.close - self.low) / self.spread if self.spread > 0 else 0.5


@dataclass(frozen=True, slots=True)
class RangeFVGSignal:
    scenario_id: str
    family: ScenarioFamily
    direction: Direction
    signal_index: int
    signal_time_ns: int
    boundary_id: str
    boundary_source: LevelSource
    boundary_level: float
    fvg_low: float
    fvg_high: float
    limit_entry: float
    structural_stop: float
    external_target_id: str
    external_target_source: LevelSource
    external_target: float
    atr: float
    invalidation_before_fill: float
    events: tuple[LogicEvent, ...]
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RangeFVGConfig:
    five_minute_atr_period: int = 20
    activity_lookback: int = 48
    external_level_max_age_bars: int = 2016
    level_merge_atr: float = 0.04
    minimum_fvg_atr: float = 0.05
    fvg_entry_fraction: float = 0.50
    acceptance_close_beyond_atr: float = 0.10
    acceptance_body_atr: float = 0.55
    acceptance_volume_ratio: float = 1.20
    acceptance_trade_ratio: float = 1.10
    acceptance_imbalance: float = 0.10
    acceptance_close_location: float = 0.68
    acceptance_opposite_efficiency_block: float = 0.35
    rejection_sweep_atr: float = 0.05
    rejection_reclaim_atr: float = 0.02
    rejection_wick_body: float = 0.50
    rejection_displacement_body_atr: float = 0.40
    rejection_imbalance: float = 0.08
    rejection_close_location: float = 0.34
    rejection_countertrend_efficiency_block: float = 0.25
    rejection_structure_break_atr: float = 0.03
    stop_buffer_atr: float = 0.05
    entry_expiry_minutes: int = 20
    maximum_hold_minutes: int = 240
    enable_acceptance: bool = True
    enable_rejection: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RangeFVGConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})


@dataclass(frozen=True, slots=True)
class SignalBundle:
    five_minute_bars: tuple[FiveMinuteBar, ...]
    signals_by_time_ns: dict[int, tuple[RangeFVGSignal, ...]]
    diagnostics: dict[str, Any]


@dataclass(slots=True)
class _MutableLevel:
    level: ExternalLevel
    consumed: bool = False
    consumed_index: int | None = None


NUMERIC_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "taker_buy_volume",
)


def _numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=list(NUMERIC_COLUMNS)).copy()
    if not isinstance(result.index, pd.DatetimeIndex):
        raise TypeError("official bar frame must use a DatetimeIndex")
    if result.index.tz is None:
        result.index = result.index.tz_localize("UTC")
    else:
        result.index = result.index.tz_convert("UTC")
    result = result.sort_index()
    return result


def aggregate_five_minute_bars(frame: pd.DataFrame, config: RangeFVGConfig) -> pd.DataFrame:
    """Aggregate source-close one-minute rows into causally completed five-minute bars."""

    source = _numeric_frame(frame)
    source["ts_event_ns"] = source.index.as_unit("ns").asi8
    bucket = source.index.floor("5min")
    five = source.groupby(bucket, sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        trade_count=("trade_count", "sum"),
        taker_buy_volume=("taker_buy_volume", "sum"),
        source_rows=("close", "size"),
        ts_event_ns=("ts_event_ns", "max"),
    )
    # Partial buckets are unusable for a fixed five-minute scenario definition.
    five = five.loc[five["source_rows"] == 5].copy()
    five.index = pd.to_datetime(five.pop("ts_event_ns"), unit="ns", utc=True)

    previous_close = five["close"].shift(1)
    true_range = pd.concat(
        [
            five["high"] - five["low"],
            (five["high"] - previous_close).abs(),
            (five["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    five["atr"] = true_range.rolling(
        config.five_minute_atr_period,
        min_periods=config.five_minute_atr_period,
    ).mean()
    activity_min_periods = min(
        config.activity_lookback,
        max(2, config.activity_lookback // 2),
    )
    volume_median = five["volume"].shift(1).rolling(
        config.activity_lookback,
        min_periods=activity_min_periods,
    ).median()
    trade_median = five["trade_count"].shift(1).rolling(
        config.activity_lookback,
        min_periods=activity_min_periods,
    ).median()
    five["volume_ratio"] = five["volume"] / volume_median.replace(0, np.nan)
    five["trade_ratio"] = five["trade_count"] / trade_median.replace(0, np.nan)
    five["imbalance"] = (
        2.0 * five["taker_buy_volume"] - five["volume"]
    ) / five["volume"].replace(0, np.nan)

    movement = five["close"].diff()
    path = movement.abs().shift(1).rolling(12, min_periods=9).sum()
    five["efficiency_60m"] = (
        five["close"].shift(1) - five["close"].shift(13)
    ).abs() / path.replace(0, np.nan)
    five["direction_60m"] = np.sign(
        five["close"].shift(1) - five["close"].shift(13)
    )
    five["session_key"] = five.index.floor("4h").astype(str)
    five["day_key"] = five.index.floor("1d").astype(str)
    week_start = (five.index - pd.to_timedelta(five.index.weekday, unit="D")).floor("1d")
    five["week_key"] = week_start.astype(str)
    return five


def _bar_from_row(index: int, timestamp: pd.Timestamp, row: pd.Series) -> FiveMinuteBar | None:
    required = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "taker_buy_volume",
        "imbalance",
        "atr",
        "volume_ratio",
        "trade_ratio",
        "efficiency_60m",
        "direction_60m",
    )
    values = {name: float(row[name]) for name in required}
    if not all(isfinite(value) for value in values.values()):
        return None
    return FiveMinuteBar(
        index=index,
        ts_event_ns=int(timestamp.as_unit("ns").value),
        open=values["open"],
        high=values["high"],
        low=values["low"],
        close=values["close"],
        volume=values["volume"],
        trade_count=values["trade_count"],
        taker_buy_volume=values["taker_buy_volume"],
        imbalance=values["imbalance"],
        atr=values["atr"],
        volume_ratio=values["volume_ratio"],
        trade_ratio=values["trade_ratio"],
        efficiency_60m=values["efficiency_60m"],
        direction_60m=values["direction_60m"],
        session_key=str(row["session_key"]),
        day_key=str(row["day_key"]),
        week_key=str(row["week_key"]),
    )


def _period_summary(rows: list[FiveMinuteBar]) -> tuple[float, float, int, int]:
    if not rows:
        raise ValueError("cannot summarize an empty completed period")
    return (
        max(bar.high for bar in rows),
        min(bar.low for bar in rows),
        rows[-1].index,
        rows[-1].ts_event_ns,
    )


def _level_id(source: LevelSource, kind: LevelKind, period_key: str, level: float) -> str:
    return f"{source.value.lower()}-{period_key}-{kind.value.lower()}-{level:.1f}"


def _append_period_levels(
    levels: list[_MutableLevel],
    rows: list[FiveMinuteBar],
    source: LevelSource,
    period_key: str,
) -> None:
    expected = {
        LevelSource.FOUR_HOUR: 48,
        LevelSource.DAY: 288,
        LevelSource.WEEK: 2016,
    }[source]
    if len(rows) < expected:
        return
    high, low, formed_index, formed_time_ns = _period_summary(rows)
    for kind, value in ((LevelKind.HIGH, high), (LevelKind.LOW, low)):
        levels.append(
            _MutableLevel(
                ExternalLevel(
                    level_id=_level_id(source, kind, period_key, value),
                    kind=kind,
                    source=source,
                    level=value,
                    formed_index=formed_index,
                    formed_time_ns=formed_time_ns,
                    period_key=period_key,
                )
            )
        )


def _dedupe_snapshot(
    levels: Iterable[_MutableLevel],
    atr: float,
    merge_atr: float,
) -> tuple[ExternalLevel, ...]:
    active = [item.level for item in levels if not item.consumed]
    active.sort(key=lambda item: (item.kind.value, item.level, -SOURCE_RANK[item.source]))
    result: list[ExternalLevel] = []
    tolerance = max(0.1, atr * merge_atr)
    for level in active:
        duplicate_index = next(
            (
                i
                for i, existing in enumerate(result)
                if existing.kind is level.kind and abs(existing.level - level.level) <= tolerance
            ),
            None,
        )
        if duplicate_index is None:
            result.append(level)
        elif SOURCE_RANK[level.source] > SOURCE_RANK[result[duplicate_index].source]:
            result[duplicate_index] = level
    return tuple(result)


def _build_level_snapshots(
    bars: tuple[FiveMinuteBar, ...],
    config: RangeFVGConfig,
) -> tuple[tuple[ExternalLevel, ...], ...]:
    levels: list[_MutableLevel] = []
    snapshots: list[tuple[ExternalLevel, ...]] = []
    session_rows: list[FiveMinuteBar] = []
    day_rows: list[FiveMinuteBar] = []
    week_rows: list[FiveMinuteBar] = []
    session_key: str | None = None
    day_key: str | None = None
    week_key: str | None = None

    for bar in bars:
        if session_key is not None and bar.session_key != session_key:
            _append_period_levels(levels, session_rows, LevelSource.FOUR_HOUR, session_key)
            session_rows = []
        if day_key is not None and bar.day_key != day_key:
            _append_period_levels(levels, day_rows, LevelSource.DAY, day_key)
            day_rows = []
        if week_key is not None and bar.week_key != week_key:
            _append_period_levels(levels, week_rows, LevelSource.WEEK, week_key)
            week_rows = []

        session_key = bar.session_key
        day_key = bar.day_key
        week_key = bar.week_key

        # A snapshot is taken before the current bar can consume a level.
        levels[:] = [
            item
            for item in levels
            if not item.consumed
            and bar.index - item.level.formed_index <= config.external_level_max_age_bars
        ]
        snapshots.append(_dedupe_snapshot(levels, bar.atr, config.level_merge_atr))

        for item in levels:
            if item.consumed:
                continue
            level = item.level
            crossed = (
                bar.high >= level.level
                if level.kind is LevelKind.HIGH
                else bar.low <= level.level
            )
            if crossed:
                item.consumed = True
                item.consumed_index = bar.index

        session_rows.append(bar)
        day_rows.append(bar)
        week_rows.append(bar)

    return tuple(snapshots)


def _select_boundary(
    levels: Iterable[ExternalLevel],
    *,
    kind: LevelKind,
    reference: float,
    crossed_to: float,
    direction: int,
) -> ExternalLevel | None:
    if direction > 0:
        candidates = [
            level
            for level in levels
            if level.kind is kind and reference <= level.level < crossed_to
        ]
    else:
        candidates = [
            level
            for level in levels
            if level.kind is kind and crossed_to < level.level <= reference
        ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (SOURCE_RANK[item.source], -abs(crossed_to - item.level)),
    )


def _select_target(
    levels: Iterable[ExternalLevel],
    *,
    direction: int,
    confirmation_extreme: float,
) -> ExternalLevel | None:
    if direction > 0:
        candidates = [
            level
            for level in levels
            if level.kind is LevelKind.HIGH and level.level > confirmation_extreme
        ]
        return min(candidates, key=lambda item: item.level) if candidates else None
    candidates = [
        level
        for level in levels
        if level.kind is LevelKind.LOW and level.level < confirmation_extreme
    ]
    return max(candidates, key=lambda item: item.level) if candidates else None


def _bullish_fvg(a: FiveMinuteBar, c: FiveMinuteBar, atr: float, minimum: float) -> bool:
    return c.low - a.high >= minimum * atr


def _bearish_fvg(a: FiveMinuteBar, c: FiveMinuteBar, atr: float, minimum: float) -> bool:
    return a.low - c.high >= minimum * atr


def _regime_blocks_acceptance(bar: FiveMinuteBar, direction: int, config: RangeFVGConfig) -> bool:
    return (
        bar.efficiency_60m >= config.acceptance_opposite_efficiency_block
        and int(bar.direction_60m) == -direction
    )


def _regime_blocks_rejection(bar: FiveMinuteBar, sweep_direction: int, config: RangeFVGConfig) -> bool:
    return (
        bar.efficiency_60m >= config.rejection_countertrend_efficiency_block
        and int(bar.direction_60m) == sweep_direction
    )


def _acceptance_signal(
    scenario_id: str,
    a: FiveMinuteBar,
    b: FiveMinuteBar,
    c: FiveMinuteBar,
    levels_before_b: tuple[ExternalLevel, ...],
    levels_before_c: tuple[ExternalLevel, ...],
    config: RangeFVGConfig,
) -> tuple[RangeFVGSignal | None, str]:
    atr = b.atr
    if b.body < config.acceptance_body_atr * atr:
        return None, "ACCEPTANCE_BODY_TOO_SMALL"
    if b.volume_ratio < config.acceptance_volume_ratio or b.trade_ratio < config.acceptance_trade_ratio:
        return None, "ACCEPTANCE_ACTIVITY_TOO_LOW"

    bullish = (
        b.close > b.open
        and b.imbalance >= config.acceptance_imbalance
        and b.close_location >= config.acceptance_close_location
        and _bullish_fvg(a, c, atr, config.minimum_fvg_atr)
    )
    bearish = (
        b.close < b.open
        and b.imbalance <= -config.acceptance_imbalance
        and b.close_location <= 1.0 - config.acceptance_close_location
        and _bearish_fvg(a, c, atr, config.minimum_fvg_atr)
    )
    if not bullish and not bearish:
        return None, "NO_ACCEPTANCE_DISPLACEMENT_FVG"

    direction = 1 if bullish else -1
    if _regime_blocks_acceptance(b, direction, config):
        return None, "ACCEPTANCE_BLOCKED_BY_OPPOSITE_EFFICIENT_AUCTION"

    boundary = _select_boundary(
        levels_before_b,
        kind=LevelKind.HIGH if direction > 0 else LevelKind.LOW,
        reference=a.close,
        crossed_to=b.close,
        direction=direction,
    )
    if boundary is None:
        return None, "NO_COMPLETED_EXTERNAL_BOUNDARY_CROSSED"
    beyond = direction * (b.close - boundary.level)
    if beyond < config.acceptance_close_beyond_atr * atr:
        return None, "ACCEPTANCE_CLOSE_NOT_FAR_ENOUGH"

    if direction > 0:
        fvg_low, fvg_high = a.high, c.low
        entry = fvg_low + config.fvg_entry_fraction * (fvg_high - fvg_low)
        stop = min(a.low, b.low, boundary.level) - config.stop_buffer_atr * atr
        target = _select_target(levels_before_c, direction=direction, confirmation_extreme=c.high)
        invalidation = min(a.low, boundary.level - config.rejection_reclaim_atr * atr)
    else:
        fvg_low, fvg_high = c.high, a.low
        entry = fvg_low + config.fvg_entry_fraction * (fvg_high - fvg_low)
        stop = max(a.high, b.high, boundary.level) + config.stop_buffer_atr * atr
        target = _select_target(levels_before_c, direction=direction, confirmation_extreme=c.low)
        invalidation = max(a.high, boundary.level + config.rejection_reclaim_atr * atr)
    if target is None:
        return None, "NO_UNCONSUMED_EXTERNAL_TARGET"
    if not (stop < entry < target.level if direction > 0 else target.level < entry < stop):
        return None, "INVALID_ACCEPTANCE_GEOMETRY"

    direction_enum = Direction.LONG if direction > 0 else Direction.SHORT
    events = (
        LogicEvent(
            scenario_id=scenario_id,
            event_type="EXTERNAL_LEVEL_ACCEPTED",
            event_time_ns=b.ts_event_ns,
            observed_time_ns=b.ts_event_ns,
            previous_state="IDLE",
            next_state="ACCEPTED",
            reason_code=f"{boundary.source.value}_{boundary.kind.value}_DISPLACEMENT_CLOSE",
            reference_price=boundary.level,
            details={
                "boundary_id": boundary.level_id,
                "body_atr": b.body / atr,
                "volume_ratio": b.volume_ratio,
                "trade_ratio": b.trade_ratio,
                "imbalance": b.imbalance,
                "efficiency_60m": b.efficiency_60m,
                "direction_60m": b.direction_60m,
            },
        ),
        LogicEvent(
            scenario_id=scenario_id,
            event_type="FVG_CONFIRMED",
            event_time_ns=c.ts_event_ns,
            observed_time_ns=c.ts_event_ns,
            previous_state="ACCEPTED",
            next_state="CONFIRMED",
            reason_code=f"FIVE_MINUTE_FVG_{direction_enum.value}",
            reference_price=entry,
            details={"fvg_low": fvg_low, "fvg_high": fvg_high},
        ),
    )
    return (
        RangeFVGSignal(
            scenario_id=scenario_id,
            family=ScenarioFamily.ACCEPTANCE,
            direction=direction_enum,
            signal_index=c.index,
            signal_time_ns=c.ts_event_ns,
            boundary_id=boundary.level_id,
            boundary_source=boundary.source,
            boundary_level=boundary.level,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
            limit_entry=entry,
            structural_stop=stop,
            external_target_id=target.level_id,
            external_target_source=target.source,
            external_target=target.level,
            atr=atr,
            invalidation_before_fill=invalidation,
            events=events,
            details={
                "displacement_index": b.index,
                "confirmation_index": c.index,
                "target_period": target.period_key,
            },
        ),
        "ACCEPTANCE_SIGNAL",
    )


def _rejection_signal(
    scenario_id: str,
    sweep: FiveMinuteBar,
    displacement: FiveMinuteBar,
    confirmation: FiveMinuteBar,
    levels_before_sweep: tuple[ExternalLevel, ...],
    levels_before_confirmation: tuple[ExternalLevel, ...],
    config: RangeFVGConfig,
) -> tuple[RangeFVGSignal | None, str]:
    atr = sweep.atr
    body_floor = max(sweep.body, 0.01 * atr)
    high_boundaries = [
        level
        for level in levels_before_sweep
        if level.kind is LevelKind.HIGH
        and sweep.high >= level.level + config.rejection_sweep_atr * atr
        and sweep.close <= level.level - config.rejection_reclaim_atr * atr
    ]
    low_boundaries = [
        level
        for level in levels_before_sweep
        if level.kind is LevelKind.LOW
        and sweep.low <= level.level - config.rejection_sweep_atr * atr
        and sweep.close >= level.level + config.rejection_reclaim_atr * atr
    ]
    if high_boundaries and low_boundaries:
        return None, "BILATERAL_SWEEP_UNRESOLVED"

    bearish_rejection = bool(high_boundaries)
    bullish_rejection = bool(low_boundaries)
    if not bearish_rejection and not bullish_rejection:
        return None, "NO_SWEEP_RECLAIM"

    if bearish_rejection:
        boundary = max(
            high_boundaries,
            key=lambda item: (SOURCE_RANK[item.source], item.level),
        )
        wick = sweep.high - max(sweep.open, sweep.close)
        displacement_valid = (
            wick / body_floor >= config.rejection_wick_body
            and displacement.close < displacement.open
            and displacement.body >= config.rejection_displacement_body_atr * displacement.atr
            and displacement.imbalance <= -config.rejection_imbalance
            and displacement.close_location <= config.rejection_close_location
            and displacement.close <= sweep.low - config.rejection_structure_break_atr * displacement.atr
            and _bearish_fvg(sweep, confirmation, displacement.atr, config.minimum_fvg_atr)
        )
        if _regime_blocks_rejection(sweep, +1, config):
            return None, "BEARISH_REJECTION_BLOCKED_BY_UP_AUCTION"
        direction = -1
    else:
        boundary = max(
            low_boundaries,
            key=lambda item: (SOURCE_RANK[item.source], -item.level),
        )
        wick = min(sweep.open, sweep.close) - sweep.low
        displacement_valid = (
            wick / body_floor >= config.rejection_wick_body
            and displacement.close > displacement.open
            and displacement.body >= config.rejection_displacement_body_atr * displacement.atr
            and displacement.imbalance >= config.rejection_imbalance
            and displacement.close_location >= 1.0 - config.rejection_close_location
            and displacement.close >= sweep.high + config.rejection_structure_break_atr * displacement.atr
            and _bullish_fvg(sweep, confirmation, displacement.atr, config.minimum_fvg_atr)
        )
        if _regime_blocks_rejection(sweep, -1, config):
            return None, "BULLISH_REJECTION_BLOCKED_BY_DOWN_AUCTION"
        direction = +1
    if not displacement_valid:
        return None, "NO_POST_SWEEP_DISPLACEMENT_FVG"

    if direction > 0:
        fvg_low, fvg_high = sweep.high, confirmation.low
        entry = fvg_low + config.fvg_entry_fraction * (fvg_high - fvg_low)
        stop = sweep.low - config.stop_buffer_atr * atr
        target = _select_target(
            levels_before_confirmation,
            direction=direction,
            confirmation_extreme=confirmation.high,
        )
        invalidation = sweep.low
    else:
        fvg_low, fvg_high = confirmation.high, sweep.low
        entry = fvg_low + config.fvg_entry_fraction * (fvg_high - fvg_low)
        stop = sweep.high + config.stop_buffer_atr * atr
        target = _select_target(
            levels_before_confirmation,
            direction=direction,
            confirmation_extreme=confirmation.low,
        )
        invalidation = sweep.high
    if target is None:
        return None, "NO_OPPOSITE_EXTERNAL_TARGET"
    if not (stop < entry < target.level if direction > 0 else target.level < entry < stop):
        return None, "INVALID_REJECTION_GEOMETRY"

    direction_enum = Direction.LONG if direction > 0 else Direction.SHORT
    events = (
        LogicEvent(
            scenario_id=scenario_id,
            event_type="EXTERNAL_LEVEL_SWEPT_RECLAIMED",
            event_time_ns=sweep.ts_event_ns,
            observed_time_ns=sweep.ts_event_ns,
            previous_state="IDLE",
            next_state="SWEEP_RECLAIMED",
            reason_code=f"{boundary.source.value}_{boundary.kind.value}_SWEEP_RECLAIM",
            reference_price=boundary.level,
            details={
                "boundary_id": boundary.level_id,
                "sweep_extreme": sweep.low if direction > 0 else sweep.high,
                "efficiency_60m": sweep.efficiency_60m,
                "direction_60m": sweep.direction_60m,
            },
        ),
        LogicEvent(
            scenario_id=scenario_id,
            event_type="POST_SWEEP_DISPLACEMENT",
            event_time_ns=displacement.ts_event_ns,
            observed_time_ns=displacement.ts_event_ns,
            previous_state="SWEEP_RECLAIMED",
            next_state="DISPLACED",
            reason_code=f"STRUCTURE_BREAK_{direction_enum.value}",
            reference_price=displacement.close,
            details={
                "body_atr": displacement.body / displacement.atr,
                "imbalance": displacement.imbalance,
            },
        ),
        LogicEvent(
            scenario_id=scenario_id,
            event_type="FVG_CONFIRMED",
            event_time_ns=confirmation.ts_event_ns,
            observed_time_ns=confirmation.ts_event_ns,
            previous_state="DISPLACED",
            next_state="CONFIRMED",
            reason_code=f"FIVE_MINUTE_FVG_{direction_enum.value}",
            reference_price=entry,
            details={"fvg_low": fvg_low, "fvg_high": fvg_high},
        ),
    )
    return (
        RangeFVGSignal(
            scenario_id=scenario_id,
            family=ScenarioFamily.REJECTION,
            direction=direction_enum,
            signal_index=confirmation.index,
            signal_time_ns=confirmation.ts_event_ns,
            boundary_id=boundary.level_id,
            boundary_source=boundary.source,
            boundary_level=boundary.level,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
            limit_entry=entry,
            structural_stop=stop,
            external_target_id=target.level_id,
            external_target_source=target.source,
            external_target=target.level,
            atr=atr,
            invalidation_before_fill=invalidation,
            events=events,
            details={
                "sweep_index": sweep.index,
                "displacement_index": displacement.index,
                "confirmation_index": confirmation.index,
                "target_period": target.period_key,
            },
        ),
        "REJECTION_SIGNAL",
    )


def build_range_fvg_signals(
    one_minute_frame: pd.DataFrame,
    config: RangeFVGConfig,
) -> SignalBundle:
    five_frame = aggregate_five_minute_bars(one_minute_frame, config)
    bars = tuple(
        bar
        for index, (timestamp, row) in enumerate(five_frame.iterrows())
        if (bar := _bar_from_row(index, timestamp, row)) is not None
    )
    snapshots = _build_level_snapshots(bars, config)
    diagnostics: Counter[str] = Counter()
    signals_by_time: dict[int, list[RangeFVGSignal]] = {}
    scenario_counter = 0

    for i in range(2, len(bars)):
        a, b, c = bars[i - 2], bars[i - 1], bars[i]
        # Bars with missing feature warmup were omitted, so require real five-minute adjacency.
        if c.ts_event_ns - b.ts_event_ns > 5 * 60 * 1_000_000_000 + 1_000_000:
            diagnostics["NON_ADJACENT_BAR_TRIPLE"] += 1
            continue
        if b.ts_event_ns - a.ts_event_ns > 5 * 60 * 1_000_000_000 + 1_000_000:
            diagnostics["NON_ADJACENT_BAR_TRIPLE"] += 1
            continue

        candidates: list[RangeFVGSignal] = []
        if config.enable_acceptance:
            scenario_counter += 1
            signal, reason = _acceptance_signal(
                f"rfvg-a-{scenario_counter:07d}",
                a,
                b,
                c,
                snapshots[i - 1],
                snapshots[i],
                config,
            )
            diagnostics[reason] += 1
            if signal is not None:
                candidates.append(signal)
        if config.enable_rejection:
            scenario_counter += 1
            signal, reason = _rejection_signal(
                f"rfvg-r-{scenario_counter:07d}",
                a,
                b,
                c,
                snapshots[i - 2],
                snapshots[i],
                config,
            )
            diagnostics[reason] += 1
            if signal is not None:
                candidates.append(signal)

        # A single five-minute FVG cannot be both bullish and bearish. If multiple structural
        # boundaries describe it, retain the higher-timeframe interaction deterministically.
        if candidates:
            candidates.sort(
                key=lambda item: (
                    SOURCE_RANK[item.boundary_source],
                    SOURCE_RANK[item.external_target_source],
                    item.family is ScenarioFamily.REJECTION,
                ),
                reverse=True,
            )
            selected = candidates[0]
            signals_by_time.setdefault(c.ts_event_ns, []).append(selected)
            diagnostics[f"SELECTED_{selected.family.value}"] += 1
            diagnostics[f"BOUNDARY_{selected.boundary_source.value}"] += 1
            diagnostics[f"TARGET_{selected.external_target_source.value}"] += 1
            if len(candidates) > 1:
                diagnostics["MULTIPLE_CANDIDATES_DEDUPED"] += len(candidates) - 1

    immutable = {key: tuple(value) for key, value in signals_by_time.items()}
    return SignalBundle(
        five_minute_bars=bars,
        signals_by_time_ns=immutable,
        diagnostics={
            "counts": dict(sorted(diagnostics.items())),
            "five_minute_bars": len(bars),
            "signal_times": len(immutable),
            "signals": sum(len(value) for value in immutable.values()),
        },
    )


def group_events_by_reason(events: Iterable[LogicEvent]) -> dict[str, int]:
    counts: Counter[str] = Counter(event.reason_code for event in events)
    return dict(sorted(counts.items()))
