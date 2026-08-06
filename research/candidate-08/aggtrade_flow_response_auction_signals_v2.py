"""Causal flow-response auction detector with strict post-event window separation.

Revision V2 fixes three implementation errors in the initial successor prototype without changing
its economic hypothesis:

* the initiative response window begins strictly after the completed external-level interaction;
* the opposite response window begins strictly after the completed absorption/reclaim bucket; and
* every response-window length and structural stop uses the configured window, never a hard-coded
  three-bar assumption.

The absorption event freezes only information observable at absorption. The final reversal signal
may additionally use the full sweep observed through its completed confirmation bucket. Detection
uses completed ten-second aggregate-trade buckets and already-completed external levels only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
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
from aggtrade_flow_response import FlowResponseState, causal_flow_response_frame
from aggtrade_flow_response_auction_signals import FlowResponseAuctionConfig
from range_fvg_logic import ExternalLevel, FiveMinuteBar


INITIATIVE_FAMILY = "FLOW_RESPONSE_INITIATIVE_CONTINUATION"
ABSORPTION_FAMILY = "FLOW_RESPONSE_ABSORPTION_REVERSAL"
IMPLEMENTATION_REVISION = "CAUSAL_FLOW_RESPONSE_EXTERNAL_AUCTION_V2"


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
    absorption_sweep_high: float | None = None
    absorption_sweep_low: float | None = None


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
    missing = [column for column in _REQUIRED_PRICE_COLUMNS if column not in data.columns]
    if missing:
        raise KeyError(f"flow-response auction input is missing columns: {missing}")
    numeric = data.loc[:, _REQUIRED_PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("flow-response auction input contains non-finite price or activity data")
    if (numeric["volume"] <= 0.0).any() or (numeric["trade_count"] <= 0.0).any():
        raise ValueError("flow-response auction input requires positive volume and trade count")
    invalid_geometry = (
        (numeric["high"] < numeric[["open", "close"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
        | (numeric["high"] < numeric["low"])
    )
    if bool(invalid_geometry.any()):
        raise ValueError("flow-response auction input contains invalid OHLC geometry")


def _validate_feature_frame(data: pd.DataFrame, features: pd.DataFrame) -> None:
    if not features.index.equals(data.index):
        raise ValueError("flow-response features must have the exact ten-second input index")
    missing = [name for name in _REQUIRED_FEATURES if name not in features.columns]
    if missing:
        raise KeyError(f"flow-response features are missing columns: {missing}")


def _observable_feature_row(row: pd.Series) -> bool:
    if str(row["flow_response_state"]) == FlowResponseState.UNOBSERVABLE.value:
        return False
    return all(
        isfinite(float(row[name]))
        for name in _REQUIRED_FEATURES
        if name != "flow_response_state"
    )


def _state_is(row: pd.Series, *, state: FlowResponseState, direction: int) -> bool:
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
    response_window: int,
    family: str,
    symbol: str,
    instrument_id: str,
) -> tuple[AcceptanceSignal | None, dict[str, Any] | None]:
    row = data.iloc[position]
    confirmation_feature = features.iloc[position]
    entry = float(row["close"])
    direction = pending.outward if family == INITIATIVE_FAMILY else -pending.outward
    target_level = _select_active_target(
        target_levels,
        direction=direction,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
        consumed=consumed,
    )
    if target_level is None:
        return None, {
            "scenario_id": pending.scenario_id,
            "symbol": symbol,
            "boundary_id": pending.boundary.level_id,
            "reason": "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET",
            "confirmation_time_ns": timestamp_ns,
            "scenario_family": family,
        }

    response_high, response_low, response_start = _response_window_extreme(
        data,
        position=position,
        window=response_window,
    )

    if family == INITIATIVE_FAMILY:
        if response_start <= pending.armed_position:
            raise RuntimeError("initiative response window is not strictly post interaction")
        stop, stop_reference, stop_source = _structural_stop_from_observed_window(
            direction=direction,
            entry=entry,
            observed_high=response_high,
            observed_low=response_low,
            atr=atr,
        )
        response_start_time_ns = int(data.index[response_start].as_unit("ns").value)
        middle_event = AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="PERSISTENT_AGGRESSIVE_FLOW_RESPONSE_OBSERVED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="INTERACTION_ARMED",
            next_state="RESPONSE_OBSERVED",
            reason_code="POST_INTERACTION_TAIL_PRESSURE_WITH_RETAINED_PRICE_PROGRESS",
            reference_price=entry,
            details={
                "response_window_start_position": response_start,
                "response_window_end_position": position,
                "response_window_start_time_ns": response_start_time_ns,
                "response_window_high": response_high,
                "response_window_low": response_low,
                **_feature_details(confirmation_feature),
            },
        )
        retest_time_ns = response_start_time_ns
        retest_high = response_high
        retest_low = response_low
        response_details: dict[str, Any] = {
            "response_window_start_position": response_start,
            "response_window_end_position": position,
            "response_window_start_time_ns": response_start_time_ns,
            "response_window_high": response_high,
            "response_window_low": response_low,
        }
        final_event_type = "INITIATIVE_PRICE_RESPONSE_CONFIRMED"
        final_reason = "OUTWARD_FLOW_CAUSED_RETAINED_POST_INTERACTION_PROGRESS"
    else:
        assert pending.absorption_position is not None
        assert pending.absorption_time_ns is not None
        assert pending.absorption_high is not None
        assert pending.absorption_low is not None
        assert pending.absorption_sweep_high is not None
        assert pending.absorption_sweep_low is not None
        if response_start <= pending.absorption_position:
            raise RuntimeError("opposite response window is not strictly post absorption")
        stop, stop_reference, stop_source = _failed_response_stop(
            direction=direction,
            entry=entry,
            sweep_high=pending.sweep_high,
            sweep_low=pending.sweep_low,
            atr=atr,
        )
        absorption_feature = features.iloc[pending.absorption_position]
        middle_event = AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="OUTWARD_FLOW_ABSORBED_AND_RECLAIMED",
            event_time_ns=pending.absorption_time_ns,
            observed_time_ns=pending.absorption_time_ns,
            previous_state="INTERACTION_ARMED",
            next_state="ABSORPTION_RECLAIMED",
            reason_code="TAIL_PRESSURE_FAILED_TO_RETAIN_PRICE_PROGRESS",
            reference_price=pending.boundary.level,
            details={
                "sweep_high_at_absorption": pending.absorption_sweep_high,
                "sweep_low_at_absorption": pending.absorption_sweep_low,
                "reclaim_high": pending.absorption_high,
                "reclaim_low": pending.absorption_low,
                **_feature_details(absorption_feature),
            },
        )
        retest_time_ns = pending.absorption_time_ns
        retest_high = pending.absorption_high
        retest_low = pending.absorption_low
        response_details = {
            "absorption_position": pending.absorption_position,
            "absorption_high": pending.absorption_high,
            "absorption_low": pending.absorption_low,
            "sweep_high_at_absorption": pending.absorption_sweep_high,
            "sweep_low_at_absorption": pending.absorption_sweep_low,
            "sweep_high_through_confirmation": pending.sweep_high,
            "sweep_low_through_confirmation": pending.sweep_low,
            "opposite_response_window_start_position": response_start,
            "opposite_response_window_end_position": position,
            "opposite_response_window_high": response_high,
            "opposite_response_window_low": response_low,
        }
        final_event_type = "INWARD_INITIATIVE_RESPONSE_CONFIRMED"
        final_reason = "SEPARATE_POST_ABSORPTION_OPPOSITE_RESPONSE_BROKE_RECLAIM_EXTREME"

    geometry = _cost_geometry(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target_level.level,
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=float(confirmation_feature["causal_noise_reserve"]),
    )
    rejection_base = {
        "scenario_id": pending.scenario_id,
        "symbol": symbol,
        "boundary_id": pending.boundary.level_id,
        "target_id": target_level.level_id,
        "scenario_family": family,
        "confirmation_time_ns": timestamp_ns,
        "implementation_revision": IMPLEMENTATION_REVISION,
    }
    if geometry is None:
        return None, {**rejection_base, "reason": "INVALID_COST_AFTER_EXTERNAL_GEOMETRY"}
    loss, gain, net_rr = geometry
    if net_rr < minimum_net_reward_risk:
        return None, {
            **rejection_base,
            "reason": "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
            "net_reward_risk": net_rr,
        }

    armed_event = AcceptanceLogicEvent(
        scenario_id=pending.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
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
        symbol=symbol,
        instrument_id=instrument_id,
        event_type=final_event_type,
        event_time_ns=timestamp_ns,
        observed_time_ns=timestamp_ns,
        previous_state=middle_event.next_state,
        next_state="CONFIRMED",
        reason_code=final_reason,
        reference_price=entry,
        details={
            **_feature_details(confirmation_feature),
            **(
                {
                    "sweep_high_through_confirmation": pending.sweep_high,
                    "sweep_low_through_confirmation": pending.sweep_low,
                    "opposite_response_window_start_position": response_start,
                    "opposite_response_window_end_position": position,
                }
                if family == ABSORPTION_FAMILY
                else {
                    "response_window_start_position": response_start,
                    "response_window_end_position": position,
                }
            ),
        },
    )

    signal = AcceptanceSignal(
        scenario_id=pending.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
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
        causal_stop_slippage_reserve=float(confirmation_feature["causal_noise_reserve"]),
        expected_loss_per_unit=loss,
        expected_gain_per_unit=gain,
        net_reward_risk=net_rr,
        armed_time_ns=pending.armed_time_ns,
        retest_time_ns=retest_time_ns,
        retest_high=retest_high,
        retest_low=retest_low,
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
            "response_window_bars": response_window,
            **response_details,
            **_feature_details(confirmation_feature),
        },
    )
    return signal, None


def _signal_counts(signals_by_time_ns: dict[int, tuple[AcceptanceSignal, ...]]) -> Counter[str]:
    return Counter(
        str(signal.details.get("scenario_family", "UNCLASSIFIED_FLOW_RESPONSE_SCENARIO"))
        for signals in signals_by_time_ns.values()
        for signal in signals
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
    require_retest_contraction: bool = True,
) -> AcceptanceSignalBundle:
    """Build immutable future-free flow-response scenarios under the V2 timing contract."""

    del require_retest_contraction
    auction_config.validate()
    if tick <= 0 or fee_rate < 0 or minimum_net_reward_risk <= 0:
        raise ValueError("invalid cost or reward-risk contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")
    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("ten-second timestamps must be unique and increasing")
    _validate_price_frame(data)

    features = (
        causal_flow_response_frame(data, tick=tick, config=auction_config.response)
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
        if not isfinite(atr) or atr <= 0.0:
            raise RuntimeError("five-minute context exposed a non-positive ATR")

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
                        "implementation_revision": IMPLEMENTATION_REVISION,
                    }
                )
                pending = None
            elif _observable_feature_row(feature):
                if pending.absorption_position is None:
                    full_post_interaction_window = (
                        position >= pending.armed_position + response_window
                    )
                    if (
                        full_post_interaction_window
                        and _state_is(
                            feature,
                            state=FlowResponseState.INITIATIVE_RESPONSE,
                            direction=pending.outward,
                        )
                        and _outside_boundary(
                            float(row["close"]),
                            pending.boundary.level,
                            pending.outward,
                        )
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
                            response_window=response_window,
                            family=INITIATIVE_FAMILY,
                            symbol=symbol,
                            instrument_id=instrument_id,
                        )
                        if rejection is not None:
                            rejected.append(rejection)
                            diagnostics[str(rejection["reason"])] += 1
                        else:
                            assert signal is not None
                            signals.setdefault(timestamp_ns, []).append(signal)
                            diagnostics["TRADEABLE_FLOW_RESPONSE_INITIATIVE"] += 1
                        pending = None
                    elif (
                        full_post_interaction_window
                        and _state_is(
                            feature,
                            state=FlowResponseState.ABSORBED_RESPONSE,
                            direction=pending.outward,
                        )
                        and _inside_or_reclaimed(
                            float(row["close"]),
                            pending.boundary.level,
                            pending.outward,
                        )
                    ):
                        pending.absorption_position = position
                        pending.absorption_time_ns = timestamp_ns
                        pending.absorption_high = float(row["high"])
                        pending.absorption_low = float(row["low"])
                        pending.absorption_sweep_high = pending.sweep_high
                        pending.absorption_sweep_low = pending.sweep_low
                        pending.expiry_position = position + auction_config.reversal_expiry_bars
                        diagnostics["OUTWARD_FLOW_RESPONSE_ABSORBED"] += 1
                else:
                    assert pending.absorption_high is not None
                    assert pending.absorption_low is not None
                    separate_opposite_window = (
                        position >= pending.absorption_position + response_window
                    )
                    breaks_reclaim = (
                        float(row["close"]) < pending.absorption_low
                        if pending.outward > 0
                        else float(row["close"]) > pending.absorption_high
                    )
                    if (
                        separate_opposite_window
                        and _state_is(
                            feature,
                            state=FlowResponseState.INITIATIVE_RESPONSE,
                            direction=-pending.outward,
                        )
                        and breaks_reclaim
                        and _inside_or_reclaimed(
                            float(row["close"]),
                            pending.boundary.level,
                            pending.outward,
                        )
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
                            response_window=response_window,
                            family=ABSORPTION_FAMILY,
                            symbol=symbol,
                            instrument_id=instrument_id,
                        )
                        if rejection is not None:
                            rejected.append(rejection)
                            diagnostics[str(rejection["reason"])] += 1
                        else:
                            assert signal is not None
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

    if pending is not None:
        diagnostics["FLOW_RESPONSE_INTERACTION_UNRESOLVED_AT_DATA_END"] += 1
        rejected.append(
            {
                "scenario_id": pending.scenario_id,
                "symbol": symbol,
                "boundary_id": pending.boundary.level_id,
                "reason": "FLOW_RESPONSE_INTERACTION_UNRESOLVED_AT_DATA_END",
                "armed_time_ns": pending.armed_time_ns,
                "absorption_time_ns": pending.absorption_time_ns,
                "implementation_revision": IMPLEMENTATION_REVISION,
            }
        )

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
    family_counts = _signal_counts(immutable)
    if family_counts[INITIATIVE_FAMILY] != diagnostics.get(
        "TRADEABLE_FLOW_RESPONSE_INITIATIVE", 0
    ):
        raise RuntimeError("initiative signal diagnostic count mismatch")
    if family_counts[ABSORPTION_FAMILY] != diagnostics.get(
        "TRADEABLE_FLOW_RESPONSE_ABSORPTION_REVERSAL", 0
    ):
        raise RuntimeError("absorption signal diagnostic count mismatch")

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
