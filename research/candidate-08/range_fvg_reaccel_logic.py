"""Causal post-FVG retrace and reacceleration confirmation.

The completed-range detector remains unchanged. This module converts its observable FVG signals
into tradeable signals only after price reprices into consequent encroachment with lower activity
than the original displacement and then prints a separate directional five-minute displacement.
It performs no order, fill, account, or position simulation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

import pandas as pd

from range_fvg_logic import (
    Direction,
    FiveMinuteBar,
    LogicEvent,
    RangeFVGConfig,
    RangeFVGSignal,
    SignalBundle,
    build_range_fvg_signals,
)


@dataclass(frozen=True, slots=True)
class RetestReaccelConfig:
    maximum_retest_bars: int = 6
    maximum_reacceleration_bars: int = 3
    reacceleration_break_atr: float = 0.03
    minimum_reacceleration_body_atr: float = 0.15
    minimum_reacceleration_imbalance: float = 0.05
    reacceleration_close_location: float = 0.62
    stop_buffer_atr: float = 0.05
    minimum_stop_atr: float = 0.25

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RetestReaccelConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})


@dataclass(frozen=True, slots=True)
class ReaccelSignalBundle:
    five_minute_bars: tuple[FiveMinuteBar, ...]
    signals_by_time_ns: dict[int, tuple[RangeFVGSignal, ...]]
    diagnostics: dict[str, Any]


def _direction_value(signal: RangeFVGSignal) -> int:
    return 1 if signal.direction is Direction.LONG else -1


def _target_hit(signal: RangeFVGSignal, bar: FiveMinuteBar) -> bool:
    return (
        bar.high >= signal.external_target
        if signal.direction is Direction.LONG
        else bar.low <= signal.external_target
    )


def _invalidated(signal: RangeFVGSignal, bar: FiveMinuteBar) -> bool:
    return (
        bar.low <= signal.invalidation_before_fill
        if signal.direction is Direction.LONG
        else bar.high >= signal.invalidation_before_fill
    )


def _retest_holds(
    signal: RangeFVGSignal,
    displacement: FiveMinuteBar,
    bar: FiveMinuteBar,
) -> bool:
    touched = (
        bar.low <= signal.limit_entry
        if signal.direction is Direction.LONG
        else bar.high >= signal.limit_entry
    )
    fvg_held = (
        bar.close >= signal.fvg_low
        if signal.direction is Direction.LONG
        else bar.close <= signal.fvg_high
    )
    contracted = (
        bar.volume < displacement.volume
        and bar.trade_count < displacement.trade_count
        and abs(bar.imbalance) < abs(displacement.imbalance)
    )
    return touched and fvg_held and contracted


def _reaccelerates(
    signal: RangeFVGSignal,
    retest: FiveMinuteBar,
    bar: FiveMinuteBar,
    config: RetestReaccelConfig,
) -> bool:
    direction = _direction_value(signal)
    directional_body = direction * (bar.close - bar.open)
    directional_flow = direction * bar.imbalance
    if direction > 0:
        displaced = bar.close >= retest.high + config.reacceleration_break_atr * bar.atr
        located = bar.close_location >= config.reacceleration_close_location
    else:
        displaced = bar.close <= retest.low - config.reacceleration_break_atr * bar.atr
        located = bar.close_location <= 1.0 - config.reacceleration_close_location
    return (
        displaced
        and located
        and directional_body >= config.minimum_reacceleration_body_atr * bar.atr
        and directional_flow >= config.minimum_reacceleration_imbalance
        and bar.volume >= retest.volume
        and bar.trade_count >= retest.trade_count
    )


def _confirmed_signal(
    base: RangeFVGSignal,
    displacement: FiveMinuteBar,
    retest: FiveMinuteBar,
    reacceleration: FiveMinuteBar,
    config: RetestReaccelConfig,
) -> RangeFVGSignal | None:
    direction = _direction_value(base)
    entry = reacceleration.close
    minimum_distance = config.minimum_stop_atr * reacceleration.atr
    if direction > 0:
        structural = retest.low - config.stop_buffer_atr * reacceleration.atr
        stop = min(structural, entry - minimum_distance)
        valid = stop < entry < base.external_target
    else:
        structural = retest.high + config.stop_buffer_atr * reacceleration.atr
        stop = max(structural, entry + minimum_distance)
        valid = base.external_target < entry < stop
    if not valid:
        return None

    scenario_id = f"{base.scenario_id}-rr"
    events = tuple(base.events) + (
        LogicEvent(
            scenario_id=scenario_id,
            event_type="FVG_RETEST_HELD",
            event_time_ns=retest.ts_event_ns,
            observed_time_ns=retest.ts_event_ns,
            previous_state="CONFIRMED",
            next_state="RETEST_HELD",
            reason_code=f"CONTRACTED_CONSEQUENT_ENCROACHMENT_{base.direction.value}",
            reference_price=base.limit_entry,
            details={
                "base_scenario_id": base.scenario_id,
                "retest_index": retest.index,
                "retest_low": retest.low,
                "retest_high": retest.high,
                "volume_fraction_of_displacement": retest.volume / max(displacement.volume, 1e-12),
                "trade_fraction_of_displacement": retest.trade_count / max(displacement.trade_count, 1e-12),
                "retest_imbalance": retest.imbalance,
            },
        ),
        LogicEvent(
            scenario_id=scenario_id,
            event_type="POST_RETEST_REACCELERATION",
            event_time_ns=reacceleration.ts_event_ns,
            observed_time_ns=reacceleration.ts_event_ns,
            previous_state="RETEST_HELD",
            next_state="CONFIRMED",
            reason_code=f"SEPARATE_FIVE_MINUTE_DISPLACEMENT_{base.direction.value}",
            reference_price=entry,
            details={
                "reacceleration_index": reacceleration.index,
                "body_atr": reacceleration.body / reacceleration.atr,
                "imbalance": reacceleration.imbalance,
                "volume_ratio": reacceleration.volume_ratio,
                "trade_ratio": reacceleration.trade_ratio,
            },
        ),
    )
    return RangeFVGSignal(
        scenario_id=scenario_id,
        family=base.family,
        direction=base.direction,
        signal_index=reacceleration.index,
        signal_time_ns=reacceleration.ts_event_ns,
        boundary_id=base.boundary_id,
        boundary_source=base.boundary_source,
        boundary_level=base.boundary_level,
        fvg_low=base.fvg_low,
        fvg_high=base.fvg_high,
        limit_entry=entry,
        structural_stop=stop,
        external_target_id=base.external_target_id,
        external_target_source=base.external_target_source,
        external_target=base.external_target,
        atr=reacceleration.atr,
        invalidation_before_fill=stop,
        events=events,
        details={
            **base.details,
            "base_scenario_id": base.scenario_id,
            "base_signal_index": base.signal_index,
            "displacement_index": displacement.index,
            "retest_index": retest.index,
            "reacceleration_index": reacceleration.index,
            "retest_delay_bars": retest.index - base.signal_index,
            "total_confirmation_delay_bars": reacceleration.index - base.signal_index,
            "entry_mode": "MARKET_AFTER_REACCELERATION",
        },
    )


def build_range_fvg_reacceleration_signals(
    one_minute_frame: pd.DataFrame,
    base_config: RangeFVGConfig,
    confirmation_config: RetestReaccelConfig,
) -> ReaccelSignalBundle:
    base_bundle: SignalBundle = build_range_fvg_signals(one_minute_frame, base_config)
    bars = base_bundle.five_minute_bars
    position_by_index = {bar.index: position for position, bar in enumerate(bars)}
    bar_by_index = {bar.index: bar for bar in bars}
    diagnostics: Counter[str] = Counter()
    signals_by_time: dict[int, list[RangeFVGSignal]] = {}

    base_signals = [
        signal
        for items in base_bundle.signals_by_time_ns.values()
        for signal in items
    ]
    diagnostics["BASE_SIGNALS"] = len(base_signals)
    for base in sorted(base_signals, key=lambda item: item.signal_time_ns):
        start_position = position_by_index.get(base.signal_index)
        displacement_index = int(base.details.get("displacement_index", base.signal_index - 1))
        displacement = bar_by_index.get(displacement_index)
        if start_position is None or displacement is None:
            diagnostics["MISSING_CAUSAL_BAR_REFERENCE"] += 1
            continue

        retest_position: int | None = None
        retest_bar: FiveMinuteBar | None = None
        terminal_reason: str | None = None
        retest_end = min(len(bars), start_position + 1 + confirmation_config.maximum_retest_bars)
        for position in range(start_position + 1, retest_end):
            bar = bars[position]
            if _target_hit(base, bar):
                terminal_reason = "TARGET_REACHED_BEFORE_RETEST"
                break
            if _invalidated(base, bar):
                terminal_reason = "STRUCTURE_INVALIDATED_BEFORE_RETEST"
                break
            if _retest_holds(base, displacement, bar):
                retest_position = position
                retest_bar = bar
                diagnostics["CONTRACTED_RETEST_HELD"] += 1
                break
        if retest_position is None or retest_bar is None:
            diagnostics[terminal_reason or "NO_CONTRACTED_RETEST"] += 1
            continue

        confirmed: RangeFVGSignal | None = None
        reacceleration_end = min(
            len(bars),
            retest_position + 1 + confirmation_config.maximum_reacceleration_bars,
        )
        for position in range(retest_position + 1, reacceleration_end):
            bar = bars[position]
            if _target_hit(base, bar):
                terminal_reason = "TARGET_REACHED_BEFORE_REACCELERATION"
                break
            if _invalidated(base, bar):
                terminal_reason = "STRUCTURE_INVALIDATED_AFTER_RETEST"
                break
            if not _reaccelerates(base, retest_bar, bar, confirmation_config):
                continue
            confirmed = _confirmed_signal(
                base,
                displacement,
                retest_bar,
                bar,
                confirmation_config,
            )
            if confirmed is None:
                terminal_reason = "INVALID_REACCELERATION_GEOMETRY"
            else:
                signals_by_time.setdefault(confirmed.signal_time_ns, []).append(confirmed)
                diagnostics["REACCELERATION_SIGNAL"] += 1
            break
        if confirmed is None:
            diagnostics[terminal_reason or "NO_SEPARATE_REACCELERATION"] += 1

    immutable = {timestamp: tuple(items) for timestamp, items in signals_by_time.items()}
    diagnostics_payload = {
        "base_detector": base_bundle.diagnostics,
        "confirmation_counts": dict(sorted(diagnostics.items())),
        "signals": sum(len(items) for items in immutable.values()),
        "signal_times": len(immutable),
    }
    return ReaccelSignalBundle(
        five_minute_bars=bars,
        signals_by_time_ns=immutable,
        diagnostics=diagnostics_payload,
    )


def group_events_by_reason(events: Iterable[LogicEvent]) -> dict[str, int]:
    counts: Counter[str] = Counter(event.reason_code for event in events)
    return dict(sorted(counts.items()))
