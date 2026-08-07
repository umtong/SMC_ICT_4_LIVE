"""Safe-observability and complete-event-chain revision of delayed reacceptance.

Revision V3 repairs only implementation and research-evidence boundaries exposed before economic
promotion:

* unobservable or non-finite flow-response rows cannot be cast to a direction;
* target-touch classification uses the exact external-level enum;
* the omitted ``INTERACTION_ARMED -> INITIAL_OUTWARD_RESPONSE`` transition is restored; and
* every emitted logic event is stamped with the V3 implementation revision.

Signal timing, thresholds, expiry, target, stop, cost geometry, position sizing and native
NautilusTrader execution are unchanged from the predeclared delayed-reacceptance candidate.
"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    AcceptanceLogicEvent,
    AcceptanceSignal,
    AcceptanceSignalBundle,
)
import aggtrade_delayed_reacceptance_signals as base
from aggtrade_flow_response import FlowResponseState
from range_fvg_logic import LevelKind


IMPLEMENTATION_REVISION = (
    "CAUSAL_DELAYED_BOUNDARY_REACCEPTANCE_V3_COMPLETE_EVENT_CHAIN"
)
base.IMPLEMENTATION_REVISION = IMPLEMENTATION_REVISION


def _observable_feature(row: pd.Series) -> bool:
    if str(row["flow_response_state"]) == FlowResponseState.UNOBSERVABLE.value:
        return False
    return all(
        isfinite(float(row[name]))
        for name in base._REQUIRED_FEATURE_COLUMNS
        if name != "flow_response_state"
    )


def _initial_response_qualifies(
    feature: pd.Series,
    *,
    outward: int,
    close: float,
    boundary: float,
    initial_mode: str,
) -> bool:
    if initial_mode not in base.INITIAL_MODES:
        raise ValueError(f"invalid initial response mode: {initial_mode!r}")
    if not _observable_feature(feature):
        return False
    direction_matches = int(np.sign(float(feature["flow_direction"]))) == outward
    if not direction_matches or not base._outside(close, boundary, outward):
        return False
    if initial_mode == base.ABLATION_INITIAL_MODE:
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
    if not _observable_feature(feature):
        return False
    if str(feature["flow_response_state"]) != FlowResponseState.INITIATIVE_RESPONSE.value:
        return False
    if int(np.sign(float(feature["flow_direction"]))) != outward:
        return False
    if not base._outside(close, boundary, outward):
        return False
    return close > counter_high if outward > 0 else close < counter_low


def _target_was_touched(
    data: pd.DataFrame,
    *,
    start_position: int,
    end_position: int,
    target: Any,
) -> bool:
    if end_position < start_position:
        return False
    observed = data.iloc[start_position : end_position + 1]
    if target.kind is LevelKind.HIGH:
        return float(observed["high"].max()) >= float(target.level)
    if target.kind is LevelKind.LOW:
        return float(observed["low"].min()) <= float(target.level)
    raise RuntimeError(f"unknown external target kind: {target.kind!r}")


def _stamp_event(event: AcceptanceLogicEvent) -> AcceptanceLogicEvent:
    return replace(
        event,
        details={
            **dict(event.details),
            "implementation_revision": IMPLEMENTATION_REVISION,
        },
    )


def _complete_event_chain(signal: AcceptanceSignal) -> AcceptanceSignal:
    if len(signal.events) != 3:
        raise RuntimeError(
            "delayed reacceptance V3 expected the prior three-event evidence shape"
        )
    armed_event, reclaim_event, final_event = signal.events
    observed_types = tuple(event.event_type for event in signal.events)
    expected_types = (
        "EXTERNAL_LIQUIDITY_INTERACTION_ARMED",
        "INITIAL_RESPONSE_RECLAIMED",
        "DELAYED_OUTWARD_REACCEPTANCE_CONFIRMED",
    )
    if observed_types != expected_types:
        raise RuntimeError(
            "delayed reacceptance V3 cannot repair an unknown evidence shape: "
            f"{observed_types!r}"
        )
    if (
        armed_event.previous_state != "IDLE"
        or armed_event.next_state != "INTERACTION_ARMED"
        or reclaim_event.previous_state != "INITIAL_OUTWARD_RESPONSE"
        or reclaim_event.next_state != "BOUNDARY_RECLAIMED"
        or final_event.previous_state != "BOUNDARY_RECLAIMED"
        or final_event.next_state != "CONFIRMED"
    ):
        raise RuntimeError("delayed reacceptance V3 observed an unexpected state contract")

    initial_time_ns = int(signal.details["initial_response_time_ns"])
    if not (
        int(armed_event.observed_time_ns)
        < initial_time_ns
        < int(reclaim_event.observed_time_ns)
        <= int(final_event.observed_time_ns)
    ):
        raise RuntimeError(
            "delayed reacceptance V3 initial-response evidence is not causally ordered"
        )

    initial_event = AcceptanceLogicEvent(
        scenario_id=signal.scenario_id,
        symbol=signal.symbol,
        instrument_id=signal.instrument_id,
        event_type="INITIAL_OUTWARD_RESPONSE_CONFIRMED_NO_ENTRY",
        event_time_ns=initial_time_ns,
        observed_time_ns=initial_time_ns,
        previous_state="INTERACTION_ARMED",
        next_state="INITIAL_OUTWARD_RESPONSE",
        reason_code="STRICTLY_POST_INTERACTION_OUTWARD_RESPONSE_OUTSIDE_BOUNDARY_NO_ENTRY",
        reference_price=signal.boundary_level,
        details={
            "boundary_id": signal.boundary_id,
            "boundary_source": signal.boundary_source,
            "outward": signal.direction,
            "initial_mode": signal.details["initial_mode"],
            "initial_response_high": signal.details["initial_response_high"],
            "initial_response_low": signal.details["initial_response_low"],
            "initial_feature": dict(
                reclaim_event.details.get("initial_feature", {})
            ),
            "entry_deferred": True,
            "implementation_revision": IMPLEMENTATION_REVISION,
        },
    )
    return replace(
        signal,
        events=(
            _stamp_event(armed_event),
            initial_event,
            _stamp_event(reclaim_event),
            _stamp_event(final_event),
        ),
        details={
            **dict(signal.details),
            "implementation_revision": IMPLEMENTATION_REVISION,
            "event_chain_contract": (
                "IDLE->INTERACTION_ARMED->INITIAL_OUTWARD_RESPONSE"
                "->BOUNDARY_RECLAIMED->CONFIRMED"
            ),
        },
    )


base._observable_feature = _observable_feature
base._initial_response_qualifies = _initial_response_qualifies
base._reacceptance_qualifies = _reacceptance_qualifies
base._target_was_touched = _target_was_touched


def build_delayed_reacceptance_signals(**kwargs: Any) -> AcceptanceSignalBundle:
    bundle = base.build_delayed_reacceptance_signals(**kwargs)
    completed = {
        timestamp_ns: tuple(_complete_event_chain(signal) for signal in signals)
        for timestamp_ns, signals in bundle.signals_by_time_ns.items()
    }
    return AcceptanceSignalBundle(
        signals_by_time_ns=completed,
        diagnostics=bundle.diagnostics,
        rejected_scenarios=bundle.rejected_scenarios,
    )


ABLATION_INITIAL_MODE = base.ABLATION_INITIAL_MODE
BASE_INITIAL_MODE = base.BASE_INITIAL_MODE
DelayedReacceptanceConfig = base.DelayedReacceptanceConfig
REACCEPTANCE_FAMILY = base.REACCEPTANCE_FAMILY


__all__ = [
    "ABLATION_INITIAL_MODE",
    "BASE_INITIAL_MODE",
    "DelayedReacceptanceConfig",
    "IMPLEMENTATION_REVISION",
    "REACCEPTANCE_FAMILY",
    "build_delayed_reacceptance_signals",
]
