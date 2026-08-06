"""Causal multi-scenario auction router for candidate-08 successor v1.

The detector separates two economically distinct scenarios at already-completed 4-hour/day/week
external liquidity:

1. INITIATIVE_ACCEPTANCE_CONTINUATION
   A high-activity outward acceptance, a boundary-holding retest (without requiring artificial
   activity contraction), and a separate same-direction reacceleration. Continuation is tradable
   only when the confirmation close has moved at least one causal stop-slippage-noise reserve beyond
   the boundary. This avoids treating a shallow within-noise excursion as initiative acceptance.

2. FAILED_AUCTION_REVERSAL
   An outward acceptance that is subsequently reclaimed through the completed boundary, followed by
   a separate inward aggressive-flow bar which breaks the reclaim-bar extreme. The structural stop
   is beyond the observed sweep extreme and the target is the nearest active completed external
   level in the reversal direction.

The module emits immutable, future-free signals only. NautilusTrader owns orders, fills, funding,
liquidation, shared NAV, and position accounting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    AcceptanceLogicEvent,
    AcceptanceSignal,
    AcceptanceSignalBundle,
    _PendingAcceptance,
    _acceptance_interaction,
    _acceptance_reaccelerates,
    _acceptance_retest_holds,
    _context_for_ten_second_close,
    _cost_geometry,
    _crossed_levels,
    _row_is_observable,
    _select_active_target,
    _select_interaction_boundary,
    causal_stop_slippage_reserve_series,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


INITIATIVE_FAMILY = "INITIATIVE_ACCEPTANCE_CONTINUATION"
FAILED_AUCTION_FAMILY = "FAILED_AUCTION_REVERSAL"


@dataclass(slots=True)
class _PendingFailedAuction:
    scenario_id: str
    boundary: ExternalLevel
    original_outward: int
    armed_time_ns: int
    reclaim_time_ns: int
    expiry_time_ns: int
    sweep_high: float
    sweep_low: float
    reclaim_high: float
    reclaim_low: float


def _failed_auction_reverses(
    row: pd.Series,
    *,
    direction: int,
    atr: float,
    reclaim_high: float,
    reclaim_low: float,
) -> bool:
    """Require a separate inward displacement after the boundary reclaim."""

    break_reclaim = (
        float(row["close"]) > reclaim_high + 0.01 * atr
        if direction > 0
        else float(row["close"]) < reclaim_low - 0.01 * atr
    )
    located = (
        float(row["close_location"]) >= 0.65
        if direction > 0
        else float(row["close_location"]) <= 0.35
    )
    directional_body = direction * float(row["close"] - row["open"])
    directional_flow = direction * float(row["imbalance"])
    active = float(row["volume_ratio"]) >= 1.0 and float(row["trade_ratio"]) >= 1.0
    return (
        break_reclaim
        and located
        and directional_body >= 0.05 * atr
        and directional_flow >= 0.10
        and active
    )


def _failed_auction_stop(
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
        return (
            min(reference - buffer, entry - minimum_distance),
            reference,
            "FAILED_AUCTION_SWEEP_LOW",
        )
    reference = sweep_high
    return (
        max(reference + buffer, entry + minimum_distance),
        reference,
        "FAILED_AUCTION_SWEEP_HIGH",
    )


def _continuation_events(
    *,
    pending: _PendingAcceptance,
    symbol: str,
    instrument_id: str,
    timestamp_ns: int,
    entry: float,
    confirmation_row: pd.Series,
) -> tuple[AcceptanceLogicEvent, ...]:
    assert pending.retest_time_ns is not None
    assert pending.retest_high is not None
    assert pending.retest_low is not None
    assert pending.retest_volume is not None
    assert pending.retest_trade_count is not None
    direction_name = "LONG" if pending.outward > 0 else "SHORT"
    return (
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
            reason_code="BOUNDARY_RETEST_HELD_WITHOUT_CONTRACTION_REQUIREMENT",
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
            event_type="INITIATIVE_REACCELERATION_CONFIRMED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="RETEST_HELD",
            next_state="CONFIRMED",
            reason_code=f"CAUSAL_NOISE_CLEARED_REACCELERATION_{direction_name}",
            reference_price=entry,
            details={
                "confirmation_imbalance": float(confirmation_row["imbalance"]),
                "confirmation_volume_ratio": float(confirmation_row["volume_ratio"]),
                "confirmation_trade_ratio": float(confirmation_row["trade_ratio"]),
            },
        ),
    )


def _failed_auction_events(
    *,
    pending: _PendingFailedAuction,
    symbol: str,
    instrument_id: str,
    timestamp_ns: int,
    entry: float,
    confirmation_row: pd.Series,
) -> tuple[AcceptanceLogicEvent, ...]:
    direction = -pending.original_outward
    direction_name = "LONG" if direction > 0 else "SHORT"
    outward_name = "HIGH" if pending.original_outward > 0 else "LOW"
    return (
        AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="EXTERNAL_LIQUIDITY_SWEPT",
            event_time_ns=pending.armed_time_ns,
            observed_time_ns=pending.armed_time_ns,
            previous_state="IDLE",
            next_state="SWEPT",
            reason_code=f"AGGRESSIVE_FLOW_CROSSED_EXTERNAL_{outward_name}",
            reference_price=pending.boundary.level,
            details={
                "boundary_id": pending.boundary.level_id,
                "boundary_source": pending.boundary.source.value,
            },
        ),
        AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="FAILED_AUCTION_RECLAIMED",
            event_time_ns=pending.reclaim_time_ns,
            observed_time_ns=pending.reclaim_time_ns,
            previous_state="SWEPT",
            next_state="RECLAIMED",
            reason_code="CLOSE_RETURNED_THROUGH_COMPLETED_EXTERNAL_BOUNDARY",
            reference_price=pending.boundary.level,
            details={
                "reclaim_high": pending.reclaim_high,
                "reclaim_low": pending.reclaim_low,
                "sweep_high_observed_at_reclaim": pending.sweep_high,
                "sweep_low_observed_at_reclaim": pending.sweep_low,
            },
        ),
        AcceptanceLogicEvent(
            scenario_id=pending.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="INWARD_DISPLACEMENT_CONFIRMED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="RECLAIMED",
            next_state="CONFIRMED",
            reason_code=f"SEPARATE_INWARD_AGGRESSIVE_FLOW_{direction_name}",
            reference_price=entry,
            details={
                "confirmation_imbalance": float(confirmation_row["imbalance"]),
                "confirmation_volume_ratio": float(confirmation_row["volume_ratio"]),
                "confirmation_trade_ratio": float(confirmation_row["trade_ratio"]),
            },
        ),
    )


def build_auction_router_signals(
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
    """Build causal initiative-continuation and failed-auction-reversal signals."""

    del require_retest_contraction
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
    failed_pending: _PendingFailedAuction | None = None
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
        reserve = float(stop_slippage_reserves.iloc[position])

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

        if failed_pending is not None:
            direction = -failed_pending.original_outward
            if timestamp_ns > failed_pending.expiry_time_ns:
                diagnostics["FAILED_AUCTION_CONFIRMATION_TIMEOUT"] += 1
                rejected.append(
                    {
                        "scenario_id": failed_pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": failed_pending.boundary.level_id,
                        "reason": "FAILED_AUCTION_CONFIRMATION_TIMEOUT",
                        "reclaim_time_ns": failed_pending.reclaim_time_ns,
                    }
                )
                failed_pending = None
                continue

            invalidated = (
                float(row["close"]) < failed_pending.sweep_low - 0.02 * atr
                if direction > 0
                else float(row["close"]) > failed_pending.sweep_high + 0.02 * atr
            )
            if invalidated:
                diagnostics["FAILED_AUCTION_REVERSAL_INVALIDATED"] += 1
                rejected.append(
                    {
                        "scenario_id": failed_pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": failed_pending.boundary.level_id,
                        "reason": "FAILED_AUCTION_REVERSAL_INVALIDATED",
                        "rejected_time_ns": timestamp_ns,
                    }
                )
                failed_pending = None
                continue

            if not _failed_auction_reverses(
                row,
                direction=direction,
                atr=atr,
                reclaim_high=failed_pending.reclaim_high,
                reclaim_low=failed_pending.reclaim_low,
            ):
                continue

            entry = float(row["close"])
            stop, stop_reference, stop_reference_source = _failed_auction_stop(
                direction=direction,
                entry=entry,
                sweep_high=failed_pending.sweep_high,
                sweep_low=failed_pending.sweep_low,
                atr=atr,
            )
            target_level = _select_active_target(
                target_levels,
                direction=direction,
                entry=entry,
                excluded_level_id=failed_pending.boundary.level_id,
                consumed=consumed,
            )
            if target_level is None:
                reason = "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": failed_pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": failed_pending.boundary.level_id,
                        "reason": reason,
                        "confirmation_time_ns": timestamp_ns,
                    }
                )
                failed_pending = None
                continue

            geometry = _cost_geometry(
                direction=direction,
                entry=entry,
                stop=stop,
                target=target_level.level,
                fee_rate=fee_rate,
                tick=tick,
                stop_slippage_reserve=reserve,
            )
            if geometry is None:
                reason = "INVALID_COST_AFTER_EXTERNAL_GEOMETRY"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": failed_pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": failed_pending.boundary.level_id,
                        "target_id": target_level.level_id,
                        "reason": reason,
                        "confirmation_time_ns": timestamp_ns,
                    }
                )
                failed_pending = None
                continue

            loss, gain, net_rr = geometry
            if net_rr < minimum_net_reward_risk:
                reason = "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": failed_pending.scenario_id,
                        "symbol": symbol,
                        "boundary_id": failed_pending.boundary.level_id,
                        "target_id": target_level.level_id,
                        "reason": reason,
                        "confirmation_time_ns": timestamp_ns,
                        "net_reward_risk": net_rr,
                    }
                )
                failed_pending = None
                continue

            events = _failed_auction_events(
                pending=failed_pending,
                symbol=symbol,
                instrument_id=instrument_id,
                timestamp_ns=timestamp_ns,
                entry=entry,
                confirmation_row=row,
            )
            signal = AcceptanceSignal(
                scenario_id=failed_pending.scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                direction=direction,
                signal_index=position,
                signal_time_ns=timestamp_ns,
                boundary_id=failed_pending.boundary.level_id,
                boundary_source=failed_pending.boundary.source.value,
                boundary_level=failed_pending.boundary.level,
                target_id=target_level.level_id,
                target_source=target_level.source.value,
                external_target=target_level.level,
                entry_reference=entry,
                structural_stop=stop,
                atr=atr,
                causal_stop_slippage_reserve=reserve,
                expected_loss_per_unit=loss,
                expected_gain_per_unit=gain,
                net_reward_risk=net_rr,
                armed_time_ns=failed_pending.armed_time_ns,
                retest_time_ns=failed_pending.reclaim_time_ns,
                retest_high=failed_pending.reclaim_high,
                retest_low=failed_pending.reclaim_low,
                events=events,
                details={
                    "scenario_family": FAILED_AUCTION_FAMILY,
                    "stop_reference": stop_reference,
                    "stop_reference_source": stop_reference_source,
                    "sweep_high": failed_pending.sweep_high,
                    "sweep_low": failed_pending.sweep_low,
                    "reclaim_high": failed_pending.reclaim_high,
                    "reclaim_low": failed_pending.reclaim_low,
                    "confirmation_position": position,
                    "confirmation_close_location": float(row["close_location"]),
                    "causal_stop_slippage_reserve": reserve,
                    "slippage_reserve_contract": "SHIFTED_60M_TEN_SECOND_TRUE_RANGE_Q99",
                    "stop_order_tag": "OBSERVED_FAILED_AUCTION_SWEEP_INVALIDATION",
                },
            )
            signals.setdefault(timestamp_ns, []).append(signal)
            diagnostics["TRADEABLE_FAILED_AUCTION_REVERSAL"] += 1
            failed_pending = None
            continue

        handled_pending = pending is not None
        if pending is not None:
            pending.displacement_high = max(pending.displacement_high, float(row["high"]))
            pending.displacement_low = min(pending.displacement_low, float(row["low"]))
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
                    scenario_id = f"{pending.scenario_id}-failed-auction"
                    failed_pending = _PendingFailedAuction(
                        scenario_id=scenario_id,
                        boundary=pending.boundary,
                        original_outward=outward,
                        armed_time_ns=pending.armed_time_ns,
                        reclaim_time_ns=timestamp_ns,
                        expiry_time_ns=timestamp_ns + 30 * 1_000_000_000,
                        sweep_high=max(pending.displacement_high, float(row["high"])),
                        sweep_low=min(pending.displacement_low, float(row["low"])),
                        reclaim_high=float(row["high"]),
                        reclaim_low=float(row["low"]),
                    )
                    diagnostics["FAILED_AUCTION_RECLAIM_ARMED"] += 1
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
                        require_contraction=False,
                    ):
                        pending.retest_position = position
                        pending.retest_time_ns = timestamp_ns
                        pending.retest_high = float(row["high"])
                        pending.retest_low = float(row["low"])
                        pending.retest_volume = float(row["volume"])
                        pending.retest_trade_count = float(row["trade_count"])
                        pending.expiry_time_ns = timestamp_ns + 30 * 1_000_000_000
                        diagnostics["ACCEPTANCE_RETEST_HELD_WITHOUT_CONTRACTION"] += 1
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
                        displacement_depth = outward * (entry - pending.boundary.level)
                        displacement_to_noise = displacement_depth / max(reserve, tick)
                        if displacement_depth < reserve:
                            reason = "SHALLOW_DISPLACEMENT_WITHIN_CAUSAL_NOISE"
                            diagnostics[reason] += 1
                            rejected.append(
                                {
                                    "scenario_id": pending.scenario_id,
                                    "symbol": symbol,
                                    "boundary_id": pending.boundary.level_id,
                                    "reason": reason,
                                    "confirmation_time_ns": timestamp_ns,
                                    "boundary_displacement_depth": displacement_depth,
                                    "causal_noise_reserve": reserve,
                                    "displacement_to_noise_ratio": displacement_to_noise,
                                }
                            )
                            pending = None
                        else:
                            from aggtrade_acceptance_signals import _structural_stop

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
                                    stop_slippage_reserve=reserve,
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
                                        events = _continuation_events(
                                            pending=pending,
                                            symbol=symbol,
                                            instrument_id=instrument_id,
                                            timestamp_ns=timestamp_ns,
                                            entry=entry,
                                            confirmation_row=row,
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
                                            causal_stop_slippage_reserve=reserve,
                                            expected_loss_per_unit=loss,
                                            expected_gain_per_unit=gain,
                                            net_reward_risk=net_rr,
                                            armed_time_ns=pending.armed_time_ns,
                                            retest_time_ns=pending.retest_time_ns,
                                            retest_high=pending.retest_high,
                                            retest_low=pending.retest_low,
                                            events=events,
                                            details={
                                                "scenario_family": INITIATIVE_FAMILY,
                                                "stop_reference": stop_reference,
                                                "stop_reference_source": stop_reference_source,
                                                "armed_position": pending.armed_position,
                                                "retest_position": pending.retest_position,
                                                "confirmation_position": position,
                                                "confirmation_close_location": float(
                                                    row["close_location"]
                                                ),
                                                "boundary_displacement_depth": displacement_depth,
                                                "causal_noise_reserve": reserve,
                                                "displacement_to_noise_ratio": displacement_to_noise,
                                                "causal_stop_slippage_reserve": reserve,
                                                "slippage_reserve_contract": (
                                                    "SHIFTED_60M_TEN_SECOND_TRUE_RANGE_Q99"
                                                ),
                                                "stop_order_tag": (
                                                    "OBSERVED_RETEST_INVALIDATION"
                                                ),
                                            },
                                        )
                                        signals.setdefault(timestamp_ns, []).append(signal)
                                        diagnostics[
                                            "TRADEABLE_INITIATIVE_ACCEPTANCE_CONTINUATION"
                                        ] += 1
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
        scenario_id = f"auction-router-{symbol.lower()}-{scenario_counter:06d}"
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
        diagnostics["AUCTION_ROUTER_ACCEPTANCE_ARMED"] += 1

    immutable = {
        timestamp: tuple(
            sorted(
                items,
                key=lambda signal: (
                    signal.net_reward_risk,
                    signal.details.get("scenario_family", ""),
                    signal.symbol,
                ),
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
