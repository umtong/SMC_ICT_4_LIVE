"""Causal external sweep -> displacement leg -> FVG retrace leg -> reacceleration.

V1 proved that external sweeps were frequent, but encoded both displacement and retracement as
single-candle predicates.  V2 corrects the scenario representation rather than loosening isolated
thresholds.  A displacement is a multi-minute directional leg which breaks a pre-sweep confirmed
internal swing and leaves an unfilled FVG.  A retracement is a subsequent multi-minute path into the
FVG whose opposing urgency is lower than the displacement leg.  A separate completed minute must
then reaccelerate through the retrace extreme before native L1 market entry.

All observations are completed and causal.  This module contains no order, fill, account, position,
sizing, PnL, or backtest engine logic.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite, sqrt
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    _context_for_ten_second_close,
    causal_stop_slippage_reserve_series,
)
from external_sweep_fvg_retrace_signals import (
    aggregate_completed_minutes,
    _confirmed_internal_swings,
    _cost_geometry,
    _crossed_and_reclaimed,
    _select_sweep,
    _select_target,
    _source_name,
    _structural_stop,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
    executable_quote_reference,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


SIGNAL_REVISION = "CAUSAL_EXTERNAL_SWEEP_MSS_FVG_LEG_RETRACE_REACCELERATION_V2"
SCENARIO_FAMILY = "EXTERNAL_SWEEP_MSS_FVG_LEG_RETRACE_REACCELERATION"


@dataclass(frozen=True, slots=True)
class ExternalSweepFvgLegConfig:
    minute_atr_bars: int = 60
    minimum_minute_history: int = 30
    internal_swing_span: int = 2
    sweep_extension_atr: float = 0.03
    reclaim_atr: float = 0.01
    maximum_displacement_minutes: int = 15
    minimum_displacement_net_atr: float = 0.35
    minimum_displacement_efficiency: float = 0.35
    minimum_impulse_body_atr: float = 0.25
    minimum_impulse_imbalance: float = 0.06
    minimum_impulse_volume_ratio: float = 1.00
    minimum_impulse_trade_ratio: float = 1.00
    minimum_fvg_atr: float = 0.01
    maximum_retrace_minutes: int = 30
    retrace_fraction: float = 0.50
    maximum_retrace_energy_fraction: float = 0.85
    maximum_reacceleration_minutes: int = 8
    minimum_reacceleration_body_atr: float = 0.15
    minimum_reacceleration_imbalance: float = 0.05
    reacceleration_close_location: float = 0.60
    minimum_reacceleration_energy_multiple: float = 1.05
    stop_buffer_atr: float = 0.03
    minimum_stop_atr: float = 0.25

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ExternalSweepFvgLegConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    def validate(self) -> None:
        positive_ints = (
            self.minute_atr_bars,
            self.minimum_minute_history,
            self.internal_swing_span,
            self.maximum_displacement_minutes,
            self.maximum_retrace_minutes,
            self.maximum_reacceleration_minutes,
        )
        if any(value <= 0 for value in positive_ints):
            raise ValueError("leg-state integer contracts must be positive")
        if self.minimum_minute_history > self.minute_atr_bars:
            raise ValueError("minimum history cannot exceed activity lookback")
        positive = (
            self.sweep_extension_atr,
            self.reclaim_atr,
            self.minimum_displacement_net_atr,
            self.minimum_displacement_efficiency,
            self.minimum_impulse_body_atr,
            self.minimum_impulse_imbalance,
            self.minimum_impulse_volume_ratio,
            self.minimum_impulse_trade_ratio,
            self.minimum_fvg_atr,
            self.retrace_fraction,
            self.maximum_retrace_energy_fraction,
            self.minimum_reacceleration_body_atr,
            self.minimum_reacceleration_imbalance,
            self.reacceleration_close_location,
            self.minimum_reacceleration_energy_multiple,
            self.stop_buffer_atr,
            self.minimum_stop_atr,
        )
        if any(value <= 0.0 for value in positive):
            raise ValueError("leg-state ratio contracts must be positive")
        if not 0.0 < self.retrace_fraction < 1.0:
            raise ValueError("retrace fraction must be in (0, 1)")
        if not 0.0 < self.minimum_displacement_efficiency <= 1.0:
            raise ValueError("displacement efficiency must be in (0, 1]")
        if not 0.0 < self.maximum_retrace_energy_fraction < 1.0:
            raise ValueError("retrace energy fraction must be in (0, 1)")
        if not 0.5 < self.reacceleration_close_location < 1.0:
            raise ValueError("reacceleration close location must be in (0.5, 1)")


@dataclass(slots=True)
class _PendingLeg:
    scenario_id: str
    boundary: ExternalLevel
    direction: int
    sweep_position: int
    sweep_time_ns: int
    sweep_close: float
    sweep_extreme: float
    internal_break_level: float
    internal_break_time_ns: int
    displacement_expiry_position: int
    state: str = "WAIT_DISPLACEMENT_LEG"
    displacement_positions: list[int] = field(default_factory=list)
    displacement_directional_urgencies: list[float] = field(default_factory=list)
    displacement_signed_volume: float = 0.0
    displacement_volume: float = 0.0
    displacement_path: float = 0.0
    displacement_previous_close: float | None = None
    displacement_impulse_bars: int = 0
    mss_break_time_ns: int | None = None
    active_fvg_low: float | None = None
    active_fvg_high: float | None = None
    active_fvg_time_ns: int | None = None
    displacement_time_ns: int | None = None
    displacement_energy: float | None = None
    displacement_flow_share: float | None = None
    retrace_expiry_position: int | None = None
    retrace_positions: list[int] = field(default_factory=list)
    retrace_opposing_urgencies: list[float] = field(default_factory=list)
    retrace_signed_volume: float = 0.0
    retrace_volume: float = 0.0
    retrace_best_energy_ratio: float = float("inf")
    fvg_ce_touched: bool = False
    retest_position: int | None = None
    retest_time_ns: int | None = None
    retest_high: float | None = None
    retest_low: float | None = None
    retrace_energy: float | None = None
    reacceleration_expiry_position: int | None = None
    events: list[QuoteResiliencyLogicEvent] = field(default_factory=list)


def _activity(row: pd.Series) -> float:
    volume_ratio = max(0.0, float(row["volume_ratio"]))
    trade_ratio = max(0.0, float(row["trade_ratio"]))
    return sqrt(volume_ratio * trade_ratio)


def _directional_urgency(row: pd.Series, direction: int) -> float:
    return _activity(row) * max(0.0, direction * float(row["imbalance"]))


def _opposing_urgency(row: pd.Series, direction: int) -> float:
    return _activity(row) * max(0.0, -direction * float(row["imbalance"]))


def _active_fvg_survives(row: pd.Series, pending: _PendingLeg) -> bool:
    if pending.active_fvg_low is None or pending.active_fvg_high is None:
        return False
    if pending.direction > 0:
        return float(row["low"]) > pending.active_fvg_low
    return float(row["high"]) < pending.active_fvg_high


def _new_directional_fvg(
    minute: pd.DataFrame,
    position: int,
    *,
    direction: int,
    atr: float,
    tick: float,
    config: ExternalSweepFvgLegConfig,
) -> tuple[float, float] | None:
    if position < 2:
        return None
    row = minute.iloc[position]
    two_back = minute.iloc[position - 2]
    minimum_gap = max(tick, config.minimum_fvg_atr * atr)
    if direction > 0:
        low = float(two_back["high"])
        high = float(row["low"])
    else:
        low = float(row["high"])
        high = float(two_back["low"])
    return (low, high) if high >= low + minimum_gap else None


def _update_displacement_leg(
    minute: pd.DataFrame,
    position: int,
    pending: _PendingLeg,
    *,
    tick: float,
    config: ExternalSweepFvgLegConfig,
) -> bool:
    row = minute.iloc[position]
    timestamp_ns = int(minute.index[position].as_unit("ns").value)
    atr = float(row["atr"])
    previous = (
        pending.sweep_close
        if pending.displacement_previous_close is None
        else pending.displacement_previous_close
    )
    pending.displacement_positions.append(position)
    pending.displacement_path += abs(float(row["close"]) - previous)
    pending.displacement_previous_close = float(row["close"])
    pending.displacement_directional_urgencies.append(
        _directional_urgency(row, pending.direction)
    )
    pending.displacement_signed_volume += float(row["signed_volume"])
    pending.displacement_volume += float(row["volume"])

    impulse = (
        pending.direction * float(row["close"] - row["open"])
        >= config.minimum_impulse_body_atr * atr
        and pending.direction * float(row["imbalance"])
        >= config.minimum_impulse_imbalance
        and float(row["volume_ratio"]) >= config.minimum_impulse_volume_ratio
        and float(row["trade_ratio"]) >= config.minimum_impulse_trade_ratio
    )
    if impulse:
        pending.displacement_impulse_bars += 1

    if pending.active_fvg_low is not None and not _active_fvg_survives(row, pending):
        pending.active_fvg_low = None
        pending.active_fvg_high = None
        pending.active_fvg_time_ns = None
    fvg = _new_directional_fvg(
        minute,
        position,
        direction=pending.direction,
        atr=atr,
        tick=tick,
        config=config,
    )
    if fvg is not None:
        pending.active_fvg_low, pending.active_fvg_high = fvg
        pending.active_fvg_time_ns = timestamp_ns

    broke = (
        float(row["close"]) > pending.internal_break_level + tick
        if pending.direction > 0
        else float(row["close"]) < pending.internal_break_level - tick
    )
    if broke and pending.mss_break_time_ns is None:
        pending.mss_break_time_ns = timestamp_ns

    directional_net = pending.direction * (float(row["close"]) - pending.sweep_close)
    efficiency = directional_net / max(pending.displacement_path, tick)
    energy = float(np.mean(pending.displacement_directional_urgencies))
    flow_share = (
        pending.direction * pending.displacement_signed_volume
        / max(pending.displacement_volume, 1e-12)
    )
    fvg_is_positioned = (
        pending.active_fvg_low is not None
        and pending.active_fvg_high is not None
        and (
            float(row["close"]) > pending.active_fvg_high
            if pending.direction > 0
            else float(row["close"]) < pending.active_fvg_low
        )
    )
    confirmed = (
        pending.mss_break_time_ns is not None
        and fvg_is_positioned
        and pending.displacement_impulse_bars >= 1
        and directional_net >= config.minimum_displacement_net_atr * atr
        and efficiency >= config.minimum_displacement_efficiency
        and energy > 0.0
        and flow_share > 0.0
    )
    if confirmed:
        pending.displacement_time_ns = timestamp_ns
        pending.displacement_energy = energy
        pending.displacement_flow_share = flow_share
        pending.retrace_expiry_position = position + config.maximum_retrace_minutes
        pending.state = "WAIT_RETRACE_LEG"
        pending.events.append(
            QuoteResiliencyLogicEvent(
                scenario_id=pending.scenario_id,
                symbol="",
                instrument_id="",
                event_type="MSS_FVG_DISPLACEMENT_LEG_CONFIRMED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="EXTERNAL_LIQUIDITY_SWEPT",
                next_state="MSS_FVG_DISPLACEMENT_LEG",
                reason_code="MULTI_MINUTE_DIRECTIONAL_LEG_BROKE_PRE_SWEEP_SWING_AND_LEFT_UNFILLED_FVG",
                reference_price=float(row["close"]),
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "leg_bars": len(pending.displacement_positions),
                    "directional_net_atr": directional_net / atr,
                    "directional_efficiency": efficiency,
                    "directional_energy": energy,
                    "directional_flow_share": flow_share,
                    "impulse_bars": pending.displacement_impulse_bars,
                    "mss_break_time_ns": pending.mss_break_time_ns,
                    "fvg_low": pending.active_fvg_low,
                    "fvg_high": pending.active_fvg_high,
                    "fvg_time_ns": pending.active_fvg_time_ns,
                },
            )
        )
    return confirmed


def _update_retrace_leg(
    row: pd.Series,
    position: int,
    timestamp_ns: int,
    pending: _PendingLeg,
    config: ExternalSweepFvgLegConfig,
) -> tuple[bool, float]:
    if (
        pending.active_fvg_low is None
        or pending.active_fvg_high is None
        or pending.displacement_energy is None
        or pending.displacement_flow_share is None
    ):
        raise RuntimeError("retrace leg started without complete displacement state")
    pending.retrace_positions.append(position)
    pending.retrace_opposing_urgencies.append(
        _opposing_urgency(row, pending.direction)
    )
    pending.retrace_signed_volume += float(row["signed_volume"])
    pending.retrace_volume += float(row["volume"])
    retrace_energy = float(np.mean(pending.retrace_opposing_urgencies))
    opposing_flow_share = max(
        0.0,
        -pending.direction * pending.retrace_signed_volume
        / max(pending.retrace_volume, 1e-12),
    )
    displacement_strength = pending.displacement_energy + max(
        pending.displacement_flow_share, 0.0
    )
    retrace_strength = retrace_energy + opposing_flow_share
    ratio = retrace_strength / max(displacement_strength, 1e-12)
    pending.retrace_best_energy_ratio = min(
        pending.retrace_best_energy_ratio,
        ratio,
    )
    ce = pending.active_fvg_low + config.retrace_fraction * (
        pending.active_fvg_high - pending.active_fvg_low
    )
    if pending.direction > 0:
        touched = float(row["low"]) <= ce
        held = float(row["close"]) >= pending.active_fvg_low
    else:
        touched = float(row["high"]) >= ce
        held = float(row["close"]) <= pending.active_fvg_high
    if touched:
        pending.fvg_ce_touched = True
    accepted = (
        touched
        and held
        and ratio <= config.maximum_retrace_energy_fraction
    )
    if accepted:
        pending.retest_position = position
        pending.retest_time_ns = timestamp_ns
        pending.retest_high = float(row["high"])
        pending.retest_low = float(row["low"])
        pending.retrace_energy = retrace_energy
        pending.reacceleration_expiry_position = (
            position + config.maximum_reacceleration_minutes
        )
        pending.state = "WAIT_REACCELERATION"
        pending.events.append(
            QuoteResiliencyLogicEvent(
                scenario_id=pending.scenario_id,
                symbol="",
                instrument_id="",
                event_type="LOW_ENERGY_FVG_RETRACE_LEG_HELD",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="MSS_FVG_DISPLACEMENT_LEG",
                next_state="LOW_ENERGY_FVG_RETRACE_LEG",
                reason_code="FVG_CONSEQUENT_ENCROACHMENT_TOUCHED_WITH_LOWER_OPPOSING_LEG_ENERGY",
                reference_price=float(row["close"]),
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "leg_bars": len(pending.retrace_positions),
                    "retrace_energy": retrace_energy,
                    "opposing_flow_share": opposing_flow_share,
                    "retrace_to_displacement_energy_ratio": ratio,
                    "consequent_encroachment": ce,
                },
            )
        )
    return accepted, ratio


def _reaccelerates(
    row: pd.Series,
    pending: _PendingLeg,
    config: ExternalSweepFvgLegConfig,
    tick: float,
) -> bool:
    if (
        pending.retest_high is None
        or pending.retest_low is None
        or pending.retrace_energy is None
    ):
        raise RuntimeError("reacceleration evaluated without completed retrace state")
    atr = float(row["atr"])
    if pending.direction > 0:
        broke = float(row["close"]) > pending.retest_high + tick
        located = float(row["close_location"]) >= config.reacceleration_close_location
    else:
        broke = float(row["close"]) < pending.retest_low - tick
        located = float(row["close_location"]) <= 1.0 - config.reacceleration_close_location
    urgency = _directional_urgency(row, pending.direction)
    return (
        broke
        and located
        and pending.direction * float(row["close"] - row["open"])
        >= config.minimum_reacceleration_body_atr * atr
        and pending.direction * float(row["imbalance"])
        >= config.minimum_reacceleration_imbalance
        and urgency
        >= config.minimum_reacceleration_energy_multiple
        * max(pending.retrace_energy, 1e-12)
    )


def _signal_from_reacceleration(
    *,
    data: pd.DataFrame,
    minute: pd.DataFrame,
    position: int,
    pending: _PendingLeg,
    target_levels: tuple[ExternalLevel, ...],
    consumed: set[str],
    stop_reserves: pd.Series,
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    config: ExternalSweepFvgLegConfig,
) -> tuple[QuoteResiliencySignal | None, str]:
    timestamp = minute.index[position]
    if timestamp not in data.index:
        return None, "NO_EXACT_TEN_SECOND_COMPLETION_ROW"
    ten_second_row = data.loc[timestamp]
    if not bool(ten_second_row.get("native_quote_snapshot_observable", False)):
        return None, "NO_NATIVE_L1_AT_REACCELERATION"
    entry = executable_quote_reference(ten_second_row, pending.direction)
    target = _select_target(
        target_levels,
        direction=pending.direction,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
        consumed=consumed,
    )
    if target is None:
        return None, "NO_ACTIVE_OPPOSITE_EXTERNAL_TARGET"
    atr = float(minute.iloc[position]["atr"])
    stop = _structural_stop(
        direction=pending.direction,
        entry=entry,
        sweep_extreme=pending.sweep_extreme,
        atr=atr,
        config=config,
    )
    reserve = float(stop_reserves.loc[timestamp])
    geometry = _cost_geometry(
        direction=pending.direction,
        quote_reference=entry,
        stop=stop,
        target=float(target.level),
        fee_rate=fee_rate,
        tick=tick,
        stop_slippage_reserve=reserve,
    )
    if geometry is None:
        return None, "INVALID_COST_AFTER_GEOMETRY"
    loss, gain, net_reward_risk = geometry
    if net_reward_risk < minimum_net_reward_risk:
        return None, "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
    if pending.displacement_time_ns is None or pending.retest_time_ns is None:
        raise RuntimeError("signal emitted before completed leg sequence")
    timestamp_ns = int(timestamp.as_unit("ns").value)
    events: list[QuoteResiliencyLogicEvent] = []
    for event in pending.events:
        events.append(
            QuoteResiliencyLogicEvent(
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
                details=dict(event.details),
            )
        )
    events.append(
        QuoteResiliencyLogicEvent(
            scenario_id=pending.scenario_id,
            symbol=symbol,
            instrument_id=instrument_id,
            event_type="SEPARATE_REACCELERATION_CONFIRMED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="LOW_ENERGY_FVG_RETRACE_LEG",
            next_state="CONFIRMED",
            reason_code="DIRECTIONAL_ENERGY_RETURNED_AND_RETRACE_EXTREME_BROKE",
            reference_price=entry,
            details={
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "native_l1_entry_reference": entry,
                "net_reward_risk": net_reward_risk,
            },
        )
    )
    signal = QuoteResiliencySignal(
        scenario_id=pending.scenario_id,
        scenario_family=SCENARIO_FAMILY,
        symbol=symbol,
        instrument_id=instrument_id,
        direction=pending.direction,
        signal_index=position,
        signal_time_ns=timestamp_ns,
        boundary_id=pending.boundary.level_id,
        boundary_source=_source_name(pending.boundary),
        boundary_level=float(pending.boundary.level),
        target_id=target.level_id,
        target_source=_source_name(target),
        external_target=float(target.level),
        entry_reference=entry,
        structural_stop=stop,
        stop_reference=pending.sweep_extreme,
        stop_reference_source="EXTERNAL_SWEEP_EXTREME",
        atr=atr,
        causal_stop_slippage_reserve=reserve,
        expected_loss_per_unit=loss,
        expected_gain_per_unit=gain,
        net_reward_risk=net_reward_risk,
        interaction_time_ns=pending.sweep_time_ns,
        response_time_ns=pending.displacement_time_ns,
        retest_time_ns=pending.retest_time_ns,
        events=tuple(events),
        details={
            "scenario_family": SCENARIO_FAMILY,
            "signal_revision": SIGNAL_REVISION,
            "internal_break_level": pending.internal_break_level,
            "internal_break_time_ns": pending.internal_break_time_ns,
            "sweep_extreme": pending.sweep_extreme,
            "fvg_low": pending.active_fvg_low,
            "fvg_high": pending.active_fvg_high,
            "displacement_leg_bars": len(pending.displacement_positions),
            "displacement_energy": pending.displacement_energy,
            "displacement_flow_share": pending.displacement_flow_share,
            "retrace_leg_bars": len(pending.retrace_positions),
            "retrace_energy": pending.retrace_energy,
            "retrace_best_energy_ratio": pending.retrace_best_energy_ratio,
            "entry_mode": "NATIVE_L1_MARKET_AFTER_SEPARATE_COMPLETED_MINUTE_REACCELERATION",
            "invalidation_contract": "SWEEP_EXTREME_PLUS_CAUSAL_ATR_BUFFER",
            "target_contract": "NEAREST_UNCONSUMED_OPPOSITE_COMPLETED_EXTERNAL_LIQUIDITY",
        },
    )
    return signal, "SIGNAL"


def build_external_sweep_fvg_leg_signals(
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
    config: ExternalSweepFvgLegConfig,
) -> QuoteResiliencySignalBundle:
    """Build future-free external-sweep leg-state signals."""

    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid execution cost contract")
    minute = aggregate_completed_minutes(data, config)  # structural duck-typed config fields
    swing_highs, swing_lows = _confirmed_internal_swings(
        minute,
        span=config.internal_swing_span,
    )
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}
    consumed: set[str] = set()
    pending: _PendingLeg | None = None
    scenario_counter = 0

    def reject(reason: str, timestamp_ns: int, active: _PendingLeg) -> None:
        diagnostics[reason] += 1
        rejected.append(
            {
                "scenario_id": active.scenario_id,
                "symbol": symbol,
                "boundary_id": active.boundary.level_id,
                "scenario_family": SCENARIO_FAMILY,
                "reason": reason,
                "sweep_time_ns": active.sweep_time_ns,
                "rejected_time_ns": timestamp_ns,
                "state": active.state,
                "signal_revision": SIGNAL_REVISION,
                "mss_break_time_ns": active.mss_break_time_ns,
                "active_fvg_low": active.active_fvg_low,
                "active_fvg_high": active.active_fvg_high,
                "displacement_leg_bars": len(active.displacement_positions),
                "displacement_impulse_bars": active.displacement_impulse_bars,
                "fvg_ce_touched": active.fvg_ce_touched,
                "retrace_leg_bars": len(active.retrace_positions),
                "best_retrace_energy_ratio": (
                    None
                    if not isfinite(active.retrace_best_energy_ratio)
                    else active.retrace_best_energy_ratio
                ),
            }
        )

    for position in range(1, len(minute.index)):
        row = minute.iloc[position]
        timestamp = minute.index[position]
        timestamp_ns = int(timestamp.as_unit("ns").value)
        required = (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "signed_volume",
            "trade_count",
            "imbalance",
            "atr",
            "volume_ratio",
            "trade_ratio",
            "close_location",
        )
        if not all(isfinite(float(row[name])) for name in required) or float(row["atr"]) <= 0.0:
            diagnostics["UNOBSERVABLE_COMPLETED_MINUTE"] += 1
            continue
        context = _context_for_ten_second_close(
            timestamp_ns=timestamp_ns,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
        )
        if context is None:
            diagnostics["NO_COMPLETE_CAUSAL_EXTERNAL_CONTEXT"] += 1
            continue
        _five_bar, boundary_levels, target_levels = context
        atr = float(row["atr"])
        sweeps, crossed = _crossed_and_reclaimed(
            boundary_levels,
            previous_close=float(minute.iloc[position - 1]["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            tick=tick,
            config=config,
            consumed=consumed,
        )
        for level in crossed:
            consumed.add(level.level_id)
            diagnostics["EXTERNAL_LEVEL_FIRST_CROSS"] += 1

        handled_pending = pending is not None
        if pending is not None:
            direction = pending.direction
            pending.sweep_extreme = (
                min(pending.sweep_extreme, float(row["low"]))
                if direction > 0 and pending.state == "WAIT_DISPLACEMENT_LEG"
                else max(pending.sweep_extreme, float(row["high"]))
                if direction < 0 and pending.state == "WAIT_DISPLACEMENT_LEG"
                else pending.sweep_extreme
            )
            invalidated = (
                float(row["low"]) <= pending.sweep_extreme - tick
                if direction > 0 and pending.state != "WAIT_DISPLACEMENT_LEG"
                else float(row["high"]) >= pending.sweep_extreme + tick
                if direction < 0 and pending.state != "WAIT_DISPLACEMENT_LEG"
                else False
            )
            if invalidated:
                reject("SWEEP_EXTREME_INVALIDATED_AFTER_DISPLACEMENT", timestamp_ns, pending)
                pending = None
            elif pending.state == "WAIT_DISPLACEMENT_LEG":
                accepted_outside = (
                    float(row["close"]) < pending.boundary.level - config.reclaim_atr * atr
                    if direction > 0
                    else float(row["close"]) > pending.boundary.level + config.reclaim_atr * atr
                )
                if accepted_outside:
                    reject("SWEEP_ACCEPTED_OUTSIDE_BEFORE_DISPLACEMENT", timestamp_ns, pending)
                    pending = None
                elif position > pending.displacement_expiry_position:
                    reject("NO_MSS_FVG_DISPLACEMENT_LEG_BEFORE_EXPIRY", timestamp_ns, pending)
                    pending = None
                elif _update_displacement_leg(
                    minute,
                    position,
                    pending,
                    tick=tick,
                    config=config,
                ):
                    diagnostics["MSS_FVG_DISPLACEMENT_LEG"] += 1
            elif pending.state == "WAIT_RETRACE_LEG":
                if (
                    direction > 0
                    and pending.active_fvg_low is not None
                    and float(row["close"]) < pending.active_fvg_low
                ) or (
                    direction < 0
                    and pending.active_fvg_high is not None
                    and float(row["close"]) > pending.active_fvg_high
                ):
                    reject("FVG_CLOSED_THROUGH_BEFORE_LOW_ENERGY_RETRACE", timestamp_ns, pending)
                    pending = None
                elif (
                    pending.retrace_expiry_position is not None
                    and position > pending.retrace_expiry_position
                ):
                    reject("NO_LOW_ENERGY_FVG_RETRACE_LEG_BEFORE_EXPIRY", timestamp_ns, pending)
                    pending = None
                else:
                    accepted, ratio = _update_retrace_leg(
                        row,
                        position,
                        timestamp_ns,
                        pending,
                        config,
                    )
                    diagnostics["RETRACE_LEG_OBSERVATIONS"] += 1
                    if pending.fvg_ce_touched:
                        diagnostics["FVG_CE_TOUCHED"] += 1
                    if accepted:
                        diagnostics["LOW_ENERGY_FVG_RETRACE_LEG"] += 1
                    elif pending.fvg_ce_touched and ratio > config.maximum_retrace_energy_fraction:
                        diagnostics["FVG_TOUCH_RETRACE_ENERGY_TOO_HIGH"] += 1
            elif pending.state == "WAIT_REACCELERATION":
                if (
                    pending.reacceleration_expiry_position is not None
                    and position > pending.reacceleration_expiry_position
                ):
                    reject("NO_SEPARATE_REACCELERATION_BEFORE_EXPIRY", timestamp_ns, pending)
                    pending = None
                elif _reaccelerates(row, pending, config, tick):
                    signal, reason = _signal_from_reacceleration(
                        data=data,
                        minute=minute,
                        position=position,
                        pending=pending,
                        target_levels=target_levels,
                        consumed=consumed,
                        stop_reserves=stop_reserves,
                        symbol=symbol,
                        instrument_id=instrument_id,
                        tick=tick,
                        fee_rate=fee_rate,
                        minimum_net_reward_risk=minimum_net_reward_risk,
                        config=config,
                    )
                    if signal is None:
                        reject(reason, timestamp_ns, pending)
                    else:
                        signals.setdefault(signal.signal_time_ns, []).append(signal)
                        diagnostics["REACCELERATION_SIGNAL"] += 1
                        diagnostics[f"SIGNAL_{signal.direction_name}"] += 1
                        diagnostics[f"SIGNAL_BOUNDARY_{signal.boundary_source}"] += 1
                        diagnostics[f"SIGNAL_TARGET_{signal.target_source}"] += 1
                    pending = None
            else:
                raise RuntimeError(f"unknown leg state: {pending.state}")

        if handled_pending or pending is not None:
            continue
        selected = _select_sweep(sweeps)
        if selected is None:
            if sweeps:
                diagnostics["AMBIGUOUS_MULTI_DIRECTION_SWEEP"] += 1
            continue
        boundary, direction = selected
        internal = swing_highs[position] if direction > 0 else swing_lows[position]
        if internal is None:
            diagnostics["SWEEP_WITHOUT_CONFIRMED_INTERNAL_SWING"] += 1
            continue
        internal_level, internal_time_ns = internal
        if internal_time_ns >= timestamp_ns:
            raise RuntimeError("internal swing was not confirmed before sweep")
        scenario_counter += 1
        scenario_id = f"external-sweep-leg-{symbol.lower()}-{scenario_counter:06d}"
        sweep_extreme = float(row["low"] if direction > 0 else row["high"])
        pending = _PendingLeg(
            scenario_id=scenario_id,
            boundary=boundary,
            direction=direction,
            sweep_position=position,
            sweep_time_ns=timestamp_ns,
            sweep_close=float(row["close"]),
            sweep_extreme=sweep_extreme,
            internal_break_level=float(internal_level),
            internal_break_time_ns=int(internal_time_ns),
            displacement_expiry_position=position + config.maximum_displacement_minutes,
            displacement_previous_close=float(row["close"]),
            events=[
                QuoteResiliencyLogicEvent(
                    scenario_id=scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="EXTERNAL_LIQUIDITY_SWEEP_RECLAIMED",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state="IDLE",
                    next_state="EXTERNAL_LIQUIDITY_SWEPT",
                    reason_code="COMPLETED_EXTERNAL_LEVEL_FIRST_CROSS_AND_CLOSE_BACK_INSIDE",
                    reference_price=float(boundary.level),
                    details={
                        "scenario_family": SCENARIO_FAMILY,
                        "signal_revision": SIGNAL_REVISION,
                        "boundary_source": _source_name(boundary),
                        "sweep_extreme": sweep_extreme,
                        "internal_break_level": internal_level,
                        "internal_break_time_ns": internal_time_ns,
                    },
                )
            ],
        )
        diagnostics["EXTERNAL_SWEEP_RECLAIMED"] += 1

    if pending is not None:
        reject(
            "OPEN_DETECTOR_STATE_AT_DATA_END",
            int(minute.index[-1].as_unit("ns").value),
            pending,
        )
    immutable = {
        timestamp_ns: tuple(
            sorted(items, key=lambda signal: signal.net_reward_risk, reverse=True)
        )
        for timestamp_ns, items in sorted(signals.items())
    }
    diagnostics["SIGNALS"] = sum(len(items) for items in immutable.values())
    diagnostics["SIGNAL_TIMES"] = len(immutable)
    diagnostics["COMPLETED_MINUTES"] = len(minute.index)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted((key, int(value)) for key, value in diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "ExternalSweepFvgLegConfig",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "build_external_sweep_fvg_leg_signals",
]
