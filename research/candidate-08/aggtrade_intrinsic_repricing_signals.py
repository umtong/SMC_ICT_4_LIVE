"""Causal intrinsic-time repricing continuation after completed external liquidity.

The discarded V3 entered at the end of the first thirty-second initiative response.  Its clean first
week showed two distinct failures: several responses reversed almost immediately, while two shorts
were stopped before the original external target was eventually reached.  This successor does not
widen the stop, shorten the target, or fit a new numeric threshold.  It changes the scenario order:

1. consume the first interaction with an already-completed 4-hour/day/week external level;
2. measure an outward initiative response over one causally frozen aggressive-activity budget;
3. require either a second same-direction intrinsic response, or an intrinsic counter-flow reprice
   which holds the boundary followed by a separate same-direction initiative response; and
4. enter only after that completed persistence or reprice-resumption event.

Physical duration is variable, but every underlying bucket remains exactly ten seconds.  The
activity budget, noise reserve, and impact response are frozen from prior completed observations.
No future outcome, order, fill, account value, PnL, model score, fixed-R target, or asset-specific
parameter enters detection.  NautilusTrader remains the sole execution and account engine.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    AcceptanceLogicEvent,
    AcceptanceSignal,
    AcceptanceSignalBundle,
    _context_for_ten_second_close,
    _cost_geometry,
    _crossed_levels,
    _select_active_target,
    _select_interaction_boundary,
)
from aggtrade_flow_response import FlowResponseConfig, FlowResponseState, causal_flow_response_frame
from aggtrade_flow_response_auction_signals_v3 import validate_exact_ten_second_cadence
from aggtrade_intrinsic_response_clock import (
    IntrinsicEventStatus,
    IntrinsicResponseClockConfig,
    IntrinsicResponseEvent,
    build_intrinsic_response_event,
    causal_activity_budget_series,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


INTRINSIC_REPRICING_FAMILY = "INTRINSIC_REPRICING_CONTINUATION"
DIRECT_PERSISTENCE_PATH = "DIRECT_INTRINSIC_PERSISTENCE"
REPRICE_RESUMPTION_PATH = "COUNTERFLOW_REPRICE_AND_RESUMPTION"
IMPLEMENTATION_REVISION = "CAUSAL_INTRINSIC_REPRICING_CONTINUATION_V1"


@dataclass(frozen=True, slots=True)
class IntrinsicRepricingConfig:
    response: FlowResponseConfig = field(default_factory=FlowResponseConfig)
    maximum_event_bars: int = 9

    def validate(self) -> None:
        self.response.validate()
        IntrinsicResponseClockConfig(
            response=self.response,
            maximum_event_bars=self.maximum_event_bars,
        ).validate()

    @property
    def clock(self) -> IntrinsicResponseClockConfig:
        return IntrinsicResponseClockConfig(
            response=self.response,
            maximum_event_bars=self.maximum_event_bars,
        )


_REQUIRED_PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "signed_volume",
    "trade_count",
)


def _validate_price_frame(data: pd.DataFrame) -> None:
    validate_exact_ten_second_cadence(data)
    missing = [name for name in _REQUIRED_PRICE_COLUMNS if name not in data.columns]
    if missing:
        raise KeyError(f"intrinsic repricing input is missing columns: {missing}")
    numeric = data.loc[:, _REQUIRED_PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("intrinsic repricing input contains non-finite values")
    if (numeric["volume"] <= 0.0).any() or (numeric["trade_count"] <= 0.0).any():
        raise ValueError("intrinsic repricing input requires positive activity")
    invalid = (
        (numeric["high"] < numeric[["open", "close"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
        | (numeric["high"] < numeric["low"])
    )
    if bool(invalid.any()):
        raise ValueError("intrinsic repricing input contains invalid OHLC geometry")


def _event_prices(data: pd.DataFrame, event: IntrinsicResponseEvent) -> tuple[float, float]:
    observed = data.iloc[event.start_position : event.end_position + 1]
    high = float(pd.to_numeric(observed["high"], errors="coerce").max())
    low = float(pd.to_numeric(observed["low"], errors="coerce").min())
    if not isfinite(high) or not isfinite(low):
        raise RuntimeError("intrinsic response event exposed no finite extreme")
    return high, low


def _event_details(event: IntrinsicResponseEvent) -> dict[str, Any]:
    return {
        "status": event.status.value,
        "response_state": event.response_state.value,
        "start_position": event.start_position,
        "end_position": event.end_position,
        "start_time_ns": event.start_time_ns,
        "end_time_ns": event.end_time_ns,
        "physical_bars": event.physical_bars,
        "frozen_activity_budget": event.frozen_activity_budget,
        "cumulative_signed_activity": event.cumulative_signed_activity,
        "cumulative_absolute_activity": event.cumulative_absolute_activity,
        "activity_budget_fraction": event.activity_budget_fraction,
        "flow_direction": event.flow_direction,
        "flow_consistency": event.flow_consistency,
        "directional_progress": event.directional_progress,
        "directional_excursion": event.directional_excursion,
        "progress_noise": event.progress_noise,
        "excursion_noise": event.excursion_noise,
        "retention": event.retention,
        "expected_response": event.expected_response,
        "response_surprise": event.response_surprise,
        "frozen_noise_reserve": event.frozen_noise_reserve,
    }


def _outside(close: float, boundary: float, direction: int) -> bool:
    return close > boundary if direction > 0 else close < boundary


def _event_is_initiative(event: IntrinsicResponseEvent, direction: int) -> bool:
    return (
        event.status is IntrinsicEventStatus.COMPLETE
        and event.response_state is FlowResponseState.INITIATIVE_RESPONSE
        and event.flow_direction == direction
    )


def _consume_crossings(
    *,
    data: pd.DataFrame,
    start_position: int,
    end_position: int,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    consumed: set[str],
) -> None:
    for position in range(max(1, start_position), min(end_position, len(data.index) - 1) + 1):
        timestamp_ns = int(data.index[position].as_unit("ns").value)
        context = _context_for_ten_second_close(
            timestamp_ns=timestamp_ns,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
        )
        if context is None:
            continue
        five_bar, boundary_levels, _target_levels = context
        atr = float(five_bar.atr)
        if not isfinite(atr) or atr <= 0.0:
            raise RuntimeError("five-minute context exposed a non-positive ATR")
        row = data.iloc[position]
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


def _structural_stop(
    *,
    direction: int,
    entry: float,
    boundary: float,
    reprice_high: float,
    reprice_low: float,
    atr: float,
) -> tuple[float, float, str]:
    buffer = 0.03 * atr
    minimum_distance = 0.10 * atr
    if direction > 0:
        reference = min(boundary, reprice_low)
        return (
            min(reference - buffer, entry - minimum_distance),
            reference,
            "HELD_BOUNDARY_OR_REPRICE_LOW",
        )
    reference = max(boundary, reprice_high)
    return (
        max(reference + buffer, entry + minimum_distance),
        reference,
        "HELD_BOUNDARY_OR_REPRICE_HIGH",
    )


def _build_signal(
    *,
    data: pd.DataFrame,
    features: pd.DataFrame,
    boundary: ExternalLevel,
    outward: int,
    scenario_id: str,
    armed_position: int,
    event_a: IntrinsicResponseEvent,
    event_b: IntrinsicResponseEvent,
    final_event: IntrinsicResponseEvent,
    entry_path: str,
    entry_position: int,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    consumed: set[str],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
) -> tuple[AcceptanceSignal | None, dict[str, Any] | None]:
    timestamp_ns = int(data.index[entry_position].as_unit("ns").value)
    context = _context_for_ten_second_close(
        timestamp_ns=timestamp_ns,
        context_times=context_times,
        context_bars=context_bars,
        snapshots=snapshots,
    )
    if context is None:
        return None, {
            "scenario_id": scenario_id,
            "symbol": symbol,
            "reason": "NO_COMPLETE_CAUSAL_CONTEXT_AT_ENTRY",
            "implementation_revision": IMPLEMENTATION_REVISION,
        }
    five_bar, _boundary_levels, target_levels = context
    atr = float(five_bar.atr)
    if not isfinite(atr) or atr <= 0.0:
        raise RuntimeError("entry context exposed a non-positive ATR")

    entry = float(data.iloc[entry_position]["close"])
    target = _select_active_target(
        target_levels,
        direction=outward,
        entry=entry,
        excluded_level_id=boundary.level_id,
        consumed=consumed,
    )
    rejection = {
        "scenario_id": scenario_id,
        "symbol": symbol,
        "boundary_id": boundary.level_id,
        "scenario_family": INTRINSIC_REPRICING_FAMILY,
        "entry_path": entry_path,
        "confirmation_time_ns": timestamp_ns,
        "implementation_revision": IMPLEMENTATION_REVISION,
    }
    if target is None:
        return None, {**rejection, "reason": "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET"}

    reprice_high, reprice_low = _event_prices(data, event_b)
    stop, stop_reference, stop_source = _structural_stop(
        direction=outward,
        entry=entry,
        boundary=boundary.level,
        reprice_high=reprice_high,
        reprice_low=reprice_low,
        atr=atr,
    )
    noise = float(features.iloc[entry_position]["causal_noise_reserve"])
    geometry = _cost_geometry(
        direction=outward,
        entry=entry,
        stop=stop,
        target=target.level,
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=noise,
    )
    if geometry is None:
        return None, {**rejection, "reason": "INVALID_COST_AFTER_EXTERNAL_GEOMETRY"}
    loss, gain, net_rr = geometry
    if net_rr < minimum_net_reward_risk:
        return None, {
            **rejection,
            "reason": "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
            "net_reward_risk": net_rr,
        }

    armed_time_ns = int(data.index[armed_position].as_unit("ns").value)
    event_a_high, event_a_low = _event_prices(data, event_a)
    confirmation_high, confirmation_low = _event_prices(data, final_event)
    armed_event = AcceptanceLogicEvent(
        scenario_id=scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
        event_time_ns=armed_time_ns,
        observed_time_ns=armed_time_ns,
        previous_state="IDLE",
        next_state="INTERACTION_ARMED",
        reason_code="FIRST_OBSERVABLE_COMPLETED_LEVEL_CROSS",
        reference_price=boundary.level,
        details={
            "boundary_id": boundary.level_id,
            "boundary_source": boundary.source.value,
            "outward": outward,
            "implementation_revision": IMPLEMENTATION_REVISION,
        },
    )
    displacement_event = AcceptanceLogicEvent(
        scenario_id=scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="INTRINSIC_INITIATIVE_DISPLACEMENT_CONFIRMED",
        event_time_ns=event_a.end_time_ns,
        observed_time_ns=event_a.end_time_ns,
        previous_state="INTERACTION_ARMED",
        next_state="INTRINSIC_DISPLACEMENT_CONFIRMED",
        reason_code="OUTWARD_ACTIVITY_BUDGET_PRODUCED_RETAINED_PRICE_RESPONSE",
        reference_price=event_a.end_close,
        details={
            "event_high": event_a_high,
            "event_low": event_a_low,
            **_event_details(event_a),
        },
    )
    final_logic_event = AcceptanceLogicEvent(
        scenario_id=scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="INTRINSIC_REPRICING_CONTINUATION_CONFIRMED",
        event_time_ns=timestamp_ns,
        observed_time_ns=timestamp_ns,
        previous_state="INTRINSIC_DISPLACEMENT_CONFIRMED",
        next_state="CONFIRMED",
        reason_code=(
            "SECOND_SAME_DIRECTION_INTRINSIC_RESPONSE"
            if entry_path == DIRECT_PERSISTENCE_PATH
            else "COUNTERFLOW_HELD_BOUNDARY_THEN_INTRINSIC_RESPONSE_RESUMED"
        ),
        reference_price=entry,
        details={
            "entry_path": entry_path,
            "event_b": _event_details(event_b),
            "final_event": _event_details(final_event),
            "confirmation_high": confirmation_high,
            "confirmation_low": confirmation_low,
            "implementation_revision": IMPLEMENTATION_REVISION,
        },
    )
    return AcceptanceSignal(
        scenario_id=scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        direction=outward,
        signal_index=entry_position,
        signal_time_ns=timestamp_ns,
        boundary_id=boundary.level_id,
        boundary_source=boundary.source.value,
        boundary_level=boundary.level,
        target_id=target.level_id,
        target_source=target.source.value,
        external_target=target.level,
        entry_reference=entry,
        structural_stop=stop,
        atr=atr,
        causal_stop_slippage_reserve=noise,
        expected_loss_per_unit=loss,
        expected_gain_per_unit=gain,
        net_reward_risk=net_rr,
        armed_time_ns=armed_time_ns,
        retest_time_ns=event_b.start_time_ns,
        retest_high=reprice_high,
        retest_low=reprice_low,
        events=(armed_event, displacement_event, final_logic_event),
        details={
            "scenario_family": INTRINSIC_REPRICING_FAMILY,
            "entry_path": entry_path,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
            "intrinsic_clock_maximum_event_bars": event_a.physical_bars
            if False
            else None,
            "event_a": _event_details(event_a),
            "event_b": _event_details(event_b),
            "final_event": _event_details(final_event),
            "stop_reference": stop_reference,
            "stop_reference_source": stop_source,
            "stop_order_tag": "INTRINSIC_REPRICE_OR_BOUNDARY_INVALIDATION",
        },
    ), None


def build_intrinsic_repricing_signals(
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
    repricing_config: IntrinsicRepricingConfig = IntrinsicRepricingConfig(),
    flow_response_features: pd.DataFrame | None = None,
    activity_budgets: pd.Series | None = None,
    require_retest_contraction: bool = True,
) -> AcceptanceSignalBundle:
    """Build immutable future-free intrinsic repricing continuation signals."""

    del require_retest_contraction
    repricing_config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost or reward-risk contract")
    _validate_price_frame(data)

    features = (
        causal_flow_response_frame(
            data,
            tick=tick,
            config=repricing_config.response,
        )
        if flow_response_features is None
        else flow_response_features.copy()
    )
    if not features.index.equals(data.index):
        raise ValueError("flow-response features must have the exact input index")
    budgets = (
        causal_activity_budget_series(features, config=repricing_config.clock)
        if activity_budgets is None
        else pd.to_numeric(activity_budgets, errors="coerce")
    )
    if not budgets.index.equals(data.index):
        raise ValueError("activity budgets must have the exact input index")

    signals: dict[int, list[AcceptanceSignal]] = {}
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    consumed: set[str] = set()
    scenario_counter = 0
    position = 1

    while position < len(data.index):
        timestamp_ns = int(data.index[position].as_unit("ns").value)
        context = _context_for_ten_second_close(
            timestamp_ns=timestamp_ns,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
        )
        if context is None:
            diagnostics["NO_COMPLETE_CAUSAL_CONTEXT_SNAPSHOT"] += 1
            position += 1
            continue
        five_bar, boundary_levels, _target_levels = context
        atr = float(five_bar.atr)
        if not isfinite(atr) or atr <= 0.0:
            raise RuntimeError("five-minute context exposed a non-positive ATR")
        row = data.iloc[position]
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
        interaction = _select_interaction_boundary(highs, lows)
        if interaction is None:
            if highs and lows:
                diagnostics["BILATERAL_COMPLETED_LEVEL_INTERACTION"] += 1
            position += 1
            continue

        boundary, outward = interaction
        scenario_counter += 1
        scenario_id = f"intrinsic-reprice-{symbol.lower()}-{scenario_counter:06d}"
        diagnostics["INTRINSIC_INTERACTION_ARMED"] += 1
        last_position = position

        if position + 1 >= len(data.index):
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "boundary_id": boundary.level_id,
                    "reason": "NO_POST_INTERACTION_INTRINSIC_EVENT",
                    "implementation_revision": IMPLEMENTATION_REVISION,
                }
            )
            break

        event_a = build_intrinsic_response_event(
            data,
            start_position=position + 1,
            tick=tick,
            config=repricing_config.clock,
            flow_response_features=features,
            activity_budgets=budgets,
        )
        last_position = max(last_position, event_a.end_position)
        _consume_crossings(
            data=data,
            start_position=position + 1,
            end_position=event_a.end_position,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
            consumed=consumed,
        )
        if not _event_is_initiative(event_a, outward) or not _outside(
            event_a.end_close,
            boundary.level,
            outward,
        ):
            diagnostics["INITIAL_INTRINSIC_RESPONSE_NOT_OUTWARD_INITIATIVE"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "boundary_id": boundary.level_id,
                    "reason": "INITIAL_INTRINSIC_RESPONSE_NOT_OUTWARD_INITIATIVE",
                    "event_a": _event_details(event_a),
                    "implementation_revision": IMPLEMENTATION_REVISION,
                }
            )
            position = last_position + 1
            continue

        if event_a.end_position + 1 >= len(data.index):
            diagnostics["NO_SECOND_INTRINSIC_EVENT"] += 1
            position = last_position + 1
            continue
        event_b = build_intrinsic_response_event(
            data,
            start_position=event_a.end_position + 1,
            tick=tick,
            config=repricing_config.clock,
            flow_response_features=features,
            activity_budgets=budgets,
        )
        last_position = max(last_position, event_b.end_position)
        _consume_crossings(
            data=data,
            start_position=event_a.end_position + 1,
            end_position=event_b.end_position,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
            consumed=consumed,
        )

        entry_path: str | None = None
        final_event = event_b
        entry_position = event_b.end_position
        if (
            _event_is_initiative(event_b, outward)
            and _outside(event_b.end_close, boundary.level, outward)
            and (
                event_b.end_close > event_a.end_close
                if outward > 0
                else event_b.end_close < event_a.end_close
            )
        ):
            entry_path = DIRECT_PERSISTENCE_PATH
        else:
            event_b_high, event_b_low = _event_prices(data, event_b)
            counterflow = (
                event_b.status is IntrinsicEventStatus.COMPLETE
                and event_b.flow_direction == -outward
                and event_b.flow_consistency
                >= repricing_config.response.minimum_flow_consistency
            )
            touched = (
                event_b_low <= boundary.level
                if outward > 0
                else event_b_high >= boundary.level
            )
            held = _outside(event_b.end_close, boundary.level, outward)
            if counterflow and touched and held and event_b.end_position + 1 < len(data.index):
                event_c = build_intrinsic_response_event(
                    data,
                    start_position=event_b.end_position + 1,
                    tick=tick,
                    config=repricing_config.clock,
                    flow_response_features=features,
                    activity_budgets=budgets,
                )
                last_position = max(last_position, event_c.end_position)
                _consume_crossings(
                    data=data,
                    start_position=event_b.end_position + 1,
                    end_position=event_c.end_position,
                    context_times=context_times,
                    context_bars=context_bars,
                    snapshots=snapshots,
                    consumed=consumed,
                )
                resumed = (
                    _event_is_initiative(event_c, outward)
                    and _outside(event_c.end_close, boundary.level, outward)
                    and (
                        event_c.end_close > event_b_high
                        if outward > 0
                        else event_c.end_close < event_b_low
                    )
                )
                if resumed:
                    entry_path = REPRICE_RESUMPTION_PATH
                    final_event = event_c
                    entry_position = event_c.end_position

        if entry_path is None:
            diagnostics["INTRINSIC_PERSISTENCE_OR_REPRICE_NOT_CONFIRMED"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "boundary_id": boundary.level_id,
                    "reason": "INTRINSIC_PERSISTENCE_OR_REPRICE_NOT_CONFIRMED",
                    "event_a": _event_details(event_a),
                    "event_b": _event_details(event_b),
                    "implementation_revision": IMPLEMENTATION_REVISION,
                }
            )
            position = last_position + 1
            continue

        signal, rejection = _build_signal(
            data=data,
            features=features,
            boundary=boundary,
            outward=outward,
            scenario_id=scenario_id,
            armed_position=position,
            event_a=event_a,
            event_b=event_b,
            final_event=final_event,
            entry_path=entry_path,
            entry_position=entry_position,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
            consumed=consumed,
            symbol=symbol,
            instrument_id=instrument_id,
            tick=tick,
            fee_rate=fee_rate,
            minimum_net_reward_risk=minimum_net_reward_risk,
        )
        if rejection is not None:
            rejected.append(rejection)
            diagnostics[str(rejection["reason"])] += 1
        else:
            assert signal is not None
            signals.setdefault(signal.signal_time_ns, []).append(signal)
            diagnostics["TRADEABLE_INTRINSIC_REPRICING_CONTINUATION"] += 1
            diagnostics[f"TRADEABLE_{entry_path}"] += 1
        position = last_position + 1

    immutable = {
        timestamp: tuple(
            sorted(items, key=lambda item: (item.net_reward_risk, item.symbol), reverse=True)
        )
        for timestamp, items in signals.items()
    }
    emitted = sum(len(items) for items in immutable.values())
    if emitted != diagnostics.get("TRADEABLE_INTRINSIC_REPRICING_CONTINUATION", 0):
        raise RuntimeError("intrinsic repricing diagnostic count mismatch")
    return AcceptanceSignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "DIRECT_PERSISTENCE_PATH",
    "IMPLEMENTATION_REVISION",
    "INTRINSIC_REPRICING_FAMILY",
    "IntrinsicRepricingConfig",
    "REPRICE_RESUMPTION_PATH",
    "build_intrinsic_repricing_signals",
]
