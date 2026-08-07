"""Causal delayed reacceptance continuation at completed external liquidity.

The clean V3 experiment showed two different outcomes after an outward initiative response:

* three trades never developed in the expected direction; and
* two shorts first reclaimed the interacted boundary and stopped, then later reached the original
  completed external target.

This successor changes the event order rather than widening risk or fitting a target:

1. interact with an already-completed 4-hour/day/week external level;
2. observe a strictly post-interaction outward response outside that boundary;
3. do not trade; wait until price closes back through the boundary and freezes the counter-auction;
4. require a separate strictly post-reclaim outward initiative response which reaccepts the boundary
   and breaks the frozen counter-auction extreme; and
5. enter only after that completed second acceptance.

The structural stop is beyond the observed counter-auction extreme, and the target remains the
nearest active completed external level in the original direction.  Every input is a completed exact
10-second aggregate-trade bucket.  No future outcome, order, fill, PnL, model score, fitted R target,
or asset-specific parameter enters detection.  NautilusTrader remains the sole execution engine.
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
from range_fvg_logic import ExternalLevel, FiveMinuteBar


REACCEPTANCE_FAMILY = "DELAYED_BOUNDARY_REACCEPTANCE_CONTINUATION"
IMPLEMENTATION_REVISION = "CAUSAL_DELAYED_BOUNDARY_REACCEPTANCE_V1"
BASE_INITIAL_MODE = "initiative_required"
ABLATION_INITIAL_MODE = "remove_initial_initiative"
INITIAL_MODES = frozenset((BASE_INITIAL_MODE, ABLATION_INITIAL_MODE))


@dataclass(frozen=True, slots=True)
class DelayedReacceptanceConfig:
    response: FlowResponseConfig = field(default_factory=FlowResponseConfig)
    setup_expiry_minutes: int = 240

    def validate(self) -> None:
        self.response.validate()
        if self.setup_expiry_minutes <= 0:
            raise ValueError("setup expiry must be positive")
        if self.setup_expiry_minutes % 10 != 0:
            raise ValueError("setup expiry must align to ten-minute blocks")

    @property
    def setup_expiry_bars(self) -> int:
        return self.setup_expiry_minutes * 6


@dataclass(slots=True)
class _PendingReacceptance:
    scenario_id: str
    boundary: ExternalLevel
    outward: int
    armed_position: int
    armed_time_ns: int
    expiry_position: int
    initial_position: int | None = None
    initial_time_ns: int | None = None
    initial_high: float | None = None
    initial_low: float | None = None
    target: ExternalLevel | None = None
    reclaim_position: int | None = None
    reclaim_time_ns: int | None = None
    counter_high: float | None = None
    counter_low: float | None = None


_REQUIRED_PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "signed_volume",
    "trade_count",
)
_REQUIRED_FEATURE_COLUMNS = (
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


def _validate_input(data: pd.DataFrame, features: pd.DataFrame) -> None:
    validate_exact_ten_second_cadence(data)
    missing = [name for name in _REQUIRED_PRICE_COLUMNS if name not in data.columns]
    if missing:
        raise KeyError(f"delayed reacceptance input is missing columns: {missing}")
    numeric = data.loc[:, _REQUIRED_PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("delayed reacceptance input contains non-finite values")
    if (numeric["volume"] <= 0.0).any() or (numeric["trade_count"] <= 0.0).any():
        raise ValueError("delayed reacceptance input requires positive activity")
    invalid = (
        (numeric["high"] < numeric[["open", "close"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close"]].min(axis=1))
        | (numeric["high"] < numeric["low"])
    )
    if bool(invalid.any()):
        raise ValueError("delayed reacceptance input contains invalid OHLC geometry")
    if not features.index.equals(data.index):
        raise ValueError("flow-response features must have the exact input index")
    missing_features = [name for name in _REQUIRED_FEATURE_COLUMNS if name not in features.columns]
    if missing_features:
        raise KeyError(f"flow-response features are missing columns: {missing_features}")


def _feature_details(row: pd.Series) -> dict[str, Any]:
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


def _outside(close: float, boundary: float, outward: int) -> bool:
    return close > boundary if outward > 0 else close < boundary


def _inside_or_reclaimed(close: float, boundary: float, outward: int) -> bool:
    return close <= boundary if outward > 0 else close >= boundary


def _initial_response_qualifies(
    feature: pd.Series,
    *,
    outward: int,
    close: float,
    boundary: float,
    initial_mode: str,
) -> bool:
    if initial_mode not in INITIAL_MODES:
        raise ValueError(f"invalid initial response mode: {initial_mode!r}")
    direction_matches = int(np.sign(float(feature["flow_direction"]))) == outward
    if not direction_matches or not _outside(close, boundary, outward):
        return False
    if initial_mode == ABLATION_INITIAL_MODE:
        return True
    return str(feature["flow_response_state"]) == FlowResponseState.INITIATIVE_RESPONSE.value


def _reacceptance_qualifies(
    feature: pd.Series,
    *,
    outward: int,
    close: float,
    boundary: float,
    counter_high: float,
    counter_low: float,
) -> bool:
    if str(feature["flow_response_state"]) != FlowResponseState.INITIATIVE_RESPONSE.value:
        return False
    if int(np.sign(float(feature["flow_direction"]))) != outward:
        return False
    if not _outside(close, boundary, outward):
        return False
    return close > counter_high if outward > 0 else close < counter_low


def _window_extreme(
    data: pd.DataFrame,
    *,
    end_position: int,
    window: int,
) -> tuple[float, float, int]:
    start = end_position - window + 1
    if start < 0:
        raise RuntimeError("response window begins before data")
    observed = data.iloc[start : end_position + 1]
    high = float(observed["high"].max())
    low = float(observed["low"].min())
    return high, low, start


def _target_was_touched(
    data: pd.DataFrame,
    *,
    start_position: int,
    end_position: int,
    target: ExternalLevel,
) -> bool:
    if end_position < start_position:
        return False
    observed = data.iloc[start_position : end_position + 1]
    if target.kind.value == "HIGH":
        return float(observed["high"].max()) >= target.level
    return float(observed["low"].min()) <= target.level


def _structural_stop(
    *,
    direction: int,
    entry: float,
    boundary: float,
    counter_high: float,
    counter_low: float,
    atr: float,
) -> tuple[float, float, str]:
    minimum_distance = 0.10 * atr
    buffer = 0.03 * atr
    if direction > 0:
        reference = min(boundary, counter_low)
        return (
            min(reference - buffer, entry - minimum_distance),
            reference,
            "COUNTER_AUCTION_LOW_OR_BOUNDARY",
        )
    reference = max(boundary, counter_high)
    return (
        max(reference + buffer, entry + minimum_distance),
        reference,
        "COUNTER_AUCTION_HIGH_OR_BOUNDARY",
    )


def _rejection(
    pending: _PendingReacceptance,
    *,
    symbol: str,
    reason: str,
    timestamp_ns: int,
    initial_mode: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scenario_id": pending.scenario_id,
        "symbol": symbol,
        "boundary_id": pending.boundary.level_id,
        "reason": reason,
        "event_time_ns": timestamp_ns,
        "initial_mode": initial_mode,
        "implementation_revision": IMPLEMENTATION_REVISION,
        "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
        **(details or {}),
    }


def _build_signal(
    *,
    data: pd.DataFrame,
    features: pd.DataFrame,
    pending: _PendingReacceptance,
    position: int,
    atr: float,
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    initial_mode: str,
) -> tuple[AcceptanceSignal | None, dict[str, Any] | None]:
    assert pending.initial_position is not None
    assert pending.initial_time_ns is not None
    assert pending.initial_high is not None
    assert pending.initial_low is not None
    assert pending.target is not None
    assert pending.reclaim_position is not None
    assert pending.reclaim_time_ns is not None
    assert pending.counter_high is not None
    assert pending.counter_low is not None

    timestamp_ns = int(data.index[position].as_unit("ns").value)
    entry = float(data.iloc[position]["close"])
    stop, stop_reference, stop_source = _structural_stop(
        direction=pending.outward,
        entry=entry,
        boundary=pending.boundary.level,
        counter_high=pending.counter_high,
        counter_low=pending.counter_low,
        atr=atr,
    )
    noise = float(features.iloc[position]["causal_noise_reserve"])
    geometry = _cost_geometry(
        direction=pending.outward,
        entry=entry,
        stop=stop,
        target=pending.target.level,
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=noise,
    )
    base = _rejection(
        pending,
        symbol=symbol,
        reason="",
        timestamp_ns=timestamp_ns,
        initial_mode=initial_mode,
        details={
            "target_id": pending.target.level_id,
            "scenario_family": REACCEPTANCE_FAMILY,
        },
    )
    base.pop("reason")
    if geometry is None:
        return None, {**base, "reason": "INVALID_COST_AFTER_EXTERNAL_GEOMETRY"}
    loss, gain, net_rr = geometry
    if net_rr < minimum_net_reward_risk:
        return None, {
            **base,
            "reason": "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
            "net_reward_risk": net_rr,
        }

    response_window = int(
        features.attrs.get("response_window_bars", 3)
    )
    reaccept_high, reaccept_low, reaccept_start = _window_extreme(
        data,
        end_position=position,
        window=response_window,
    )
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
            "initial_mode": initial_mode,
            "implementation_revision": IMPLEMENTATION_REVISION,
        },
    )
    reclaim_event = AcceptanceLogicEvent(
        scenario_id=pending.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="INITIAL_RESPONSE_RECLAIMED",
        event_time_ns=pending.reclaim_time_ns,
        observed_time_ns=pending.reclaim_time_ns,
        previous_state="INITIAL_OUTWARD_RESPONSE",
        next_state="BOUNDARY_RECLAIMED",
        reason_code="PRICE_CLOSED_BACK_THROUGH_COMPLETED_BOUNDARY",
        reference_price=pending.boundary.level,
        details={
            "initial_response_time_ns": pending.initial_time_ns,
            "initial_response_high": pending.initial_high,
            "initial_response_low": pending.initial_low,
            "counter_high_at_reclaim": pending.counter_high,
            "counter_low_at_reclaim": pending.counter_low,
            "initial_feature": _feature_details(
                features.iloc[pending.initial_position]
            ),
        },
    )
    final_event = AcceptanceLogicEvent(
        scenario_id=pending.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="DELAYED_OUTWARD_REACCEPTANCE_CONFIRMED",
        event_time_ns=timestamp_ns,
        observed_time_ns=timestamp_ns,
        previous_state="BOUNDARY_RECLAIMED",
        next_state="CONFIRMED",
        reason_code="SEPARATE_OUTWARD_INITIATIVE_RESPONSE_BROKE_COUNTER_AUCTION",
        reference_price=entry,
        details={
            "response_window_start_position": reaccept_start,
            "response_window_end_position": position,
            "response_window_high": reaccept_high,
            "response_window_low": reaccept_low,
            "counter_high": pending.counter_high,
            "counter_low": pending.counter_low,
            "reacceptance_feature": _feature_details(features.iloc[position]),
            "initial_mode": initial_mode,
        },
    )
    return AcceptanceSignal(
        scenario_id=pending.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        direction=pending.outward,
        signal_index=position,
        signal_time_ns=timestamp_ns,
        boundary_id=pending.boundary.level_id,
        boundary_source=pending.boundary.source.value,
        boundary_level=pending.boundary.level,
        target_id=pending.target.level_id,
        target_source=pending.target.source.value,
        external_target=pending.target.level,
        entry_reference=entry,
        structural_stop=stop,
        atr=atr,
        causal_stop_slippage_reserve=noise,
        expected_loss_per_unit=loss,
        expected_gain_per_unit=gain,
        net_reward_risk=net_rr,
        armed_time_ns=pending.armed_time_ns,
        retest_time_ns=pending.reclaim_time_ns,
        retest_high=pending.counter_high,
        retest_low=pending.counter_low,
        events=(armed_event, reclaim_event, final_event),
        details={
            "scenario_family": REACCEPTANCE_FAMILY,
            "implementation_revision": IMPLEMENTATION_REVISION,
            "initial_mode": initial_mode,
            "initial_response_time_ns": pending.initial_time_ns,
            "initial_response_high": pending.initial_high,
            "initial_response_low": pending.initial_low,
            "reclaim_time_ns": pending.reclaim_time_ns,
            "counter_high": pending.counter_high,
            "counter_low": pending.counter_low,
            "reacceptance_window_start_position": reaccept_start,
            "reacceptance_window_end_position": position,
            "stop_reference": stop_reference,
            "stop_reference_source": stop_source,
            "stop_order_tag": "COUNTER_AUCTION_OR_BOUNDARY_INVALIDATION",
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_10_SECONDS",
            **_feature_details(features.iloc[position]),
        },
    ), None


def build_delayed_reacceptance_signals(
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
    reacceptance_config: DelayedReacceptanceConfig = DelayedReacceptanceConfig(),
    flow_response_features: pd.DataFrame | None = None,
    initial_mode: str = BASE_INITIAL_MODE,
    require_retest_contraction: bool = True,
) -> AcceptanceSignalBundle:
    """Build immutable future-free delayed reacceptance signals."""

    del require_retest_contraction
    reacceptance_config.validate()
    if initial_mode not in INITIAL_MODES:
        raise ValueError(f"invalid initial response mode: {initial_mode!r}")
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost or reward-risk contract")
    features = (
        causal_flow_response_frame(
            data,
            tick=tick,
            config=reacceptance_config.response,
        )
        if flow_response_features is None
        else flow_response_features.copy()
    )
    features.attrs["response_window_bars"] = reacceptance_config.response.response_window_bars
    _validate_input(data, features)

    signals: dict[int, list[AcceptanceSignal]] = {}
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    consumed: set[str] = set()
    pending: _PendingReacceptance | None = None
    scenario_counter = 0
    window = reacceptance_config.response.response_window_bars

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
            if position > pending.expiry_position:
                diagnostics["DELAYED_REACCEPTANCE_SETUP_TIMEOUT"] += 1
                rejected.append(
                    _rejection(
                        pending,
                        symbol=symbol,
                        reason="DELAYED_REACCEPTANCE_SETUP_TIMEOUT",
                        timestamp_ns=timestamp_ns,
                        initial_mode=initial_mode,
                    )
                )
                pending = None
            elif pending.initial_position is None:
                full_post_interaction_window = position >= pending.armed_position + window
                if full_post_interaction_window and _initial_response_qualifies(
                    feature,
                    outward=pending.outward,
                    close=float(row["close"]),
                    boundary=pending.boundary.level,
                    initial_mode=initial_mode,
                ):
                    target = _select_active_target(
                        target_levels,
                        direction=pending.outward,
                        entry=float(row["close"]),
                        excluded_level_id=pending.boundary.level_id,
                        consumed=consumed,
                    )
                    if target is None:
                        diagnostics["NO_ACTIVE_COMPLETED_EXTERNAL_TARGET"] += 1
                        rejected.append(
                            _rejection(
                                pending,
                                symbol=symbol,
                                reason="NO_ACTIVE_COMPLETED_EXTERNAL_TARGET",
                                timestamp_ns=timestamp_ns,
                                initial_mode=initial_mode,
                            )
                        )
                        pending = None
                    else:
                        initial_high, initial_low, _ = _window_extreme(
                            data,
                            end_position=position,
                            window=window,
                        )
                        pending.initial_position = position
                        pending.initial_time_ns = timestamp_ns
                        pending.initial_high = initial_high
                        pending.initial_low = initial_low
                        pending.target = target
                        diagnostics["INITIAL_OUTWARD_RESPONSE_CONFIRMED_NO_ENTRY"] += 1
            elif pending.reclaim_position is None:
                assert pending.target is not None
                if _target_was_touched(
                    data,
                    start_position=position,
                    end_position=position,
                    target=pending.target,
                ):
                    diagnostics["TARGET_REACHED_BEFORE_RECLAIM"] += 1
                    rejected.append(
                        _rejection(
                            pending,
                            symbol=symbol,
                            reason="TARGET_REACHED_BEFORE_RECLAIM",
                            timestamp_ns=timestamp_ns,
                            initial_mode=initial_mode,
                            details={"target_id": pending.target.level_id},
                        )
                    )
                    pending = None
                elif _inside_or_reclaimed(
                    float(row["close"]),
                    pending.boundary.level,
                    pending.outward,
                ):
                    observed = data.iloc[pending.initial_position + 1 : position + 1]
                    if observed.empty:
                        raise RuntimeError("reclaim occurred without a counter-auction observation")
                    pending.reclaim_position = position
                    pending.reclaim_time_ns = timestamp_ns
                    pending.counter_high = float(observed["high"].max())
                    pending.counter_low = float(observed["low"].min())
                    diagnostics["BOUNDARY_RECLAIMED_AFTER_INITIAL_RESPONSE"] += 1
            else:
                assert pending.target is not None
                assert pending.reclaim_position is not None
                assert pending.counter_high is not None
                assert pending.counter_low is not None
                full_post_reclaim_window = position >= pending.reclaim_position + window
                response_start = position - window + 1
                prior_counter = data.iloc[
                    pending.initial_position + 1 : max(
                        pending.reclaim_position + 1,
                        response_start,
                    )
                ]
                if not prior_counter.empty:
                    pending.counter_high = max(
                        pending.counter_high,
                        float(prior_counter["high"].max()),
                    )
                    pending.counter_low = min(
                        pending.counter_low,
                        float(prior_counter["low"].min()),
                    )
                if _target_was_touched(
                    data,
                    start_position=position,
                    end_position=position,
                    target=pending.target,
                ):
                    diagnostics["TARGET_REACHED_BEFORE_REACCEPTANCE"] += 1
                    rejected.append(
                        _rejection(
                            pending,
                            symbol=symbol,
                            reason="TARGET_REACHED_BEFORE_REACCEPTANCE",
                            timestamp_ns=timestamp_ns,
                            initial_mode=initial_mode,
                            details={"target_id": pending.target.level_id},
                        )
                    )
                    pending = None
                elif full_post_reclaim_window and _reacceptance_qualifies(
                    feature,
                    outward=pending.outward,
                    close=float(row["close"]),
                    boundary=pending.boundary.level,
                    counter_high=pending.counter_high,
                    counter_low=pending.counter_low,
                ):
                    response_high, response_low, response_start = _window_extreme(
                        data,
                        end_position=position,
                        window=window,
                    )
                    if (
                        pending.outward > 0
                        and response_high >= pending.target.level
                    ) or (
                        pending.outward < 0
                        and response_low <= pending.target.level
                    ):
                        diagnostics["TARGET_REACHED_INSIDE_REACCEPTANCE_WINDOW"] += 1
                        rejected.append(
                            _rejection(
                                pending,
                                symbol=symbol,
                                reason="TARGET_REACHED_INSIDE_REACCEPTANCE_WINDOW",
                                timestamp_ns=timestamp_ns,
                                initial_mode=initial_mode,
                                details={"target_id": pending.target.level_id},
                            )
                        )
                        pending = None
                    else:
                        signal, rejection = _build_signal(
                            data=data,
                            features=features,
                            pending=pending,
                            position=position,
                            atr=atr,
                            symbol=symbol,
                            instrument_id=instrument_id,
                            tick=tick,
                            fee_rate=fee_rate,
                            minimum_net_reward_risk=minimum_net_reward_risk,
                            initial_mode=initial_mode,
                        )
                        if rejection is not None:
                            rejected.append(rejection)
                            diagnostics[str(rejection["reason"])] += 1
                        else:
                            assert signal is not None
                            signals.setdefault(timestamp_ns, []).append(signal)
                            diagnostics["TRADEABLE_DELAYED_REACCEPTANCE"] += 1
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
        pending = _PendingReacceptance(
            scenario_id=f"delayed-reaccept-{symbol.lower()}-{scenario_counter:06d}",
            boundary=boundary,
            outward=outward,
            armed_position=position,
            armed_time_ns=timestamp_ns,
            expiry_position=position + reacceptance_config.setup_expiry_bars,
        )
        diagnostics["DELAYED_REACCEPTANCE_INTERACTION_ARMED"] += 1

    if pending is not None:
        diagnostics["DELAYED_REACCEPTANCE_UNRESOLVED_AT_DATA_END"] += 1
        rejected.append(
            _rejection(
                pending,
                symbol=symbol,
                reason="DELAYED_REACCEPTANCE_UNRESOLVED_AT_DATA_END",
                timestamp_ns=int(data.index[-1].as_unit("ns").value),
                initial_mode=initial_mode,
            )
        )

    immutable = {
        timestamp: tuple(
            sorted(items, key=lambda item: (item.net_reward_risk, item.symbol), reverse=True)
        )
        for timestamp, items in signals.items()
    }
    emitted = sum(len(values) for values in immutable.values())
    if emitted != diagnostics.get("TRADEABLE_DELAYED_REACCEPTANCE", 0):
        raise RuntimeError("delayed reacceptance signal diagnostic mismatch")
    return AcceptanceSignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "ABLATION_INITIAL_MODE",
    "BASE_INITIAL_MODE",
    "DelayedReacceptanceConfig",
    "IMPLEMENTATION_REVISION",
    "REACCEPTANCE_FAMILY",
    "build_delayed_reacceptance_signals",
]
