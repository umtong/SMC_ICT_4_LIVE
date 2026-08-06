"""Causal external-liquidity auction scenarios driven by flow versus price response.

This successor detector is intentionally independent from the failed retest/contraction families.
It combines two observable structures only:

1. already-completed 4-hour/day/week external liquidity; and
2. the causal aggressive-flow/price-response states from :mod:`aggtrade_flow_response`.

A completed external level first arms an interaction.  The detector then waits for one of two
mutually exclusive market states built entirely from completed ten-second buckets:

* ``FLOW_RESPONSE_INITIATIVE_CONTINUATION`` — persistent tail aggressive pressure causes at least one
  causal-noise unit of retained progress beyond the interacted level; or
* ``FLOW_RESPONSE_ABSORPTION_REVERSAL`` — outward tail pressure creates an excursion but gives it
  back through the level, followed by a separate opposite initiative-response window which breaks
  the reclaim extreme.

No future outcome, fixed-R projection, order, fill, account, PnL, or model score enters detection.
NautilusTrader remains the sole execution and account engine when this detector becomes eligible for
an execution candidate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Mapping

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
from aggtrade_flow_response import (
    FlowResponseConfig,
    FlowResponseState,
    causal_flow_response_frame,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


INITIATIVE_FAMILY = "FLOW_RESPONSE_INITIATIVE_CONTINUATION"
ABSORPTION_FAMILY = "FLOW_RESPONSE_ABSORPTION_REVERSAL"
IMPLEMENTATION_REVISION = "CAUSAL_FLOW_RESPONSE_EXTERNAL_AUCTION_V1"


@dataclass(frozen=True, slots=True)
class FlowResponseAuctionConfig:
    response: FlowResponseConfig = field(default_factory=FlowResponseConfig)
    interaction_response_windows: int = 3
    reversal_confirmation_windows: int = 2

    def validate(self) -> None:
        self.response.validate()
        if self.interaction_response_windows < 1:
            raise ValueError("interaction response windows must be positive")
        if self.reversal_confirmation_windows < 1:
            raise ValueError("reversal confirmation windows must be positive")

    @property
    def interaction_expiry_bars(self) -> int:
        return self.response.response_window_bars * self.interaction_response_windows

    @property
    def reversal_expiry_bars(self) -> int:
        return self.response.response_window_bars * self.reversal_confirmation_windows


@dataclass(slots=True)
class _PendingInteraction:
    scenario_id: str
    boundary: ExternalLevel
    outward: int
    armed_position: int
    armed_time_ns: int
    expiry_position: int
    sweep_high: float
    sweep_low: float
    absorption_position: int | None = None
    absorption_time_ns: int | None = None
    absorption_high: float | None = None
    absorption_low: float | None = None


_REQUIRED_FEATURES = (
    "flow_response_state",
    "flow_direction",
    "flow_consistency",
    "window_pressure_ratio",
    "progress_noise",
    "excursion_noise",
    "retention",
    "response_surprise",
    "causal_noise_reserve",
)


def _validate_feature_frame(data: pd.DataFrame, features: pd.DataFrame) -> None:
    if not features.index.equals(data.index):
        raise ValueError("flow-response features must have the exact ten-second input index")
    missing = [name for name in _REQUIRED_FEATURES if name not in features.columns]
    if missing:
        raise KeyError(f"flow-response features are missing columns: {missing}")


def _observable_feature_row(row: pd.Series) -> bool:
    if str(row["flow_response_state"]) == FlowResponseState.UNOBSERVABLE.value:
        return False
    numeric = (
        "flow_direction",
        "flow_consistency",
        "window_pressure_ratio",
        "progress_noise",
        "excursion_noise",
        "retention",
        "response_surprise",
        "causal_noise_reserve",
    )
    return all(isfinite(float(row[name])) for name in numeric)


def _state_is(
    row: pd.Series,
    *,
    state: FlowResponseState,
    direction: int,
) -> bool:
    return (
        str(row["flow_response_state"]) == state.value
        and int(np.sign(float(row["flow_direction"]))) == int(direction)
    )


def _outside_boundary(close: float, boundary: float, outward: int) -> bool:
    return close > boundary if outward > 0 else close < boundary


def _inside_or_reclaimed(close: float, boundary: float, outward: int) -> bool:
    return close <= boundary if outward > 0 else close >= boundary


def _response_window_extreme(
    data: pd.DataFrame,
    *,
    position: int,
    window: int,
) -> tuple[float, float, int]:
    start = position - window + 1
    if start < 0:
        raise RuntimeError("response window starts before the ten-second frame")
    observed = data.iloc[start : position + 1]
    high = float(pd.to_numeric(observed["high"], errors="coerce").max())
    low = float(pd.to_numeric(observed["low"], errors="coerce").min())
    if not isfinite(high) or not isfinite(low):
        raise RuntimeError("response window contains no finite price extreme")
    return high, low, start


def _structural_stop_from_observed_window(
    *,
    direction: int,
    entry: float,
    observed_high: float,
    observed_low: float,
    atr: float,
) -> tuple[float, float, str]:
    minimum_distance = 0.10 * atr
    buffer = 0.03 * atr
    if direction > 0:
        reference = observed_low
        return min(reference - buffer, entry - minimum_distance), reference, "RESPONSE_WINDOW_LOW"
    reference = observed_high
    return max(reference + buffer, entry + minimum_distance), reference, "RESPONSE_WINDOW_HIGH"


def _failed_response_stop(
    *,
    direction: int,
    entry: float,
    sweep_high: float,
    sweep_low: float,
    atr: float,
) -> tuple[float, float, str]:
    minimum_distance = 0.10 * atr
    buffer = 0.03 * atr
    if direction > 0:
        reference = sweep_low
        return min(reference - buffer, entry - minimum_distance), reference, "OBSERVED_SWEEP_LOW"
    reference = sweep_high
    return max(reference + buffer, entry + minimum_distance), reference, "OBSERVED_SWEEP_HIGH"


def _feature_details(row: pd.Series) -> dict[str, float | str]:
    return {
        "flow_response_state": str(row["flow_response_state"]),
        "flow_direction": float(row["flow_direction"]),
        "flow_consistency": float(row["flow_consistency"]),
        "window_pressure_ratio": float(row["window_pressure_ratio"]),
        "progress_noise": float(row["progress_noise"]),
        "excursion_noise": float(row["excursion_noise"]),
        "retention": float(row["retention"]),
        "response_surprise": float(row["response_surprise"]),
        "causal_noise_reserve": float(row["causal_noise_reserve"]),
    }


def _build_signal(
    *,
    data: pd.DataFrame,
    features: pd.DataFrame,
    pending: _PendingInteraction,
    position: int,
    timestamp_ns: int,
    atr: float,
    target_levels: tuple[ExternalLevel, ...],
    consumed: set[str],
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    family: str,
) -> tuple[AcceptanceSignal | None, dict[str, Any] | None]:
    row = data.iloc[position]
    feature = features.iloc[position]
    entry = float(row["close"])
    target_level = _select_active_target(
        target_levels,
        direction=pending.outward if family == INITIATIVE_FAMILY else -pending.outward,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
        consumed=consumed,
    )
    if target_level is None:
        return None, {
            "scenario_id": pending.scenario_id,
            "symbol": None,
            "boundary_id": pending.boundary.level_id,
            "reason": "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET",
            "confirmation_time_ns": timestamp_ns,
            "scenario_family": family,
        }

    if family == INITIATIVE_FAMILY:
        direction = pending.outward
        observed_high, observed_low, response_start = _response_window_extreme(
            data,
            position=position,
            window=3,
        )
        stop, stop_reference, stop_source = _structural_stop_from_observed_window(
            direction=direction,
            entry=entry,
            observed_high=observed_high,
            observed_low=observed_low,
            atr=atr,
        )
        middle_event = AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol="",
            instrument_id="",
            event_type="PERSISTENT_AGGRESSIVE_FLOW_RESPONSE_OBSERVED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="INTERACTION_ARMED",
            next_state="RESPONSE_OBSERVED",
            reason_code="TAIL_PRESSURE_WITH_RETAINED_PRICE_PROGRESS",
            reference_price=entry,
            details=_feature_details(feature),
        )
        final_event_type = "INITIATIVE_PRICE_RESPONSE_CONFIRMED"
        final_reason = "OUTWARD_FLOW_CAUSED_RETAINED_PROGRESS"
        retest_time_ns = int(data.index[response_start].as_unit("ns").value)
        response_details = {
            "response_window_start_position": response_start,
            "response_window_high": observed_high,
            "response_window_low": observed_low,
        }
    else:
        direction = -pending.outward
        stop, stop_reference, stop_source = _failed_response_stop(
            direction=direction,
            entry=entry,
            sweep_high=pending.sweep_high,
            sweep_low=pending.sweep_low,
            atr=atr,
        )
        assert pending.absorption_time_ns is not None
        assert pending.absorption_high is not None
        assert pending.absorption_low is not None
        middle_event = AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol="",
            instrument_id="",
            event_type="OUTWARD_FLOW_ABSORBED_AND_RECLAIMED",
            event_time_ns=pending.absorption_time_ns,
            observed_time_ns=pending.absorption_time_ns,
            previous_state="INTERACTION_ARMED",
            next_state="ABSORPTION_RECLAIMED",
            reason_code="TAIL_PRESSURE_FAILED_TO_RETAIN_PRICE_PROGRESS",
            reference_price=pending.boundary.level,
            details={
                "sweep_high": pending.sweep_high,
                "sweep_low": pending.sweep_low,
                "reclaim_high": pending.absorption_high,
                "reclaim_low": pending.absorption_low,
            },
        )
        final_event_type = "INWARD_INITIATIVE_RESPONSE_CONFIRMED"
        final_reason = "SEPARATE_OPPOSITE_FLOW_RESPONSE_BROKE_RECLAIM_EXTREME"
        retest_time_ns = pending.absorption_time_ns
        response_details = {
            "absorption_position": pending.absorption_position,
            "absorption_high": pending.absorption_high,
            "absorption_low": pending.absorption_low,
            "sweep_high": pending.sweep_high,
            "sweep_low": pending.sweep_low,
        }

    geometry = _cost_geometry(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target_level.level,
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=float(feature["causal_noise_reserve"]),
    )
    rejection_base = {
        "scenario_id": pending.scenario_id,
        "boundary_id": pending.boundary.level_id,
        "target_id": target_level.level_id,
        "scenario_family": family,
        "confirmation_time_ns": timestamp_ns,
    }
    if geometry is None:
        return None, {
            **rejection_base,
            "reason": "INVALID_COST_AFTER_EXTERNAL_GEOMETRY",
        }
    loss, gain, net_rr = geometry
    if net_rr < minimum_net_reward_risk:
        return None, {
            **rejection_base,
            "reason": "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
            "net_reward_risk": net_rr,
        }

    armed_event = AcceptanceLogicEvent(
        scenario_id=pending.scenario_id,
        symbol="",
        instrument_id="",
        event_type="EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
        event_time_ns=pending.armed_time_ns,
        observed_time_ns=pending.armed_time_ns,
        previous_state="IDLE",
        next_state="INTERACTION_ARMED",
        reason_code="FIRST_OBSERVABLE_COMPLETED_LEVEL_CROSS",
        reference_price=pending.boundary.level,
        details={
            "boundary_id": pending.boundary.level_id,
            "boundary_source": pending.boundary.source.value,
            "outward": pending.outward,
        },
    )
    final_event = AcceptanceLogicEvent(
        scenario_id=pending.scenario_id,
        symbol="",
        instrument_id="",
        event_type=final_event_type,
        event_time_ns=timestamp_ns,
        observed_time_ns=timestamp_ns,
        previous_state=middle_event.next_state,
        next_state="CONFIRMED",
        reason_code=final_reason,
        reference_price=entry,
        details=_feature_details(feature),
    )
    signal = AcceptanceSignal(
        scenario_id=pending.scenario_id,
        symbol="",
        instrument_id="",
        direction=direction,
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
        causal_stop_slippage_reserve=float(feature["causal_noise_reserve"]),
        expected_loss_per_unit=loss,
        expected_gain_per_unit=gain,
        net_reward_risk=net_rr,
        armed_time_ns=pending.armed_time_ns,
        retest_time_ns=retest_time_ns,
        retest_high=float(response_details.get("absorption_high", response_details.get("response_window_high"))),
        retest_low=float(response_details.get("absorption_low", response_details.get("response_window_low"))),
        events=(armed_event, middle_event, final_event),
        details={
            "scenario_family": family,
            "stop_order_tag": (
                "FLOW_RESPONSE_WINDOW_INVALIDATION"
                if family == INITIATIVE_FAMILY
                else "OBSERVED_FAILED_RESPONSE_SWEEP_INVALIDATION"
            ),
            "stop_reference": stop_reference,
            "stop_reference_source": stop_source,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "response_window_bars": 3,
            **response_details,
            **_feature_details(feature),
        },
    )
    return signal, None


def _attach_identity(
    signal: AcceptanceSignal,
    *,
    symbol: str,
    instrument_id: str,
) -> AcceptanceSignal:
    events = tuple(
        AcceptanceLogicEvent(
            scenario_id=event.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type=event.event_type,
            event_time_ns=event.event_time_ns,
            observed_time_ns=event.observed_time_ns,
            previous_state=event.previous_state,
            next_state=event.next_state,
            reason_code=event.reason_code,
            reference_price=event.reference_price,
            details=event.details,
        )
        for event in signal.events
    )
    return AcceptanceSignal(
        scenario_id=signal.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        direction=signal.direction,
        signal_index=signal.signal_index,
        signal_time_ns=signal.signal_time_ns,
        boundary_id=signal.boundary_id,
        boundary_source=signal.boundary_source,
        boundary_level=signal.boundary_level,
        target_id=signal.target_id,
        target_source=signal.target_source,
        external_target=signal.external_target,
        entry_reference=signal.entry_reference,
        structural_stop=signal.structural_stop,
        atr=signal.atr,
        causal_stop_slippage_reserve=signal.causal_stop_slippage_reserve,
        expected_loss_per_unit=signal.expected_loss_per_unit,
        expected_gain_per_unit=signal.expected_gain_per_unit,
        net_reward_risk=signal.net_reward_risk,
        armed_time_ns=signal.armed_time_ns,
        retest_time_ns=signal.retest_time_ns,
        retest_high=signal.retest_high,
        retest_low=signal.retest_low,
        events=events,
        details=signal.details,
    )


def build_flow_response_auction_signals(
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
    auction_config: FlowResponseAuctionConfig = FlowResponseAuctionConfig(),
    flow_response_features: pd.DataFrame | None = None,
) -> AcceptanceSignalBundle:
    """Build immutable future-free flow-response auction scenarios."""

    auction_config.validate()
    if tick <= 0 or fee_rate < 0 or minimum_net_reward_risk <= 0:
        raise ValueError("invalid cost or reward-risk contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")
    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("ten-second timestamps must be unique and increasing")

    features = (
        causal_flow_response_frame(
            data,
            tick=tick,
            config=auction_config.response,
        )
        if flow_response_features is None
        else flow_response_features.copy()
    )
    _validate_feature_frame(data, features)

    signals: dict[int, list[AcceptanceSignal]] = {}
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    consumed: set[str] = set()
    pending: _PendingInteraction | None = None
    scenario_counter = 0
    response_window = auction_config.response.response_window_bars

    for position in range(1, len(data.index)):
        row = data.iloc[position]
        feature = features.iloc[position]
        timestamp_ns = int(data.index[position].as_unit("ns").value)
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
            pending.sweep_high = max(pending.sweep_high, float(row["high"]))
            pending.sweep_low = min(pending.sweep_low, float(row["low"]))
            if position > pending.expiry_position:
                diagnostics["FLOW_RESPONSE_INTERACTION_TIMEOUT"] += 1
                rejected.append(
                    {
                        "scenario_id": pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": pending.boundary.level_id,
                        "reason": "FLOW_RESPONSE_INTERACTION_TIMEOUT",
                        "armed_time_ns": pending.armed_time_ns,
                    }
                )
                pending = None
            elif _observable_feature_row(feature):
                if pending.absorption_position is None:
                    full_post_interaction_window = (
                        position >= pending.armed_position + response_window - 1
                    )
                    if full_post_interaction_window and _state_is(
                        feature,
                        state=FlowResponseState.INITIATIVE_RESPONSE,
                        direction=pending.outward,
                    ) and _outside_boundary(
                        float(row["close"]),
                        pending.boundary.level,
                        pending.outward,
                    ):
                        signal, rejection = _build_signal(
                            data=data,
                            features=features,
                            pending=pending,
                            position=position,
                            timestamp_ns=timestamp_ns,
                            atr=atr,
                            target_levels=target_levels,
                            consumed=consumed,
                            tick=tick,
                            fee_rate=fee_rate,
                            minimum_net_reward_risk=minimum_net_reward_risk,
                            family=INITIATIVE_FAMILY,
                        )
                        if rejection is not None:
                            rejection["symbol"] = symbol
                            rejected.append(rejection)
                            diagnostics[str(rejection["reason"])] += 1
                        else:
                            assert signal is not None
                            signal = _attach_identity(
                                signal,
                                symbol=symbol,
                                instrument_id=instrument_id,
                            )
                            signals.setdefault(timestamp_ns, []).append(signal)
                            diagnostics["TRADEABLE_FLOW_RESPONSE_INITIATIVE"] += 1
                        pending = None
                    elif full_post_interaction_window and _state_is(
                        feature,
                        state=FlowResponseState.ABSORBED_RESPONSE,
                        direction=pending.outward,
                    ) and _inside_or_reclaimed(
                        float(row["close"]),
                        pending.boundary.level,
                        pending.outward,
                    ):
                        pending.absorption_position = position
                        pending.absorption_time_ns = timestamp_ns
                        pending.absorption_high = float(row["high"])
                        pending.absorption_low = float(row["low"])
                        pending.expiry_position = (
                            position + auction_config.reversal_expiry_bars
                        )
                        diagnostics["OUTWARD_FLOW_RESPONSE_ABSORBED"] += 1
                else:
                    assert pending.absorption_high is not None
                    assert pending.absorption_low is not None
                    separate_opposite_window = (
                        position >= pending.absorption_position + response_window - 1
                    )
                    breaks_reclaim = (
                        float(row["close"]) < pending.absorption_low
                        if pending.outward > 0
                        else float(row["close"]) > pending.absorption_high
                    )
                    if separate_opposite_window and _state_is(
                        feature,
                        state=FlowResponseState.INITIATIVE_RESPONSE,
                        direction=-pending.outward,
                    ) and breaks_reclaim and _inside_or_reclaimed(
                        float(row["close"]),
                        pending.boundary.level,
                        pending.outward,
                    ):
                        signal, rejection = _build_signal(
                            data=data,
                            features=features,
                            pending=pending,
                            position=position,
                            timestamp_ns=timestamp_ns,
                            atr=atr,
                            target_levels=target_levels,
                            consumed=consumed,
                            tick=tick,
                            fee_rate=fee_rate,
                            minimum_net_reward_risk=minimum_net_reward_risk,
                            family=ABSORPTION_FAMILY,
                        )
                        if rejection is not None:
                            rejection["symbol"] = symbol
                            rejected.append(rejection)
                            diagnostics[str(rejection["reason"])] += 1
                        else:
                            assert signal is not None
                            signal = _attach_identity(
                                signal,
                                symbol=symbol,
                                instrument_id=instrument_id,
                            )
                            signals.setdefault(timestamp_ns, []).append(signal)
                            diagnostics["TRADEABLE_FLOW_RESPONSE_ABSORPTION_REVERSAL"] += 1
                        pending = None
            if handled_pending:
                continue

        interaction = _select_interaction_boundary(highs, lows)
        if interaction is None:
            if highs and lows:
                diagnostics["BILATERAL_COMPLETED_LEVEL_INTERACTION"] += 1
            continue
        boundary, outward = interaction
        scenario_counter += 1
        pending = _PendingInteraction(
            scenario_id=f"flow-response-{symbol.lower()}-{scenario_counter:06d}",
            boundary=boundary,
            outward=outward,
            armed_position=position,
            armed_time_ns=timestamp_ns,
            expiry_position=position + auction_config.interaction_expiry_bars,
            sweep_high=float(row["high"]),
            sweep_low=float(row["low"]),
        )
        diagnostics["FLOW_RESPONSE_INTERACTION_ARMED"] += 1

    immutable = {
        timestamp: tuple(
            sorted(
                items,
                key=lambda signal: (signal.net_reward_risk, signal.symbol),
                reverse=True,
            )
        )
        for timestamp, items in signals.items()
    }
    return AcceptanceSignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "ABSORPTION_FAMILY",
    "FlowResponseAuctionConfig",
    "IMPLEMENTATION_REVISION",
    "INITIATIVE_FAMILY",
    "build_flow_response_auction_signals",
]
