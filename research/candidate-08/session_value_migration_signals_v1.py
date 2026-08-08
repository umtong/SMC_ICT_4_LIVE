"""Causal previous-day value migration and session boundary-retest detector.

A complete UTC day supplies a volume-weighted typical-price distribution. Its mean plus/minus one
weighted standard deviation defines the completed value range. A major session can trade a value-
migration continuation only after two consecutive completed fifteen-minute closes outside one value
edge, with the cumulative session VWAP also outside that edge, followed by a separate completed
five-minute retest that touches and holds the accepted boundary. The frozen objective is one full
completed value-range width beyond the edge. Ten-second data supplies only the next contiguous
execution observation and a causal stop-execution reserve.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from math import isfinite, sqrt
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from opening_initial_balance_failed_auction_signals_v1 import (
    SESSION_SPECS,
    SessionSpec,
    session_open_utc,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar
from session_raid_reversal_signals_v2 import contiguous_first_execution_position_after


SIGNAL_REVISION = "SESSION_VALUE_MIGRATION_ACCEPTANCE_SIGNALS_V1"
SCENARIO_FAMILY = "SESSION_PREVIOUS_DAY_VALUE_MIGRATION_CONTINUATION"
FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
FIFTEEN_MINUTES_NS = 15 * 60 * 1_000_000_000
ONE_DAY_NS = 24 * 60 * 60 * 1_000_000_000


@dataclass(frozen=True, slots=True)
class SessionValueMigrationConfig:
    opportunity_minutes: int = 240
    acceptance_excursion_sigma: float = 0.05
    retest_tolerance_atr: float = 0.05
    stop_buffer_atr: float = 0.05
    minimum_stop_distance_atr: float = 0.10
    target_value_range_multiple: float = 1.0

    def validate(self) -> None:
        if not 120 <= self.opportunity_minutes <= 360:
            raise ValueError("value-migration opportunity must remain intraday")
        if min(
            self.acceptance_excursion_sigma,
            self.retest_tolerance_atr,
            self.stop_buffer_atr,
            self.minimum_stop_distance_atr,
        ) <= 0.0:
            raise ValueError("relative value-migration distances must be positive")
        if self.target_value_range_multiple != 1.0:
            raise ValueError("V1 fixes the target at one completed value-range extension")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "SessionValueMigrationConfig":
        config = cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})
        config.validate()
        return config


@dataclass(frozen=True, slots=True)
class DailyValueProfile:
    utc_day_start_ns: int
    utc_day: str
    observed_time_ns: int
    vwap: float
    sigma: float
    value_low: float
    value_high: float
    range_width: float
    lower_extension: float
    upper_extension: float
    actual_low: float
    actual_high: float
    total_volume: float


@dataclass(frozen=True, slots=True)
class SessionWindow:
    spec: SessionSpec
    local_day: date
    start_time_ns: int
    end_time_ns: int


@dataclass(frozen=True, slots=True)
class SessionFifteenBar:
    bucket_index: int
    start_time_ns: int
    end_time_ns: int
    first_five_position: int
    last_five_position: int
    open: float
    high: float
    low: float
    close: float
    session_vwap: float


@dataclass(frozen=True, slots=True)
class MigrationAttempt:
    scenario_id: str
    profile: DailyValueProfile
    window: SessionWindow
    direction: int
    boundary_side: str
    boundary_level: float
    target: float
    first_close: SessionFifteenBar
    second_close: SessionFifteenBar | None = None


def _bar_start_ns(bar: FiveMinuteBar) -> int:
    return int(bar.ts_event_ns) // FIVE_MINUTES_NS * FIVE_MINUTES_NS


def _day_start_ns(timestamp_ns: int) -> int:
    return int(timestamp_ns) // ONE_DAY_NS * ONE_DAY_NS


def build_completed_daily_value_profiles(
    bars: tuple[FiveMinuteBar, ...],
    diagnostics: Counter[str] | None = None,
) -> dict[int, DailyValueProfile]:
    """Build volume-weighted profiles only from complete contiguous UTC days."""

    counts = diagnostics if diagnostics is not None else Counter()
    grouped: dict[int, list[tuple[int, FiveMinuteBar]]] = {}
    for position, bar in enumerate(bars):
        start_ns = _bar_start_ns(bar)
        grouped.setdefault(_day_start_ns(start_ns), []).append((position, bar))

    result: dict[int, DailyValueProfile] = {}
    for day_start, positioned in sorted(grouped.items()):
        positioned.sort(key=lambda item: _bar_start_ns(item[1]))
        expected_starts = [day_start + offset * FIVE_MINUTES_NS for offset in range(288)]
        starts = [_bar_start_ns(bar) for _, bar in positioned]
        if starts != expected_starts:
            counts["INCOMPLETE_PREVIOUS_UTC_DAY_PROFILE"] += 1
            continue
        prices: list[float] = []
        weights: list[float] = []
        valid = True
        for _, bar in positioned:
            typical = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
            volume = float(bar.volume)
            if not all(isfinite(value) for value in (typical, volume)) or volume <= 0.0:
                valid = False
                break
            prices.append(typical)
            weights.append(volume)
        if not valid:
            counts["UNOBSERVABLE_PREVIOUS_UTC_DAY_PROFILE"] += 1
            continue
        total_volume = float(sum(weights))
        vwap = float(sum(price * weight for price, weight in zip(prices, weights)) / total_volume)
        variance = float(
            sum(weight * (price - vwap) ** 2 for price, weight in zip(prices, weights))
            / total_volume
        )
        sigma = sqrt(max(variance, 0.0))
        if not isfinite(sigma) or sigma <= 0.0:
            counts["DEGENERATE_PREVIOUS_UTC_DAY_PROFILE"] += 1
            continue
        value_low = vwap - sigma
        value_high = vwap + sigma
        width = value_high - value_low
        rows = [bar for _, bar in positioned]
        result[day_start] = DailyValueProfile(
            utc_day_start_ns=day_start,
            utc_day=pd.Timestamp(day_start, unit="ns", tz="UTC").date().isoformat(),
            observed_time_ns=int(rows[-1].ts_event_ns),
            vwap=vwap,
            sigma=sigma,
            value_low=value_low,
            value_high=value_high,
            range_width=width,
            lower_extension=value_low - width,
            upper_extension=value_high + width,
            actual_low=min(float(bar.low) for bar in rows),
            actual_high=max(float(bar.high) for bar in rows),
            total_volume=total_volume,
        )
        counts["COMPLETE_PREVIOUS_UTC_DAY_PROFILE"] += 1
    return result


def _session_windows(
    bars: tuple[FiveMinuteBar, ...],
    config: SessionValueMigrationConfig,
) -> tuple[SessionWindow, ...]:
    if not bars:
        return ()
    first = pd.Timestamp(int(bars[0].ts_event_ns), unit="ns", tz="UTC").date() - timedelta(days=2)
    last = pd.Timestamp(int(bars[-1].ts_event_ns), unit="ns", tz="UTC").date() + timedelta(days=2)
    windows: list[SessionWindow] = []
    cursor = first
    while cursor <= last:
        for spec in SESSION_SPECS:
            start_ns = int(pd.Timestamp(session_open_utc(cursor, spec)).as_unit("ns").value)
            windows.append(
                SessionWindow(
                    spec=spec,
                    local_day=cursor,
                    start_time_ns=start_ns,
                    end_time_ns=start_ns + config.opportunity_minutes * 60 * 1_000_000_000,
                )
            )
        cursor += timedelta(days=1)
    return tuple(sorted(windows, key=lambda item: (item.start_time_ns, item.spec.name)))


def _aggregate_session_fifteen(
    bars: tuple[FiveMinuteBar, ...],
    window: SessionWindow,
) -> tuple[SessionFifteenBar, ...]:
    positioned = [
        (position, bar)
        for position, bar in enumerate(bars)
        if window.start_time_ns <= _bar_start_ns(bar) < window.end_time_ns
    ]
    positioned.sort(key=lambda item: _bar_start_ns(item[1]))
    if not positioned:
        return ()

    expected_start = window.start_time_ns
    contiguous: list[tuple[int, FiveMinuteBar]] = []
    for item in positioned:
        start_ns = _bar_start_ns(item[1])
        if start_ns != expected_start:
            break
        contiguous.append(item)
        expected_start += FIVE_MINUTES_NS

    result: list[SessionFifteenBar] = []
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    for bucket_index in range(len(contiguous) // 3):
        rows = contiguous[bucket_index * 3 : bucket_index * 3 + 3]
        if len(rows) != 3:
            break
        for _, bar in rows:
            volume = float(bar.volume)
            typical = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
            if not all(isfinite(value) for value in (volume, typical)) or volume <= 0.0:
                return tuple(result)
            cumulative_pv += volume * typical
            cumulative_volume += volume
        first_position, first_bar = rows[0]
        last_position, last_bar = rows[-1]
        result.append(
            SessionFifteenBar(
                bucket_index=bucket_index,
                start_time_ns=_bar_start_ns(first_bar),
                end_time_ns=int(last_bar.ts_event_ns),
                first_five_position=first_position,
                last_five_position=last_position,
                open=float(first_bar.open),
                high=max(float(bar.high) for _, bar in rows),
                low=min(float(bar.low) for _, bar in rows),
                close=float(last_bar.close),
                session_vwap=cumulative_pv / cumulative_volume,
            )
        )
    return tuple(result)


def _first_outside_close(
    bar: SessionFifteenBar,
    profile: DailyValueProfile,
    config: SessionValueMigrationConfig,
) -> tuple[int, str, float, float] | None:
    excursion = config.acceptance_excursion_sigma * profile.sigma
    long_accept = bar.close >= profile.value_high + excursion and bar.close > bar.open
    short_accept = bar.close <= profile.value_low - excursion and bar.close < bar.open
    if long_accept and short_accept:
        return 0, "BILATERAL", float("nan"), float("nan")
    if long_accept:
        return 1, "HIGH", profile.value_high, profile.upper_extension
    if short_accept:
        return -1, "LOW", profile.value_low, profile.lower_extension
    return None


def _target_consumed(direction: int, high: float, low: float, target: float) -> bool:
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


def build_session_value_migration_signals(
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
    config: SessionValueMigrationConfig,
    require_retest_contraction: bool = True,
) -> QuoteResiliencySignalBundle:
    """Emit one accepted previous-day value migration per major session."""

    del context_times, snapshots, require_retest_contraction
    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid cost contract")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("ten-second data must use a timezone-aware DatetimeIndex")

    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    grouped: dict[int, list[QuoteResiliencySignal]] = {}
    profiles = build_completed_daily_value_profiles(context_bars, diagnostics)
    data_times = data.index.as_unit("ns").asi8
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    scenario_counter = 0

    for window in _session_windows(context_bars, config):
        prior_day_start = _day_start_ns(window.start_time_ns) - ONE_DAY_NS
        profile = profiles.get(prior_day_start)
        if profile is None:
            diagnostics["NO_COMPLETE_PREVIOUS_DAY_PROFILE_FOR_SESSION"] += 1
            continue
        if profile.observed_time_ns >= window.start_time_ns:
            raise RuntimeError("previous-day value profile was not observable before session open")
        fifteen = _aggregate_session_fifteen(context_bars, window)
        if len(fifteen) < 2:
            diagnostics["INSUFFICIENT_COMPLETE_SESSION_M15_BARS"] += 1
            continue

        attempt: MigrationAttempt | None = None
        terminal = False
        for index, bar in enumerate(fifteen):
            if attempt is None:
                outside = _first_outside_close(bar, profile, config)
                if outside is None:
                    continue
                direction, side, boundary, target = outside
                if direction == 0:
                    diagnostics["BILATERAL_PREVIOUS_VALUE_BREAK"] += 1
                    terminal = True
                    break
                scenario_counter += 1
                scenario_id = (
                    f"value-migration-{symbol.lower()}-{window.spec.name.lower()}-"
                    f"{window.local_day.isoformat()}-{scenario_counter:05d}"
                )
                if _target_consumed(direction, bar.high, bar.low, target):
                    diagnostics["VALUE_EXTENSION_CONSUMED_IN_FIRST_OUTSIDE_M15"] += 1
                    rejected.append(
                        {
                            "scenario_id": scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": "VALUE_EXTENSION_CONSUMED_IN_FIRST_OUTSIDE_M15",
                            "observed_time_ns": bar.end_time_ns,
                        }
                    )
                    terminal = True
                    break
                attempt = MigrationAttempt(
                    scenario_id=scenario_id,
                    profile=profile,
                    window=window,
                    direction=direction,
                    boundary_side=side,
                    boundary_level=boundary,
                    target=target,
                    first_close=bar,
                )
                diagnostics["FIRST_M15_CLOSE_OUTSIDE_PREVIOUS_VALUE"] += 1
                continue

            if attempt.second_close is None:
                if bar.start_time_ns - attempt.first_close.start_time_ns != FIFTEEN_MINUTES_NS:
                    diagnostics["MISSING_IMMEDIATE_SECOND_VALUE_ACCEPTANCE_CLOSE"] += 1
                    terminal = True
                    break
                direction = attempt.direction
                outside = (
                    bar.close > attempt.boundary_level
                    if direction > 0
                    else bar.close < attempt.boundary_level
                )
                vwap_migrated = (
                    bar.session_vwap > attempt.boundary_level
                    if direction > 0
                    else bar.session_vwap < attempt.boundary_level
                )
                if not outside or not vwap_migrated:
                    reason = (
                        "SECOND_M15_CLOSE_RETURNED_INSIDE_VALUE"
                        if not outside
                        else "SESSION_VWAP_DID_NOT_MIGRATE_OUTSIDE_VALUE"
                    )
                    diagnostics[reason] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": reason,
                            "observed_time_ns": bar.end_time_ns,
                            "session_vwap": bar.session_vwap,
                            "boundary_level": attempt.boundary_level,
                        }
                    )
                    terminal = True
                    break
                if _target_consumed(direction, bar.high, bar.low, attempt.target):
                    diagnostics["VALUE_EXTENSION_CONSUMED_BEFORE_RETEST"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": "VALUE_EXTENSION_CONSUMED_BEFORE_RETEST",
                            "observed_time_ns": bar.end_time_ns,
                        }
                    )
                    terminal = True
                    break
                attempt = replace(attempt, second_close=bar)
                diagnostics["SECOND_M15_CLOSE_AND_SESSION_VWAP_MIGRATED"] += 1
                continue

            direction = attempt.direction
            for five_position in range(
                attempt.second_close.last_five_position + 1,
                len(context_bars),
            ):
                five = context_bars[five_position]
                start_ns = _bar_start_ns(five)
                if start_ns >= window.end_time_ns:
                    break
                if _target_consumed(direction, float(five.high), float(five.low), attempt.target):
                    diagnostics["VALUE_EXTENSION_CONSUMED_BEFORE_RETEST"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": "VALUE_EXTENSION_CONSUMED_BEFORE_RETEST",
                            "observed_time_ns": int(five.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                if not isfinite(float(five.atr)) or float(five.atr) <= 0.0:
                    continue
                tolerance = config.retest_tolerance_atr * float(five.atr)
                if direction > 0:
                    invalidated = float(five.close) < attempt.boundary_level - tolerance
                    held = (
                        float(five.low) <= attempt.boundary_level + tolerance
                        and float(five.close) > attempt.boundary_level
                    )
                else:
                    invalidated = float(five.close) > attempt.boundary_level + tolerance
                    held = (
                        float(five.high) >= attempt.boundary_level - tolerance
                        and float(five.close) < attempt.boundary_level
                    )
                if invalidated:
                    diagnostics["ACCEPTED_VALUE_MIGRATION_REENTERED_PREVIOUS_VALUE"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": "ACCEPTED_VALUE_MIGRATION_REENTERED_PREVIOUS_VALUE",
                            "observed_time_ns": int(five.ts_event_ns),
                        }
                    )
                    terminal = True
                    break
                if not held:
                    continue

                retest_ns = int(five.ts_event_ns)
                execution_position = contiguous_first_execution_position_after(data_times, retest_ns)
                if execution_position is None:
                    diagnostics["NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": "NO_CONTIGUOUS_NEXT_TEN_SECOND_EXECUTION_BUCKET",
                            "retest_time_ns": retest_ns,
                        }
                    )
                    terminal = True
                    break
                execution_row = data.iloc[execution_position]
                execution_ns = int(data_times[execution_position])
                if _target_consumed(
                    direction,
                    float(execution_row["high"]),
                    float(execution_row["low"]),
                    attempt.target,
                ):
                    diagnostics["VALUE_EXTENSION_CONSUMED_IN_EXECUTION_OBSERVATION"] += 1
                    terminal = True
                    break

                entry = float(execution_row["close"])
                buffer = config.stop_buffer_atr * float(five.atr)
                minimum_distance = config.minimum_stop_distance_atr * float(five.atr)
                if direction > 0:
                    stop_reference = min(
                        attempt.boundary_level,
                        float(five.low),
                        float(execution_row["low"]),
                    )
                    stop = min(stop_reference - buffer, entry - minimum_distance)
                    stop_source = "PREVIOUS_VALUE_HIGH_RETEST_SEQUENCE_LOW"
                else:
                    stop_reference = max(
                        attempt.boundary_level,
                        float(five.high),
                        float(execution_row["high"]),
                    )
                    stop = max(stop_reference + buffer, entry + minimum_distance)
                    stop_source = "PREVIOUS_VALUE_LOW_RETEST_SEQUENCE_HIGH"

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
                    diagnostics["INVALID_COST_AFTER_VALUE_EXTENSION_GEOMETRY"] += 1
                    terminal = True
                    break
                loss, gain, net_rr = geometry
                if net_rr < minimum_net_reward_risk:
                    diagnostics["INSUFFICIENT_COST_AFTER_VALUE_EXTENSION"] += 1
                    rejected.append(
                        {
                            "scenario_id": attempt.scenario_id,
                            "symbol": symbol,
                            "session": window.spec.name,
                            "reason": "INSUFFICIENT_COST_AFTER_VALUE_EXTENSION",
                            "net_reward_risk": net_rr,
                        }
                    )
                    terminal = True
                    break

                boundary_id = (
                    f"UTC-{profile.utc_day}-VALUE-{attempt.boundary_side}"
                )
                target_side = "UPPER" if direction > 0 else "LOWER"
                target_id = f"UTC-{profile.utc_day}-VALUE-RANGE-{target_side}-EXTENSION"
                direction_name = "LONG" if direction > 0 else "SHORT"
                shared = {
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "session": window.spec.name,
                    "session_timezone": window.spec.timezone_name,
                    "session_local_date": window.local_day.isoformat(),
                    "previous_utc_day": profile.utc_day,
                    "previous_day_vwap": profile.vwap,
                    "previous_day_sigma": profile.sigma,
                    "previous_value_low": profile.value_low,
                    "previous_value_high": profile.value_high,
                    "previous_value_range_width": profile.range_width,
                    "accepted_session_vwap": attempt.second_close.session_vwap,
                    "ten_second_alpha_inputs": False,
                }
                events = (
                    QuoteResiliencyLogicEvent(
                        scenario_id=attempt.scenario_id,
                        symbol=symbol,
                        instrument_id=instrument_id,
                        event_type="FIRST_M15_CLOSE_OUTSIDE_PREVIOUS_VALUE",
                        event_time_ns=attempt.first_close.end_time_ns,
                        observed_time_ns=attempt.first_close.end_time_ns,
                        previous_state="PREVIOUS_DAY_VALUE_PROFILE_COMPLETE",
                        next_state="FIRST_OUTSIDE_VALUE_CLOSE",
                        reason_code=f"FIRST_COMPLETED_M15_CLOSE_OUTSIDE_VALUE_{attempt.boundary_side}",
                        reference_price=attempt.boundary_level,
                        details={**shared, "lifecycle_stage": "FIRST_CLOSE"},
                    ),
                    QuoteResiliencyLogicEvent(
                        scenario_id=attempt.scenario_id,
                        symbol=symbol,
                        instrument_id=instrument_id,
                        event_type="SECOND_M15_CLOSE_AND_SESSION_VWAP_MIGRATION_ACCEPTED",
                        event_time_ns=attempt.second_close.end_time_ns,
                        observed_time_ns=attempt.second_close.end_time_ns,
                        previous_state="FIRST_OUTSIDE_VALUE_CLOSE",
                        next_state="VALUE_MIGRATION_ACCEPTED",
                        reason_code=f"SECOND_M15_CLOSE_AND_SESSION_VWAP_{direction_name}_OF_VALUE_EDGE",
                        reference_price=attempt.second_close.session_vwap,
                        details={**shared, "lifecycle_stage": "ACCEPTANCE"},
                    ),
                    QuoteResiliencyLogicEvent(
                        scenario_id=attempt.scenario_id,
                        symbol=symbol,
                        instrument_id=instrument_id,
                        event_type="PREVIOUS_VALUE_EDGE_RETEST_HELD",
                        event_time_ns=retest_ns,
                        observed_time_ns=retest_ns,
                        previous_state="VALUE_MIGRATION_ACCEPTED",
                        next_state="CONFIRMED",
                        reason_code="SEPARATE_COMPLETED_M5_RETEST_TOUCHES_AND_CLOSES_OUTSIDE_VALUE",
                        reference_price=attempt.boundary_level,
                        details={
                            **shared,
                            "lifecycle_stage": "RETEST",
                            "retest_five_index": int(five.index),
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
                    boundary_source=f"PREVIOUS_DAY_VALUE_{attempt.boundary_side}",
                    boundary_level=attempt.boundary_level,
                    target_id=target_id,
                    target_source="PREVIOUS_DAY_VALUE_RANGE_ONE_WIDTH_EXTENSION",
                    external_target=attempt.target,
                    entry_reference=entry,
                    structural_stop=stop,
                    stop_reference=stop_reference,
                    stop_reference_source=stop_source,
                    atr=float(five.atr),
                    causal_stop_slippage_reserve=float(stop_reserves.iloc[execution_position]),
                    expected_loss_per_unit=loss,
                    expected_gain_per_unit=gain,
                    net_reward_risk=net_rr,
                    interaction_time_ns=attempt.first_close.end_time_ns,
                    response_time_ns=attempt.second_close.end_time_ns,
                    retest_time_ns=retest_ns,
                    events=events,
                    details={
                        **shared,
                        "first_m15_bucket": attempt.first_close.bucket_index,
                        "second_m15_bucket": attempt.second_close.bucket_index,
                        "retest_five_index": int(five.index),
                        "execution_position": execution_position,
                        "next_execution_bucket_gap_ns": execution_ns - retest_ns,
                        "target_contract": "ONE_COMPLETED_PREVIOUS_VALUE_RANGE_WIDTH_EXTENSION",
                        "entry_contract": "NEXT_CONTIGUOUS_TEN_SECOND_EXECUTION_OBSERVATION_ONLY",
                        "slippage_reserve_contract": "SHIFTED_60M_TEN_SECOND_TRUE_RANGE_Q99",
                    },
                )
                grouped.setdefault(execution_ns, []).append(signal)
                diagnostics["TRADEABLE_SESSION_VALUE_MIGRATION_SIGNAL"] += 1
                terminal = True
                break

            break

        if attempt is not None and not terminal:
            reason = (
                "NO_IMMEDIATE_SECOND_VALUE_ACCEPTANCE_CLOSE_BEFORE_SESSION_END"
                if attempt.second_close is None
                else "NO_PREVIOUS_VALUE_EDGE_RETEST_BEFORE_SESSION_END"
            )
            diagnostics[reason] += 1
            rejected.append(
                {
                    "scenario_id": attempt.scenario_id,
                    "symbol": symbol,
                    "session": window.spec.name,
                    "reason": reason,
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
    "DailyValueProfile",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "SessionValueMigrationConfig",
    "build_completed_daily_value_profiles",
    "build_session_value_migration_signals",
]
