"""Causal acceptance-only signals for NautilusTrader execution.

Only information observable at the completed ten-second confirmation bar is emitted.
No future first-touch, MFE, MAE, account, order, or fill result is evaluated here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_probe import (
    acceptance_interaction,
    acceptance_reaccelerates,
    acceptance_retest_holds,
)
from aggtrade_orderflow_probe import (
    PendingEvent,
    _crossed_boundary,
    _net,
    _select_target,
    _snapshot_after_latest_complete,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


@dataclass(frozen=True, slots=True)
class AcceptanceSignal:
    symbol: str
    scenario_id: str
    direction: int
    signal_time_ns: int
    confirmation_time: str
    confirmation_close: float
    estimated_entry: float
    structural_stop: float
    external_target: float
    boundary_id: str
    boundary_source: str
    boundary_level: float
    target_id: str
    target_source: str
    expected_loss_per_unit: float
    expected_gain_per_unit: float
    net_reward_risk: float
    atr: float
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_name(self) -> str:
        return "LONG" if self.direction > 0 else "SHORT"


@dataclass(frozen=True, slots=True)
class AcceptanceSignalBundle:
    signals_by_time_ns: dict[int, tuple[AcceptanceSignal, ...]]
    diagnostics: dict[str, int]
    signal_count: int


def acceptance_structural_stop(
    pending: PendingEvent,
    *,
    direction: int,
    entry: float,
    atr: float,
) -> tuple[float, str]:
    """Use the observed retest extreme, not the breakout extreme, as invalidation."""

    minimum_distance = 0.10 * atr
    buffer_distance = 0.03 * atr
    if pending.retest_high is None or pending.retest_low is None:
        raise ValueError("acceptance continuation requires an observed retest extreme")
    if direction > 0:
        return (
            min(float(pending.retest_low) - buffer_distance, entry - minimum_distance),
            "ACCEPTANCE_RETEST_LOW",
        )
    return (
        max(float(pending.retest_high) + buffer_distance, entry + minimum_distance),
        "ACCEPTANCE_RETEST_HIGH",
    )


def _make_signal(
    *,
    symbol: str,
    pending: PendingEvent,
    confirmation_position: int,
    data: pd.DataFrame,
    levels: tuple[ExternalLevel, ...],
    atr: float,
    tick: float,
    minimum_net_reward_risk: float,
) -> tuple[AcceptanceSignal | None, str]:
    row = data.iloc[confirmation_position]
    direction = int(pending.trade_direction)
    confirmation_close = float(row["close"])
    estimated_entry = confirmation_close + direction * tick
    stop, stop_source = acceptance_structural_stop(
        pending,
        direction=direction,
        entry=estimated_entry,
        atr=atr,
    )
    target_level = _select_target(
        levels,
        direction=direction,
        entry=estimated_entry,
        excluded_level_id=pending.boundary.level_id,
    )
    if target_level is None:
        return None, "NO_CAUSAL_EXTERNAL_TARGET"
    target = float(target_level.level)
    valid = stop < estimated_entry < target if direction > 0 else target < estimated_entry < stop
    if not valid:
        return None, "INVALID_EXTERNAL_GEOMETRY"

    expected_loss = -_net(direction, estimated_entry, stop, tick)
    expected_gain = _net(direction, estimated_entry, target, tick)
    net_rr = expected_gain / expected_loss if expected_loss > 0 else float("-inf")
    if expected_gain <= 0 or net_rr < minimum_net_reward_risk:
        return None, "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"

    confirmation_time = data.index[confirmation_position]
    retest_time = (
        data.index[int(pending.retest_position)]
        if pending.retest_position is not None
        else None
    )
    return AcceptanceSignal(
        symbol=symbol,
        scenario_id=f"{symbol}-{pending.scenario_id}",
        direction=direction,
        signal_time_ns=int(confirmation_time.as_unit("ns").value),
        confirmation_time=confirmation_time.isoformat(),
        confirmation_close=confirmation_close,
        estimated_entry=estimated_entry,
        structural_stop=stop,
        external_target=target,
        boundary_id=pending.boundary.level_id,
        boundary_source=pending.boundary.source.value,
        boundary_level=float(pending.boundary.level),
        target_id=target_level.level_id,
        target_source=target_level.source.value,
        expected_loss_per_unit=expected_loss,
        expected_gain_per_unit=expected_gain,
        net_reward_risk=net_rr,
        atr=atr,
        details={
            "contract": "COMPLETED_LEVEL_AGGRESSIVE_FLOW_ACCEPTANCE_RETEST_REACCELERATION",
            "armed_time": data.index[pending.armed_position].isoformat(),
            "retest_time": retest_time.isoformat() if retest_time is not None else None,
            "confirmation_delay_seconds": (confirmation_position - pending.armed_position) * 10,
            "stop_reference_source": stop_source,
            "retest_high": pending.retest_high,
            "retest_low": pending.retest_low,
            "armed_imbalance": pending.displacement_imbalance,
            "confirmation_imbalance": float(row["imbalance"]),
            "confirmation_volume_ratio": float(row["volume_ratio"]),
            "confirmation_trade_ratio": float(row["trade_ratio"]),
        },
    ), "TRADEABLE_SIGNAL"


def build_acceptance_signals(
    *,
    symbol: str,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[FiveMinuteBar, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    tick: float,
    minimum_net_reward_risk: float = 1.20,
) -> AcceptanceSignalBundle:
    """Build causal signals without inspecting any row after confirmation."""

    diagnostics: Counter[str] = Counter()
    consumed: set[str] = set()
    pending: PendingEvent | None = None
    scenario_counter = 0
    signals_by_time: dict[int, list[AcceptanceSignal]] = {}

    for position in range(1, len(data.index)):
        row = data.iloc[position]
        required = (
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), float(row["imbalance"]),
            float(row["volume_ratio"]), float(row["trade_ratio"]),
            float(row["close_location"]),
        )
        if not all(np.isfinite(value) for value in required):
            continue
        timestamp_ns = int(data.index[position].as_unit("ns").value)
        context = _snapshot_after_latest_complete(
            timestamp_ns, context_times, context_bars, snapshots,
        )
        if context is None:
            continue
        five_bar, levels = context
        atr = float(five_bar.atr)
        handled_pending = pending is not None

        if pending is not None:
            outward = int(pending.outward_direction)
            if position > pending.expiry_position:
                diagnostics["ACCEPTANCE_SEQUENCE_TIMEOUT"] += 1
                pending = None
            else:
                reclaimed = (
                    float(row["close"]) < pending.boundary.level - 0.02 * atr
                    if outward > 0
                    else float(row["close"]) > pending.boundary.level + 0.02 * atr
                )
                if reclaimed:
                    diagnostics["ACCEPTANCE_RECLAIMED"] += 1
                    pending = None
                elif pending.retest_position is None:
                    if acceptance_retest_holds(
                        row,
                        boundary_level=pending.boundary.level,
                        outward=outward,
                        atr=atr,
                        displacement_volume=pending.displacement_volume,
                        displacement_trade_count=pending.displacement_trade_count,
                        displacement_imbalance=pending.displacement_imbalance,
                    ):
                        pending.retest_position = position
                        pending.retest_high = float(row["high"])
                        pending.retest_low = float(row["low"])
                        pending.retest_volume = float(row["volume"])
                        pending.retest_trade_count = float(row["trade_count"])
                        pending.expiry_position = position + 3
                        diagnostics["ACCEPTANCE_RETEST_HELD"] += 1
                else:
                    assert pending.retest_high is not None
                    assert pending.retest_low is not None
                    assert pending.retest_volume is not None
                    assert pending.retest_trade_count is not None
                    if acceptance_reaccelerates(
                        row,
                        outward=outward,
                        atr=atr,
                        retest_high=pending.retest_high,
                        retest_low=pending.retest_low,
                        retest_volume=pending.retest_volume,
                        retest_trade_count=pending.retest_trade_count,
                    ):
                        signal, reason = _make_signal(
                            symbol=symbol,
                            pending=pending,
                            confirmation_position=position,
                            data=data,
                            levels=levels,
                            atr=atr,
                            tick=tick,
                            minimum_net_reward_risk=minimum_net_reward_risk,
                        )
                        diagnostics[reason] += 1
                        if signal is not None:
                            signals_by_time.setdefault(signal.signal_time_ns, []).append(signal)
                        pending = None
            if handled_pending:
                continue

        crossed = _crossed_boundary(
            levels,
            previous_close=float(data.iloc[position - 1]["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            atr=atr,
            consumed=consumed,
        )
        if crossed is None:
            continue
        boundary, outward = crossed
        if not acceptance_interaction(
            row,
            boundary_level=boundary.level,
            outward=outward,
            atr=atr,
        ):
            diagnostics["NON_ACCEPTANCE_INTERACTION_IGNORED"] += 1
            continue

        scenario_counter += 1
        consumed.add(boundary.level_id)
        pending = PendingEvent(
            scenario_id=f"agg-acceptance-{scenario_counter:06d}",
            family="BREAKOUT_ACCEPTANCE_CONTINUATION",
            trade_direction=outward,
            outward_direction=outward,
            boundary=boundary,
            armed_position=position,
            expiry_position=position + 6,
            extreme=float(row["high"] if outward > 0 else row["low"]),
            reference_high=float(row["high"]),
            reference_low=float(row["low"]),
            displacement_volume=float(row["volume"]),
            displacement_trade_count=float(row["trade_count"]),
            displacement_imbalance=float(row["imbalance"]),
        )
        diagnostics["ACCEPTANCE_ARMED"] += 1

    immutable = {
        timestamp: tuple(sorted(items, key=lambda item: (-item.net_reward_risk, item.symbol)))
        for timestamp, items in sorted(signals_by_time.items())
    }
    return AcceptanceSignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(diagnostics.items())),
        signal_count=sum(len(items) for items in immutable.values()),
    )
