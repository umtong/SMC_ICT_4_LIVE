"""Quarter-hour directional auction with micro resumption and a measured second leg.

V1 established that quarter-hour bursts and contracted retracements were frequent, but a completed
one-minute reacceleration was too coarse and a pre-existing fifteen-minute swing was often too close
to compensate realistic costs. V2 changes the scenario representation without relaxing the V1
precursor thresholds:

1. a completed UTC quarter-hour burst aligns aggressive flow, recent quarter-hour boundary flow and
   the previous completed four-hour auction;
2. a lower-activity retracement reaches value while the burst origin holds;
3. the latest causally confirmed ten-second pivot inside that retracement is frozen;
4. a later completed ten-second row breaks it with aligned aggressive pressure, quote OFI,
   directional body/close location and normal spread; and
5. the liquidity objective is the causal measured second auction leg: the accepted burst body is
   projected from the retracement extreme.

The measured target is not a fixed reward multiple and is known before entry. This module contains
no order, fill, account, position, sizing, PnL or backtest-engine logic. NautilusTrader remains the
only execution and accounting authority.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from external_sweep_fvg_micro_reaccel_signals_v3 import (
    FrozenMicroBreak,
    MicroTrigger,
    _frozen_micro_break,
    _scan_micro_confirmation,
)
from external_sweep_fvg_retrace_signals import (
    _cost_geometry,
    aggregate_completed_minutes,
)
from qh_session_liquidity_signals_v1 import (
    QuarterHourSessionLiquidityConfig,
    _burst_direction,
    _contracted_retrace,
    _enrich_minute,
    _structural_stop,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
    executable_quote_reference,
)


SIGNAL_REVISION = "CAUSAL_QH_SESSION_MEASURED_MICRO_RESUMPTION_V2"
SCENARIO_FAMILY = "QH_SESSION_MEASURED_SECOND_LEG_MICRO_RESUMPTION"


@dataclass(frozen=True, slots=True)
class QuarterHourMeasuredMicroConfig(QuarterHourSessionLiquidityConfig):
    micro_swing_span: int = 2
    micro_confirmation_seconds: int = 180
    minimum_micro_aggressive_pressure_ratio: float = 0.50
    minimum_micro_quote_ofi_ratio: float = 0.25
    minimum_micro_body_fraction: float = 0.50
    micro_close_location: float = 0.65
    maximum_micro_spread_ratio: float = 1.50

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "QuarterHourMeasuredMicroConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    def validate(self) -> None:
        QuarterHourSessionLiquidityConfig.validate(self)
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
            raise ValueError("micro-resumption thresholds must be positive")
        if not 0.0 < self.minimum_micro_body_fraction <= 1.0:
            raise ValueError("minimum_micro_body_fraction must be in (0, 1]")
        if not 0.5 < self.micro_close_location < 1.0:
            raise ValueError("micro_close_location must be in (0.5, 1)")
        if self.maximum_micro_spread_ratio < 1.0:
            raise ValueError("maximum_micro_spread_ratio must be at least one")


@dataclass(slots=True)
class _MicroInvalidationContext:
    direction: int
    sweep_extreme: float


_REQUIRED_MINUTE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "atr20",
    "imbalance_qh",
    "volume_ratio_qh",
    "trade_ratio_qh",
    "body_atr_qh",
    "close_location_qh",
    "boundary_lag_mean4",
    "previous_session_direction",
)


def _measured_second_leg_target(
    *,
    burst_open: float,
    burst_close: float,
    retrace_high: float,
    retrace_low: float,
    direction: int,
) -> tuple[float, float]:
    """Project the accepted burst body from the causal retracement extreme."""

    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or +1")
    impulse = direction * (burst_close - burst_open)
    if not isfinite(impulse) or impulse <= 0.0:
        raise ValueError("burst body must be directionally positive")
    target = retrace_low + impulse if direction > 0 else retrace_high - impulse
    return float(target), float(impulse)


def _target_consumed_before_entry(
    trigger: MicroTrigger,
    *,
    target: float,
    direction: int,
) -> bool:
    return (
        float(trigger.row["high"]) >= target
        if direction > 0
        else float(trigger.row["low"]) <= target
    )


def _event(
    *,
    scenario_id: str,
    symbol: str,
    instrument_id: str,
    event_type: str,
    event_time_ns: int,
    previous_state: str,
    next_state: str,
    reason_code: str,
    reference_price: float,
    details: dict[str, Any],
) -> QuoteResiliencyLogicEvent:
    return QuoteResiliencyLogicEvent(
        scenario_id=scenario_id,
        symbol=symbol,
        instrument_id=instrument_id,
        event_type=event_type,
        event_time_ns=event_time_ns,
        observed_time_ns=event_time_ns,
        previous_state=previous_state,
        next_state=next_state,
        reason_code=reason_code,
        reference_price=reference_price,
        details={
            "scenario_family": SCENARIO_FAMILY,
            "signal_revision": SIGNAL_REVISION,
            **details,
        },
    )


def build_qh_session_measured_micro_signals(
    *,
    data: pd.DataFrame,
    context_times: np.ndarray,
    context_bars: tuple[Any, ...],
    snapshots: tuple[Any, ...],
    symbol: str,
    instrument_id: str,
    tick: float,
    fee_rate: float,
    minimum_net_reward_risk: float,
    config: QuarterHourMeasuredMicroConfig,
) -> QuoteResiliencySignalBundle:
    """Build immutable, future-free measured-leg micro-resumption signals."""

    del context_times, context_bars, snapshots
    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid execution cost contract")
    minute = _enrich_minute(aggregate_completed_minutes(data, config), config)
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}

    for burst_position in range(len(minute.index)):
        burst = minute.iloc[burst_position]
        if not all(isfinite(float(burst[name])) for name in _REQUIRED_MINUTE_COLUMNS):
            continue
        direction = _burst_direction(burst, config)
        if direction == 0:
            continue
        diagnostics["QH_INTENSE_SESSION_ALIGNED_BURST"] += 1
        burst_time = minute.index[burst_position]
        burst_time_ns = int(burst_time.as_unit("ns").value)
        scenario_id = f"qh-measured-micro-{symbol.lower()}-{burst_time_ns}"

        retrace_position = _contracted_retrace(minute, burst_position, direction, config)
        if retrace_position is None:
            diagnostics["NO_CONTRACTED_RETRACE"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "reason": "NO_CONTRACTED_RETRACE",
                    "burst_time_ns": burst_time_ns,
                    "direction": "LONG" if direction > 0 else "SHORT",
                    "signal_revision": SIGNAL_REVISION,
                }
            )
            continue
        diagnostics["CONTRACTED_RETRACE"] += 1
        retrace = minute.iloc[retrace_position]
        retrace_time = minute.index[retrace_position]
        retrace_time_ns = int(retrace_time.as_unit("ns").value)
        try:
            frozen: FrozenMicroBreak = _frozen_micro_break(
                data,
                direction=direction,
                start_time_ns=burst_time_ns,
                end_time_ns=retrace_time_ns,
                span=config.micro_swing_span,
            )
        except ValueError as exc:
            diagnostics["NO_CAUSAL_RETRACE_MICROSTRUCTURE"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "reason": "NO_CAUSAL_RETRACE_MICROSTRUCTURE",
                    "details": str(exc),
                    "burst_time_ns": burst_time_ns,
                    "retrace_time_ns": retrace_time_ns,
                    "direction": "LONG" if direction > 0 else "SHORT",
                    "signal_revision": SIGNAL_REVISION,
                }
            )
            continue
        diagnostics["FROZEN_RETRACE_MICRO_BREAK"] += 1
        invalidation = _MicroInvalidationContext(
            direction=direction,
            sweep_extreme=float(retrace["low"] if direction > 0 else retrace["high"]),
        )
        trigger, terminal_reason, _last_scanned = _scan_micro_confirmation(
            data,
            pending=invalidation,  # type: ignore[arg-type]
            frozen=frozen,
            start_after_ns=retrace_time_ns,
            end_at_ns=retrace_time_ns + config.micro_confirmation_seconds * 1_000_000_000,
            tick=tick,
            config=config,  # type: ignore[arg-type]
            diagnostics=diagnostics,
        )
        if trigger is None:
            reason = terminal_reason or "NO_TEN_SECOND_MICRO_RESUMPTION_BEFORE_EXPIRY"
            diagnostics[reason] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "reason": reason,
                    "burst_time_ns": burst_time_ns,
                    "retrace_time_ns": retrace_time_ns,
                    "micro_break_level": frozen.level,
                    "micro_break_source": frozen.source,
                    "direction": "LONG" if direction > 0 else "SHORT",
                    "signal_revision": SIGNAL_REVISION,
                }
            )
            continue

        diagnostics["MICRO_RESUMPTION"] += 1
        quote_reference = executable_quote_reference(trigger.row, direction)
        expected_entry = quote_reference + direction * tick
        target, impulse = _measured_second_leg_target(
            burst_open=float(burst["open"]),
            burst_close=float(burst["close"]),
            retrace_high=float(retrace["high"]),
            retrace_low=float(retrace["low"]),
            direction=direction,
        )
        if _target_consumed_before_entry(trigger, target=target, direction=direction):
            diagnostics["MEASURED_TARGET_REACHED_BEFORE_ENTRY"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "reason": "MEASURED_TARGET_REACHED_BEFORE_ENTRY",
                    "target": target,
                    "trigger_time_ns": int(trigger.timestamp.as_unit("ns").value),
                    "direction": "LONG" if direction > 0 else "SHORT",
                    "signal_revision": SIGNAL_REVISION,
                }
            )
            continue
        causal_minute = minute.loc[minute.index <= trigger.timestamp]
        if causal_minute.empty:
            diagnostics["NO_COMPLETED_MINUTE_AT_MICRO_TRIGGER"] += 1
            continue
        atr = float(causal_minute.iloc[-1]["atr20"])
        if not isfinite(atr) or atr <= 0.0:
            diagnostics["INVALID_CAUSAL_ATR_AT_MICRO_TRIGGER"] += 1
            continue
        stop, stop_reference, stop_source = _structural_stop(
            direction=direction,
            entry=expected_entry,
            retrace_high=float(retrace["high"]),
            retrace_low=float(retrace["low"]),
            atr=atr,
            config=config,
        )
        reserve = float(stop_reserves.loc[trigger.timestamp])
        geometry = _cost_geometry(
            direction=direction,
            quote_reference=quote_reference,
            stop=stop,
            target=target,
            fee_rate=fee_rate,
            tick=tick,
            stop_slippage_reserve=reserve,
        )
        if geometry is None:
            diagnostics["INVALID_COST_AFTER_MEASURED_TARGET_GEOMETRY"] += 1
            continue
        loss, gain, net_reward_risk = geometry
        if net_reward_risk < minimum_net_reward_risk:
            diagnostics["INSUFFICIENT_COST_AFTER_MEASURED_TARGET"] += 1
            continue

        trigger_time_ns = int(trigger.timestamp.as_unit("ns").value)
        events = (
            _event(
                scenario_id=scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="QH_SESSION_ALIGNED_BURST_ACCEPTED",
                event_time_ns=burst_time_ns,
                previous_state="IDLE",
                next_state="DIRECTIONAL_AUCTION_BURST",
                reason_code="QUARTER_HOUR_INTENSE_FLOW_ALIGNED_WITH_RECENT_BOUNDARIES_AND_PREVIOUS_4H_AUCTION",
                reference_price=float(burst["close"]),
                details={
                    "burst_imbalance": float(burst["imbalance_qh"]),
                    "burst_volume_ratio": float(burst["volume_ratio_qh"]),
                    "burst_trade_ratio": float(burst["trade_ratio_qh"]),
                    "burst_body_atr": float(burst["body_atr_qh"]),
                    "boundary_lag_mean4": float(burst["boundary_lag_mean4"]),
                    "previous_session_direction": float(burst["previous_session_direction"]),
                },
            ),
            _event(
                scenario_id=scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="QH_CONTRACTED_RETRACE_HELD",
                event_time_ns=retrace_time_ns,
                previous_state="DIRECTIONAL_AUCTION_BURST",
                next_state="CONTRACTED_RETRACE",
                reason_code="BURST_VALUE_REVISITED_WITH_LOWER_ACTIVITY_WHILE_ORIGIN_HELD",
                reference_price=float(retrace["close"]),
                details={
                    "retrace_volume_fraction": float(retrace["volume"] / burst["volume"]),
                    "retrace_trade_fraction": float(retrace["trade_count"] / burst["trade_count"]),
                    "retrace_imbalance": float(retrace["imbalance_qh"]),
                    "retrace_high": float(retrace["high"]),
                    "retrace_low": float(retrace["low"]),
                    "frozen_micro_break_level": frozen.level,
                    "frozen_micro_break_source": frozen.source,
                    "frozen_micro_pivot_time_ns": frozen.pivot_time_ns,
                },
            ),
            _event(
                scenario_id=scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="QH_MICRO_RESUMPTION_TO_MEASURED_SECOND_LEG_CONFIRMED",
                event_time_ns=trigger_time_ns,
                previous_state="CONTRACTED_RETRACE",
                next_state="CONFIRMED",
                reason_code="TEN_SECOND_STRUCTURE_FLOW_AND_QUOTE_STATE_RESUMED_TOWARD_CAUSAL_MEASURED_SECOND_LEG",
                reference_price=quote_reference,
                details={
                    **trigger.metrics,
                    "burst_body_impulse": impulse,
                    "measured_second_leg_target": target,
                    "native_l1_entry_reference": quote_reference,
                    "net_reward_risk": net_reward_risk,
                },
            ),
        )
        signal = QuoteResiliencySignal(
            scenario_id=scenario_id,
            scenario_family=SCENARIO_FAMILY,
            symbol=symbol,
            instrument_id=instrument_id,
            direction=direction,
            signal_index=int(data.index.get_loc(trigger.timestamp)),
            signal_time_ns=trigger_time_ns,
            boundary_id=f"quarter-hour-{burst_time_ns}",
            boundary_source="QUARTER_HOUR_ALGORITHMIC_BOUNDARY",
            boundary_level=float(burst["open"]),
            target_id=f"measured-second-leg-{burst_time_ns}",
            target_source="BURST_BODY_MEASURED_SECOND_AUCTION_LEG",
            external_target=target,
            entry_reference=quote_reference,
            structural_stop=stop,
            stop_reference=stop_reference,
            stop_reference_source=stop_source,
            atr=atr,
            causal_stop_slippage_reserve=reserve,
            expected_loss_per_unit=loss,
            expected_gain_per_unit=gain,
            net_reward_risk=net_reward_risk,
            interaction_time_ns=burst_time_ns,
            response_time_ns=retrace_time_ns,
            retest_time_ns=retrace_time_ns,
            events=events,
            details={
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "burst_time_ns": burst_time_ns,
                "retrace_time_ns": retrace_time_ns,
                "micro_trigger_time_ns": trigger_time_ns,
                "burst_body_impulse": impulse,
                "measured_second_leg_target": target,
                "target_contract": "BURST_BODY_PROJECTED_FROM_CAUSAL_RETRACE_EXTREME",
                "entry_mode": "NATIVE_L1_MARKET_AFTER_COMPLETED_TEN_SECOND_STRUCTURE_FLOW_AND_QUOTE_RESUMPTION",
                "invalidation_contract": "CONTRACTED_RETRACE_EXTREME_PLUS_CAUSAL_ATR_BUFFER",
            },
        )
        signals.setdefault(trigger_time_ns, []).append(signal)
        diagnostics["QH_MEASURED_MICRO_SIGNAL"] += 1
        diagnostics[f"SIGNAL_{signal.direction_name}"] += 1

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
    "QuarterHourMeasuredMicroConfig",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "_measured_second_leg_target",
    "build_qh_session_measured_micro_signals",
]
