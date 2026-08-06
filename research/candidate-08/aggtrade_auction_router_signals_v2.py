"""Causal implementation refinement for candidate-08 auction-router v1.

The original router froze the failed-auction sweep extreme at the reclaim bar. A later confirmation
bar can extend the sweep intrabar and still close with a valid inward displacement. Because entry is
submitted only after that completed confirmation bar, every high and low through confirmation is
already observable and must be included in the structural invalidation level.

This module preserves the original state machine and changes no scenario threshold. It post-validates
only emitted failed-auction signals using rows from reclaim through the signal timestamp, widens the
stop to the complete observed sweep when required, and recalculates the same cost and reward-risk
contract. Signals which no longer meet the already-fixed cost-after gate are rejected rather than
being traded with an understated stop.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    AcceptanceLogicEvent,
    AcceptanceSignal,
    AcceptanceSignalBundle,
    _cost_geometry,
)
from aggtrade_auction_router_signals import (
    FAILED_AUCTION_FAMILY,
    INITIATIVE_FAMILY,
    _failed_auction_stop,
    build_auction_router_signals as _build_v1,
)


IMPLEMENTATION_REVISION = "FAILED_AUCTION_FULL_OBSERVED_SWEEP_THROUGH_CONFIRMATION_V2"
_V1_TRADEABLE_DIAGNOSTIC = "TRADEABLE_FAILED_AUCTION_REVERSAL"


def _family(signal: AcceptanceSignal) -> str:
    return str(signal.details.get("scenario_family", "UNCLASSIFIED_AUCTION_SCENARIO"))


def _observed_sweep_through_confirmation(
    data: pd.DataFrame,
    signal: AcceptanceSignal,
) -> tuple[float, float, int, int]:
    """Return causal high/low from reclaim through the completed confirmation bar."""

    if not 0 <= int(signal.signal_index) < len(data.index):
        raise RuntimeError(
            f"signal index outside ten-second frame: {signal.scenario_id} index={signal.signal_index}"
        )
    if "sweep_high" not in signal.details or "sweep_low" not in signal.details:
        raise RuntimeError(
            f"failed-auction signal lacks reclaim-time sweep state: {signal.scenario_id}"
        )

    timestamps_ns = data.index.as_unit("ns").asi8
    confirmation_position = int(signal.signal_index)
    confirmation_time_ns = int(timestamps_ns[confirmation_position])
    if confirmation_time_ns != int(signal.signal_time_ns):
        raise RuntimeError(
            "signal index/timestamp contract changed: "
            f"{signal.scenario_id} index_time={confirmation_time_ns} "
            f"signal_time={signal.signal_time_ns}"
        )

    reclaim_time_ns = int(signal.retest_time_ns)
    reclaim_position = int(np.searchsorted(timestamps_ns, reclaim_time_ns, side="left"))
    if (
        reclaim_position >= len(timestamps_ns)
        or int(timestamps_ns[reclaim_position]) != reclaim_time_ns
    ):
        raise RuntimeError(
            "failed-auction reclaim timestamp is not an exact completed ten-second row: "
            f"{signal.scenario_id} reclaim_time={reclaim_time_ns}"
        )
    if reclaim_position > confirmation_position:
        raise RuntimeError(
            f"failed-auction reclaim occurs after confirmation: {signal.scenario_id}"
        )

    observed = data.iloc[reclaim_position : confirmation_position + 1]
    observed_high = float(pd.to_numeric(observed["high"], errors="coerce").max())
    observed_low = float(pd.to_numeric(observed["low"], errors="coerce").min())
    initial_high = float(signal.details["sweep_high"])
    initial_low = float(signal.details["sweep_low"])
    sweep_high = max(initial_high, observed_high)
    sweep_low = min(initial_low, observed_low)
    if not all(isfinite(value) for value in (sweep_high, sweep_low)):
        raise RuntimeError(f"non-finite failed-auction sweep: {signal.scenario_id}")
    return sweep_high, sweep_low, reclaim_position, confirmation_position


def _refined_events(
    events: tuple[AcceptanceLogicEvent, ...],
    *,
    sweep_high: float,
    sweep_low: float,
) -> tuple[AcceptanceLogicEvent, ...]:
    refined: list[AcceptanceLogicEvent] = []
    for event in events:
        if event.event_type == "INWARD_DISPLACEMENT_CONFIRMED":
            refined.append(
                replace(
                    event,
                    details={
                        **event.details,
                        "sweep_high_observed_through_confirmation": sweep_high,
                        "sweep_low_observed_through_confirmation": sweep_low,
                        "implementation_revision": IMPLEMENTATION_REVISION,
                    },
                )
            )
        else:
            refined.append(event)
    return tuple(refined)


def _refine_failed_auction_signal(
    *,
    data: pd.DataFrame,
    signal: AcceptanceSignal,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
) -> tuple[AcceptanceSignal | None, dict[str, Any] | None, bool]:
    """Return the causally corrected signal, optional rejection, and whether the extreme changed."""

    sweep_high, sweep_low, reclaim_position, confirmation_position = (
        _observed_sweep_through_confirmation(data, signal)
    )
    old_high = float(signal.details["sweep_high"])
    old_low = float(signal.details["sweep_low"])
    changed = sweep_high > old_high or sweep_low < old_low

    stop, stop_reference, stop_reference_source = _failed_auction_stop(
        direction=int(signal.direction),
        entry=float(signal.entry_reference),
        sweep_high=sweep_high,
        sweep_low=sweep_low,
        atr=float(signal.atr),
    )
    geometry = _cost_geometry(
        direction=int(signal.direction),
        entry=float(signal.entry_reference),
        stop=stop,
        target=float(signal.external_target),
        fee_rate=float(fee_rate),
        tick=float(tick),
        stop_slippage_reserve=float(signal.causal_stop_slippage_reserve),
    )
    rejection_base = {
        "scenario_id": signal.scenario_id,
        "symbol": signal.symbol,
        "boundary_id": signal.boundary_id,
        "target_id": signal.target_id,
        "confirmation_time_ns": int(signal.signal_time_ns),
        "sweep_high_at_reclaim": old_high,
        "sweep_low_at_reclaim": old_low,
        "sweep_high_through_confirmation": sweep_high,
        "sweep_low_through_confirmation": sweep_low,
        "implementation_revision": IMPLEMENTATION_REVISION,
    }
    if geometry is None:
        return (
            None,
            {
                **rejection_base,
                "reason": "UPDATED_SWEEP_INVALID_COST_AFTER_EXTERNAL_GEOMETRY",
            },
            changed,
        )

    loss, gain, net_rr = geometry
    if net_rr < float(minimum_net_reward_risk):
        return (
            None,
            {
                **rejection_base,
                "reason": "UPDATED_SWEEP_INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET",
                "net_reward_risk": net_rr,
            },
            changed,
        )

    details = {
        **signal.details,
        "stop_reference": stop_reference,
        "stop_reference_source": stop_reference_source,
        "sweep_high_at_reclaim": old_high,
        "sweep_low_at_reclaim": old_low,
        "sweep_high": sweep_high,
        "sweep_low": sweep_low,
        "observed_sweep_reclaim_position": reclaim_position,
        "observed_sweep_confirmation_position": confirmation_position,
        "observed_sweep_start_time_ns": int(signal.retest_time_ns),
        "observed_sweep_end_time_ns": int(signal.signal_time_ns),
        "implementation_revision": IMPLEMENTATION_REVISION,
    }
    return (
        replace(
            signal,
            structural_stop=stop,
            expected_loss_per_unit=loss,
            expected_gain_per_unit=gain,
            net_reward_risk=net_rr,
            events=_refined_events(
                signal.events,
                sweep_high=sweep_high,
                sweep_low=sweep_low,
            ),
            details=details,
        ),
        None,
        changed,
    )


def _remove_v1_tradeable_diagnostic(diagnostics: Counter[str]) -> None:
    current = int(diagnostics.get(_V1_TRADEABLE_DIAGNOSTIC, 0))
    if current <= 0:
        raise RuntimeError(
            "failed-auction refinement removed a signal without a matching v1 tradeable count"
        )
    diagnostics[_V1_TRADEABLE_DIAGNOSTIC] = current - 1
    diagnostics["FAILED_AUCTION_REVERSAL_REMOVED_BY_SWEEP_REFINEMENT"] += 1


def build_auction_router_signals(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[Any, ...],
    snapshots: tuple[tuple[Any, ...], ...],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    require_retest_contraction: bool = True,
) -> AcceptanceSignalBundle:
    """Build v1 states, then enforce complete observed failed-auction invalidation geometry."""

    bundle = _build_v1(
        data=data,
        context_times=context_times,
        context_bars=context_bars,
        snapshots=snapshots,
        symbol=symbol,
        instrument_id=instrument_id,
        tick=tick,
        fee_rate=fee_rate,
        minimum_net_reward_risk=minimum_net_reward_risk,
        require_retest_contraction=require_retest_contraction,
    )
    diagnostics: Counter[str] = Counter(bundle.diagnostics)
    rejected = list(bundle.rejected_scenarios)
    signals: dict[int, tuple[AcceptanceSignal, ...]] = {}

    for timestamp_ns in sorted(bundle.signals_by_time_ns):
        retained: list[AcceptanceSignal] = []
        for signal in bundle.signals_by_time_ns[timestamp_ns]:
            if _family(signal) != FAILED_AUCTION_FAMILY:
                retained.append(signal)
                continue
            refined, rejection, changed = _refine_failed_auction_signal(
                data=data,
                signal=signal,
                tick=tick,
                fee_rate=fee_rate,
                minimum_net_reward_risk=minimum_net_reward_risk,
            )
            diagnostics[
                "FAILED_AUCTION_SWEEP_EXTREME_REFINED"
                if changed
                else "FAILED_AUCTION_SWEEP_EXTREME_ALREADY_COMPLETE"
            ] += 1
            if rejection is not None:
                reason = str(rejection["reason"])
                diagnostics[reason] += 1
                _remove_v1_tradeable_diagnostic(diagnostics)
                rejected.append(rejection)
                continue
            assert refined is not None
            retained.append(refined)
        if retained:
            signals[int(timestamp_ns)] = tuple(
                sorted(
                    retained,
                    key=lambda signal: (signal.net_reward_risk, signal.symbol),
                    reverse=True,
                )
            )

    actual_failed_signals = sum(
        _family(signal) == FAILED_AUCTION_FAMILY
        for items in signals.values()
        for signal in items
    )
    reported_failed_signals = int(diagnostics.get(_V1_TRADEABLE_DIAGNOSTIC, 0))
    if actual_failed_signals != reported_failed_signals:
        raise RuntimeError(
            "failed-auction tradeable diagnostic diverged from emitted v2 signals: "
            f"actual={actual_failed_signals} reported={reported_failed_signals}"
        )

    return AcceptanceSignalBundle(
        signals_by_time_ns=signals,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "FAILED_AUCTION_FAMILY",
    "IMPLEMENTATION_REVISION",
    "INITIATIVE_FAMILY",
    "build_auction_router_signals",
]
