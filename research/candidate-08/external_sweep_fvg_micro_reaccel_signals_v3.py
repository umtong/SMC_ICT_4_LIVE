"""Causal external sweep/FVG leg context with completed ten-second micro reacceleration.

The one-minute state machine remains responsible for external-liquidity sweep/reclaim, pre-sweep
internal MSS, displacement/FVG formation, and lower-energy retracement into consequent encroachment.
Execution confirmation moves to its proper lower timeframe: after the retracement is complete, the
latest causally confirmed ten-second pivot inside the retracement is frozen.  A later completed
10-second row must break that pivot with aligned aggressive trade pressure, displayed quote OFI,
directional body/close location, normal spread, and an executable native L1 snapshot.

No threshold is selected from future outcomes.  The module contains no order, fill, account,
position, sizing, PnL, or backtest engine logic; NautilusTrader remains authoritative.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import (
    _context_for_ten_second_close,
    causal_stop_slippage_reserve_series,
)
from external_sweep_fvg_leg_signals_v2 import (
    ExternalSweepFvgLegConfig,
    _PendingLeg,
    _confirmed_internal_swings,
    _cost_geometry,
    _crossed_and_reclaimed,
    _select_sweep,
    _select_target,
    _source_name,
    _structural_stop,
    _update_displacement_leg,
    _update_retrace_leg,
    aggregate_completed_minutes,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
    executable_quote_reference,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


SIGNAL_REVISION = "CAUSAL_EXTERNAL_SWEEP_MSS_FVG_MICRO_REACCELERATION_V3"
SCENARIO_FAMILY = "EXTERNAL_SWEEP_MSS_FVG_MICRO_REACCELERATION"


@dataclass(frozen=True, slots=True)
class ExternalSweepFvgMicroConfig(ExternalSweepFvgLegConfig):
    micro_swing_span: int = 2
    micro_confirmation_seconds: int = 180
    minimum_micro_aggressive_pressure_ratio: float = 0.50
    minimum_micro_quote_ofi_ratio: float = 0.25
    minimum_micro_body_fraction: float = 0.50
    micro_close_location: float = 0.65
    maximum_micro_spread_ratio: float = 1.50

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "ExternalSweepFvgMicroConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    def validate(self) -> None:
        ExternalSweepFvgLegConfig.validate(self)
        if self.micro_swing_span <= 0:
            raise ValueError("micro_swing_span must be positive")
        if self.micro_confirmation_seconds <= 0 or self.micro_confirmation_seconds % 10:
            raise ValueError("micro_confirmation_seconds must be a positive ten-second multiple")
        positive = (
            self.minimum_micro_aggressive_pressure_ratio,
            self.minimum_micro_quote_ofi_ratio,
            self.minimum_micro_body_fraction,
            self.micro_close_location,
            self.maximum_micro_spread_ratio,
        )
        if any(value <= 0.0 for value in positive):
            raise ValueError("micro confirmation thresholds must be positive")
        if not 0.0 < self.minimum_micro_body_fraction <= 1.0:
            raise ValueError("minimum_micro_body_fraction must be in (0, 1]")
        if not 0.5 < self.micro_close_location < 1.0:
            raise ValueError("micro_close_location must be in (0.5, 1)")
        if self.maximum_micro_spread_ratio < 1.0:
            raise ValueError("maximum_micro_spread_ratio must be at least 1")


@dataclass(frozen=True, slots=True)
class FrozenMicroBreak:
    level: float
    pivot_time_ns: int
    source: str
    frozen_time_ns: int


@dataclass(frozen=True, slots=True)
class MicroTrigger:
    timestamp: pd.Timestamp
    row: pd.Series
    metrics: dict[str, float | int | str]


_MICRO_REQUIRED_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "aggressive_pressure_ratio",
    "quote_ofi_ratio",
    "spread_median_ratio",
    "native_quote_snapshot_observable",
    "bid_close",
    "ask_close",
)


def _frozen_micro_break(
    data: pd.DataFrame,
    *,
    direction: int,
    start_time_ns: int,
    end_time_ns: int,
    span: int,
) -> FrozenMicroBreak:
    """Freeze the latest pivot confirmed entirely before the retracement completion time.

    For a long reversal the execution transition is a break above the latest retracement pivot high;
    for a short reversal it is a break below the latest retracement pivot low.  If a short retrace
    contains no fully confirmed pivot, the final completed 30-second micro-range edge is used.  The
    fallback is fixed ex ante and remains fully causal.
    """

    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    index_ns = data.index.as_unit("ns").asi8
    mask = (index_ns > int(start_time_ns)) & (index_ns <= int(end_time_ns))
    subset = data.loc[mask, ["high", "low"]].copy()
    if subset.empty:
        raise ValueError("no completed ten-second retracement rows were available")
    highs = pd.to_numeric(subset["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(subset["low"], errors="coerce").to_numpy(dtype=float)
    times = subset.index.as_unit("ns").asi8
    if not np.isfinite(highs).all() or not np.isfinite(lows).all():
        raise ValueError("retracement microstructure contains nonfinite prices")

    latest: tuple[float, int] | None = None
    if len(subset.index) >= 2 * span + 1:
        for candidate in range(span, len(subset.index) - span):
            if direction > 0:
                left = highs[candidate - span : candidate]
                right = highs[candidate + 1 : candidate + span + 1]
                if highs[candidate] > float(np.max(left)) and highs[candidate] >= float(np.max(right)):
                    latest = (float(highs[candidate]), int(times[candidate]))
            else:
                left = lows[candidate - span : candidate]
                right = lows[candidate + 1 : candidate + span + 1]
                if lows[candidate] < float(np.min(left)) and lows[candidate] <= float(np.min(right)):
                    latest = (float(lows[candidate]), int(times[candidate]))
    if latest is not None:
        return FrozenMicroBreak(
            level=latest[0],
            pivot_time_ns=latest[1],
            source="LATEST_CAUSALLY_CONFIRMED_RETRACE_TEN_SECOND_PIVOT",
            frozen_time_ns=int(end_time_ns),
        )

    tail = subset.iloc[-min(3, len(subset.index)) :]
    if direction > 0:
        edge_index = tail["high"].astype(float).idxmax()
        level = float(tail.loc[edge_index, "high"])
    else:
        edge_index = tail["low"].astype(float).idxmin()
        level = float(tail.loc[edge_index, "low"])
    return FrozenMicroBreak(
        level=level,
        pivot_time_ns=int(edge_index.as_unit("ns").value),
        source="FINAL_COMPLETED_THIRTY_SECOND_RETRACE_RANGE_EDGE",
        frozen_time_ns=int(end_time_ns),
    )


def _micro_trigger_metrics(
    row: pd.Series,
    *,
    direction: int,
    break_level: float,
    tick: float,
) -> dict[str, float | int | str] | None:
    try:
        values = {name: float(row[name]) for name in _MICRO_REQUIRED_COLUMNS[:-3]}
        spread_ratio = float(row["spread_median_ratio"])
    except (KeyError, TypeError, ValueError):
        return None
    numeric = tuple(values.values()) + (spread_ratio,)
    if not all(isfinite(value) for value in numeric):
        return None
    high = values["high"]
    low = values["low"]
    open_price = values["open"]
    close = values["close"]
    width = high - low
    if width <= 0.0:
        return None
    body_fraction = direction * (close - open_price) / width
    close_location = (close - low) / width
    directional_location = close_location if direction > 0 else 1.0 - close_location
    broke = close > break_level + tick if direction > 0 else close < break_level - tick
    return {
        "broke_frozen_micro_level": int(broke),
        "frozen_micro_break_level": break_level,
        "directional_body_fraction": body_fraction,
        "directional_close_location": directional_location,
        "directional_aggressive_pressure_ratio": direction
        * values["aggressive_pressure_ratio"],
        "directional_quote_ofi_ratio": direction * values["quote_ofi_ratio"],
        "spread_median_ratio": spread_ratio,
        "close": close,
        "high": high,
        "low": low,
    }


def _scan_micro_confirmation(
    data: pd.DataFrame,
    *,
    pending: _PendingLeg,
    frozen: FrozenMicroBreak,
    start_after_ns: int,
    end_at_ns: int,
    tick: float,
    config: ExternalSweepFvgMicroConfig,
    diagnostics: Counter[str],
) -> tuple[MicroTrigger | None, str | None, int]:
    """Scan newly completed ten-second rows exactly once and return the first valid trigger."""

    index_ns = data.index.as_unit("ns").asi8
    mask = (index_ns > int(start_after_ns)) & (index_ns <= int(end_at_ns))
    rows = data.loc[mask]
    last_scanned = int(start_after_ns)
    for timestamp, row in rows.iterrows():
        timestamp_ns = int(timestamp.as_unit("ns").value)
        last_scanned = timestamp_ns
        diagnostics["MICRO_ROWS_SCANNED"] += 1
        invalidated = (
            float(row["low"]) <= pending.sweep_extreme - tick
            if pending.direction > 0
            else float(row["high"]) >= pending.sweep_extreme + tick
        )
        if invalidated:
            diagnostics["MICRO_SWEEP_EXTREME_INVALIDATION"] += 1
            return None, "SWEEP_EXTREME_INVALIDATED_DURING_MICRO_CONFIRMATION", last_scanned
        if not bool(row.get("native_quote_snapshot_observable", False)):
            diagnostics["MICRO_NO_NATIVE_L1"] += 1
            continue
        metrics = _micro_trigger_metrics(
            row,
            direction=pending.direction,
            break_level=frozen.level,
            tick=tick,
        )
        if metrics is None:
            diagnostics["MICRO_UNOBSERVABLE"] += 1
            continue
        if not bool(metrics["broke_frozen_micro_level"]):
            continue
        diagnostics["MICRO_STRUCTURE_BREAK"] += 1
        if float(metrics["directional_aggressive_pressure_ratio"]) < config.minimum_micro_aggressive_pressure_ratio:
            diagnostics["MICRO_BREAK_WITHOUT_AGGRESSIVE_PRESSURE"] += 1
            continue
        if float(metrics["directional_quote_ofi_ratio"]) < config.minimum_micro_quote_ofi_ratio:
            diagnostics["MICRO_BREAK_WITHOUT_QUOTE_OFI"] += 1
            continue
        if float(metrics["directional_body_fraction"]) < config.minimum_micro_body_fraction:
            diagnostics["MICRO_BREAK_WITHOUT_DIRECTIONAL_BODY"] += 1
            continue
        if float(metrics["directional_close_location"]) < config.micro_close_location:
            diagnostics["MICRO_BREAK_WITHOUT_DIRECTIONAL_CLOSE"] += 1
            continue
        if float(metrics["spread_median_ratio"]) > config.maximum_micro_spread_ratio:
            diagnostics["MICRO_BREAK_WITH_WIDE_SPREAD"] += 1
            continue
        metrics.update(
            {
                "micro_break_source": frozen.source,
                "micro_pivot_time_ns": frozen.pivot_time_ns,
                "micro_frozen_time_ns": frozen.frozen_time_ns,
                "micro_trigger_time_ns": timestamp_ns,
            }
        )
        diagnostics["MICRO_REACCELERATION_CONFIRMED"] += 1
        return MicroTrigger(timestamp=timestamp, row=row, metrics=metrics), None, last_scanned
    return None, None, last_scanned


def _signal_from_micro_trigger(
    *,
    trigger: MicroTrigger,
    data: pd.DataFrame,
    minute: pd.DataFrame,
    pending: _PendingLeg,
    target_levels: tuple[ExternalLevel, ...],
    consumed: set[str],
    stop_reserves: pd.Series,
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    config: ExternalSweepFvgMicroConfig,
) -> tuple[QuoteResiliencySignal | None, str]:
    timestamp = trigger.timestamp
    row = trigger.row
    if not bool(row.get("native_quote_snapshot_observable", False)):
        return None, "NO_NATIVE_L1_AT_MICRO_REACCELERATION"
    entry = executable_quote_reference(row, pending.direction)
    target = _select_target(
        target_levels,
        direction=pending.direction,
        entry=entry,
        excluded_level_id=pending.boundary.level_id,
        consumed=consumed,
    )
    if target is None:
        return None, "NO_ACTIVE_OPPOSITE_EXTERNAL_TARGET"
    causal_minute = minute.loc[minute.index <= timestamp]
    if causal_minute.empty:
        return None, "NO_COMPLETED_MINUTE_AT_MICRO_TRIGGER"
    atr = float(causal_minute.iloc[-1]["atr"])
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
        return None, "INVALID_COST_AFTER_MICRO_GEOMETRY"
    loss, gain, net_reward_risk = geometry
    if net_reward_risk < minimum_net_reward_risk:
        return None, "INSUFFICIENT_COST_AFTER_EXTERNAL_TARGET"
    if pending.displacement_time_ns is None or pending.retest_time_ns is None:
        raise RuntimeError("micro signal emitted before completed leg sequence")
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
            event_type="TEN_SECOND_MICRO_REACCELERATION_CONFIRMED",
            event_time_ns=timestamp_ns,
            observed_time_ns=timestamp_ns,
            previous_state="LOW_ENERGY_FVG_RETRACE_LEG",
            next_state="CONFIRMED",
            reason_code="FROZEN_RETRACE_PIVOT_BROKEN_WITH_ALIGNED_AGGRESSIVE_FLOW_AND_QUOTE_OFI",
            reference_price=entry,
            details={
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "native_l1_entry_reference": entry,
                "net_reward_risk": net_reward_risk,
                **trigger.metrics,
            },
        )
    )
    signal_index = int(data.index.get_loc(timestamp))
    return (
        QuoteResiliencySignal(
            scenario_id=pending.scenario_id,
            scenario_family=SCENARIO_FAMILY,
            symbol=symbol,
            instrument_id=instrument_id,
            direction=pending.direction,
            signal_index=signal_index,
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
                "entry_mode": "NATIVE_L1_MARKET_AFTER_COMPLETED_TEN_SECOND_MICRO_BOS",
                "invalidation_contract": "SWEEP_EXTREME_PLUS_CAUSAL_ATR_BUFFER",
                "target_contract": "NEAREST_UNCONSUMED_OPPOSITE_COMPLETED_EXTERNAL_LIQUIDITY",
                **trigger.metrics,
            },
        ),
        "SIGNAL",
    )


def build_external_sweep_fvg_micro_signals(
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
    config: ExternalSweepFvgMicroConfig,
) -> QuoteResiliencySignalBundle:
    """Build future-free one-minute context with ten-second execution transitions."""

    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid execution cost contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("data must use a timezone-aware DatetimeIndex")
    minute = aggregate_completed_minutes(data, config)
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
    micro_breaks: dict[str, FrozenMicroBreak] = {}
    micro_scan_after_ns: dict[str, int] = {}
    micro_expiry_ns: dict[str, int] = {}

    def reject(reason: str, timestamp_ns: int, active: _PendingLeg) -> None:
        diagnostics[reason] += 1
        frozen = micro_breaks.get(active.scenario_id)
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
                "micro_break_level": None if frozen is None else frozen.level,
                "micro_break_source": None if frozen is None else frozen.source,
                "micro_pivot_time_ns": None if frozen is None else frozen.pivot_time_ns,
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

        # The micro execution path is scanned before using the newly completed minute for any other
        # state transition.  Every candidate row carries its own timestamp and only data at or before
        # that timestamp enters the signal and cost geometry.
        handled_pending = pending is not None
        if pending is not None and pending.state == "WAIT_REACCELERATION":
            frozen = micro_breaks[pending.scenario_id]
            start_after = micro_scan_after_ns[pending.scenario_id]
            expiry = micro_expiry_ns[pending.scenario_id]
            scan_end = min(timestamp_ns, expiry)
            trigger, invalidation_reason, last_scanned = _scan_micro_confirmation(
                data,
                pending=pending,
                frozen=frozen,
                start_after_ns=start_after,
                end_at_ns=scan_end,
                tick=tick,
                config=config,
                diagnostics=diagnostics,
            )
            micro_scan_after_ns[pending.scenario_id] = last_scanned
            if invalidation_reason is not None:
                reject(invalidation_reason, last_scanned, pending)
                pending = None
            elif trigger is not None:
                trigger_ns = int(trigger.timestamp.as_unit("ns").value)
                trigger_context = _context_for_ten_second_close(
                    timestamp_ns=trigger_ns,
                    context_times=context_times,
                    context_bars=context_bars,
                    snapshots=snapshots,
                )
                if trigger_context is None:
                    reject("NO_COMPLETE_CONTEXT_AT_MICRO_TRIGGER", trigger_ns, pending)
                else:
                    _trigger_five_bar, _trigger_boundary_levels, trigger_targets = trigger_context
                    signal, reason = _signal_from_micro_trigger(
                        trigger=trigger,
                        data=data,
                        minute=minute,
                        pending=pending,
                        target_levels=trigger_targets,
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
                        reject(reason, trigger_ns, pending)
                    else:
                        signals.setdefault(signal.signal_time_ns, []).append(signal)
                        diagnostics["MICRO_REACCELERATION_SIGNAL"] += 1
                        diagnostics[f"SIGNAL_{signal.direction_name}"] += 1
                        diagnostics[f"SIGNAL_BOUNDARY_{signal.boundary_source}"] += 1
                        diagnostics[f"SIGNAL_TARGET_{signal.target_source}"] += 1
                pending = None
            elif timestamp_ns >= expiry:
                reject("NO_TEN_SECOND_MICRO_REACCELERATION_BEFORE_EXPIRY", expiry, pending)
                pending = None

        context = _context_for_ten_second_close(
            timestamp_ns=timestamp_ns,
            context_times=context_times,
            context_bars=context_bars,
            snapshots=snapshots,
        )
        if context is None:
            diagnostics["NO_COMPLETE_CAUSAL_EXTERNAL_CONTEXT"] += 1
            continue
        _five_bar, boundary_levels, _target_levels = context
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
                        if pending.displacement_time_ns is None or pending.retest_time_ns is None:
                            raise RuntimeError("accepted retrace lacked completed timestamps")
                        frozen = _frozen_micro_break(
                            data,
                            direction=pending.direction,
                            start_time_ns=pending.displacement_time_ns,
                            end_time_ns=pending.retest_time_ns,
                            span=config.micro_swing_span,
                        )
                        micro_breaks[pending.scenario_id] = frozen
                        micro_scan_after_ns[pending.scenario_id] = pending.retest_time_ns
                        micro_expiry_ns[pending.scenario_id] = (
                            pending.retest_time_ns
                            + config.micro_confirmation_seconds * 1_000_000_000
                        )
                        diagnostics["LOW_ENERGY_FVG_RETRACE_LEG"] += 1
                        diagnostics[f"MICRO_BREAK_SOURCE_{frozen.source}"] += 1
                    elif pending.fvg_ce_touched and ratio > config.maximum_retrace_energy_fraction:
                        diagnostics["FVG_TOUCH_RETRACE_ENERGY_TOO_HIGH"] += 1
            elif pending.state != "WAIT_REACCELERATION":
                raise RuntimeError(f"unknown micro leg state: {pending.state}")

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
        scenario_id = f"external-sweep-micro-{symbol.lower()}-{scenario_counter:06d}"
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
    "ExternalSweepFvgMicroConfig",
    "FrozenMicroBreak",
    "MicroTrigger",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "build_external_sweep_fvg_micro_signals",
]
