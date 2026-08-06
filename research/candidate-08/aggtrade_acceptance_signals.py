"""Causal ten-second breakout-acceptance signal detector for candidate-08.

This module is deliberately limited to market-state detection. It consumes completed ten-second
aggregate-trade bars and already-completed 4-hour/day/week external-liquidity levels, then emits a
tradeable signal only after this observable sequence:

1. aggressive flow accepts beyond a completed external boundary;
2. a lower-energy ten-second retest touches and holds that boundary;
3. a separate same-direction aggressive-flow bar breaks the retest extreme.

It performs no order, fill, account, position, or outcome simulation. NautilusTrader owns those
responsibilities. Structural invalidation is the observed retest extreme, not the breakout extreme.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, SOURCE_RANK


@dataclass(frozen=True, slots=True)
class AcceptanceLogicEvent:
    scenario_id: str
    symbol: str
    instrument_id: str
    event_type: str
    event_time_ns: int
    observed_time_ns: int
    previous_state: str
    next_state: str
    reason_code: str
    reference_price: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AcceptanceSignal:
    scenario_id: str
    symbol: str
    instrument_id: str
    direction: int
    signal_index: int
    signal_time_ns: int
    boundary_id: str
    boundary_source: str
    boundary_level: float
    target_id: str
    target_source: str
    external_target: float
    entry_reference: float
    structural_stop: float
    atr: float
    causal_stop_slippage_reserve: float
    expected_loss_per_unit: float
    expected_gain_per_unit: float
    net_reward_risk: float
    armed_time_ns: int
    retest_time_ns: int
    retest_high: float
    retest_low: float
    events: tuple[AcceptanceLogicEvent, ...]
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_name(self) -> str:
        return "LONG" if self.direction > 0 else "SHORT"


@dataclass(frozen=True, slots=True)
class AcceptanceSignalBundle:
    signals_by_time_ns: dict[int, tuple[AcceptanceSignal, ...]]
    diagnostics: dict[str, int]
    rejected_scenarios: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class _PendingAcceptance:
    scenario_id: str
    boundary: ExternalLevel
    outward: int
    armed_position: int
    armed_time_ns: int
    expiry_time_ns: int
    displacement_volume: float
    displacement_trade_count: float
    displacement_imbalance: float
    displacement_high: float
    displacement_low: float
    retest_position: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retest_volume: float | None = None
    retest_trade_count: float | None = None


_REQUIRED_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "imbalance",
    "volume_ratio",
    "trade_ratio",
    "close_location",
)


def _row_is_observable(row: pd.Series) -> bool:
    return all(isfinite(float(row[name])) for name in _REQUIRED_COLUMNS)


def _acceptance_interaction(
    row: pd.Series,
    *,
    boundary_level: float,
    outward: int,
    atr: float,
) -> bool:
    outward_flow = outward * float(row["imbalance"])
    outward_body = outward * float(row["close"] - row["open"])
    high_activity = float(row["volume_ratio"]) >= 1.50 and float(row["trade_ratio"]) >= 1.20
    accepted = (
        float(row["close"]) >= boundary_level + 0.05 * atr
        if outward > 0
        else float(row["close"]) <= boundary_level - 0.05 * atr
    )
    located = (
        float(row["close_location"]) >= 0.65
        if outward > 0
        else float(row["close_location"]) <= 0.35
    )
    return (
        accepted
        and located
        and high_activity
        and outward_flow >= 0.20
        and outward_body >= 0.08 * atr
    )


def _acceptance_retest_holds(
    row: pd.Series,
    *,
    boundary_level: float,
    outward: int,
    atr: float,
    displacement_volume: float,
    displacement_trade_count: float,
    displacement_imbalance: float,
    require_contraction: bool = True,
) -> bool:
    touched = (
        float(row["low"]) <= boundary_level + 0.05 * atr
        if outward > 0
        else float(row["high"]) >= boundary_level - 0.05 * atr
    )
    held = (
        float(row["close"]) >= boundary_level
        if outward > 0
        else float(row["close"]) <= boundary_level
    )
    contracted = (
        float(row["volume"]) <= 0.80 * displacement_volume
        and float(row["trade_count"]) <= 0.90 * displacement_trade_count
        and abs(float(row["imbalance"])) < abs(displacement_imbalance)
    )
    return touched and held and (contracted or not require_contraction)


def _acceptance_reaccelerates(
    row: pd.Series,
    *,
    outward: int,
    atr: float,
    retest_high: float,
    retest_low: float,
    retest_volume: float,
    retest_trade_count: float,
) -> bool:
    break_structure = (
        float(row["close"]) > retest_high + 0.01 * atr
        if outward > 0
        else float(row["close"]) < retest_low - 0.01 * atr
    )
    located = (
        float(row["close_location"]) >= 0.65
        if outward > 0
        else float(row["close_location"]) <= 0.35
    )
    directional_body = outward * float(row["close"] - row["open"])
    directional_flow = outward * float(row["imbalance"])
    return (
        break_structure
        and located
        and directional_body >= 0.05 * atr
        and directional_flow >= 0.10
        and float(row["volume"]) >= retest_volume
        and float(row["trade_count"]) >= retest_trade_count
    )


def _crossed_levels(
    levels: tuple[ExternalLevel, ...],
    *,
    previous_close: float,
    high: float,
    low: float,
    atr: float,
    consumed: set[str],
) -> tuple[list[ExternalLevel], list[ExternalLevel]]:
    """Return all newly crossed highs and lows, so every interaction is consumed causally."""

    highs = [
        level
        for level in levels
        if level.level_id not in consumed
        and level.kind is LevelKind.HIGH
        and previous_close <= level.level
        and high >= level.level + 0.02 * atr
    ]
    lows = [
        level
        for level in levels
        if level.level_id not in consumed
        and level.kind is LevelKind.LOW
        and previous_close >= level.level
        and low <= level.level - 0.02 * atr
    ]
    return highs, lows


def _select_interaction_boundary(
    highs: list[ExternalLevel],
    lows: list[ExternalLevel],
) -> tuple[ExternalLevel, int] | None:
    if highs and lows:
        return None
    if highs:
        return max(highs, key=lambda level: (SOURCE_RANK[level.source], level.level)), 1
    if lows:
        return max(lows, key=lambda level: (SOURCE_RANK[level.source], -level.level)), -1
    return None


def _select_active_target(
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
            and level.kind is LevelKind.HIGH
            and level.level > entry
        ]
    else:
        candidates = [
            level
            for level in levels
            if level.level_id != excluded_level_id
            and level.level_id not in consumed
            and level.kind is LevelKind.LOW
            and level.level < entry
        ]
    if not candidates:
        return None
    return min(candidates, key=lambda level: abs(level.level - entry))


def _structural_stop(
    *,
    direction: int,
    entry: float,
    retest_high: float,
    retest_low: float,
    atr: float,
) -> tuple[float, float, str]:
    """Use the observed retest extreme as the scenario invalidation reference."""

    minimum_distance = 0.10 * atr
    buffer = 0.03 * atr
    if direction > 0:
        reference = retest_low
        return min(reference - buffer, entry - minimum_distance), reference, "ACCEPTANCE_RETEST_LOW"
    reference = retest_high
    return max(reference + buffer, entry + minimum_distance), reference, "ACCEPTANCE_RETEST_HIGH"


def _cost_geometry(
    *,
    direction: int,
    entry: float,
    stop: float,
    target: float,
    fee_rate: float,
    tick: float,
    stop_slippage_reserve: float | None = None,
) -> tuple[float, float, float] | None:
    valid = stop < entry < target if direction > 0 else target < entry < stop
    if not valid:
        return None
    stop_reserve = max(tick, float(stop_slippage_reserve or tick))
    # Entry is reserved by one adverse tick. Stop-market slippage uses a causal 99th-percentile
    # ten-second true-range estimate from only already-completed bars. This is execution
    # infrastructure, not a signal score or alpha parameter.
    loss = abs(entry - stop) + fee_rate * (entry + stop) + tick + stop_reserve
    gross_gain = target - entry if direction > 0 else entry - target
    gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
    if loss <= 0 or gain <= 0:
        return None
    return loss, gain, gain / loss


def _context_for_ten_second_close(
    *,
    timestamp_ns: int,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
) -> tuple[FiveMinuteBar, tuple[ExternalLevel, ...], tuple[ExternalLevel, ...]] | None:
    """Return causal context plus boundary-before and target-after level snapshots.

    A ten-second bucket can close at exactly the same timestamp as a five-minute bucket.
    In that case the interaction boundary must come from the snapshot *before* that five-minute
    bar consumed it, while targets may use the state observable immediately after the completed
    five-minute bar. For ten-second closes between five-minute closes, both views are the state
    after the latest completed five-minute bar.
    """

    if len(context_times) != len(context_bars) or len(snapshots) != len(context_bars):
        raise ValueError("five-minute context arrays must have equal lengths")
    if len(context_times) == 0:
        return None
    context_position = int(np.searchsorted(context_times, timestamp_ns, side="right") - 1)
    if context_position < 0:
        return None
    context_close_ns = int(context_times[context_position])
    # Binance one-minute klines are stamped at the final millisecond or microsecond of the
    # interval (for example 00:04:59.999), whereas a ten-second aggTrade bucket covering the
    # same final slice is stamped at the exact bucket end (00:05:00). Treat only that immediate
    # UTC five-minute boundary as the same completed interval. Later ten-second closes use the
    # postbar state.
    five_minutes_ns = 5 * 60 * 1_000_000_000
    same_completed_five_minute_interval = (
        timestamp_ns % five_minutes_ns == 0
        and 0 <= timestamp_ns - context_close_ns < 1_000_000_000
    )
    if same_completed_five_minute_interval:
        before_position = context_position
        after_position = context_position + 1
    else:
        before_position = context_position + 1
        after_position = before_position
    if after_position >= len(snapshots):
        return None
    return (
        context_bars[context_position],
        snapshots[before_position],
        snapshots[after_position],
    )


def causal_stop_slippage_reserve_series(
    data: pd.DataFrame,
    *,
    tick: float,
    lookback_bars: int = 360,
    quantile: float = 0.99,
) -> pd.Series:
    """Return a shifted, causal stop-market slippage reserve for every ten-second close.

    The reserve is the 99th percentile of completed ten-second true range over the preceding
    hour. It is deliberately independent of direction, signal outcome, and target geometry.
    A minimum of six minutes of history is required; until then one tick is used.
    """

    if tick <= 0 or lookback_bars <= 0 or not 0.5 <= quantile < 1.0:
        raise ValueError("invalid stop-slippage reserve contract")
    high = pd.to_numeric(data["high"], errors="coerce")
    low = pd.to_numeric(data["low"], errors="coerce")
    close = pd.to_numeric(data["close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    reserve = true_range.shift(1).rolling(
        lookback_bars,
        min_periods=min(36, lookback_bars),
    ).quantile(quantile)
    return reserve.fillna(float(tick)).clip(lower=float(tick))


def build_acceptance_signals(
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
    require_retest_contraction: bool = True,
) -> AcceptanceSignalBundle:
    """Build immutable, future-free acceptance signals from observable completed data."""

    if tick <= 0 or fee_rate < 0 or minimum_net_reward_risk <= 0:
        raise ValueError("invalid cost or reward-risk contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")

    stop_slippage_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    signals: dict[int, list[AcceptanceSignal]] = {}
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    consumed: set[str] = set()
    pending: _PendingAcceptance | None = None
    scenario_counter = 0

    for position in range(1, len(data.index)):
        row = data.iloc[position]
        if not _row_is_observable(row):
            diagnostics["UNOBSERVABLE_TEN_SECOND_BAR"] += 1
            continue
        timestamp = data.index[position]
        timestamp_ns = int(timestamp.as_unit("ns").value)
        context = _context_for_ten_second_close(
            timestamp_ns=timestamp_ns,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
        )
        if context is None:
            diagnostics["NO_COMPLETE_CAUSAL_CONTEXT_SNAPSHOT"] += 1
            continue
        five_bar, boundary_levels, target_levels = context
        atr = float(five_bar.atr)

        # Consume every completed level at its first observable ten-second crossing, whether or not
        # that interaction later qualifies as acceptance. This prevents stale targets/boundaries.
        highs, lows = _crossed_levels(
            boundary_levels,
            previous_close=float(data.iloc[position - 1]["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            atr=atr,
            consumed=consumed,
        )
        for level in (*highs, *lows):
            consumed.add(level.level_id)

        handled_pending = pending is not None
        if pending is not None:
            outward = pending.outward
            if timestamp_ns > pending.expiry_time_ns:
                diagnostics["ACCEPTANCE_SEQUENCE_TIMEOUT"] += 1
                rejected.append(
                    {
                        "scenario_id": pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": pending.boundary.level_id,
                        "reason": "ACCEPTANCE_SEQUENCE_TIMEOUT",
                        "armed_time_ns": pending.armed_time_ns,
                    }
                )
                pending = None
            else:
                reclaimed = (
                    float(row["close"]) < pending.boundary.level - 0.02 * atr
                    if outward > 0
                    else float(row["close"]) > pending.boundary.level + 0.02 * atr
                )
                if reclaimed:
                    diagnostics["ACCEPTANCE_RECLAIMED"] += 1
                    rejected.append(
                        {
                            "scenario_id": pending.scenario_id,
                            "symbol": symbol,
                            "boundary_id": pending.boundary.level_id,
                            "reason": "ACCEPTANCE_RECLAIMED",
                            "armed_time_ns": pending.armed_time_ns,
                            "rejected_time_ns": timestamp_ns,
                        }
                    )
                    pending = None
                elif pending.retest_position is None:
                    if _acceptance_retest_holds(
                        row,
                        boundary_level=pending.boundary.level,
                        outward=outward,
                        atr=atr,
                        displacement_volume=pending.displacement_volume,
                        displacement_trade_count=pending.displacement_trade_count,
                        displacement_imbalance=pending.displacement_imbalance,
                        require_contraction=require_retest_contraction,
                    ):
                        pending.retest_position = position
                        pending.retest_time_ns = timestamp_ns
                        pending.retest_high = float(row["high"])
                        pending.retest_low = float(row["low"])
                        pending.retest_volume = float(row["volume"])
                        pending.retest_trade_count = float(row["trade_count"])
                        pending.expiry_time_ns = timestamp_ns + 30 * 1_000_000_000
                        diagnostics["ACCEPTANCE_RETEST_HELD"] += 1
                else:
                    assert pending.retest_time_ns is not None
                    assert pending.retest_high is not None
                    assert pending.retest_low is not None
                    assert pending.retest_volume is not None
                    assert pending.retest_trade_count is not None
                    if _acceptance_reaccelerates(
                        row,
                        outward=outward,
                        atr=atr,
                        retest_high=pending.retest_high,
                        retest_low=pending.retest_low,
                        retest_volume=pending.retest_volume,
                        retest_trade_count=pending.retest_trade_count,
                    ):
                        entry = float(row["close"])
                        stop, stop_reference, stop_reference_source = _structural_stop(
                            direction=outward,
                            entry=entry,
                            retest_high=pending.retest_high,
                            retest_low=pending.retest_low,
                            atr=atr,
                        )
                        target_level = _select_active_target(
                            target_levels,
                            direction=outward,
                            entry=entry,
                            excluded_level_id=pending.boundary.level_id,
                            consumed=consumed,
                        )
                        if target_level is None:
                            reason = "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET"
                            diagnostics[reason] += 1
                            rejected.append(
                                {
                                    "scenario_id": pending.scenario_id,
                                    "symbol": symbol,
                                    "boundary_id": pending.boundary.level_id,
                                    "reason": reason,
                                    "confirmation_time_ns": timestamp_ns,
                                }
                            )
                        else:
                            geometry = _cost_geometry(
                                direction=outward,
                                entry=entry,
                                stop=stop,
                                target=target_level.level,
                                fee_rate=fee_rate,
                                tick=tick,
                                stop_slippage_reserve=float(stop_slippage_reserves.iloc[position]),
                            )
                            if geometry is None:
                                reason = "INVALID_COST_AFTER_EXTERNAL_GEOMETRY"
                                diagnostics[reason] += 1
                                rejected.append(
                                    {
                                        "scenario_id": pending.scenario_id,
                                        "symbol": symbol,
                                        "boundary_id": pending.boundary.level_id,
                                        "target_id": target_level.level_id,
                                        "reason": reason,
                                        "confirmation_time_ns": timestamp_ns,
                                    }
                                )
                            else:
                                loss, gain, net_rr = geometry
                                if net_rr < minimum_net_reward_risk:
                                    reason = "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
                                    diagnostics[reason] += 1
                                    rejected.append(
                                        {
                                            "scenario_id": pending.scenario_id,
                                            "symbol": symbol,
                                            "boundary_id": pending.boundary.level_id,
                                            "target_id": target_level.level_id,
                                            "reason": reason,
                                            "confirmation_time_ns": timestamp_ns,
                                            "net_reward_risk": net_rr,
                                        }
                                    )
                                else:
                                    direction_name = "LONG" if outward > 0 else "SHORT"
                                    events = (
                                        AcceptanceLogicEvent(
                                            scenario_id=pending.scenario_id,
                                            symbol=symbol,
                                            instrument_id=instrument_id,
                                            event_type="EXTERNAL_LEVEL_ACCEPTED",
                                            event_time_ns=pending.armed_time_ns,
                                            observed_time_ns=pending.armed_time_ns,
                                            previous_state="IDLE",
                                            next_state="ACCEPTED",
                                            reason_code=f"AGGRESSIVE_FLOW_ACCEPTANCE_{direction_name}",
                                            reference_price=pending.boundary.level,
                                            details={
                                                "boundary_id": pending.boundary.level_id,
                                                "boundary_source": pending.boundary.source.value,
                                                "displacement_imbalance": pending.displacement_imbalance,
                                            },
                                        ),
                                        AcceptanceLogicEvent(
                                            scenario_id=pending.scenario_id,
                                            symbol=symbol,
                                            instrument_id=instrument_id,
                                            event_type="ACCEPTANCE_RETEST_HELD",
                                            event_time_ns=pending.retest_time_ns,
                                            observed_time_ns=pending.retest_time_ns,
                                            previous_state="ACCEPTED",
                                            next_state="RETEST_HELD",
                                            reason_code="CONTRACTED_TEN_SECOND_RETEST",
                                            reference_price=pending.boundary.level,
                                            details={
                                                "retest_high": pending.retest_high,
                                                "retest_low": pending.retest_low,
                                                "retest_volume_fraction": pending.retest_volume
                                                / max(pending.displacement_volume, 1e-12),
                                                "retest_trade_fraction": pending.retest_trade_count
                                                / max(pending.displacement_trade_count, 1e-12),
                                            },
                                        ),
                                        AcceptanceLogicEvent(
                                            scenario_id=pending.scenario_id,
                                            symbol=symbol,
                                            instrument_id=instrument_id,
                                            event_type="ACCEPTANCE_REACCELERATION_CONFIRMED",
                                            event_time_ns=timestamp_ns,
                                            observed_time_ns=timestamp_ns,
                                            previous_state="RETEST_HELD",
                                            next_state="CONFIRMED",
                                            reason_code=f"SEPARATE_AGGRESSIVE_FLOW_REACCELERATION_{direction_name}",
                                            reference_price=entry,
                                            details={
                                                "confirmation_imbalance": float(row["imbalance"]),
                                                "confirmation_volume_ratio": float(row["volume_ratio"]),
                                                "confirmation_trade_ratio": float(row["trade_ratio"]),
                                            },
                                        ),
                                    )
                                    signal = AcceptanceSignal(
                                        scenario_id=pending.scenario_id,
                                        symbol=symbol,
                                        instrument_id=instrument_id,
                                        direction=outward,
                                        signal_index=position,
                                        signal_time_ns=timestamp_ns,
                                        boundary_id=pending.boundary.level_id,
                                        boundary_source=pending.boundary.source.value,
                                        boundary_level=pending.boundary.level,
                                        target_id=target_level.level_id,
                                        target_source=target_level.source.value,
                                        external_target=target_level.level,
                                        entry_reference=entry,
                                        structural_stop=stop,
                                        atr=atr,
                                        causal_stop_slippage_reserve=float(
                                            stop_slippage_reserves.iloc[position]
                                        ),
                                        expected_loss_per_unit=loss,
                                        expected_gain_per_unit=gain,
                                        net_reward_risk=net_rr,
                                        armed_time_ns=pending.armed_time_ns,
                                        retest_time_ns=pending.retest_time_ns,
                                        retest_high=pending.retest_high,
                                        retest_low=pending.retest_low,
                                        events=events,
                                        details={
                                            "stop_reference": stop_reference,
                                            "stop_reference_source": stop_reference_source,
                                            "armed_position": pending.armed_position,
                                            "retest_position": pending.retest_position,
                                            "confirmation_position": position,
                                            "confirmation_close_location": float(row["close_location"]),
                                            "causal_stop_slippage_reserve": float(
                                                stop_slippage_reserves.iloc[position]
                                            ),
                                            "slippage_reserve_contract": (
                                                "SHIFTED_60M_TEN_SECOND_TRUE_RANGE_Q99"
                                            ),
                                        },
                                    )
                                    signals.setdefault(timestamp_ns, []).append(signal)
                                    diagnostics["TRADEABLE_ACCEPTANCE_SIGNAL"] += 1
                        pending = None
            if handled_pending:
                continue

        interaction = _select_interaction_boundary(highs, lows)
        if interaction is None:
            if highs and lows:
                diagnostics["BILATERAL_COMPLETED_LEVEL_INTERACTION"] += 1
            continue
        boundary, outward = interaction
        if not _acceptance_interaction(
            row,
            boundary_level=boundary.level,
            outward=outward,
            atr=atr,
        ):
            diagnostics["NON_ACCEPTANCE_INTERACTION_CONSUMED"] += 1
            continue

        scenario_counter += 1
        scenario_id = f"agg-acceptance-{symbol.lower()}-{scenario_counter:06d}"
        pending = _PendingAcceptance(
            scenario_id=scenario_id,
            boundary=boundary,
            outward=outward,
            armed_position=position,
            armed_time_ns=timestamp_ns,
            expiry_time_ns=timestamp_ns + 60 * 1_000_000_000,
            displacement_volume=float(row["volume"]),
            displacement_trade_count=float(row["trade_count"]),
            displacement_imbalance=float(row["imbalance"]),
            displacement_high=float(row["high"]),
            displacement_low=float(row["low"]),
        )
        diagnostics["ACCEPTANCE_ARMED"] += 1

    # Per-asset builders normally emit one signal at a timestamp. Keep a deterministic simple sort
    # without looking up mutable snapshots; global ranking is recalculated after venue rounding.
    immutable = {
        timestamp: tuple(sorted(items, key=lambda signal: (signal.net_reward_risk, signal.symbol), reverse=True))
        for timestamp, items in signals.items()
    }
    return AcceptanceSignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )
