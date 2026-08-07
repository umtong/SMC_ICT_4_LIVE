"""Causal external-liquidity sweep -> MSS/FVG retrace -> reacceleration signals.

The detector consumes completed ten-second aggregate-trade and native top-of-book features plus
already-completed 4-hour/day/week liquidity snapshots.  It emits one reversal family only after:

1. a completed external level is swept and reclaimed;
2. a separately completed one-minute displacement breaks a pre-sweep confirmed internal swing and
   leaves a three-candle fair-value gap;
3. price returns to the gap's consequent encroachment with contracted activity while the sweep
   extreme remains intact; and
4. a separately completed one-minute bar reaccelerates through the retest extreme.

The module owns no orders, fills, account state, sizing, PnL, or backtest engine.  NautilusTrader
remains authoritative for those responsibilities.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    _context_for_ten_second_close,
    causal_stop_slippage_reserve_series,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
    executable_quote_reference,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, SOURCE_RANK


SIGNAL_REVISION = "CAUSAL_EXTERNAL_SWEEP_MSS_FVG_RETRACE_REACCELERATION_V1"
SCENARIO_FAMILY = "EXTERNAL_SWEEP_MSS_FVG_RETRACE_REACCELERATION"


@dataclass(frozen=True, slots=True)
class ExternalSweepFvgConfig:
    minute_atr_bars: int = 60
    minimum_minute_history: int = 30
    internal_swing_span: int = 2
    sweep_extension_atr: float = 0.03
    reclaim_atr: float = 0.01
    maximum_displacement_minutes: int = 12
    minimum_displacement_body_atr: float = 0.45
    minimum_displacement_imbalance: float = 0.10
    minimum_displacement_volume_ratio: float = 1.15
    minimum_displacement_trade_ratio: float = 1.05
    displacement_close_location: float = 0.68
    minimum_fvg_atr: float = 0.02
    maximum_retrace_minutes: int = 20
    retrace_fraction: float = 0.50
    maximum_retrace_volume_fraction: float = 0.90
    maximum_retrace_trade_fraction: float = 0.95
    maximum_retrace_imbalance_fraction: float = 0.80
    maximum_reacceleration_minutes: int = 6
    minimum_reacceleration_body_atr: float = 0.20
    minimum_reacceleration_imbalance: float = 0.08
    reacceleration_close_location: float = 0.62
    stop_buffer_atr: float = 0.03
    minimum_stop_atr: float = 0.25

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ExternalSweepFvgConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    def validate(self) -> None:
        integer_fields = (
            self.minute_atr_bars,
            self.minimum_minute_history,
            self.internal_swing_span,
            self.maximum_displacement_minutes,
            self.maximum_retrace_minutes,
            self.maximum_reacceleration_minutes,
        )
        if any(value <= 0 for value in integer_fields):
            raise ValueError("external-sweep integer contracts must be positive")
        if self.minimum_minute_history > self.minute_atr_bars:
            raise ValueError("minimum minute history cannot exceed ATR/activity lookback")
        positive_fields = (
            self.sweep_extension_atr,
            self.reclaim_atr,
            self.minimum_displacement_body_atr,
            self.minimum_displacement_imbalance,
            self.minimum_displacement_volume_ratio,
            self.minimum_displacement_trade_ratio,
            self.displacement_close_location,
            self.minimum_fvg_atr,
            self.retrace_fraction,
            self.maximum_retrace_volume_fraction,
            self.maximum_retrace_trade_fraction,
            self.maximum_retrace_imbalance_fraction,
            self.minimum_reacceleration_body_atr,
            self.minimum_reacceleration_imbalance,
            self.reacceleration_close_location,
            self.stop_buffer_atr,
            self.minimum_stop_atr,
        )
        if any(value <= 0.0 for value in positive_fields):
            raise ValueError("external-sweep ratio contracts must be positive")
        if not 0.0 < self.retrace_fraction < 1.0:
            raise ValueError("retrace_fraction must be in (0, 1)")
        if not 0.5 < self.displacement_close_location < 1.0:
            raise ValueError("displacement close location must be in (0.5, 1)")
        if not 0.5 < self.reacceleration_close_location < 1.0:
            raise ValueError("reacceleration close location must be in (0.5, 1)")


@dataclass(slots=True)
class _PendingSweep:
    scenario_id: str
    boundary: ExternalLevel
    direction: int
    sweep_position: int
    sweep_time_ns: int
    sweep_extreme: float
    internal_break_level: float
    internal_break_time_ns: int
    displacement_expiry_position: int
    state: str = "WAIT_DISPLACEMENT"
    displacement_position: int | None = None
    displacement_time_ns: int | None = None
    displacement_volume: float | None = None
    displacement_trade_count: float | None = None
    displacement_imbalance: float | None = None
    fvg_low: float | None = None
    fvg_high: float | None = None
    retrace_expiry_position: int | None = None
    retest_position: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_volume: float | None = None
    retest_trade_count: float | None = None
    reacceleration_expiry_position: int | None = None
    events: list[QuoteResiliencyLogicEvent] = field(default_factory=list)


_REQUIRED_TEN_SECOND_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "signed_volume",
    "trade_count",
    "bid_close",
    "ask_close",
    "bid_qty_close",
    "ask_qty_close",
    "native_quote_snapshot_observable",
)


def _minute_labels(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    minute_ns = 60 * 1_000_000_000
    values = index.as_unit("ns").asi8
    labels = ((values - 1) // minute_ns + 1) * minute_ns
    return pd.DatetimeIndex(pd.to_datetime(labels, utc=True))


def aggregate_completed_minutes(data: pd.DataFrame, config: ExternalSweepFvgConfig) -> pd.DataFrame:
    """Aggregate exact completed ten-second rows into right-labelled completed one-minute bars."""

    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError("ten-second data must have unique increasing timestamps")
    missing = sorted(set(_REQUIRED_TEN_SECOND_COLUMNS) - set(data.columns))
    if missing:
        raise ValueError(f"ten-second data missing columns: {missing}")

    source = data.loc[:, list(_REQUIRED_TEN_SECOND_COLUMNS)].copy()
    for column in _REQUIRED_TEN_SECOND_COLUMNS[:-1]:
        source[column] = pd.to_numeric(source[column], errors="coerce")
    source["minute_end"] = _minute_labels(source.index)
    minute = source.groupby("minute_end", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        signed_volume=("signed_volume", "sum"),
        trade_count=("trade_count", "sum"),
        bid_close=("bid_close", "last"),
        ask_close=("ask_close", "last"),
        bid_qty_close=("bid_qty_close", "last"),
        ask_qty_close=("ask_qty_close", "last"),
        native_quote_snapshot_observable=("native_quote_snapshot_observable", "last"),
        source_rows=("close", "size"),
    )
    minute.index = pd.DatetimeIndex(minute.index)
    minute = minute.loc[minute["source_rows"] == 6].copy()
    if minute.empty:
        raise ValueError("no complete one-minute bars were available")

    previous_close = minute["close"].shift(1)
    true_range = pd.concat(
        [
            minute["high"] - minute["low"],
            (minute["high"] - previous_close).abs(),
            (minute["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    minute["atr"] = true_range.shift(1).rolling(
        config.minute_atr_bars,
        min_periods=config.minimum_minute_history,
    ).median()
    minute["volume_baseline"] = minute["volume"].shift(1).rolling(
        config.minute_atr_bars,
        min_periods=config.minimum_minute_history,
    ).median()
    minute["trade_baseline"] = minute["trade_count"].shift(1).rolling(
        config.minute_atr_bars,
        min_periods=config.minimum_minute_history,
    ).median()
    minute["imbalance"] = minute["signed_volume"] / minute["volume"].replace(0.0, np.nan)
    minute["volume_ratio"] = minute["volume"] / minute["volume_baseline"].replace(0.0, np.nan)
    minute["trade_ratio"] = minute["trade_count"] / minute["trade_baseline"].replace(0.0, np.nan)
    spread = minute["high"] - minute["low"]
    minute["close_location"] = (minute["close"] - minute["low"]) / spread.replace(0.0, np.nan)
    minute["body"] = (minute["close"] - minute["open"]).abs()
    minute.attrs["completion_contract"] = "SIX_EXACT_COMPLETED_TEN_SECOND_BUCKETS_PER_MINUTE"
    return minute


def _confirmed_internal_swings(
    minute: pd.DataFrame,
    *,
    span: int,
) -> tuple[list[tuple[float, int] | None], list[tuple[float, int] | None]]:
    """Return the latest causally confirmed swing high/low observable at every minute."""

    highs: list[tuple[float, int] | None] = [None] * len(minute.index)
    lows: list[tuple[float, int] | None] = [None] * len(minute.index)
    latest_high: tuple[float, int] | None = None
    latest_low: tuple[float, int] | None = None
    high_values = minute["high"].to_numpy(dtype=float)
    low_values = minute["low"].to_numpy(dtype=float)
    times = minute.index.as_unit("ns").asi8

    for current in range(len(minute.index)):
        candidate = current - span
        if candidate >= span:
            left_high = high_values[candidate - span : candidate]
            right_high = high_values[candidate + 1 : current + 1]
            left_low = low_values[candidate - span : candidate]
            right_low = low_values[candidate + 1 : current + 1]
            if (
                len(right_high) == span
                and high_values[candidate] > float(np.max(left_high))
                and high_values[candidate] >= float(np.max(right_high))
            ):
                latest_high = (float(high_values[candidate]), int(times[candidate]))
            if (
                len(right_low) == span
                and low_values[candidate] < float(np.min(left_low))
                and low_values[candidate] <= float(np.min(right_low))
            ):
                latest_low = (float(low_values[candidate]), int(times[candidate]))
        highs[current] = latest_high
        lows[current] = latest_low
    return highs, lows


def _source_name(level: ExternalLevel) -> str:
    value = getattr(level.source, "value", level.source)
    return str(value)


def _kind_is(level: ExternalLevel, kind: LevelKind) -> bool:
    return level.kind is kind or str(getattr(level.kind, "value", level.kind)) == str(kind.value)


def _crossed_and_reclaimed(
    levels: tuple[ExternalLevel, ...],
    *,
    previous_close: float,
    high: float,
    low: float,
    close: float,
    atr: float,
    tick: float,
    config: ExternalSweepFvgConfig,
    consumed: set[str],
) -> tuple[list[tuple[ExternalLevel, int]], list[ExternalLevel]]:
    sweeps: list[tuple[ExternalLevel, int]] = []
    crossed: list[ExternalLevel] = []
    extension = max(tick, config.sweep_extension_atr * atr)
    reclaim = config.reclaim_atr * atr
    for level in levels:
        if level.level_id in consumed:
            continue
        high_cross = (
            _kind_is(level, LevelKind.HIGH)
            and previous_close <= level.level
            and high >= level.level + extension
        )
        low_cross = (
            _kind_is(level, LevelKind.LOW)
            and previous_close >= level.level
            and low <= level.level - extension
        )
        if not high_cross and not low_cross:
            continue
        crossed.append(level)
        if high_cross and close <= level.level - reclaim:
            sweeps.append((level, -1))
        elif low_cross and close >= level.level + reclaim:
            sweeps.append((level, 1))
    return sweeps, crossed


def _select_sweep(candidates: list[tuple[ExternalLevel, int]]) -> tuple[ExternalLevel, int] | None:
    directions = {direction for _level, direction in candidates}
    if len(directions) != 1 or not candidates:
        return None
    direction = next(iter(directions))
    if direction < 0:
        return max(
            candidates,
            key=lambda item: (SOURCE_RANK[item[0].source], item[0].level),
        )
    return max(
        candidates,
        key=lambda item: (SOURCE_RANK[item[0].source], -item[0].level),
    )


def _displacement_fvg(
    minute: pd.DataFrame,
    position: int,
    pending: _PendingSweep,
    config: ExternalSweepFvgConfig,
    tick: float,
) -> tuple[float, float] | None:
    if position < 2:
        return None
    row = minute.iloc[position]
    two_back = minute.iloc[position - 2]
    atr = float(row["atr"])
    values = (
        atr,
        float(row["imbalance"]),
        float(row["volume_ratio"]),
        float(row["trade_ratio"]),
        float(row["close_location"]),
    )
    if not all(isfinite(value) for value in values) or atr <= 0.0:
        return None
    direction = pending.direction
    directional_body = direction * float(row["close"] - row["open"])
    directional_flow = direction * float(row["imbalance"])
    minimum_gap = max(tick, config.minimum_fvg_atr * atr)
    if direction > 0:
        broke = float(row["close"]) > pending.internal_break_level + tick
        gap_low = float(two_back["high"])
        gap_high = float(row["low"])
        fvg = gap_high >= gap_low + minimum_gap
        located = float(row["close_location"]) >= config.displacement_close_location
    else:
        broke = float(row["close"]) < pending.internal_break_level - tick
        gap_low = float(row["high"])
        gap_high = float(two_back["low"])
        fvg = gap_high >= gap_low + minimum_gap
        located = float(row["close_location"]) <= 1.0 - config.displacement_close_location
    if not (
        broke
        and fvg
        and located
        and directional_body >= config.minimum_displacement_body_atr * atr
        and directional_flow >= config.minimum_displacement_imbalance
        and float(row["volume_ratio"]) >= config.minimum_displacement_volume_ratio
        and float(row["trade_ratio"]) >= config.minimum_displacement_trade_ratio
    ):
        return None
    return gap_low, gap_high


def _retrace_holds(
    row: pd.Series,
    pending: _PendingSweep,
    config: ExternalSweepFvgConfig,
) -> bool:
    if (
        pending.fvg_low is None
        or pending.fvg_high is None
        or pending.displacement_volume is None
        or pending.displacement_trade_count is None
        or pending.displacement_imbalance is None
    ):
        raise RuntimeError("retrace evaluated before displacement state was complete")
    consequent_encroachment = pending.fvg_low + config.retrace_fraction * (
        pending.fvg_high - pending.fvg_low
    )
    if pending.direction > 0:
        touched = float(row["low"]) <= consequent_encroachment
        held = float(row["close"]) >= pending.fvg_low
    else:
        touched = float(row["high"]) >= consequent_encroachment
        held = float(row["close"]) <= pending.fvg_high
    contracted = (
        float(row["volume"]) <= config.maximum_retrace_volume_fraction * pending.displacement_volume
        and float(row["trade_count"])
        <= config.maximum_retrace_trade_fraction * pending.displacement_trade_count
        and abs(float(row["imbalance"]))
        <= config.maximum_retrace_imbalance_fraction * abs(pending.displacement_imbalance)
    )
    return touched and held and contracted


def _reaccelerates(
    row: pd.Series,
    pending: _PendingSweep,
    config: ExternalSweepFvgConfig,
    tick: float,
) -> bool:
    if (
        pending.retest_high is None
        or pending.retest_low is None
        or pending.retest_volume is None
        or pending.retest_trade_count is None
    ):
        raise RuntimeError("reacceleration evaluated before retest state was complete")
    atr = float(row["atr"])
    if not isfinite(atr) or atr <= 0.0:
        return False
    direction = pending.direction
    if direction > 0:
        broke = float(row["close"]) > pending.retest_high + tick
        located = float(row["close_location"]) >= config.reacceleration_close_location
    else:
        broke = float(row["close"]) < pending.retest_low - tick
        located = float(row["close_location"]) <= 1.0 - config.reacceleration_close_location
    return (
        broke
        and located
        and direction * float(row["close"] - row["open"])
        >= config.minimum_reacceleration_body_atr * atr
        and direction * float(row["imbalance"])
        >= config.minimum_reacceleration_imbalance
        and float(row["volume"]) >= pending.retest_volume
        and float(row["trade_count"]) >= pending.retest_trade_count
    )


def _select_target(
    levels: tuple[ExternalLevel, ...],
    *,
    direction: int,
    entry: float,
    excluded_level_id: str,
    consumed: set[str],
) -> ExternalLevel | None:
    if direction > 0:
        candidates = [
            level
            for level in levels
            if level.level_id != excluded_level_id
            and level.level_id not in consumed
            and _kind_is(level, LevelKind.HIGH)
            and level.level > entry
        ]
    else:
        candidates = [
            level
            for level in levels
            if level.level_id != excluded_level_id
            and level.level_id not in consumed
            and _kind_is(level, LevelKind.LOW)
            and level.level < entry
        ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda level: (abs(level.level - entry), -SOURCE_RANK[level.source]),
    )


def _structural_stop(
    *,
    direction: int,
    entry: float,
    sweep_extreme: float,
    atr: float,
    config: ExternalSweepFvgConfig,
) -> float:
    buffer = config.stop_buffer_atr * atr
    minimum_distance = config.minimum_stop_atr * atr
    if direction > 0:
        return min(sweep_extreme - buffer, entry - minimum_distance)
    return max(sweep_extreme + buffer, entry + minimum_distance)


def _cost_geometry(
    *,
    direction: int,
    quote_reference: float,
    stop: float,
    target: float,
    fee_rate: float,
    tick: float,
    stop_slippage_reserve: float,
) -> tuple[float, float, float] | None:
    expected_entry_fill = quote_reference + tick if direction > 0 else quote_reference - tick
    valid = stop < expected_entry_fill < target if direction > 0 else target < expected_entry_fill < stop
    if not valid:
        return None
    loss = (
        abs(expected_entry_fill - stop)
        + fee_rate * (expected_entry_fill + stop)
        + max(tick, stop_slippage_reserve)
    )
    gross_gain = (
        target - expected_entry_fill
        if direction > 0
        else expected_entry_fill - target
    )
    gain = gross_gain - fee_rate * (expected_entry_fill + target) - tick
    if loss <= 0.0 or gain <= 0.0:
        return None
    return loss, gain, gain / loss


def _signal_from_reacceleration(
    *,
    data: pd.DataFrame,
    minute: pd.DataFrame,
    position: int,
    pending: _PendingSweep,
    target_levels: tuple[ExternalLevel, ...],
    consumed: set[str],
    stop_reserves: pd.Series,
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    config: ExternalSweepFvgConfig,
) -> tuple[QuoteResiliencySignal | None, str]:
    timestamp = minute.index[position]
    if timestamp not in data.index:
        return None, "NO_EXACT_TEN_SECOND_COMPLETION_ROW"
    ten_second_row = data.loc[timestamp]
    if not bool(ten_second_row.get("native_quote_snapshot_observable", False)):
        return None, "NO_NATIVE_L1_AT_REACCELERATION"
    direction = pending.direction
    entry = executable_quote_reference(ten_second_row, direction)
    target = _select_target(
        target_levels,
        direction=direction,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
        consumed=consumed,
    )
    if target is None:
        return None, "NO_ACTIVE_OPPOSITE_EXTERNAL_TARGET"
    atr = float(minute.iloc[position]["atr"])
    stop = _structural_stop(
        direction=direction,
        entry=entry,
        sweep_extreme=pending.sweep_extreme,
        atr=atr,
        config=config,
    )
    reserve = float(stop_reserves.loc[timestamp])
    geometry = _cost_geometry(
        direction=direction,
        quote_reference=entry,
        stop=stop,
        target=float(target.level),
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=reserve,
    )
    if geometry is None:
        return None, "INVALID_COST_AFTER_GEOMETRY"
    expected_loss, expected_gain, net_reward_risk = geometry
    if net_reward_risk < minimum_net_reward_risk:
        return None, "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
    if pending.displacement_time_ns is None or pending.retest_time_ns is None:
        raise RuntimeError("signal emitted before state sequence completed")

    timestamp_ns = int(timestamp.as_unit("ns").value)
    events = tuple(
        pending.events
        + [
            QuoteResiliencyLogicEvent(
                scenario_id=pending.scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="FVG_RETRACE_REACCELERATION_CONFIRMED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="FVG_RETRACE_HELD",
                next_state="CONFIRMED",
                reason_code="SEPARATE_DIRECTIONAL_REACCELERATION_THROUGH_RETEST_EXTREME",
                reference_price=entry,
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "native_l1_entry_reference": entry,
                    "net_reward_risk": net_reward_risk,
                },
            )
        ]
    )
    signal = QuoteResiliencySignal(
        scenario_id=pending.scenario_id,
        scenario_family=SCENARIO_FAMILY,
        symbol=symbol,
        instrument_id=instrument_id,
        direction=direction,
        signal_index=position,
        signal_time_ns=timestamp_ns,
        boundary_id=pending.boundary.level_id,
        boundary_source=_source_name(pending.boundary),
        boundary_level=float(pending.boundary.level),
        target_id=target.level_id,
        target_source=_source_name(target),
        external_target=float(target.level),
        entry_reference=entry,
        structural_stop=stop,
        stop_reference=pending.sweep_extreme,
        stop_reference_source="EXTERNAL_SWEEP_EXTREME",
        atr=atr,
        causal_stop_slippage_reserve=reserve,
        expected_loss_per_unit=expected_loss,
        expected_gain_per_unit=expected_gain,
        net_reward_risk=net_reward_risk,
        interaction_time_ns=pending.sweep_time_ns,
        response_time_ns=pending.displacement_time_ns,
        retest_time_ns=pending.retest_time_ns,
        events=events,
        details={
            "scenario_family": SCENARIO_FAMILY,
            "signal_revision": SIGNAL_REVISION,
            "internal_break_level": pending.internal_break_level,
            "internal_break_time_ns": pending.internal_break_time_ns,
            "sweep_extreme": pending.sweep_extreme,
            "fvg_low": pending.fvg_low,
            "fvg_high": pending.fvg_high,
            "consequent_encroachment": (
                None
                if pending.fvg_low is None or pending.fvg_high is None
                else pending.fvg_low
                + config.retrace_fraction * (pending.fvg_high - pending.fvg_low)
            ),
            "displacement_position": pending.displacement_position,
            "retest_position": pending.retest_position,
            "reacceleration_position": position,
            "entry_mode": "NATIVE_L1_MARKET_AFTER_SEPARATE_REACCELERATION",
            "invalidation_contract": "SWEEP_EXTREME_PLUS_CAUSAL_ATR_BUFFER",
            "target_contract": "NEAREST_UNCONSUMED_OPPOSITE_COMPLETED_EXTERNAL_LIQUIDITY",
        },
    )
    return signal, "SIGNAL"


def build_external_sweep_fvg_signals(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    config: ExternalSweepFvgConfig,
) -> QuoteResiliencySignalBundle:
    """Build immutable, outcome-free signals from the completed causal state sequence."""

    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    minute = aggregate_completed_minutes(data, config)
    swing_highs, swing_lows = _confirmed_internal_swings(
        minute,
        span=config.internal_swing_span,
    )
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}
    consumed: set[str] = set()
    pending: _PendingSweep | None = None
    scenario_counter = 0

    def reject(reason: str, timestamp_ns: int, active: _PendingSweep) -> None:
        diagnostics[reason] += 1
        rejected.append(
            {
                "scenario_id": active.scenario_id,
                "symbol": symbol,
                "boundary_id": active.boundary.level_id,
                "scenario_family": SCENARIO_FAMILY,
                "reason": reason,
                "sweep_time_ns": active.sweep_time_ns,
                "rejected_time_ns": timestamp_ns,
                "state": active.state,
                "signal_revision": SIGNAL_REVISION,
            }
        )

    for position in range(1, len(minute.index)):
        row = minute.iloc[position]
        timestamp = minute.index[position]
        timestamp_ns = int(timestamp.as_unit("ns").value)
        numeric = (
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
            float(row["trade_count"]),
            float(row["imbalance"]),
            float(row["atr"]),
            float(row["volume_ratio"]),
            float(row["trade_ratio"]),
            float(row["close_location"]),
        )
        if not all(isfinite(value) for value in numeric) or float(row["atr"]) <= 0.0:
            diagnostics["UNOBSERVABLE_COMPLETED_MINUTE"] += 1
            continue
        context = _context_for_ten_second_close(
            timestamp_ns=timestamp_ns,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
        )
        if context is None:
            diagnostics["NO_COMPLETE_CAUSAL_EXTERNAL_CONTEXT"] += 1
            continue
        _five_bar, boundary_levels, target_levels = context
        atr = float(row["atr"])
        sweeps, crossed = _crossed_and_reclaimed(
            boundary_levels,
            previous_close=float(minute.iloc[position - 1]["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            tick=tick,
            config=config,
            consumed=consumed,
        )
        for level in crossed:
            consumed.add(level.level_id)
            diagnostics["EXTERNAL_LEVEL_FIRST_CROSS"] += 1

        handled_pending = pending is not None
        if pending is not None:
            direction = pending.direction
            # Before the displacement is confirmed, an extension that still reclaims the boundary
            # belongs to the same sweep.  Acceptance outside the boundary terminates the reversal.
            if pending.state == "WAIT_DISPLACEMENT":
                pending.sweep_extreme = (
                    min(pending.sweep_extreme, float(row["low"]))
                    if direction > 0
                    else max(pending.sweep_extreme, float(row["high"]))
                )
                accepted_outside = (
                    float(row["close"]) < pending.boundary.level - config.reclaim_atr * atr
                    if direction > 0
                    else float(row["close"]) > pending.boundary.level + config.reclaim_atr * atr
                )
                if accepted_outside:
                    reject("SWEEP_ACCEPTED_OUTSIDE_BEFORE_MSS", timestamp_ns, pending)
                    pending = None
                elif position > pending.displacement_expiry_position:
                    reject("NO_MSS_FVG_DISPLACEMENT_BEFORE_EXPIRY", timestamp_ns, pending)
                    pending = None
                else:
                    fvg = _displacement_fvg(minute, position, pending, config, tick)
                    if fvg is not None:
                        pending.displacement_position = position
                        pending.displacement_time_ns = timestamp_ns
                        pending.displacement_volume = float(row["volume"])
                        pending.displacement_trade_count = float(row["trade_count"])
                        pending.displacement_imbalance = float(row["imbalance"])
                        pending.fvg_low, pending.fvg_high = fvg
                        pending.retrace_expiry_position = (
                            position + config.maximum_retrace_minutes
                        )
                        pending.state = "WAIT_FVG_RETRACE"
                        pending.events.append(
                            QuoteResiliencyLogicEvent(
                                scenario_id=pending.scenario_id,
                                symbol=symbol,
                                instrument_id=instrument_id,
                                event_type="MSS_FVG_DISPLACEMENT_CONFIRMED",
                                event_time_ns=timestamp_ns,
                                observed_time_ns=timestamp_ns,
                                previous_state="EXTERNAL_LIQUIDITY_SWEPT",
                                next_state="MSS_FVG_DISPLACED",
                                reason_code="PRE_SWEEP_INTERNAL_SWING_BROKEN_WITH_DIRECTIONAL_FVG",
                                reference_price=float(row["close"]),
                                details={
                                    "scenario_family": SCENARIO_FAMILY,
                                    "internal_break_level": pending.internal_break_level,
                                    "fvg_low": pending.fvg_low,
                                    "fvg_high": pending.fvg_high,
                                },
                            )
                        )
                        diagnostics["MSS_FVG_DISPLACEMENT"] += 1
            elif pending.state == "WAIT_FVG_RETRACE":
                invalidated = (
                    float(row["low"]) <= pending.sweep_extreme - tick
                    if direction > 0
                    else float(row["high"]) >= pending.sweep_extreme + tick
                )
                if invalidated:
                    reject("SWEEP_EXTREME_INVALIDATED_BEFORE_RETRACE", timestamp_ns, pending)
                    pending = None
                elif (
                    pending.retrace_expiry_position is not None
                    and position > pending.retrace_expiry_position
                ):
                    reject("NO_CONTRACTED_FVG_RETRACE_BEFORE_EXPIRY", timestamp_ns, pending)
                    pending = None
                elif _retrace_holds(row, pending, config):
                    pending.retest_position = position
                    pending.retest_time_ns = timestamp_ns
                    pending.retest_high = float(row["high"])
                    pending.retest_low = float(row["low"])
                    pending.retest_volume = float(row["volume"])
                    pending.retest_trade_count = float(row["trade_count"])
                    pending.reacceleration_expiry_position = (
                        position + config.maximum_reacceleration_minutes
                    )
                    pending.state = "WAIT_REACCELERATION"
                    pending.events.append(
                        QuoteResiliencyLogicEvent(
                            scenario_id=pending.scenario_id,
                            symbol=symbol,
                            instrument_id=instrument_id,
                            event_type="FVG_RETRACE_HELD",
                            event_time_ns=timestamp_ns,
                            observed_time_ns=timestamp_ns,
                            previous_state="MSS_FVG_DISPLACED",
                            next_state="FVG_RETRACE_HELD",
                            reason_code="CONSEQUENT_ENCROACHMENT_TOUCHED_WITH_CONTRACTED_ACTIVITY",
                            reference_price=float(row["close"]),
                            details={
                                "scenario_family": SCENARIO_FAMILY,
                                "retest_volume": pending.retest_volume,
                                "retest_trade_count": pending.retest_trade_count,
                                "retest_imbalance": float(row["imbalance"]),
                            },
                        )
                    )
                    diagnostics["CONTRACTED_FVG_RETRACE_HELD"] += 1
            elif pending.state == "WAIT_REACCELERATION":
                invalidated = (
                    float(row["low"]) <= pending.sweep_extreme - tick
                    if direction > 0
                    else float(row["high"]) >= pending.sweep_extreme + tick
                )
                if invalidated:
                    reject("SWEEP_EXTREME_INVALIDATED_AFTER_RETRACE", timestamp_ns, pending)
                    pending = None
                elif (
                    pending.reacceleration_expiry_position is not None
                    and position > pending.reacceleration_expiry_position
                ):
                    reject("NO_SEPARATE_REACCELERATION_BEFORE_EXPIRY", timestamp_ns, pending)
                    pending = None
                elif _reaccelerates(row, pending, config, tick):
                    signal, reason = _signal_from_reacceleration(
                        data=data,
                        minute=minute,
                        position=position,
                        pending=pending,
                        target_levels=target_levels,
                        consumed=consumed,
                        stop_reserves=stop_reserves,
                        symbol=symbol,
                        instrument_id=instrument_id,
                        tick=tick,
                        fee_rate=fee_rate,
                        minimum_net_reward_risk=minimum_net_reward_risk,
                        config=config,
                    )
                    if signal is None:
                        reject(reason, timestamp_ns, pending)
                    else:
                        signals.setdefault(signal.signal_time_ns, []).append(signal)
                        diagnostics["REACCELERATION_SIGNAL"] += 1
                        diagnostics[f"SIGNAL_{signal.direction_name}"] += 1
                        diagnostics[f"SIGNAL_BOUNDARY_{signal.boundary_source}"] += 1
                        diagnostics[f"SIGNAL_TARGET_{signal.target_source}"] += 1
                    pending = None
            else:
                raise RuntimeError(f"unknown external-sweep state: {pending.state}")

        if handled_pending or pending is not None:
            continue
        selected = _select_sweep(sweeps)
        if selected is None:
            if sweeps:
                diagnostics["AMBIGUOUS_MULTI_DIRECTION_SWEEP"] += 1
            continue
        boundary, direction = selected
        internal = swing_highs[position] if direction > 0 else swing_lows[position]
        if internal is None:
            diagnostics["SWEEP_WITHOUT_CONFIRMED_INTERNAL_SWING"] += 1
            continue
        internal_level, internal_time_ns = internal
        if internal_time_ns >= timestamp_ns:
            raise RuntimeError("internal swing was not confirmed before sweep observation")
        scenario_counter += 1
        scenario_id = f"external-sweep-fvg-{symbol.lower()}-{scenario_counter:06d}"
        sweep_extreme = float(row["low"] if direction > 0 else row["high"])
        sweep_event = QuoteResiliencyLogicEvent(
            scenario_id=scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="EXTERNAL_LIQUIDITY_SWEEP_RECLAIMED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="IDLE",
            next_state="EXTERNAL_LIQUIDITY_SWEPT",
            reason_code="COMPLETED_EXTERNAL_LEVEL_FIRST_CROSS_AND_CLOSE_BACK_INSIDE",
            reference_price=float(boundary.level),
            details={
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "boundary_source": _source_name(boundary),
                "boundary_kind": str(getattr(boundary.kind, "value", boundary.kind)),
                "sweep_extreme": sweep_extreme,
                "internal_break_level": internal_level,
                "internal_break_time_ns": internal_time_ns,
            },
        )
        pending = _PendingSweep(
            scenario_id=scenario_id,
            boundary=boundary,
            direction=direction,
            sweep_position=position,
            sweep_time_ns=timestamp_ns,
            sweep_extreme=sweep_extreme,
            internal_break_level=float(internal_level),
            internal_break_time_ns=int(internal_time_ns),
            displacement_expiry_position=(
                position + config.maximum_displacement_minutes
            ),
            events=[sweep_event],
        )
        diagnostics["EXTERNAL_SWEEP_RECLAIMED"] += 1

    if pending is not None:
        reject("OPEN_DETECTOR_STATE_AT_DATA_END", int(minute.index[-1].as_unit("ns").value), pending)
    immutable = {
        timestamp_ns: tuple(
            sorted(
                items,
                key=lambda signal: (
                    signal.net_reward_risk,
                    SOURCE_RANK.get(signal.target_source, 0),
                ),
                reverse=True,
            )
        )
        for timestamp_ns, items in sorted(signals.items())
    }
    diagnostics["SIGNALS"] = sum(len(items) for items in immutable.values())
    diagnostics["SIGNAL_TIMES"] = len(immutable)
    diagnostics["COMPLETED_MINUTES"] = len(minute.index)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted((key, int(value)) for key, value in diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "ExternalSweepFvgConfig",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "aggregate_completed_minutes",
    "build_external_sweep_fvg_signals",
]
