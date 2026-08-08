"""Causal session-opening initial-balance failed-auction detector for candidate-08.

This detector is a day-trading scenario, not a generic wick pattern. Each UTC/Europe-London/
America-New-York session first establishes a completed thirty-minute initial balance. The first later
auction beyond one edge is eligible only when a completed five-minute bar closes back inside that
balance. A separate completed five-minute displacement must then continue away from the failed edge.
The opposite initial-balance edge is the frozen liquidity objective. Ten-second data supplies only
the first contiguous executable observation and causal stop-slippage reserve; it never chooses the
scenario direction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_raid_reversal_signals_v2 import contiguous_first_execution_position_after


SIGNAL_REVISION = "OPENING_INITIAL_BALANCE_FAILED_AUCTION_SIGNALS_V1"
SCENARIO_FAMILY = "SESSION_OPENING_INITIAL_BALANCE_FAILED_AUCTION"
FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
ONE_SECOND_NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class OpeningAuctionConfig:
    initial_balance_minutes: int = 30
    opportunity_minutes_after_balance: int = 180
    sweep_excursion_atr: float = 0.05
    displacement_lookback: int = 12
    displacement_close_location: float = 2.0 / 3.0
    stop_buffer_atr: float = 0.05
    minimum_stop_distance_atr: float = 0.10

    def validate(self) -> None:
        if self.initial_balance_minutes != 30:
            raise ValueError("V1 fixes the session initial balance at thirty minutes")
        if not 60 <= self.opportunity_minutes_after_balance <= 240:
            raise ValueError("opening-auction opportunity must remain intraday")
        if self.displacement_lookback < 6:
            raise ValueError("displacement median requires at least six prior bars")
        if not 0.5 < self.displacement_close_location < 1.0:
            raise ValueError("invalid displacement close location")
        if min(
            self.sweep_excursion_atr,
            self.stop_buffer_atr,
            self.minimum_stop_distance_atr,
        ) <= 0.0:
            raise ValueError("relative opening-auction distances must be positive")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "OpeningAuctionConfig":
        config = cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})
        config.validate()
        return config


@dataclass(frozen=True, slots=True)
class SessionSpec:
    name: str
    timezone_name: str
    local_open_hour: int
    local_open_minute: int


SESSION_SPECS = (
    SessionSpec("ASIA", "UTC", 0, 0),
    SessionSpec("LONDON", "Europe/London", 8, 0),
    SessionSpec("NEW_YORK", "America/New_York", 9, 30),
)


@dataclass(frozen=True, slots=True)
class InitialBalance:
    session_name: str
    timezone_name: str
    local_date: str
    start_time_ns: int
    end_time_ns: int
    opportunity_end_ns: int
    high: float
    low: float
    first_five_position: int
    last_five_position: int

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(frozen=True, slots=True)
class FailedAuctionAttempt:
    scenario_id: str
    balance: InitialBalance
    direction: int
    swept_edge: str
    sweep_position: int
    sweep_time_ns: int
    sweep_high: float
    sweep_low: float
    sweep_open: float
    sweep_close: float
    atr: float


@dataclass(frozen=True, slots=True)
class _SessionWindow:
    spec: SessionSpec
    local_day: date
    start_time_ns: int
    end_time_ns: int
    opportunity_end_ns: int



def session_open_utc(local_day: date, spec: SessionSpec) -> datetime:
    """Return the actual UTC instant of a local session open, including DST."""

    local_zone = ZoneInfo(spec.timezone_name)
    local = datetime.combine(
        local_day,
        time(spec.local_open_hour, spec.local_open_minute),
        tzinfo=local_zone,
    )
    return local.astimezone(timezone.utc)


def _bar_start_ns(bar: FiveMinuteBar) -> int:
    return int(bar.ts_event_ns) // FIVE_MINUTES_NS * FIVE_MINUTES_NS


def _session_windows(
    bars: tuple[FiveMinuteBar, ...],
    config: OpeningAuctionConfig,
) -> tuple[_SessionWindow, ...]:
    if not bars:
        return ()
    first = pd.Timestamp(int(bars[0].ts_event_ns), unit="ns", tz="UTC").date() - timedelta(days=2)
    last = pd.Timestamp(int(bars[-1].ts_event_ns), unit="ns", tz="UTC").date() + timedelta(days=2)
    windows: list[_SessionWindow] = []
    cursor = first
    while cursor <= last:
        for spec in SESSION_SPECS:
            start = pd.Timestamp(session_open_utc(cursor, spec)).as_unit("ns")
            start_ns = int(start.value)
            end_ns = start_ns + config.initial_balance_minutes * 60 * ONE_SECOND_NS
            opportunity_end_ns = (
                end_ns + config.opportunity_minutes_after_balance * 60 * ONE_SECOND_NS
            )
            windows.append(
                _SessionWindow(
                    spec=spec,
                    local_day=cursor,
                    start_time_ns=start_ns,
                    end_time_ns=end_ns,
                    opportunity_end_ns=opportunity_end_ns,
                )
            )
        cursor += timedelta(days=1)
    return tuple(sorted(windows, key=lambda item: (item.start_time_ns, item.spec.name)))


def build_initial_balances(
    bars: tuple[FiveMinuteBar, ...],
    config: OpeningAuctionConfig,
    diagnostics: Counter[str] | None = None,
) -> tuple[InitialBalance, ...]:
    """Build only complete six-bar initial balances at actual local session opens."""

    config.validate()
    counts = diagnostics if diagnostics is not None else Counter()
    position_by_start = {_bar_start_ns(bar): position for position, bar in enumerate(bars)}
    balances: list[InitialBalance] = []
    expected_bars = config.initial_balance_minutes // 5
    for window in _session_windows(bars, config):
        starts = [window.start_time_ns + offset * FIVE_MINUTES_NS for offset in range(expected_bars)]
        positions = [position_by_start.get(start) for start in starts]
        if any(position is None for position in positions):
            counts["INCOMPLETE_INITIAL_BALANCE"] += 1
            continue
        complete_positions = [int(position) for position in positions if position is not None]
        if complete_positions != list(
            range(complete_positions[0], complete_positions[0] + expected_bars)
        ):
            counts["NONCONTIGUOUS_INITIAL_BALANCE"] += 1
            continue
        selected = [bars[position] for position in complete_positions]
        if not all(isfinite(value) for bar in selected for value in (bar.high, bar.low, bar.atr)):
            counts["UNOBSERVABLE_INITIAL_BALANCE"] += 1
            continue
        balances.append(
            InitialBalance(
                session_name=window.spec.name,
                timezone_name=window.spec.timezone_name,
                local_date=window.local_day.isoformat(),
                start_time_ns=window.start_time_ns,
                end_time_ns=window.end_time_ns,
                opportunity_end_ns=window.opportunity_end_ns,
                high=max(bar.high for bar in selected),
                low=min(bar.low for bar in selected),
                first_five_position=complete_positions[0],
                last_five_position=complete_positions[-1],
            )
        )
        counts["COMPLETE_INITIAL_BALANCE"] += 1
    return tuple(balances)


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


def _failed_edge(
    bar: FiveMinuteBar,
    balance: InitialBalance,
    config: OpeningAuctionConfig,
) -> tuple[int, str] | None:
    if not isfinite(bar.atr) or bar.atr <= 0.0:
        return None
    excursion = config.sweep_excursion_atr * bar.atr
    upper = bar.high >= balance.high + excursion and balance.low < bar.close < balance.high
    lower = bar.low <= balance.low - excursion and balance.low < bar.close < balance.high
    if upper and lower:
        return 0, "BILATERAL"
    if upper:
        return -1, "HIGH"
    if lower:
        return 1, "LOW"
    return None


def _displacement_confirms(
    bar: FiveMinuteBar,
    attempt: FailedAuctionAttempt,
    *,
    prior_body_median: float,
    prior_range_median: float,
    config: OpeningAuctionConfig,
) -> bool:
    if not all(
        isfinite(value)
        for value in (
            bar.atr,
            prior_body_median,
            prior_range_median,
        )
    ):
        return False
    direction = attempt.direction
    body = abs(bar.close - bar.open)
    bar_range = bar.high - bar.low
    if bar_range <= 0.0:
        return False
    close_location = (bar.close - bar.low) / bar_range
    located = (
        close_location >= config.displacement_close_location
        if direction > 0
        else close_location <= 1.0 - config.displacement_close_location
    )
    sweep_midpoint = (attempt.sweep_high + attempt.sweep_low) / 2.0
    continued = bar.close > sweep_midpoint if direction > 0 else bar.close < sweep_midpoint
    directional_body = direction * (bar.close - bar.open) > 0.0
    inside_from_failed_edge = (
        bar.close > attempt.balance.low
        if direction > 0
        else bar.close < attempt.balance.high
    )
    return (
        directional_body
        and continued
        and located
        and inside_from_failed_edge
        and body >= prior_body_median
        and bar_range >= prior_range_median
    )


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
    # Raw detector reserves one adverse entry tick. The shared V2 bar-market execution adapter adds
    # the second deterministic tick before quantity sizing and recomputes net reward-risk.
    loss = abs(entry - stop) + fee_rate * (entry + stop) + tick + stop_reserve
    gross_gain = target - entry if direction > 0 else entry - target
    gain = gross_gain - fee_rate * (entry + target) - 2.0 * tick
    if loss <= 0.0 or gain <= 0.0:
        return None
    return loss, gain, gain / loss


def _route_positions(
    bars: tuple[FiveMinuteBar, ...],
    balance: InitialBalance,
) -> tuple[int, ...]:
    return tuple(
        position
        for position, bar in enumerate(bars)
        if balance.end_time_ns <= _bar_start_ns(bar) < balance.opportunity_end_ns
    )


def build_opening_failed_auction_signals(
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
    config: OpeningAuctionConfig,
    require_retest_contraction: bool = True,
) -> QuoteResiliencySignalBundle:
    """Emit one immutable failed-opening-auction attempt per completed session."""

    del context_times, snapshots, require_retest_contraction
    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")

    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    grouped: dict[int, list[QuoteResiliencySignal]] = {}
    balances = build_initial_balances(context_bars, config, diagnostics)
    body_median, range_median = _shifted_prior_medians(
        context_bars,
        config.displacement_lookback,
    )
    data_times = data.index.as_unit("ns").asi8
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    scenario_counter = 0

    for balance in balances:
        route_positions = _route_positions(context_bars, balance)
        if not route_positions:
            diagnostics["NO_COMPLETE_OPENING_AUCTION_ROUTE_BARS"] += 1
            continue
        attempt: FailedAuctionAttempt | None = None
        terminal = False
        for position in route_positions:
            bar = context_bars[position]
            if attempt is None:
                edge = _failed_edge(bar, balance, config)
                if edge is None:
                    continue
                direction, swept_edge = edge
                if direction == 0:
                    diagnostics["BILATERAL_INITIAL_BALANCE_SWEEP"] += 1
                    rejected.append(
                        {
                            "symbol": symbol,
                            "session": balance.session_name,
                            "local_date": balance.local_date,
                            "reason": "BILATERAL_INITIAL_BALANCE_SWEEP",
                            "observed_time_ns": int(bar.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                scenario_counter += 1
                scenario_id = (
                    f"opening-failed-{symbol.lower()}-{balance.session_name.lower()}-"
                    f"{balance.local_date}-{scenario_counter:05d}"
                )
                attempt = FailedAuctionAttempt(
                    scenario_id=scenario_id,
                    balance=balance,
                    direction=direction,
                    swept_edge=swept_edge,
                    sweep_position=position,
                    sweep_time_ns=int(bar.ts_event_ns),
                    sweep_high=float(bar.high),
                    sweep_low=float(bar.low),
                    sweep_open=float(bar.open),
                    sweep_close=float(bar.close),
                    atr=float(bar.atr),
                )
                diagnostics["INITIAL_BALANCE_EDGE_SWEPT_AND_RECLAIMED"] += 1
                continue

            direction = attempt.direction
            invalidated = (
                bar.close <= balance.low
                if direction > 0
                else bar.close >= balance.high
            )
            if invalidated:
                reason = "FAILED_AUCTION_REACCEPTED_BEYOND_SWEPT_EDGE"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": reason,
                        "sweep_time_ns": attempt.sweep_time_ns,
                        "observed_time_ns": int(bar.ts_event_ns),
                    }
                )
                terminal = True
                break

            target_consumed = (
                bar.high >= balance.high
                if direction > 0
                else bar.low <= balance.low
            )
            if target_consumed:
                reason = "OPPOSITE_INITIAL_BALANCE_EDGE_CONSUMED_BEFORE_ENTRY"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": reason,
                        "sweep_time_ns": attempt.sweep_time_ns,
                        "observed_time_ns": int(bar.ts_event_ns),
                    }
                )
                terminal = True
                break

            if not _displacement_confirms(
                bar,
                attempt,
                prior_body_median=float(body_median[position]),
                prior_range_median=float(range_median[position]),
                config=config,
            ):
                continue

            confirmation_ns = int(bar.ts_event_ns)
            execution_position = contiguous_first_execution_position_after(
                data_times,
                confirmation_ns,
            )
            if execution_position is None:
                reason = "NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": reason,
                        "confirmation_time_ns": confirmation_ns,
                    }
                )
                terminal = True
                break

            execution_row = data.iloc[execution_position]
            execution_ns = int(data_times[execution_position])
            execution_target_consumed = (
                float(execution_row["high"]) >= balance.high
                if direction > 0
                else float(execution_row["low"]) <= balance.low
            )
            if execution_target_consumed:
                reason = "OPPOSITE_INITIAL_BALANCE_EDGE_CONSUMED_IN_EXECUTION_OBSERVATION"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": reason,
                        "confirmation_time_ns": confirmation_ns,
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
                    attempt.sweep_low,
                    float(bar.low),
                    float(execution_row["low"]),
                )
                stop = min(stop_reference - buffer, entry - minimum_distance)
                target = balance.high
                boundary_level = balance.low
                boundary_side = "LOW"
                target_side = "HIGH"
                stop_source = "FAILED_OPENING_AUCTION_SEQUENCE_LOW"
            else:
                stop_reference = max(
                    attempt.sweep_high,
                    float(bar.high),
                    float(execution_row["high"]),
                )
                stop = max(stop_reference + buffer, entry + minimum_distance)
                target = balance.low
                boundary_level = balance.high
                boundary_side = "HIGH"
                target_side = "LOW"
                stop_source = "FAILED_OPENING_AUCTION_SEQUENCE_HIGH"

            geometry = _cost_geometry(
                direction=direction,
                entry=entry,
                stop=stop,
                target=target,
                fee_rate=fee_rate,
                tick=tick,
                stop_slippage_reserve=float(stop_reserves.iloc[execution_position]),
            )
            if geometry is None:
                reason = "INVALID_COST_AFTER_OPPOSITE_INITIAL_BALANCE_EDGE"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": reason,
                        "entry_reference": entry,
                        "structural_stop": stop,
                        "external_target": target,
                    }
                )
                terminal = True
                break
            loss, gain, net_rr = geometry
            if net_rr < minimum_net_reward_risk:
                reason = "INSUFFICIENT_COST_AFTER_OPPOSITE_INITIAL_BALANCE_EDGE"
                diagnostics[reason] += 1
                rejected.append(
                    {
                        "scenario_id": attempt.scenario_id,
                        "symbol": symbol,
                        "session": balance.session_name,
                        "local_date": balance.local_date,
                        "reason": reason,
                        "net_reward_risk": net_rr,
                    }
                )
                terminal = True
                break

            boundary_id = (
                f"{balance.session_name}-{balance.local_date}-INITIAL_BALANCE-{boundary_side}"
            )
            target_id = (
                f"{balance.session_name}-{balance.local_date}-INITIAL_BALANCE-{target_side}"
            )
            direction_name = "LONG" if direction > 0 else "SHORT"
            shared_details = {
                "scenario_family": SCENARIO_FAMILY,
                "signal_revision": SIGNAL_REVISION,
                "session": balance.session_name,
                "session_timezone": balance.timezone_name,
                "session_local_date": balance.local_date,
                "session_open_time_ns": balance.start_time_ns,
                "initial_balance_end_time_ns": balance.end_time_ns,
                "initial_balance_high": balance.high,
                "initial_balance_low": balance.low,
                "initial_balance_midpoint": balance.midpoint,
                "swept_edge": attempt.swept_edge,
                "ten_second_alpha_inputs": False,
            }
            events = (
                QuoteResiliencyLogicEvent(
                    scenario_id=attempt.scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="INITIAL_BALANCE_EDGE_SWEPT_AND_RECLAIMED",
                    event_time_ns=attempt.sweep_time_ns,
                    observed_time_ns=attempt.sweep_time_ns,
                    previous_state="INITIAL_BALANCE_COMPLETE",
                    next_state="FAILED_EDGE_AUCTION_OBSERVED",
                    reason_code=f"COMPLETED_FIVE_MINUTE_{boundary_side}_SWEEP_CLOSE_BACK_INSIDE",
                    reference_price=boundary_level,
                    details={**shared_details, "lifecycle_stage": "SWEEP_RECLAIM"},
                ),
                QuoteResiliencyLogicEvent(
                    scenario_id=attempt.scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="FAILED_AUCTION_DISPLACEMENT_CONFIRMED",
                    event_time_ns=confirmation_ns,
                    observed_time_ns=confirmation_ns,
                    previous_state="FAILED_EDGE_AUCTION_OBSERVED",
                    next_state="FAILED_AUCTION_DISPLACEMENT_CONFIRMED",
                    reason_code=f"SEPARATE_COMPLETED_FIVE_MINUTE_DISPLACEMENT_{direction_name}",
                    reference_price=float(bar.close),
                    details={
                        **shared_details,
                        "lifecycle_stage": "DISPLACEMENT",
                        "confirmation_five_index": int(bar.index),
                        "confirmation_body": abs(float(bar.close - bar.open)),
                        "confirmation_range": float(bar.high - bar.low),
                    },
                ),
                QuoteResiliencyLogicEvent(
                    scenario_id=attempt.scenario_id,
                    symbol=symbol,
                    instrument_id=instrument_id,
                    event_type="NEXT_EXECUTION_BUCKET_OBSERVED",
                    event_time_ns=execution_ns,
                    observed_time_ns=execution_ns,
                    previous_state="FAILED_AUCTION_DISPLACEMENT_CONFIRMED",
                    next_state="CONFIRMED",
                    reason_code="FIRST_CONTIGUOUS_TEN_SECOND_BUCKET_AFTER_M5_CONFIRMATION",
                    reference_price=entry,
                    details={
                        **shared_details,
                        "lifecycle_stage": "EXECUTION",
                        "next_execution_bucket_gap_ns": execution_ns - confirmation_ns,
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
                boundary_source=f"{balance.session_name}_INITIAL_BALANCE_{boundary_side}",
                boundary_level=boundary_level,
                target_id=target_id,
                target_source=f"{balance.session_name}_INITIAL_BALANCE_{target_side}",
                external_target=target,
                entry_reference=entry,
                structural_stop=stop,
                stop_reference=stop_reference,
                stop_reference_source=stop_source,
                atr=attempt.atr,
                causal_stop_slippage_reserve=float(stop_reserves.iloc[execution_position]),
                expected_loss_per_unit=loss,
                expected_gain_per_unit=gain,
                net_reward_risk=net_rr,
                interaction_time_ns=attempt.sweep_time_ns,
                response_time_ns=confirmation_ns,
                retest_time_ns=None,
                events=events,
                details={
                    **shared_details,
                    "sweep_five_index": int(context_bars[attempt.sweep_position].index),
                    "confirmation_five_index": int(bar.index),
                    "execution_position": execution_position,
                    "stop_reference": stop_reference,
                    "stop_reference_source": stop_source,
                    "causal_stop_slippage_reserve": float(
                        stop_reserves.iloc[execution_position]
                    ),
                    "slippage_reserve_contract": "SHIFTED_60M_TEN_SECOND_TRUE_RANGE_Q99",
                    "target_contract": "OPPOSITE_COMPLETED_INITIAL_BALANCE_EDGE",
                    "entry_contract": "NEXT_CONTIGUOUS_TEN_SECOND_EXECUTION_OBSERVATION_ONLY",
                },
            )
            grouped.setdefault(execution_ns, []).append(signal)
            diagnostics["TRADEABLE_OPENING_FAILED_AUCTION_SIGNAL"] += 1
            terminal = True
            break

        if attempt is not None and not terminal:
            reason = "NO_SEPARATE_DISPLACEMENT_BEFORE_OPENING_ROUTE_END"
            diagnostics[reason] += 1
            rejected.append(
                {
                    "scenario_id": attempt.scenario_id,
                    "symbol": symbol,
                    "session": balance.session_name,
                    "local_date": balance.local_date,
                    "reason": reason,
                    "sweep_time_ns": attempt.sweep_time_ns,
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
    "InitialBalance",
    "OpeningAuctionConfig",
    "SCENARIO_FAMILY",
    "SESSION_SPECS",
    "SIGNAL_REVISION",
    "build_initial_balances",
    "build_opening_failed_auction_signals",
    "session_open_utc",
]
