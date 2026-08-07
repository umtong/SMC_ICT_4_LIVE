"""Causal quarter-hour continuation toward pre-burst intraday liquidity.

The scenario represents a scheduled auction rather than a candle pattern:

1. a completed one-minute burst begins on a UTC quarter-hour boundary;
2. aggressive flow, price displacement, recent boundary flow and the previous completed
   four-hour auction all agree on direction;
3. a lower-activity retracement reaches value without invalidating the burst origin;
4. a separate completed minute reaccelerates through the retracement extreme; and
5. an already-confirmed, still-unconsumed fifteen-minute swing in the same direction supplies the
   liquidity objective.

The target must have been confirmed before the burst and remain untouched through entry. The
module contains no order, fill, account, position, sizing, PnL or backtest engine logic;
NautilusTrader remains authoritative for execution and accounting.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series
from external_sweep_fvg_retrace_signals import (
    _cost_geometry,
    aggregate_completed_minutes,
)
from quote_resiliency_signals import (
    QuoteResiliencyLogicEvent,
    QuoteResiliencySignal,
    QuoteResiliencySignalBundle,
    executable_quote_reference,
)


SIGNAL_REVISION = "CAUSAL_QH_SESSION_ALIGNED_INTRADAY_LIQUIDITY_V1"
SCENARIO_FAMILY = "QH_SESSION_ALIGNED_INTRADAY_LIQUIDITY_CONTINUATION"


@dataclass(frozen=True, slots=True)
class QuarterHourSessionLiquidityConfig:
    minute_atr_bars: int = 60
    minimum_minute_history: int = 30
    burst_imbalance: float = 0.18
    burst_volume_ratio: float = 1.50
    burst_trade_ratio: float = 1.25
    burst_body_atr: float = 0.50
    burst_close_location: float = 0.62
    minimum_boundary_lag_observations: int = 2
    maximum_retrace_minutes: int = 6
    retrace_body_fraction: float = 0.25
    origin_hold_atr: float = 0.10
    excessive_retrace_atr: float = 0.35
    maximum_retrace_volume_fraction: float = 0.80
    maximum_retrace_trade_fraction: float = 0.90
    maximum_retrace_imbalance_fraction: float = 0.75
    retrace_imbalance_allowance: float = 0.03
    maximum_reacceleration_minutes: int = 4
    reacceleration_break_atr: float = 0.05
    minimum_reacceleration_body_atr: float = 0.15
    minimum_reacceleration_imbalance: float = 0.05
    reacceleration_close_location: float = 0.62
    minimum_reacceleration_volume_multiple: float = 1.05
    target_swing_span: int = 1
    target_max_age_minutes: int = 480
    stop_buffer_atr: float = 0.05
    minimum_stop_atr: float = 0.25

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "QuarterHourSessionLiquidityConfig":
        return cls(**{key: values[key] for key in cls.__dataclass_fields__ if key in values})

    def validate(self) -> None:
        positive_ints = (
            self.minute_atr_bars,
            self.minimum_minute_history,
            self.minimum_boundary_lag_observations,
            self.maximum_retrace_minutes,
            self.maximum_reacceleration_minutes,
            self.target_swing_span,
            self.target_max_age_minutes,
        )
        if any(value <= 0 for value in positive_ints):
            raise ValueError("quarter-hour integer contracts must be positive")
        if self.minimum_minute_history > self.minute_atr_bars:
            raise ValueError("minimum minute history cannot exceed activity lookback")
        if self.minimum_boundary_lag_observations > 4:
            raise ValueError("boundary lag observations cannot exceed four")
        positive = (
            self.burst_imbalance,
            self.burst_volume_ratio,
            self.burst_trade_ratio,
            self.burst_body_atr,
            self.burst_close_location,
            self.retrace_body_fraction,
            self.origin_hold_atr,
            self.excessive_retrace_atr,
            self.maximum_retrace_volume_fraction,
            self.maximum_retrace_trade_fraction,
            self.maximum_retrace_imbalance_fraction,
            self.retrace_imbalance_allowance,
            self.reacceleration_break_atr,
            self.minimum_reacceleration_body_atr,
            self.minimum_reacceleration_imbalance,
            self.reacceleration_close_location,
            self.minimum_reacceleration_volume_multiple,
            self.stop_buffer_atr,
            self.minimum_stop_atr,
        )
        if any(value <= 0.0 for value in positive):
            raise ValueError("quarter-hour ratio contracts must be positive")
        if not 0.0 < self.retrace_body_fraction < 1.0:
            raise ValueError("retrace body fraction must be in (0, 1)")
        if not 0.5 < self.burst_close_location < 1.0:
            raise ValueError("burst close location must be in (0.5, 1)")
        if not 0.5 < self.reacceleration_close_location < 1.0:
            raise ValueError("reacceleration close location must be in (0.5, 1)")
        fractions = (
            self.maximum_retrace_volume_fraction,
            self.maximum_retrace_trade_fraction,
            self.maximum_retrace_imbalance_fraction,
        )
        if any(value > 1.0 for value in fractions):
            raise ValueError("retrace contraction fractions cannot exceed one")


@dataclass(frozen=True, slots=True)
class IntradayLiquidityLevel:
    level_id: str
    kind: str
    level: float
    pivot_time_ns: int
    observed_time_ns: int

    @property
    def source(self) -> str:
        return f"CONFIRMED_15M_SWING_{self.kind}"


def _enrich_minute(minute: pd.DataFrame, config: QuarterHourSessionLiquidityConfig) -> pd.DataFrame:
    result = minute.copy()
    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr20"] = true_range.rolling(20, min_periods=20).mean()
    result["volume_baseline60"] = result["volume"].shift(1).rolling(
        config.minute_atr_bars,
        min_periods=config.minimum_minute_history,
    ).median()
    result["trade_baseline60"] = result["trade_count"].shift(1).rolling(
        config.minute_atr_bars,
        min_periods=config.minimum_minute_history,
    ).median()
    result["volume_ratio_qh"] = result["volume"] / result["volume_baseline60"].replace(0.0, np.nan)
    result["trade_ratio_qh"] = result["trade_count"] / result["trade_baseline60"].replace(0.0, np.nan)
    result["imbalance_qh"] = result["signed_volume"] / result["volume"].replace(0.0, np.nan)
    result["body_atr_qh"] = (result["close"] - result["open"]).abs() / result["atr20"].replace(0.0, np.nan)
    spread = result["high"] - result["low"]
    result["close_location_qh"] = (result["close"] - result["low"]) / spread.replace(0.0, np.nan)

    completed_open = result.index - pd.Timedelta(nanoseconds=1)
    session_key = completed_open.floor("4h")
    session_table = result.groupby(session_key, sort=True).agg(
        session_open=("open", "first"),
        session_close=("close", "last"),
    )
    session_table["previous_session_direction"] = np.sign(
        session_table["session_close"].shift(1) - session_table["session_open"].shift(1)
    )
    result["previous_session_direction"] = session_key.map(
        session_table["previous_session_direction"]
    )

    result["quarter_hour_boundary"] = completed_open.minute.isin((0, 15, 30, 45))
    result["boundary_lag_mean4"] = np.nan
    history: deque[float] = deque(maxlen=4)
    lag_column = result.columns.get_loc("boundary_lag_mean4")
    for position in range(len(result.index)):
        if not bool(result.iloc[position]["quarter_hour_boundary"]):
            continue
        if len(history) >= config.minimum_boundary_lag_observations:
            result.iat[position, lag_column] = float(np.mean(tuple(history)))
        imbalance = float(result.iloc[position]["imbalance_qh"])
        if isfinite(imbalance):
            history.append(imbalance)
    return result


def _aggregate_fifteen_minutes(minute: pd.DataFrame) -> pd.DataFrame:
    completed_open = minute.index - pd.Timedelta(nanoseconds=1)
    bucket = completed_open.floor("15min")
    source = minute.copy()
    source["bucket"] = bucket
    fifteen = source.groupby("bucket", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        source_rows=("close", "size"),
    )
    fifteen = fifteen.loc[fifteen["source_rows"] == 15].copy()
    fifteen.index = pd.DatetimeIndex(fifteen.index) + pd.Timedelta(minutes=15)
    return fifteen


def _confirmed_intraday_levels(
    minute: pd.DataFrame,
    *,
    span: int,
) -> tuple[IntradayLiquidityLevel, ...]:
    fifteen = _aggregate_fifteen_minutes(minute)
    highs = fifteen["high"].to_numpy(dtype=float)
    lows = fifteen["low"].to_numpy(dtype=float)
    times = fifteen.index.as_unit("ns").asi8
    levels: list[IntradayLiquidityLevel] = []
    for observed_position in range(len(fifteen.index)):
        candidate = observed_position - span
        if candidate < span:
            continue
        left_high = highs[candidate - span : candidate]
        right_high = highs[candidate + 1 : observed_position + 1]
        left_low = lows[candidate - span : candidate]
        right_low = lows[candidate + 1 : observed_position + 1]
        if len(right_high) != span:
            continue
        pivot_time_ns = int(times[candidate])
        observed_time_ns = int(times[observed_position])
        if highs[candidate] > float(np.max(left_high)) and highs[candidate] > float(np.max(right_high)):
            levels.append(
                IntradayLiquidityLevel(
                    level_id=f"15m-high-{pivot_time_ns}-{highs[candidate]:.12g}",
                    kind="HIGH",
                    level=float(highs[candidate]),
                    pivot_time_ns=pivot_time_ns,
                    observed_time_ns=observed_time_ns,
                )
            )
        if lows[candidate] < float(np.min(left_low)) and lows[candidate] < float(np.min(right_low)):
            levels.append(
                IntradayLiquidityLevel(
                    level_id=f"15m-low-{pivot_time_ns}-{lows[candidate]:.12g}",
                    kind="LOW",
                    level=float(lows[candidate]),
                    pivot_time_ns=pivot_time_ns,
                    observed_time_ns=observed_time_ns,
                )
            )
    return tuple(sorted(levels, key=lambda item: (item.observed_time_ns, item.level_id)))


def _level_consumed_before_entry(
    minute: pd.DataFrame,
    level: IntradayLiquidityLevel,
    *,
    entry_time: pd.Timestamp,
) -> bool:
    observed = pd.Timestamp(level.observed_time_ns, unit="ns", tz="UTC")
    path = minute.loc[(minute.index > observed) & (minute.index <= entry_time)]
    if path.empty:
        return False
    if level.kind == "HIGH":
        return float(path["high"].max()) >= level.level
    return float(path["low"].min()) <= level.level


def _select_target(
    minute: pd.DataFrame,
    levels: tuple[IntradayLiquidityLevel, ...],
    *,
    direction: int,
    expected_entry: float,
    burst_time_ns: int,
    entry_time: pd.Timestamp,
    maximum_age_minutes: int,
) -> IntradayLiquidityLevel | None:
    maximum_age_ns = maximum_age_minutes * 60 * 1_000_000_000
    candidates: list[IntradayLiquidityLevel] = []
    for level in levels:
        if level.observed_time_ns >= burst_time_ns:
            continue
        if burst_time_ns - level.observed_time_ns > maximum_age_ns:
            continue
        if direction > 0 and not (level.kind == "HIGH" and level.level > expected_entry):
            continue
        if direction < 0 and not (level.kind == "LOW" and level.level < expected_entry):
            continue
        if _level_consumed_before_entry(minute, level, entry_time=entry_time):
            continue
        candidates.append(level)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (abs(item.level - expected_entry), -item.observed_time_ns),
    )


def _burst_direction(row: pd.Series, config: QuarterHourSessionLiquidityConfig) -> int:
    if not bool(row["quarter_hour_boundary"]):
        return 0
    values = (
        float(row["atr20"]),
        float(row["imbalance_qh"]),
        float(row["volume_ratio_qh"]),
        float(row["trade_ratio_qh"]),
        float(row["body_atr_qh"]),
        float(row["close_location_qh"]),
        float(row["boundary_lag_mean4"]),
        float(row["previous_session_direction"]),
    )
    if not all(isfinite(value) for value in values) or values[0] <= 0.0:
        return 0
    imbalance = values[1]
    direction = 1 if imbalance > 0.0 else -1
    directional_body = direction * float(row["close"] - row["open"])
    located = (
        float(row["close_location_qh"]) >= config.burst_close_location
        if direction > 0
        else float(row["close_location_qh"]) <= 1.0 - config.burst_close_location
    )
    if not (
        abs(imbalance) >= config.burst_imbalance
        and float(row["volume_ratio_qh"]) >= config.burst_volume_ratio
        and float(row["trade_ratio_qh"]) >= config.burst_trade_ratio
        and float(row["body_atr_qh"]) >= config.burst_body_atr
        and directional_body > 0.0
        and located
    ):
        return 0
    if direction * float(row["boundary_lag_mean4"]) <= 0.0:
        return 0
    if int(np.sign(float(row["previous_session_direction"]))) != direction:
        return 0
    return direction


def _contracted_retrace(
    minute: pd.DataFrame,
    burst_position: int,
    direction: int,
    config: QuarterHourSessionLiquidityConfig,
) -> int | None:
    burst = minute.iloc[burst_position]
    atr = float(burst["atr20"])
    body = abs(float(burst["close"] - burst["open"]))
    if not isfinite(atr) or atr <= 0.0 or body <= 0.0:
        return None
    for position in range(
        burst_position + 1,
        min(len(minute.index), burst_position + 1 + config.maximum_retrace_minutes),
    ):
        row = minute.iloc[position]
        if direction > 0:
            reached_value = float(row["low"]) <= float(burst["close"]) - config.retrace_body_fraction * body
            origin_held = float(row["close"]) >= float(burst["open"]) - config.origin_hold_atr * atr
            excessive = float(row["low"]) < float(burst["open"]) - config.excessive_retrace_atr * atr
        else:
            reached_value = float(row["high"]) >= float(burst["close"]) + config.retrace_body_fraction * body
            origin_held = float(row["close"]) <= float(burst["open"]) + config.origin_hold_atr * atr
            excessive = float(row["high"]) > float(burst["open"]) + config.excessive_retrace_atr * atr
        if excessive:
            return None
        contracted = (
            float(row["volume"]) <= config.maximum_retrace_volume_fraction * float(burst["volume"])
            and float(row["trade_count"])
            <= config.maximum_retrace_trade_fraction * float(burst["trade_count"])
            and abs(float(row["imbalance_qh"]))
            <= config.maximum_retrace_imbalance_fraction * abs(float(burst["imbalance_qh"]))
            + config.retrace_imbalance_allowance
        )
        if reached_value and origin_held and contracted:
            return position
    return None


def _reacceleration(
    minute: pd.DataFrame,
    retrace_position: int,
    direction: int,
    config: QuarterHourSessionLiquidityConfig,
) -> int | None:
    retrace = minute.iloc[retrace_position]
    atr = float(retrace["atr20"])
    if not isfinite(atr) or atr <= 0.0:
        return None
    for position in range(
        retrace_position + 1,
        min(len(minute.index), retrace_position + 1 + config.maximum_reacceleration_minutes),
    ):
        row = minute.iloc[position]
        if direction > 0:
            displaced = float(row["close"]) >= float(retrace["high"]) + config.reacceleration_break_atr * atr
            located = float(row["close_location_qh"]) >= config.reacceleration_close_location
        else:
            displaced = float(row["close"]) <= float(retrace["low"]) - config.reacceleration_break_atr * atr
            located = float(row["close_location_qh"]) <= 1.0 - config.reacceleration_close_location
        if (
            displaced
            and located
            and direction * float(row["close"] - row["open"])
            >= config.minimum_reacceleration_body_atr * atr
            and direction * float(row["imbalance_qh"])
            >= config.minimum_reacceleration_imbalance
            and float(row["volume"])
            >= config.minimum_reacceleration_volume_multiple * float(retrace["volume"])
            and float(row["trade_count"]) >= float(retrace["trade_count"])
        ):
            return position
    return None


def _structural_stop(
    *,
    direction: int,
    entry: float,
    retrace_high: float,
    retrace_low: float,
    atr: float,
    config: QuarterHourSessionLiquidityConfig,
) -> tuple[float, float, str]:
    minimum_distance = config.minimum_stop_atr * atr
    buffer = config.stop_buffer_atr * atr
    if direction > 0:
        reference = retrace_low
        return min(reference - buffer, entry - minimum_distance), reference, "CONTRACTED_RETRACE_LOW"
    reference = retrace_high
    return max(reference + buffer, entry + minimum_distance), reference, "CONTRACTED_RETRACE_HIGH"


def build_qh_session_liquidity_signals(
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
    config: QuarterHourSessionLiquidityConfig,
) -> QuoteResiliencySignalBundle:
    """Build immutable future-free quarter-hour continuation signals."""

    del context_times, context_bars, snapshots
    config.validate()
    if tick <= 0.0 or fee_rate < 0.0 or minimum_net_reward_risk <= 0.0:
        raise ValueError("invalid execution cost contract")
    minute = _enrich_minute(aggregate_completed_minutes(data, config), config)
    levels = _confirmed_intraday_levels(minute, span=config.target_swing_span)
    stop_reserves = causal_stop_slippage_reserve_series(data, tick=tick)
    diagnostics: Counter[str] = Counter()
    rejected: list[dict[str, Any]] = []
    signals: dict[int, list[QuoteResiliencySignal]] = {}

    required = (
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
    for burst_position in range(len(minute.index)):
        burst = minute.iloc[burst_position]
        if not all(isfinite(float(burst[name])) for name in required):
            continue
        direction = _burst_direction(burst, config)
        if direction == 0:
            continue
        diagnostics["QH_INTENSE_SESSION_ALIGNED_BURST"] += 1
        burst_time = minute.index[burst_position]
        burst_time_ns = int(burst_time.as_unit("ns").value)
        scenario_id = f"qh-session-liquidity-{symbol.lower()}-{burst_time_ns}"
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
                }
            )
            continue
        diagnostics["CONTRACTED_RETRACE"] += 1
        reacceleration_position = _reacceleration(
            minute,
            retrace_position,
            direction,
            config,
        )
        if reacceleration_position is None:
            diagnostics["NO_SEPARATE_REACCELERATION"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "reason": "NO_SEPARATE_REACCELERATION",
                    "burst_time_ns": burst_time_ns,
                    "retrace_time_ns": int(minute.index[retrace_position].as_unit("ns").value),
                    "direction": "LONG" if direction > 0 else "SHORT",
                }
            )
            continue
        diagnostics["SEPARATE_REACCELERATION"] += 1
        entry_time = minute.index[reacceleration_position]
        if entry_time not in data.index:
            diagnostics["NO_EXACT_TEN_SECOND_COMPLETION_ROW"] += 1
            continue
        quote_row = data.loc[entry_time]
        if not bool(quote_row.get("native_quote_snapshot_observable", False)):
            diagnostics["NO_NATIVE_L1_AT_REACCELERATION"] += 1
            continue
        quote_reference = executable_quote_reference(quote_row, direction)
        expected_entry = quote_reference + direction * tick
        target = _select_target(
            minute,
            levels,
            direction=direction,
            expected_entry=expected_entry,
            burst_time_ns=burst_time_ns,
            entry_time=entry_time,
            maximum_age_minutes=config.target_max_age_minutes,
        )
        if target is None:
            diagnostics["NO_PREBURST_UNCONSUMED_15M_LIQUIDITY_TARGET"] += 1
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "symbol": symbol,
                    "reason": "NO_PREBURST_UNCONSUMED_15M_LIQUIDITY_TARGET",
                    "burst_time_ns": burst_time_ns,
                    "entry_time_ns": int(entry_time.as_unit("ns").value),
                    "direction": "LONG" if direction > 0 else "SHORT",
                }
            )
            continue
        retrace = minute.iloc[retrace_position]
        entry_bar = minute.iloc[reacceleration_position]
        atr = float(entry_bar["atr20"])
        stop, stop_reference, stop_source = _structural_stop(
            direction=direction,
            entry=expected_entry,
            retrace_high=float(retrace["high"]),
            retrace_low=float(retrace["low"]),
            atr=atr,
            config=config,
        )
        reserve = float(stop_reserves.loc[entry_time])
        geometry = _cost_geometry(
            direction=direction,
            quote_reference=quote_reference,
            stop=stop,
            target=target.level,
            fee_rate=fee_rate,
            tick=tick,
            stop_slippage_reserve=reserve,
        )
        if geometry is None:
            diagnostics["INVALID_COST_AFTER_INTRADAY_TARGET_GEOMETRY"] += 1
            continue
        loss, gain, net_reward_risk = geometry
        if net_reward_risk < minimum_net_reward_risk:
            diagnostics["INSUFFICIENT_COST_AFTER_INTRADAY_TARGET"] += 1
            continue

        retrace_time_ns = int(minute.index[retrace_position].as_unit("ns").value)
        entry_time_ns = int(entry_time.as_unit("ns").value)
        events = (
            QuoteResiliencyLogicEvent(
                scenario_id=scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="QH_SESSION_ALIGNED_BURST_ACCEPTED",
                event_time_ns=burst_time_ns,
                observed_time_ns=burst_time_ns,
                previous_state="IDLE",
                next_state="DIRECTIONAL_AUCTION_BURST",
                reason_code="QUARTER_HOUR_INTENSE_FLOW_ALIGNED_WITH_RECENT_BOUNDARIES_AND_PREVIOUS_4H_AUCTION",
                reference_price=float(burst["close"]),
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "burst_imbalance": float(burst["imbalance_qh"]),
                    "burst_volume_ratio": float(burst["volume_ratio_qh"]),
                    "burst_trade_ratio": float(burst["trade_ratio_qh"]),
                    "burst_body_atr": float(burst["body_atr_qh"]),
                    "boundary_lag_mean4": float(burst["boundary_lag_mean4"]),
                    "previous_session_direction": float(burst["previous_session_direction"]),
                },
            ),
            QuoteResiliencyLogicEvent(
                scenario_id=scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="QH_CONTRACTED_RETRACE_HELD",
                event_time_ns=retrace_time_ns,
                observed_time_ns=retrace_time_ns,
                previous_state="DIRECTIONAL_AUCTION_BURST",
                next_state="CONTRACTED_RETRACE",
                reason_code="BURST_VALUE_REVISITED_WITH_LOWER_ACTIVITY_WHILE_ORIGIN_HELD",
                reference_price=float(retrace["close"]),
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "retrace_volume_fraction": float(retrace["volume"] / burst["volume"]),
                    "retrace_trade_fraction": float(retrace["trade_count"] / burst["trade_count"]),
                    "retrace_imbalance": float(retrace["imbalance_qh"]),
                    "retrace_high": float(retrace["high"]),
                    "retrace_low": float(retrace["low"]),
                },
            ),
            QuoteResiliencyLogicEvent(
                scenario_id=scenario_id,
                symbol=symbol,
                instrument_id=instrument_id,
                event_type="QH_REACCELERATION_TO_INTRADAY_LIQUIDITY_CONFIRMED",
                event_time_ns=entry_time_ns,
                observed_time_ns=entry_time_ns,
                previous_state="CONTRACTED_RETRACE",
                next_state="CONFIRMED",
                reason_code="SEPARATE_DIRECTIONAL_REACCELERATION_TOWARD_PREBURST_UNCONSUMED_15M_SWING",
                reference_price=quote_reference,
                details={
                    "scenario_family": SCENARIO_FAMILY,
                    "signal_revision": SIGNAL_REVISION,
                    "target_id": target.level_id,
                    "target_level": target.level,
                    "target_pivot_time_ns": target.pivot_time_ns,
                    "target_observed_time_ns": target.observed_time_ns,
                    "entry_imbalance": float(entry_bar["imbalance_qh"]),
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
            signal_index=int(data.index.get_loc(entry_time)),
            signal_time_ns=entry_time_ns,
            boundary_id=f"quarter-hour-{burst_time_ns}",
            boundary_source="QUARTER_HOUR_ALGORITHMIC_BOUNDARY",
            boundary_level=float(burst["open"]),
            target_id=target.level_id,
            target_source=target.source,
            external_target=target.level,
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
                "entry_time_ns": entry_time_ns,
                "target_confirmed_before_burst": target.observed_time_ns < burst_time_ns,
                "target_age_minutes_at_burst": (burst_time_ns - target.observed_time_ns) / 60_000_000_000,
                "target_contract": "NEAREST_PREBURST_UNCONSUMED_CAUSALLY_CONFIRMED_15M_SWING",
                "entry_mode": "NATIVE_L1_MARKET_AFTER_SEPARATE_COMPLETED_MINUTE_REACCELERATION",
                "invalidation_contract": "CONTRACTED_RETRACE_EXTREME_PLUS_CAUSAL_ATR_BUFFER",
            },
        )
        signals.setdefault(entry_time_ns, []).append(signal)
        diagnostics["QH_INTRADAY_LIQUIDITY_SIGNAL"] += 1
        diagnostics[f"SIGNAL_{signal.direction_name}"] += 1
        diagnostics[f"TARGET_{target.source}"] += 1

    immutable = {
        timestamp_ns: tuple(
            sorted(items, key=lambda signal: signal.net_reward_risk, reverse=True)
        )
        for timestamp_ns, items in sorted(signals.items())
    }
    diagnostics["SIGNALS"] = sum(len(items) for items in immutable.values())
    diagnostics["SIGNAL_TIMES"] = len(immutable)
    diagnostics["COMPLETED_MINUTES"] = len(minute.index)
    diagnostics["CONFIRMED_15M_LIQUIDITY_LEVELS"] = len(levels)
    return QuoteResiliencySignalBundle(
        signals_by_time_ns=immutable,
        diagnostics=dict(sorted((key, int(value)) for key, value in diagnostics.items())),
        rejected_scenarios=tuple(rejected),
    )


__all__ = [
    "QuarterHourSessionLiquidityConfig",
    "SCENARIO_FAMILY",
    "SIGNAL_REVISION",
    "build_qh_session_liquidity_signals",
]
