"""DST-aware session-opening drive acceptance and boundary-retest detector.

A completed thirty-minute initial balance defines the opening auction. The first five-minute
price-discovery displacement that closes beyond one edge is not sufficient by itself: the immediately
following completed five-minute bar must also close outside, then a later separate completed
five-minute retest must touch and hold the accepted edge. The objective is the symmetric one-initial-
balance extension frozen at the breakout. Ten-second data supplies only the next contiguous market-
entry observation and causal execution reserve.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from opening_initial_balance_failed_auction_signals_v1 import (
    InitialBalance,
    OpeningAuctionConfig,
    build_initial_balances,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_raid_reversal_signals_v2 import contiguous_first_execution_position_after


SIGNAL_REVISION = "OPENING_DRIVE_ACCEPTANCE_RETEST_SIGNALS_V1"
SCENARIO_FAMILY = "SESSION_OPENING_DRIVE_ACCEPTANCE_CONTINUATION"
FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class OpeningDriveConfig:
    initial_balance_minutes: int = 30
    opportunity_minutes_after_balance: int = 180
    breakout_excursion_atr: float = 0.05
    displacement_lookback: int = 12
    displacement_close_location: float = 2.0 / 3.0
    retest_tolerance_atr: float = 0.05
    stop_buffer_atr: float = 0.05
    minimum_stop_distance_atr: float = 0.10
    target_initial_balance_multiple: float = 1.0

    def validate(self) -> None:
        if self.initial_balance_minutes != 30:
            raise ValueError("V1 fixes the initial balance at thirty minutes")
        if not 60 <= self.opportunity_minutes_after_balance <= 240:
            raise ValueError("opening drive opportunity must remain intraday")
        if self.displacement_lookback < 6:
            raise ValueError("displacement median requires at least six prior bars")
        if not 0.5 < self.displacement_close_location < 1.0:
            raise ValueError("invalid displacement close location")
        if min(
            self.breakout_excursion_atr,
            self.retest_tolerance_atr,
            self.stop_buffer_atr,
            self.minimum_stop_distance_atr,
        ) <= 0.0:
            raise ValueError("relative opening-drive distances must be positive")
        if self.target_initial_balance_multiple != 1.0:
            raise ValueError("V1 fixes the objective at one initial-balance extension")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "OpeningDriveConfig":
        config = cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})
        config.validate()
        return config

    def balance_config(self) -> OpeningAuctionConfig:
        return OpeningAuctionConfig(
            initial_balance_minutes=self.initial_balance_minutes,
            opportunity_minutes_after_balance=self.opportunity_minutes_after_balance,
            sweep_excursion_atr=self.breakout_excursion_atr,
            displacement_lookback=self.displacement_lookback,
            displacement_close_location=self.displacement_close_location,
            stop_buffer_atr=self.stop_buffer_atr,
            minimum_stop_distance_atr=self.minimum_stop_distance_atr,
        )


@dataclass(frozen=True, slots=True)
class OpeningDriveAttempt:
    scenario_id: str
    balance: InitialBalance
    direction: int
    boundary_side: str
    boundary_level: float
    target: float
    breakout_position: int
    breakout_time_ns: int
    breakout_high: float
    breakout_low: float
    breakout_close: float
    atr: float
    acceptance_position: int | None = None
    acceptance_time_ns: int | None = None
    acceptance_high: float | None = None
    acceptance_low: float | None = None


def _bar_start_ns(bar: FiveMinuteBar) -> int:
    return int(bar.ts_event_ns) // FIVE_MINUTES_NS * FIVE_MINUTES_NS


def _route_positions(
    bars: tuple[FiveMinuteBar, ...],
    balance: InitialBalance,
) -> tuple[int, ...]:
    return tuple(
        position
        for position, bar in enumerate(bars)
        if balance.end_time_ns <= _bar_start_ns(bar) < balance.opportunity_end_ns
    )


def _shifted_prior_medians(
    bars: tuple[FiveMinuteBar, ...],
    lookback: int,
) -> tuple[np.ndarray, np.ndarray]:
    bodies = np.asarray([abs(bar.close - bar.open) for bar in bars], dtype=float)
    ranges = np.asarray([bar.high - bar.low for bar in bars], dtype=float)
    body_median = np.full(len(bars), np.nan)
    range_median = np.full(len(bars), np.nan)
    for position in range(len(bars)):
        start = max(0, position - lookback)
        if position - start >= min(6, lookback):
            body_median[position] = float(np.median(bodies[start:position]))
            range_median[position] = float(np.median(ranges[start:position]))
    return body_median, range_median


def _opening_breakout(
    bar: FiveMinuteBar,
    balance: InitialBalance,
    *,
    prior_body_median: float,
    prior_range_median: float,
    config: OpeningDriveConfig,
) -> tuple[int, str, float] | None:
    if not all(
        isfinite(value)
        for value in (bar.atr, prior_body_median, prior_range_median)
    ) or bar.atr <= 0.0:
        return None
    bar_range = bar.high - bar.low
    body = abs(bar.close - bar.open)
    if bar_range <= 0.0:
        return None
    close_location = (bar.close - bar.low) / bar_range
    excursion = config.breakout_excursion_atr * bar.atr
    long_break = (
        bar.close >= balance.high + excursion
        and bar.close > bar.open
        and close_location >= config.displacement_close_location
    )
    short_break = (
        bar.close <= balance.low - excursion
        and bar.close < bar.open
        and close_location <= 1.0 - config.displacement_close_location
    )
    if body < prior_body_median or bar_range < prior_range_median:
        return None
    if long_break and short_break:
        return 0, "BILATERAL", float("nan")
    if long_break:
        return 1, "HIGH", balance.high
    if short_break:
        return -1, "LOW", balance.low
    return None


def _target_for(balance: InitialBalance, direction: int) -> float:
    width = balance.high - balance.low
    if width <= 0.0:
        raise ValueError("initial balance must have positive width")
    return balance.high + width if direction > 0 else balance.low - width


def _target_consumed(
    *,
    direction: int,
    high: float,
    low: float,
    target: float,
) -> bool:
    return high >= target if direction > 0 else low <= target


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
    stop_reserve = max(tick, stop_slippage_reserve)
    loss = abs(entry - stop) + fee_rate * (entry + stop) + tick + stop_reserve
    gross_gain = target - entry if direction > 0 else entry - target
    gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
    if loss <= 0.0 or gain <= 0.0:
        return None
    return loss, gain, gain / loss


def build_opening_drive_acceptance_signals(
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
    config: OpeningDriveConfig,
    require_retest_contraction: bool = True,
) -> QuoteResiliencySignalBundle:
    """Emit the first accepted and retested opening drive per completed session."""

    del context_times, snapshots, require_retest_contraction
    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")

    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    grouped: dict[int, list[QuoteResiliencySignal]] = {}
    balances = build_initial_balances(context_bars, config.balance_config(), diagnostics)
    body_median, range_median = _shifted_prior_medians(
        context_bars,
        config.displacement_lookback,
    )
    data_times = data.index.as_unit("ns").asi8
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    scenario_counter = 0

    for balance in balances:
        positions = _route_positions(context_bars, balance)
        if not positions:
            diagnostics["NO_COMPLETE_OPENING_DRIVE_ROUTE_BARS"] += 1
            continue
        attempt: OpeningDriveAttempt | None = None
        terminal = False
        awaiting_second_close = False

        for position in positions:
            bar = context_bars[position]
            if attempt is None:
                breakout = _opening_breakout(
                    bar,
                    balance,
                    prior_body_median=float(body_median[position]),
                    prior_range_median=float(range_median[position]),
                    config=config,
                )
                if breakout is None:
                    continue
                direction, boundary_side, boundary_level = breakout
                if direction == 0:
                    diagnostics["BILATERAL_OPENING_DRIVE_BREAKOUT"] += 1
                    rejected.append(
                        {
                            "symbol": symbol,
                            "session": balance.session_name,
                            "local_date": balance.local_date,
                            "reason": "BILATERAL_OPENING_DRIVE_BREAKOUT",
                            "observed_time_ns": int(bar.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                scenario_counter += 1
                target = _target_for(balance, direction)
                scenario_id = (
                    f"opening-drive-{symbol.lower()}-{balance.session_name.lower()}-"
                    f"{balance.local_date}-{scenario_counter:05d}"
                )
                if _target_consumed(
                    direction=direction,
                    high=float(bar.high),
                    low=float(bar.low),
                    target=target,
                ):
                    diagnostics["IB_EXTENSION_CONSUMED_IN_BREAKOUT_BAR"] += 1
                    rejected.append(
                        {
                            "scenario_id": scenario_id,
                            "symbol": symbol,
                            "session": balance.session_name,
                            "local_date": balance.local_date,
                            "reason": "IB_EXTENSION_CONSUMED_IN_BREAKOUT_BAR",
                            "breakout_time_ns": int(bar.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                attempt = OpeningDriveAttempt(
                    scenario_id=scenario_id,
                    balance=balance,
                    direction=direction,
                    boundary_side=boundary_side,
                    boundary_level=boundary_level,
                    target=target,
                    breakout_position=position,
                    breakout_time_ns=int(bar.ts_event_ns),
                    breakout_high=float(bar.high),
                    breakout_low=float(bar.low),
                    breakout_close=float(bar.close),
                    atr=float(bar.atr),
                )
                awaiting_second_close = True
                diagnostics["OPENING_DRIVE_DISPLACEMENT_CLOSE_OUTSIDE"] += 1
                continue

            direction = attempt.direction
            if awaiting_second_close:
                previous_start = _bar_start_ns(context_bars[attempt.breakout_position])
                if _bar_start_ns(bar) - previous_start != FIVE_MINUTES_NS:
                    diagnostics["MISSING_IMMEDIATE_SECOND_OUTSIDE_CLOSE"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": balance.session_name,
                            "local_date": balance.local_date,
                            "reason": "MISSING_IMMEDIATE_SECOND_OUTSIDE_CLOSE",
                        }
                    )
                    terminal = True
                    break
                accepted = (
                    bar.close > attempt.boundary_level
                    if direction > 0
                    else bar.close < attempt.boundary_level
                )
                if not accepted:
                    diagnostics["OPENING_DRIVE_NOT_ACCEPTED_ON_SECOND_CLOSE"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": balance.session_name,
                            "local_date": balance.local_date,
                            "reason": "OPENING_DRIVE_NOT_ACCEPTED_ON_SECOND_CLOSE",
                            "breakout_time_ns": attempt.breakout_time_ns,
                            "observed_time_ns": int(bar.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                if _target_consumed(
                    direction=direction,
                    high=float(bar.high),
                    low=float(bar.low),
                    target=attempt.target,
                ):
                    diagnostics["IB_EXTENSION_CONSUMED_BEFORE_RETEST"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": balance.session_name,
                            "local_date": balance.local_date,
                            "reason": "IB_EXTENSION_CONSUMED_BEFORE_RETEST",
                            "observed_time_ns": int(bar.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                attempt = OpeningDriveAttempt(
                    **{
                        **{name: getattr(attempt, name) for name in (
                            "scenario_id", "balance", "direction", "boundary_side",
                            "boundary_level", "target", "breakout_position",
                            "breakout_time_ns", "breakout_high", "breakout_low",
                            "breakout_close", "atr"
                        )},
                        "acceptance_position": position,
                        "acceptance_time_ns": int(bar.ts_event_ns),
                        "acceptance_high": float(bar.high),
                        "acceptance_low": float(bar.low),
                    }
                )
                awaiting_second_close = False
                diagnostics["SECOND_OUTSIDE_CLOSE_ACCEPTED"] += 1
                continue

            if _target_consumed(
                direction=direction,
                high=float(bar.high),
                low=float(bar.low),
                target=attempt.target,
            ):
                diagnostics["IB_EXTENSION_CONSUMED_BEFORE_RETEST"] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": "IB_EXTENSION_CONSUMED_BEFORE_RETEST",
                        "observed_time_ns": int(bar.ts_event_ns),
                    }
                )
                terminal = True
                break

            tolerance = config.retest_tolerance_atr * attempt.atr
            if direction > 0:
                invalidated = bar.close < attempt.boundary_level - tolerance
                held = bar.low <= attempt.boundary_level + tolerance and bar.close > attempt.boundary_level
            else:
                invalidated = bar.close > attempt.boundary_level + tolerance
                held = bar.high >= attempt.boundary_level - tolerance and bar.close < attempt.boundary_level
            if invalidated:
                diagnostics["ACCEPTED_OPENING_DRIVE_REENTERED_INITIAL_BALANCE"] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": "ACCEPTED_OPENING_DRIVE_REENTERED_INITIAL_BALANCE",
                        "observed_time_ns": int(bar.ts_event_ns),
                    }
                )
                terminal = True
                break
            if not held:
                continue

            retest_ns = int(bar.ts_event_ns)
            execution_position = contiguous_first_execution_position_after(data_times, retest_ns)
            if execution_position is None:
                diagnostics["NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET"] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": "NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET",
                        "retest_time_ns": retest_ns,
                    }
                )
                terminal = True
                break

            execution_row = data.iloc[execution_position]
            execution_ns = int(data_times[execution_position])
            if _target_consumed(
                direction=direction,
                high=float(execution_row["high"]),
                low=float(execution_row["low"]),
                target=attempt.target,
            ):
                diagnostics["IB_EXTENSION_CONSUMED_IN_EXECUTION_OBSERVATION"] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": "IB_EXTENSION_CONSUMED_IN_EXECUTION_OBSERVATION",
                        "execution_time_ns": execution_ns,
                    }
                )
                terminal = True
                break

            entry = float(execution_row["close"])
            buffer = config.stop_buffer_atr * attempt.atr
            minimum_distance = config.minimum_stop_distance_atr * attempt.atr
            if direction > 0:
                stop_reference = min(
                    attempt.boundary_level,
                    float(bar.low),
                    float(execution_row["low"]),
                )
                stop = min(stop_reference - buffer, entry - minimum_distance)
                stop_source = "ACCEPTED_IB_HIGH_RETEST_SEQUENCE_LOW"
                target_side = "HIGH_EXTENSION_1X_IB"
            else:
                stop_reference = max(
                    attempt.boundary_level,
                    float(bar.high),
                    float(execution_row["high"]),
                )
                stop = max(stop_reference + buffer, entry + minimum_distance)
                stop_source = "ACCEPTED_IB_LOW_RETEST_SEQUENCE_HIGH"
                target_side = "LOW_EXTENSION_1X_IB"

            geometry = _cost_geometry(
                direction=direction,
                entry=entry,
                stop=stop,
                target=attempt.target,
                fee_rate=fee_rate,
                tick=tick,
                stop_slippage_reserve=float(stop_reserves.iloc[execution_position]),
            )
            if geometry is None:
                diagnostics["INVALID_COST_AFTER_IB_EXTENSION_GEOMETRY"] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": "INVALID_COST_AFTER_IB_EXTENSION_GEOMETRY",
                        "entry_reference": entry,
                        "structural_stop": stop,
                        "target": attempt.target,
                    }
                )
                terminal = True
                break
            loss, gain, net_rr = geometry
            if net_rr < minimum_net_reward_risk:
                diagnostics["INSUFFICIENT_COST_AFTER_IB_EXTENSION"] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": "INSUFFICIENT_COST_AFTER_IB_EXTENSION",
                        "net_reward_risk": net_rr,
                    }
                )
                terminal = True
                break

            boundary_id = (
                f"{balance.session_name}-{balance.local_date}-INITIAL_BALANCE-"
                f"{attempt.boundary_side}"
            )
            target_id = (
                f"{balance.session_name}-{balance.local_date}-{target_side}"
            )
            direction_name = "LONG" if direction > 0 else "SHORT"
            shared = {
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "session": balance.session_name,
                "session_timezone": balance.timezone_name,
                "session_local_date": balance.local_date,
                "session_open_time_ns": balance.start_time_ns,
                "initial_balance_end_time_ns": balance.end_time_ns,
                "initial_balance_high": balance.high,
                "initial_balance_low": balance.low,
                "initial_balance_width": balance.high - balance.low,
                "accepted_boundary": attempt.boundary_level,
                "ten_second_alpha_inputs": False,
            }
            events = (
                QuoteResiliencyLogicEvent(
                    scenario_id=attempt.scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="OPENING_DRIVE_DISPLACEMENT_CLOSE_OUTSIDE",
                    event_time_ns=attempt.breakout_time_ns,
                    observed_time_ns=attempt.breakout_time_ns,
                    previous_state="INITIAL_BALANCE_COMPLETE",
                    next_state="OPENING_DRIVE_BREAKOUT_OBSERVED",
                    reason_code=f"M5_DISPLACEMENT_CLOSE_OUTSIDE_IB_{attempt.boundary_side}",
                    reference_price=attempt.boundary_level,
                    details={**shared, "lifecycle_stage": "BREAKOUT"},
                ),
                QuoteResiliencyLogicEvent(
                    scenario_id=attempt.scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="SECOND_OUTSIDE_CLOSE_ACCEPTED",
                    event_time_ns=int(attempt.acceptance_time_ns),
                    observed_time_ns=int(attempt.acceptance_time_ns),
                    previous_state="OPENING_DRIVE_BREAKOUT_OBSERVED",
                    next_state="OUTSIDE_PRICE_ACCEPTED",
                    reason_code=f"IMMEDIATE_SECOND_M5_CLOSE_REMAINS_{direction_name}_OF_IB_EDGE",
                    reference_price=float(attempt.boundary_level),
                    details={**shared, "lifecycle_stage": "ACCEPTANCE"},
                ),
                QuoteResiliencyLogicEvent(
                    scenario_id=attempt.scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="ACCEPTED_BOUNDARY_RETEST_HELD",
                    event_time_ns=retest_ns,
                    observed_time_ns=retest_ns,
                    previous_state="OUTSIDE_PRICE_ACCEPTED",
                    next_state="CONFIRMED",
                    reason_code="SEPARATE_COMPLETED_M5_RETEST_TOUCHES_AND_CLOSES_OUTSIDE",
                    reference_price=float(attempt.boundary_level),
                    details={
                        **shared,
                        "lifecycle_stage": "RETEST",
                        "retest_five_index": int(bar.index),
                        "next_execution_bucket_time_ns": execution_ns,
                    },
                ),
            )
            signal = QuoteResiliencySignal(
                scenario_id=attempt.scenario_id,
                scenario_family=SCENARIO_FAMILY,
                symbol=symbol,
                instrument_id=instrument_id,
                direction=direction,
                signal_index=execution_position,
                signal_time_ns=execution_ns,
                boundary_id=boundary_id,
                boundary_source=f"{balance.session_name}_INITIAL_BALANCE_{attempt.boundary_side}",
                boundary_level=attempt.boundary_level,
                target_id=target_id,
                target_source="INITIAL_BALANCE_ONE_RANGE_EXTENSION",
                external_target=attempt.target,
                entry_reference=entry,
                structural_stop=stop,
                stop_reference=stop_reference,
                stop_reference_source=stop_source,
                atr=attempt.atr,
                causal_stop_slippage_reserve=float(stop_reserves.iloc[execution_position]),
                expected_loss_per_unit=loss,
                expected_gain_per_unit=gain,
                net_reward_risk=net_rr,
                interaction_time_ns=attempt.breakout_time_ns,
                response_time_ns=int(attempt.acceptance_time_ns),
                retest_time_ns=retest_ns,
                events=events,
                details={
                    **shared,
                    "breakout_five_index": int(context_bars[attempt.breakout_position].index),
                    "acceptance_five_index": int(
                        context_bars[int(attempt.acceptance_position)].index
                    ),
                    "retest_five_index": int(bar.index),
                    "execution_position": execution_position,
                    "next_execution_bucket_gap_ns": execution_ns - retest_ns,
                    "target_contract": "ONE_COMPLETED_INITIAL_BALANCE_RANGE_EXTENSION",
                    "entry_contract": "NEXT_CONTIGUOUS_TEN_SECOND_EXECUTION_OBSERVATION_ONLY",
                    "slippage_reserve_contract": "SHIFTED_60M_TEN_SECOND_TRUE_RANGE_Q99",
                },
            )
            grouped.setdefault(execution_ns, []).append(signal)
            diagnostics["TRADEABLE_OPENING_DRIVE_ACCEPTANCE_SIGNAL"] += 1
            terminal = True
            break

        if attempt is not None and not terminal:
            reason = (
                "NO_IMMEDIATE_SECOND_OUTSIDE_CLOSE_BEFORE_ROUTE_END"
                if awaiting_second_close
                else "NO_ACCEPTED_BOUNDARY_RETEST_BEFORE_ROUTE_END"
            )
            diagnostics[reason] += 1
            rejected.append(
                {
                    "scenario_id": attempt.scenario_id,
                    "symbol": symbol,
                    "session": balance.session_name,
                    "local_date": balance.local_date,
                    "reason": reason,
                    "opportunity_end_ns": balance.opportunity_end_ns,
                }
            )

    immutable = {
        timestamp: tuple(
            sorted(items, key=lambda signal: (signal.net_reward_risk, signal.scenario_id), reverse=True)
        )
        for timestamp, items in sorted(grouped.items())
    }
    diagnostics["SIGNAL"] = sum(len(items) for items in immutable.values())
    diagnostics["SIGNAL_TIMES"] = len(immutable)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "OpeningDriveConfig",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "build_opening_drive_acceptance_signals",
]
