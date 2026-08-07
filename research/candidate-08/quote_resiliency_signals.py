"""Causal external-liquidity quote-resiliency state machine for candidate-08.

The detector consumes completed ten-second trade/quote features and already-completed 4-hour, day
and week external-liquidity levels.  It separates two economic scenarios:

* displayed opposing liquidity replenishes after a sweep and the auction fails;
* displayed opposing liquidity withdraws while same-side support replaces behind an accepted move.

Every transition uses only completed observations.  The module contains no orders, fills, account
state, position sizing, PnL or backtest engine; NautilusTrader remains authoritative for execution.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    _context_for_ten_second_close,
    causal_stop_slippage_reserve_series,
)
from quote_resiliency_features_v3 import QuoteResiliencyConfig
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, SOURCE_RANK


SIGNAL_REVISION = "EXTERNAL_LIQUIDITY_QUOTE_RESILIENCY_SIGNALS_V1"
REVERSAL_FAMILY = "QUOTE_REPLENISHED_FAILED_AUCTION_REVERSAL"
CONTINUATION_FAMILY = "QUOTE_WITHDRAWAL_ACCEPTANCE_CONTINUATION"


@dataclass(frozen=True, slots=True)
class QuoteResiliencyLogicEvent:
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
class QuoteResiliencySignal:
    scenario_id: str
    scenario_family: str
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
    stop_reference: float
    stop_reference_source: str
    atr: float
    causal_stop_slippage_reserve: float
    expected_loss_per_unit: float
    expected_gain_per_unit: float
    net_reward_risk: float
    interaction_time_ns: int
    response_time_ns: int
    retest_time_ns: int | None
    events: tuple[QuoteResiliencyLogicEvent, ...]
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_name(self) -> str:
        return "LONG" if self.direction > 0 else "SHORT"


@dataclass(frozen=True, slots=True)
class QuoteResiliencySignalBundle:
    signals_by_time_ns: dict[int, tuple[QuoteResiliencySignal, ...]]
    diagnostics: dict[str, int]
    rejected_scenarios: tuple[dict[str, Any], ...]


@dataclass(slots=True)
class _PendingScenario:
    scenario_id: str
    boundary: ExternalLevel
    outward: int
    interaction_position: int
    interaction_time_ns: int
    interaction_pressure_abs: float
    expiry_position: int
    state: str = "LIQUIDITY_RESPONSE"
    response_position: int | None = None
    response_time_ns: int | None = None
    response_high: float = float("-inf")
    response_low: float = float("inf")
    bid_add_qty: float = 0.0
    bid_remove_qty: float = 0.0
    ask_add_qty: float = 0.0
    ask_remove_qty: float = 0.0
    quote_ofi_qty: float = 0.0
    break_level: float | None = None
    retest_position: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    events: list[QuoteResiliencyLogicEvent] = field(default_factory=list)


_MARKET_COLUMNS = ("open", "high", "low", "close")
_QUOTE_COLUMNS = (
    "aggressive_pressure_ratio",
    "quote_ofi_ratio",
    "quote_ofi_qty",
    "bid_add_qty",
    "bid_remove_qty",
    "ask_add_qty",
    "ask_remove_qty",
    "spread_median_ratio",
)


def _finite_columns(row: pd.Series, columns: tuple[str, ...]) -> bool:
    try:
        return all(isfinite(float(row[column])) for column in columns)
    except (KeyError, TypeError, ValueError):
        return False


def _quote_observable(row: pd.Series) -> bool:
    return (
        bool(row.get("quote_resiliency_observable", False))
        and _finite_columns(row, _QUOTE_COLUMNS)
    )


def _crossed_levels(
    levels: tuple[ExternalLevel, ...],
    *,
    previous_close: float,
    high: float,
    low: float,
    consumed: set[str],
) -> tuple[list[ExternalLevel], list[ExternalLevel]]:
    highs = [
        level
        for level in levels
        if level.level_id not in consumed
        and level.kind is LevelKind.HIGH
        and previous_close <= level.level
        and high >= level.level
    ]
    lows = [
        level
        for level in levels
        if level.level_id not in consumed
        and level.kind is LevelKind.LOW
        and previous_close >= level.level
        and low <= level.level
    ]
    return highs, lows


def _select_interaction(
    highs: list[ExternalLevel],
    lows: list[ExternalLevel],
) -> tuple[ExternalLevel, int] | None:
    if highs and lows:
        return None
    if highs:
        return max(highs, key=lambda item: (SOURCE_RANK[item.source], item.level)), 1
    if lows:
        return max(lows, key=lambda item: (SOURCE_RANK[item.source], -item.level)), -1
    return None


def _select_target(
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
        return min(candidates, key=lambda item: item.level) if candidates else None
    candidates = [
        level
        for level in levels
        if level.level_id != excluded_level_id
        and level.level_id not in consumed
        and level.kind is LevelKind.LOW
        and level.level < entry
    ]
    return max(candidates, key=lambda item: item.level) if candidates else None


def _ratio(numerator: float, denominator: float) -> float:
    return (float(numerator) + 1e-12) / (float(denominator) + 1e-12)


def _accumulate_response(pending: _PendingScenario, row: pd.Series) -> None:
    pending.response_high = max(pending.response_high, float(row["high"]))
    pending.response_low = min(pending.response_low, float(row["low"]))
    pending.bid_add_qty += float(row["bid_add_qty"])
    pending.bid_remove_qty += float(row["bid_remove_qty"])
    pending.ask_add_qty += float(row["ask_add_qty"])
    pending.ask_remove_qty += float(row["ask_remove_qty"])
    pending.quote_ofi_qty += float(row["quote_ofi_qty"])


def _response_metrics(pending: _PendingScenario) -> dict[str, float]:
    return {
        "bid_replenishment_ratio": _ratio(
            pending.bid_add_qty,
            pending.bid_remove_qty,
        ),
        "bid_withdrawal_ratio": _ratio(
            pending.bid_remove_qty,
            pending.bid_add_qty,
        ),
        "ask_replenishment_ratio": _ratio(
            pending.ask_add_qty,
            pending.ask_remove_qty,
        ),
        "ask_withdrawal_ratio": _ratio(
            pending.ask_remove_qty,
            pending.ask_add_qty,
        ),
        "cumulative_quote_ofi_qty": pending.quote_ofi_qty,
    }


def _classify_response(
    pending: _PendingScenario,
    row: pd.Series,
    config: QuoteResiliencyConfig,
) -> tuple[str | None, dict[str, float]]:
    metrics = _response_metrics(pending)
    close = float(row["close"])
    boundary = pending.boundary.level
    spread_recovered = float(row["spread_median_ratio"]) <= float(
        config.maximum_spread_median_ratio
    )
    if pending.outward > 0:
        reversal = (
            pending.ask_add_qty > 0.0
            and metrics["ask_replenishment_ratio"]
            >= float(config.minimum_quote_response_ratio)
            and pending.quote_ofi_qty < 0.0
            and close < boundary
        )
        continuation = (
            pending.ask_remove_qty > 0.0
            and metrics["ask_withdrawal_ratio"]
            >= float(config.minimum_quote_response_ratio)
            and pending.bid_add_qty > 0.0
            and metrics["bid_replenishment_ratio"]
            >= float(config.minimum_same_side_support_ratio)
            and pending.quote_ofi_qty > 0.0
            and close > boundary
            and spread_recovered
        )
    else:
        reversal = (
            pending.bid_add_qty > 0.0
            and metrics["bid_replenishment_ratio"]
            >= float(config.minimum_quote_response_ratio)
            and pending.quote_ofi_qty > 0.0
            and close > boundary
        )
        continuation = (
            pending.bid_remove_qty > 0.0
            and metrics["bid_withdrawal_ratio"]
            >= float(config.minimum_quote_response_ratio)
            and pending.ask_add_qty > 0.0
            and metrics["ask_replenishment_ratio"]
            >= float(config.minimum_same_side_support_ratio)
            and pending.quote_ofi_qty < 0.0
            and close < boundary
            and spread_recovered
        )
    if reversal:
        return REVERSAL_FAMILY, metrics
    if continuation:
        return CONTINUATION_FAMILY, metrics
    return None, metrics


def _confirmation_flow_holds(
    row: pd.Series,
    *,
    direction: int,
    config: QuoteResiliencyConfig,
    quote_ofi_confirmation_required: bool,
) -> bool:
    directional_pressure = direction * float(row["aggressive_pressure_ratio"])
    directional_quote_ofi = direction * float(row["quote_ofi_ratio"])
    return (
        directional_pressure >= float(config.minimum_confirmation_pressure_ratio)
        and (
            not quote_ofi_confirmation_required
            or directional_quote_ofi
            >= float(config.minimum_confirmation_quote_ofi_ratio)
        )
    )


def _structural_stop(
    *,
    direction: int,
    entry: float,
    reference: float,
    atr: float,
) -> float:
    minimum_distance = 0.10 * atr
    buffer = 0.03 * atr
    if direction > 0:
        return min(reference - buffer, entry - minimum_distance)
    return max(reference + buffer, entry + minimum_distance)


def _cost_geometry(
    *,
    direction: int,
    entry: float,
    stop: float,
    target: float,
    fee_rate: float,
    tick: float,
    stop_slippage_reserve: float,
) -> tuple[float, float, float] | None:
    valid = stop < entry < target if direction > 0 else target < entry < stop
    if not valid:
        return None
    stop_reserve = max(tick, float(stop_slippage_reserve))
    loss = abs(entry - stop) + fee_rate * (entry + stop) + tick + stop_reserve
    gross_gain = target - entry if direction > 0 else entry - target
    gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
    if loss <= 0.0 or gain <= 0.0:
        return None
    return loss, gain, gain / loss


def _logic_event(
    pending: _PendingScenario,
    *,
    symbol: str,
    instrument_id: str,
    event_type: str,
    event_time_ns: int,
    previous_state: str,
    next_state: str,
    reason_code: str,
    reference_price: float | None = None,
    details: dict[str, Any] | None = None,
) -> QuoteResiliencyLogicEvent:
    return QuoteResiliencyLogicEvent(
        scenario_id=pending.scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type=event_type,
        event_time_ns=event_time_ns,
        observed_time_ns=event_time_ns,
        previous_state=previous_state,
        next_state=next_state,
        reason_code=reason_code,
        reference_price=reference_price,
        details=details or {},
    )


def _rejection_record(
    pending: _PendingScenario,
    *,
    symbol: str,
    reason: str,
    timestamp_ns: int,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scenario_id": pending.scenario_id,
        "symbol": symbol,
        "boundary_id": pending.boundary.level_id,
        "boundary_source": pending.boundary.source.value,
        "boundary_level": pending.boundary.level,
        "outward_direction": pending.outward,
        "state": pending.state,
        "reason": reason,
        "interaction_time_ns": pending.interaction_time_ns,
        "rejected_time_ns": timestamp_ns,
        "details": details or {},
    }


def _emit_signal(
    *,
    pending: _PendingScenario,
    family: str,
    direction: int,
    position: int,
    timestamp_ns: int,
    row: pd.Series,
    target_levels: tuple[ExternalLevel, ...],
    consumed: set[str],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    atr: float,
    stop_slippage_reserve: float,
    diagnostics: Counter[str],
    rejected: list[dict[str, Any]],
) -> QuoteResiliencySignal | None:
    entry = float(row["close"])
    if family == REVERSAL_FAMILY:
        stop_reference = pending.response_low if direction > 0 else pending.response_high
        stop_reference_source = "FULL_SWEEP_RESPONSE_EXTREME"
    else:
        if pending.retest_low is None or pending.retest_high is None:
            raise RuntimeError("continuation confirmation missing frozen retest extreme")
        stop_reference = pending.retest_low if direction > 0 else pending.retest_high
        stop_reference_source = "FROZEN_RETEST_EXTREME"
    stop = _structural_stop(
        direction=direction,
        entry=entry,
        reference=float(stop_reference),
        atr=atr,
    )
    target = _select_target(
        target_levels,
        direction=direction,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
        consumed=consumed,
    )
    if target is None:
        reason = "NO_ACTIVE_COMPLETED_EXTERNAL_TARGET"
        diagnostics[reason] += 1
        rejected.append(
            _rejection_record(
                pending,
                symbol=symbol,
                reason=reason,
                timestamp_ns=timestamp_ns,
            )
        )
        return None
    geometry = _cost_geometry(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target.level,
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=stop_slippage_reserve,
    )
    if geometry is None:
        reason = "INVALID_COST_AFTER_EXTERNAL_GEOMETRY"
        diagnostics[reason] += 1
        rejected.append(
            _rejection_record(
                pending,
                symbol=symbol,
                reason=reason,
                timestamp_ns=timestamp_ns,
                details={"target_id": target.level_id},
            )
        )
        return None
    loss, gain, net_rr = geometry
    if net_rr < minimum_net_reward_risk:
        reason = "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
        diagnostics[reason] += 1
        rejected.append(
            _rejection_record(
                pending,
                symbol=symbol,
                reason=reason,
                timestamp_ns=timestamp_ns,
                details={
                    "target_id": target.level_id,
                    "net_reward_risk": net_rr,
                },
            )
        )
        return None

    event = _logic_event(
        pending,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type="SCENARIO_CONFIRMED",
        event_time_ns=timestamp_ns,
        previous_state=pending.state,
        next_state="CONFIRMED",
        reason_code=(
            "OPPOSITE_FLOW_BREAKS_FROZEN_RECLAIM_EXTREME"
            if family == REVERSAL_FAMILY
            else "SAME_DIRECTION_FLOW_BREAKS_FROZEN_RETEST_EXTREME"
        ),
        reference_price=entry,
        details={
            "direction": "LONG" if direction > 0 else "SHORT",
            "aggressive_pressure_ratio": float(row["aggressive_pressure_ratio"]),
            "quote_ofi_ratio": float(row["quote_ofi_ratio"]),
            "target_id": target.level_id,
            "net_reward_risk": net_rr,
        },
    )
    events = tuple([*pending.events, event])
    diagnostics[f"SIGNAL_{family}"] += 1
    return QuoteResiliencySignal(
        scenario_id=pending.scenario_id,
        scenario_family=family,
        symbol=symbol,
        instrument_id=instrument_id,
        direction=direction,
        signal_index=position,
        signal_time_ns=timestamp_ns,
        boundary_id=pending.boundary.level_id,
        boundary_source=pending.boundary.source.value,
        boundary_level=pending.boundary.level,
        target_id=target.level_id,
        target_source=target.source.value,
        external_target=target.level,
        entry_reference=entry,
        structural_stop=stop,
        stop_reference=float(stop_reference),
        stop_reference_source=stop_reference_source,
        atr=atr,
        causal_stop_slippage_reserve=max(tick, stop_slippage_reserve),
        expected_loss_per_unit=loss,
        expected_gain_per_unit=gain,
        net_reward_risk=net_rr,
        interaction_time_ns=pending.interaction_time_ns,
        response_time_ns=int(pending.response_time_ns or pending.interaction_time_ns),
        retest_time_ns=pending.retest_time_ns,
        events=events,
        details={
            "signal_revision": SIGNAL_REVISION,
            "outward_direction": pending.outward,
            "interaction_pressure_abs": pending.interaction_pressure_abs,
            "response_high": pending.response_high,
            "response_low": pending.response_low,
            **_response_metrics(pending),
        },
    )


def build_quote_resiliency_signals(
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
    config: QuoteResiliencyConfig | None = None,
    quote_ofi_confirmation_required: bool | None = None,
) -> QuoteResiliencySignalBundle:
    """Build immutable, future-free quote-resiliency signals."""

    cfg = config or QuoteResiliencyConfig()
    cfg.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost or reward-risk contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second features must use a timezone-aware DatetimeIndex")
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError("ten-second features must have unique increasing timestamps")
    required_gate = (
        bool(cfg.quote_ofi_confirmation_required)
        if quote_ofi_confirmation_required is None
        else bool(quote_ofi_confirmation_required)
    )

    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}
    consumed: set[str] = set()
    pending: _PendingScenario | None = None
    scenario_counter = 0

    for position in range(1, len(data.index)):
        row = data.iloc[position]
        previous = data.iloc[position - 1]
        if not _finite_columns(row, _MARKET_COLUMNS) or not isfinite(float(previous["close"])):
            diagnostics["UNOBSERVABLE_MARKET_BUCKET"] += 1
            if pending is not None:
                rejected.append(
                    _rejection_record(
                        pending,
                        symbol=symbol,
                        reason="UNOBSERVABLE_MARKET_DURING_ACTIVE_SEQUENCE",
                        timestamp_ns=int(data.index[position].as_unit("ns").value),
                    )
                )
                pending = None
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
            if pending is not None:
                rejected.append(
                    _rejection_record(
                        pending,
                        symbol=symbol,
                        reason="CONTEXT_UNAVAILABLE_DURING_ACTIVE_SEQUENCE",
                        timestamp_ns=timestamp_ns,
                    )
                )
                pending = None
            continue
        five_bar, boundary_levels, target_levels = context
        atr = float(five_bar.atr)
        if not isfinite(atr) or atr <= 0.0:
            diagnostics["INVALID_CAUSAL_ATR"] += 1
            continue

        highs, lows = _crossed_levels(
            boundary_levels,
            previous_close=float(previous["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            consumed=consumed,
        )
        for level in (*highs, *lows):
            consumed.add(level.level_id)
        interaction = _select_interaction(highs, lows)
        if highs and lows:
            diagnostics["BOTH_SIDES_INTERACTED_SAME_BUCKET"] += 1

        observable = _quote_observable(row)
        if not observable:
            diagnostics["UNOBSERVABLE_QUOTE_RESILIENCY_BUCKET"] += 1
            if pending is not None:
                rejected.append(
                    _rejection_record(
                        pending,
                        symbol=symbol,
                        reason="UNOBSERVABLE_QUOTE_DURING_ACTIVE_SEQUENCE",
                        timestamp_ns=timestamp_ns,
                    )
                )
                pending = None
            continue

        handled_pending = pending is not None
        if pending is not None:
            if position > pending.expiry_position:
                reason = "SCENARIO_SEQUENCE_TIMEOUT"
                diagnostics[reason] += 1
                rejected.append(
                    _rejection_record(
                        pending,
                        symbol=symbol,
                        reason=reason,
                        timestamp_ns=timestamp_ns,
                    )
                )
                pending = None
            else:
                if pending.state == "LIQUIDITY_RESPONSE":
                    _accumulate_response(pending, row)
                    family, metrics = _classify_response(pending, row, cfg)
                    if family == REVERSAL_FAMILY:
                        direction = -pending.outward
                        pending.response_position = position
                        pending.response_time_ns = timestamp_ns
                        pending.break_level = (
                            float(row["high"]) if direction > 0 else float(row["low"])
                        )
                        pending.state = "REVERSAL_CONFIRMATION_PENDING"
                        pending.expiry_position = position + int(cfg.confirmation_window_bars)
                        pending.events.append(
                            _logic_event(
                                pending,
                                symbol=symbol,
                                instrument_id=instrument_id,
                                event_type="QUOTE_REPLENISHED_RECLAIM",
                                event_time_ns=timestamp_ns,
                                previous_state="LIQUIDITY_RESPONSE",
                                next_state=pending.state,
                                reason_code="OPPOSING_DISPLAYED_SUPPLY_REPLENISHED_AND_BOUNDARY_RECLAIMED",
                                reference_price=pending.boundary.level,
                                details=metrics,
                            )
                        )
                        diagnostics["REVERSAL_RESPONSE_CLASSIFIED"] += 1
                    elif family == CONTINUATION_FAMILY:
                        pending.response_position = position
                        pending.response_time_ns = timestamp_ns
                        pending.state = "ACCEPTANCE_WAIT_RETEST"
                        pending.expiry_position = pending.interaction_position + int(
                            cfg.setup_expiry_bars
                        )
                        pending.events.append(
                            _logic_event(
                                pending,
                                symbol=symbol,
                                instrument_id=instrument_id,
                                event_type="QUOTE_WITHDRAWAL_ACCEPTED",
                                event_time_ns=timestamp_ns,
                                previous_state="LIQUIDITY_RESPONSE",
                                next_state=pending.state,
                                reason_code="OPPOSING_DISPLAYED_SUPPLY_WITHDREW_AND_SAME_SIDE_SUPPORT_REPLACED",
                                reference_price=pending.boundary.level,
                                details=metrics,
                            )
                        )
                        diagnostics["CONTINUATION_RESPONSE_CLASSIFIED"] += 1
                    elif position >= pending.interaction_position + int(cfg.response_window_bars):
                        reason = "LIQUIDITY_RESPONSE_NOT_CLASSIFIED"
                        diagnostics[reason] += 1
                        rejected.append(
                            _rejection_record(
                                pending,
                                symbol=symbol,
                                reason=reason,
                                timestamp_ns=timestamp_ns,
                                details=metrics,
                            )
                        )
                        pending = None
                elif pending.state == "REVERSAL_CONFIRMATION_PENDING":
                    direction = -pending.outward
                    structure_break = (
                        float(row["close"]) > float(pending.break_level)
                        if direction > 0
                        else float(row["close"]) < float(pending.break_level)
                    )
                    if structure_break and _confirmation_flow_holds(
                        row,
                        direction=direction,
                        config=cfg,
                        quote_ofi_confirmation_required=required_gate,
                    ):
                        signal = _emit_signal(
                            pending=pending,
                            family=REVERSAL_FAMILY,
                            direction=direction,
                            position=position,
                            timestamp_ns=timestamp_ns,
                            row=row,
                            target_levels=target_levels,
                            consumed=consumed,
                            symbol=symbol,
                            instrument_id=instrument_id,
                            tick=tick,
                            fee_rate=fee_rate,
                            minimum_net_reward_risk=minimum_net_reward_risk,
                            atr=atr,
                            stop_slippage_reserve=float(stop_reserves.iloc[position]),
                            diagnostics=diagnostics,
                            rejected=rejected,
                        )
                        if signal is not None:
                            signals.setdefault(timestamp_ns, []).append(signal)
                        pending = None
                elif pending.state == "ACCEPTANCE_WAIT_RETEST":
                    reclaimed = (
                        float(row["close"]) < pending.boundary.level
                        if pending.outward > 0
                        else float(row["close"]) > pending.boundary.level
                    )
                    if reclaimed:
                        reason = "ACCEPTED_BOUNDARY_RECLAIMED_BEFORE_RETEST_CONFIRMATION"
                        diagnostics[reason] += 1
                        rejected.append(
                            _rejection_record(
                                pending,
                                symbol=symbol,
                                reason=reason,
                                timestamp_ns=timestamp_ns,
                            )
                        )
                        pending = None
                    else:
                        touched = (
                            float(row["low"]) <= pending.boundary.level
                            if pending.outward > 0
                            else float(row["high"]) >= pending.boundary.level
                        )
                        pressure_abs = abs(float(row["aggressive_pressure_ratio"]))
                        weaker = pressure_abs <= float(
                            cfg.maximum_retest_pressure_fraction
                        ) * pending.interaction_pressure_abs
                        if touched and weaker:
                            pending.retest_position = position
                            pending.retest_time_ns = timestamp_ns
                            pending.retest_high = float(row["high"])
                            pending.retest_low = float(row["low"])
                            pending.state = "CONTINUATION_CONFIRMATION_PENDING"
                            pending.expiry_position = position + int(
                                cfg.confirmation_window_bars
                            )
                            pending.events.append(
                                _logic_event(
                                    pending,
                                    symbol=symbol,
                                    instrument_id=instrument_id,
                                    event_type="LOWER_PRESSURE_RETEST_HELD",
                                    event_time_ns=timestamp_ns,
                                    previous_state="ACCEPTANCE_WAIT_RETEST",
                                    next_state=pending.state,
                                    reason_code="BOUNDARY_TOUCHED_HELD_WITH_WEAKER_AGGRESSIVE_PRESSURE",
                                    reference_price=pending.boundary.level,
                                    details={
                                        "retest_pressure_abs": pressure_abs,
                                        "interaction_pressure_abs": pending.interaction_pressure_abs,
                                    },
                                )
                            )
                            diagnostics["CONTINUATION_RETEST_HELD"] += 1
                elif pending.state == "CONTINUATION_CONFIRMATION_PENDING":
                    reclaimed = (
                        float(row["close"]) < pending.boundary.level
                        if pending.outward > 0
                        else float(row["close"]) > pending.boundary.level
                    )
                    if reclaimed:
                        reason = "ACCEPTED_BOUNDARY_RECLAIMED_AFTER_RETEST"
                        diagnostics[reason] += 1
                        rejected.append(
                            _rejection_record(
                                pending,
                                symbol=symbol,
                                reason=reason,
                                timestamp_ns=timestamp_ns,
                            )
                        )
                        pending = None
                    else:
                        if pending.retest_high is None or pending.retest_low is None:
                            raise RuntimeError("continuation state lost retest geometry")
                        structure_break = (
                            float(row["close"]) > pending.retest_high
                            if pending.outward > 0
                            else float(row["close"]) < pending.retest_low
                        )
                        if structure_break and _confirmation_flow_holds(
                            row,
                            direction=pending.outward,
                            config=cfg,
                            quote_ofi_confirmation_required=required_gate,
                        ):
                            signal = _emit_signal(
                                pending=pending,
                                family=CONTINUATION_FAMILY,
                                direction=pending.outward,
                                position=position,
                                timestamp_ns=timestamp_ns,
                                row=row,
                                target_levels=target_levels,
                                consumed=consumed,
                                symbol=symbol,
                                instrument_id=instrument_id,
                                tick=tick,
                                fee_rate=fee_rate,
                                minimum_net_reward_risk=minimum_net_reward_risk,
                                atr=atr,
                                stop_slippage_reserve=float(stop_reserves.iloc[position]),
                                diagnostics=diagnostics,
                                rejected=rejected,
                            )
                            if signal is not None:
                                signals.setdefault(timestamp_ns, []).append(signal)
                            pending = None
                else:
                    raise RuntimeError(f"unknown quote resiliency state: {pending.state}")

        # A newly crossed level is never armed on the same bucket used to advance an existing
        # sequence.  This preserves one causal scenario at a time and prevents event aliasing.
        if not handled_pending and pending is None and interaction is not None:
            boundary, outward = interaction
            outward_pressure = outward * float(row["aggressive_pressure_ratio"])
            if outward_pressure < float(cfg.minimum_outward_pressure_ratio):
                diagnostics["EXTERNAL_INTERACTION_PRESSURE_TOO_LOW"] += 1
                continue
            scenario_counter += 1
            scenario_id = f"quote-resiliency-{symbol.lower()}-{scenario_counter:06d}"
            pending = _PendingScenario(
                scenario_id=scenario_id,
                boundary=boundary,
                outward=outward,
                interaction_position=position,
                interaction_time_ns=timestamp_ns,
                interaction_pressure_abs=abs(float(row["aggressive_pressure_ratio"])),
                expiry_position=position + int(cfg.response_window_bars),
                response_high=float(row["high"]),
                response_low=float(row["low"]),
            )
            pending.events.append(
                _logic_event(
                    pending,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="EXTERNAL_LIQUIDITY_INTERACTED",
                    event_time_ns=timestamp_ns,
                    previous_state="IDLE",
                    next_state="LIQUIDITY_RESPONSE",
                    reason_code="AGGRESSIVE_PRESSURE_CROSSED_COMPLETED_EXTERNAL_LEVEL",
                    reference_price=boundary.level,
                    details={
                        "outward_direction": outward,
                        "aggressive_pressure_ratio": float(
                            row["aggressive_pressure_ratio"]
                        ),
                        "boundary_source": boundary.source.value,
                    },
                )
            )
            diagnostics["EXTERNAL_INTERACTION_ARMED"] += 1

    if pending is not None:
        final_ns = int(data.index[-1].as_unit("ns").value)
        reason = "EVALUATION_ENDED_WITH_INCOMPLETE_SCENARIO"
        diagnostics[reason] += 1
        rejected.append(
            _rejection_record(
                pending,
                symbol=symbol,
                reason=reason,
                timestamp_ns=final_ns,
            )
        )

    return QuoteResiliencySignalBundle(
        signals_by_time_ns={
            timestamp: tuple(items) for timestamp, items in signals.items()
        },
        diagnostics=dict(diagnostics),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "CONTINUATION_FAMILY",
    "REVERSAL_FAMILY",
    "SIGNAL_REVISION",
    "QuoteResiliencyLogicEvent",
    "QuoteResiliencySignal",
    "QuoteResiliencySignalBundle",
    "build_quote_resiliency_signals",
]
