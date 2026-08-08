"""Causal completed-session liquidity sweep reversal for candidate-02 v115.

Each scenario uses one objectively completed dealing range as its liquidity
source:

* ASIA: current UTC day's 00:00-06:00 range, tradable after 06:00.
* LONDON: current UTC day's 06:00-12:00 range, tradable after 12:00.
* PREVIOUS_DAY: the immediately preceding completed UTC day.

A boundary is consumed on its first valid sweep.  A trade is emitted only after
that sweep rejects, opposite displacement breaks pre-sweep internal structure,
a causal three-candle FVG forms, and price retraces into and rejects the FVG.
The invalidation is beyond the sweep extreme and the objective is the opposite
boundary of the same pre-observed dealing range.

Signal construction only.  NautilusTrader remains the sole execution,
commission, position, risk and NAV engine.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, _true_range, cost_after_reward_risk
from v111_liquidity_sweep_core import (
    LiquiditySweepConfig,
    _latest_feature,
    _normalize,
    _session_label,
    build_state,
)
from v113_persistent_pool_router_core import _directional_fvg

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class SessionRangeSweepConfig(LiquiditySweepConfig):
    liquidity_source: str = "ASIA"
    asia_end_hour_utc: int = 6
    london_end_hour_utc: int = 12
    active_day_end_hour_utc: int = 20
    minimum_range_rows_fraction: float = 0.98

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "SessionRangeSweepConfig":
        data = dict(values)
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v115 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        LiquiditySweepConfig.__post_init__(self)
        if self.liquidity_source not in {"ASIA", "LONDON", "PREVIOUS_DAY"}:
            raise ValueError(f"unknown v115 liquidity source: {self.liquidity_source}")
        if not 0 < self.asia_end_hour_utc < self.london_end_hour_utc < self.active_day_end_hour_utc <= 24:
            raise ValueError("v115 session hours are not strictly ordered")
        if not 0 < self.minimum_range_rows_fraction <= 1:
            raise ValueError("v115 minimum range completeness must be in (0,1]")


@dataclass(slots=True)
class _CompletedRange:
    range_id: str
    source: str
    trading_day: pd.Timestamp
    source_start: pd.Timestamp
    source_end: pd.Timestamp
    available_time: pd.Timestamp
    valid_end: pd.Timestamp
    high: float
    low: float
    rows: int
    high_consumed: bool = False
    low_consumed: bool = False

    @property
    def width(self) -> float:
        return self.high - self.low


_LAST_DIAGNOSTICS: dict[str, Any] = {"summary": {}, "examples": {}}


def get_last_scenario_diagnostics() -> dict[str, Any]:
    return {
        "summary": dict(_LAST_DIAGNOSTICS.get("summary", {})),
        "examples": {
            str(key): list(values)
            for key, values in dict(_LAST_DIAGNOSTICS.get("examples", {})).items()
        },
    }


def _build_completed_ranges(
    raw: pd.DataFrame,
    *,
    config: SessionRangeSweepConfig,
) -> dict[pd.Timestamp, _CompletedRange]:
    first_day = raw.index.min().normalize()
    last_day = raw.index.max().normalize()
    result: dict[pd.Timestamp, _CompletedRange] = {}
    for day in pd.date_range(first_day, last_day, freq="D", tz=UTC):
        day = pd.Timestamp(day)
        if config.liquidity_source == "ASIA":
            source_start = day
            source_end = day + pd.Timedelta(hours=config.asia_end_hour_utc)
            available = source_end
            valid_end = day + pd.Timedelta(hours=config.active_day_end_hour_utc)
            expected_rows = config.asia_end_hour_utc * 60
        elif config.liquidity_source == "LONDON":
            source_start = day + pd.Timedelta(hours=config.asia_end_hour_utc)
            source_end = day + pd.Timedelta(hours=config.london_end_hour_utc)
            available = source_end
            valid_end = day + pd.Timedelta(hours=config.active_day_end_hour_utc)
            expected_rows = (config.london_end_hour_utc - config.asia_end_hour_utc) * 60
        else:
            source_start = day - pd.Timedelta(days=1)
            source_end = day
            available = day
            valid_end = day + pd.Timedelta(hours=config.active_day_end_hour_utc)
            expected_rows = 24 * 60

        bars = raw.loc[(raw.index > source_start) & (raw.index <= source_end)]
        if len(bars) < math.ceil(expected_rows * config.minimum_range_rows_fraction):
            continue
        high = float(bars["high"].max())
        low = float(bars["low"].min())
        if not (math.isfinite(high) and math.isfinite(low) and high > low):
            continue
        result[day] = _CompletedRange(
            range_id=f"{config.liquidity_source}-{day.strftime('%Y%m%d')}",
            source=config.liquidity_source,
            trading_day=day,
            source_start=source_start,
            source_end=source_end,
            available_time=available,
            valid_end=valid_end,
            high=high,
            low=low,
            rows=len(bars),
        )
    return result


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: SessionRangeSweepConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    global _LAST_DIAGNOSTICS

    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError("v115 raw bars must be unique and increasing")

    ranges = _build_completed_ranges(raw, config=config)
    tr = _true_range(raw)
    atr = tr.shift(1).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    body = (raw["close"] - raw["open"]).abs()
    bar_range = raw["high"] - raw["low"]
    body_threshold = body.shift(1).rolling(
        config.displacement_history_minutes,
        min_periods=config.displacement_min_history_minutes,
    ).quantile(config.displacement_body_quantile)
    range_threshold = bar_range.shift(1).rolling(
        config.displacement_history_minutes,
        min_periods=config.displacement_min_history_minutes,
    ).quantile(config.displacement_range_quantile)

    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    signals: list[RotationSignal] = []
    end_position = int(raw.index.searchsorted(end, side="left"))

    def record(
        stage: str,
        *,
        side: str | None = None,
        session: str | None = None,
        event_time: pd.Timestamp | None = None,
        reason: str,
        **values: Any,
    ) -> None:
        counts[stage] += 1
        if side is not None:
            counts[f"{stage}:{side}"] += 1
        if session is not None:
            counts[f"{stage}:{session}"] += 1
        if len(examples[stage]) < 7:
            examples[stage].append(
                {
                    "side": side,
                    "session": session,
                    "event_time_utc": event_time.isoformat() if event_time is not None else None,
                    "reason": reason,
                    **values,
                }
            )

    for completed_range in ranges.values():
        record(
            "DEALING_RANGE_COMPLETED",
            event_time=completed_range.available_time,
            reason="source session/day fully closed and both external liquidity boundaries became immutable",
            range_id=completed_range.range_id,
            source=completed_range.source,
            high=completed_range.high,
            low=completed_range.low,
            rows=completed_range.rows,
        )

    for index in range(1, len(raw)):
        timestamp = raw.index[index]
        if not start <= timestamp < end:
            continue
        trading_day = timestamp.normalize()
        completed_range = ranges.get(trading_day)
        if completed_range is None:
            continue
        if not completed_range.available_time < timestamp < completed_range.valid_end:
            continue

        atr_value = float(atr.iloc[index])
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        current = raw.iloc[index]
        previous_close = float(raw.iloc[index - 1]["close"])
        total_range = max(float(current["high"] - current["low"]), 1e-12)
        upper_wick = float(current["high"] - max(current["open"], current["close"])) / total_range
        lower_wick = float(min(current["open"], current["close"]) - current["low"]) / total_range
        high_excursion = (float(current["high"]) - completed_range.high) / atr_value
        low_excursion = (completed_range.low - float(current["low"])) / atr_value

        high_sweep = (
            not completed_range.high_consumed
            and previous_close <= completed_range.high + config.reacceptance_buffer_atr * atr_value
            and config.sweep_min_atr <= high_excursion <= config.sweep_max_atr
            and float(current["close"]) < completed_range.high
            and upper_wick >= config.minimum_rejection_wick_fraction
        )
        low_sweep = (
            not completed_range.low_consumed
            and previous_close >= completed_range.low - config.reacceptance_buffer_atr * atr_value
            and config.sweep_min_atr <= low_excursion <= config.sweep_max_atr
            and float(current["close"]) > completed_range.low
            and lower_wick >= config.minimum_rejection_wick_fraction
        )
        # Liquidity is consumed even when the completed sweep bar does not
        # qualify as a controlled rejection scenario.
        high_crossed = (
            not completed_range.high_consumed
            and previous_close <= completed_range.high + config.reacceptance_buffer_atr * atr_value
            and float(current["high"]) >= completed_range.high + config.sweep_min_atr * atr_value
        )
        low_crossed = (
            not completed_range.low_consumed
            and previous_close >= completed_range.low - config.reacceptance_buffer_atr * atr_value
            and float(current["low"]) <= completed_range.low - config.sweep_min_atr * atr_value
        )
        if not high_crossed and not low_crossed:
            continue
        if high_crossed:
            completed_range.high_consumed = True
        if low_crossed:
            completed_range.low_consumed = True
        session = _session_label(timestamp)
        if high_crossed and low_crossed:
            record(
                "AMBIGUOUS_TWO_SIDED_RANGE_SWEEP",
                session=session,
                event_time=timestamp,
                reason="one completed minute consumed both range boundaries; intrabar ordering is unknowable",
                range_id=completed_range.range_id,
            )
            continue
        if high_crossed and not high_sweep:
            record(
                "HIGH_LIQUIDITY_CONSUMED_WITHOUT_REJECTION",
                session=session,
                event_time=timestamp,
                reason="high boundary was consumed but the bar did not close back inside with controlled rejection",
                range_id=completed_range.range_id,
                excursion_atr=high_excursion,
            )
            continue
        if low_crossed and not low_sweep:
            record(
                "LOW_LIQUIDITY_CONSUMED_WITHOUT_REJECTION",
                session=session,
                event_time=timestamp,
                reason="low boundary was consumed but the bar did not close back inside with controlled rejection",
                range_id=completed_range.range_id,
                excursion_atr=low_excursion,
            )
            continue

        side = "SELL" if high_sweep else "BUY"
        swept_level = completed_range.high if high_sweep else completed_range.low
        sweep_extreme = float(current["high"] if high_sweep else current["low"])
        target = completed_range.low if high_sweep else completed_range.high
        sweep_excursion = high_excursion if high_sweep else low_excursion
        record(
            "SESSION_LIQUIDITY_SWEPT_AND_REJECTED",
            side=side,
            session=session,
            event_time=timestamp,
            reason="completed dealing-range boundary was swept and the bar closed back inside",
            range_id=completed_range.range_id,
            source=completed_range.source,
            swept_level=swept_level,
            sweep_extreme=sweep_extreme,
            opposite_range_liquidity_target=target,
            range_width=completed_range.width,
            sweep_excursion_atr=sweep_excursion,
        )

        internal = raw.iloc[
            max(0, index - config.internal_structure_minutes) : index
        ]
        if len(internal) < config.internal_structure_minutes:
            continue
        internal_low = float(internal["low"].min())
        internal_high = float(internal["high"].max())
        fvg: dict[str, Any] | None = None
        invalidated_before_transfer = False
        scan_end = min(
            len(raw) - 1,
            end_position - 1,
            index + config.displacement_window_minutes,
        )
        for third_index in range(index + 2, scan_end + 1):
            decision = raw.iloc[index + 1 : third_index + 1]
            if side == "SELL":
                if (decision["close"] > swept_level + config.reacceptance_buffer_atr * atr_value).any():
                    invalidated_before_transfer = True
                    break
            else:
                if (decision["close"] < swept_level - config.reacceptance_buffer_atr * atr_value).any():
                    invalidated_before_transfer = True
                    break
            candidate = _directional_fvg(
                raw=raw,
                third_index=third_index,
                side=side,
                body=body,
                bar_range=bar_range,
                body_threshold=body_threshold,
                range_threshold=range_threshold,
                atr_value=atr_value,
                minimum_gap_atr=config.fvg_min_atr,
            )
            if candidate is None:
                continue
            third_close = float(raw.iloc[third_index]["close"])
            choch = third_close < internal_low if side == "SELL" else third_close > internal_high
            held_inside = third_close < swept_level if side == "SELL" else third_close > swept_level
            if choch and held_inside:
                fvg = candidate
                break

        if fvg is None:
            record(
                "INVALIDATED_BEFORE_STRUCTURE_TRANSFER" if invalidated_before_transfer else "NO_DISPLACEMENT_CHOCH_FVG",
                side=side,
                session=session,
                event_time=timestamp,
                reason=(
                    "price re-accepted beyond swept session liquidity before reversal transfer"
                    if invalidated_before_transfer
                    else "no displacement bar broke internal structure while leaving a causal FVG"
                ),
                range_id=completed_range.range_id,
            )
            continue

        record(
            "DISPLACEMENT_CHOCH_FVG_CONFIRMED",
            side=side,
            session=session,
            event_time=timestamp,
            reason="post-sweep displacement broke internal structure and left a three-candle imbalance",
            range_id=completed_range.range_id,
            displacement_time_utc=fvg["displacement_time"].isoformat(),
            fvg_time_utc=fvg["fvg_time"].isoformat(),
            fvg_size_atr=float(fvg["gap_size"]) / atr_value,
        )

        retrace_level = float(
            fvg["gap_lower"]
            + config.fvg_retrace_fraction * (fvg["gap_upper"] - fvg["gap_lower"])
        )
        retest_index: int | None = None
        invalidation_reason: str | None = None
        retest_start = int(fvg["third_index"]) + 1
        retest_end = min(
            len(raw) - 2,
            end_position - 2,
            retest_start + config.retrace_window_minutes - 1,
        )
        for candidate_index in range(retest_start, retest_end + 1):
            candidate = raw.iloc[candidate_index]
            if side == "SELL":
                if float(candidate["close"]) > swept_level + config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "price re-accepted above swept session high"
                    break
                if float(candidate["high"]) > sweep_extreme + config.stop_buffer_atr * atr_value:
                    invalidation_reason = "sweep extreme failed before entry"
                    break
                touched = float(candidate["high"]) >= retrace_level
                rejected = float(candidate["close"]) < retrace_level and float(candidate["close"]) < float(candidate["open"])
            else:
                if float(candidate["close"]) < swept_level - config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "price re-accepted below swept session low"
                    break
                if float(candidate["low"]) < sweep_extreme - config.stop_buffer_atr * atr_value:
                    invalidation_reason = "sweep extreme failed before entry"
                    break
                touched = float(candidate["low"]) <= retrace_level
                rejected = float(candidate["close"]) > retrace_level and float(candidate["close"]) > float(candidate["open"])
            if touched and rejected:
                retest_index = candidate_index
                break

        if retest_index is None:
            record(
                "INVALIDATED_BEFORE_FVG_RETEST" if invalidation_reason else "NO_FVG_RETEST",
                side=side,
                session=session,
                event_time=timestamp,
                reason=invalidation_reason or "FVG was not retraced and rejected during the causal state lifetime",
                range_id=completed_range.range_id,
                retrace_level=retrace_level,
            )
            continue

        activation_index = retest_index + config.activation_delay_minutes
        activation_time = raw.index[activation_index]
        if not start <= activation_time < end:
            continue
        entry = float(raw.iloc[activation_index]["close"])
        stop = (
            sweep_extreme + config.stop_buffer_atr * atr_value
            if side == "SELL"
            else sweep_extreme - config.stop_buffer_atr * atr_value
        )
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            record(
                "ENTRY_GEOMETRY_REJECTED",
                side=side,
                session=session,
                event_time=timestamp,
                reason="activation price no longer lay between sweep invalidation and opposite range liquidity",
                range_id=completed_range.range_id,
                entry=entry,
                stop=stop,
                target=target,
            )
            continue

        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(rr) or not (
            config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
        ):
            record(
                "COST_AFTER_RR_REJECTED",
                side=side,
                session=session,
                event_time=timestamp,
                reason="opposite completed-range liquidity could not pay realistic entry and failure costs",
                range_id=completed_range.range_id,
                cost_after_reward_risk=rr,
                entry=entry,
                stop=stop,
                target=target,
            )
            continue

        retest_time = raw.index[retest_index]
        feature_time, feature_details = _latest_feature(state, retest_time)
        activation_ns = int(activation_time.value)
        details: dict[str, Any] = {
            "scenario": "COMPLETED_SESSION_RANGE_LIQUIDITY_SWEEP_REVERSAL",
            "liquidity_source": completed_range.source,
            "state_sequence": [
                "DEALING_RANGE_COMPLETED",
                "EXTERNAL_BOUNDARIES_FIXED",
                "SESSION_LIQUIDITY_SWEPT_AND_REJECTED",
                "DISPLACEMENT_AND_CHOCH",
                "FVG_FORMED",
                "FVG_RETRACE_REJECTED",
                "ENTRY_ARMED",
            ],
            "range_id": completed_range.range_id,
            "source_start_ns": int(completed_range.source_start.value),
            "source_end_ns": int(completed_range.source_end.value),
            "range_available_time_ns": int(completed_range.available_time.value),
            "range_high": completed_range.high,
            "range_low": completed_range.low,
            "range_width": completed_range.width,
            "sweep_time_ns": int(timestamp.value),
            "swept_liquidity_level": swept_level,
            "sweep_extreme": sweep_extreme,
            "sweep_excursion_atr": sweep_excursion,
            "internal_structure_low": internal_low,
            "internal_structure_high": internal_high,
            "displacement_time_ns": int(fvg["displacement_time"].value),
            "fvg_time_ns": int(fvg["fvg_time"].value),
            "fvg_lower": fvg["gap_lower"],
            "fvg_upper": fvg["gap_upper"],
            "fvg_retrace_level": retrace_level,
            "fvg_retest_time_ns": int(retest_time.value),
            "activation_time_ns": activation_ns,
            "structural_invalidation": stop,
            "opposite_range_liquidity_target": target,
            "session": session,
            "atr_1m_prior_to_sweep": atr_value,
            "latest_available_feature_open_time_ns": (
                int(feature_time.value) if feature_time is not None else None
            ),
            "microstructure_context": feature_details,
        }
        score = (
            rr
            + float(fvg["body_ratio"])
            + float(fvg["range_ratio"])
            + float(fvg["gap_size"]) / atr_value
        )
        signals.append(
            RotationSignal(
                scenario_id=(
                    f"v115-{completed_range.source.lower()}-range-sweep-{side.lower()}-{activation_ns}"
                ),
                observed_time_ns=activation_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=score,
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=(
                    int(feature_time.value)
                    if feature_time is not None
                    else int(timestamp.value)
                ),
                source_feature_available_time_ns=activation_ns,
                source_max_market_time_ns=int(retest_time.value),
                details=details,
            )
        )
        record(
            "ENTRY_ARMED",
            side=side,
            session=session,
            event_time=timestamp,
            reason="completed range, sweep, structure transfer, FVG retest, invalidation and opposite objective aligned",
            range_id=completed_range.range_id,
            activation_time_utc=activation_time.isoformat(),
            cost_after_reward_risk=rr,
        )

    by_activation: dict[int, RotationSignal] = {}
    for signal in signals:
        prior = by_activation.get(signal.observed_time_ns)
        if prior is None or signal.score > prior.score:
            by_activation[signal.observed_time_ns] = signal
    result = sorted(by_activation.values(), key=lambda value: value.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v115 entry used an incomplete activation bar")
        if signal.observed_time_ns - signal.source_max_market_time_ns != 60_000_000_000:
            raise AssertionError("v115 activation delay is not exactly one minute")
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v115 feature availability contract mismatch")
        if int(signal.details["range_available_time_ns"]) >= int(signal.details["sweep_time_ns"]):
            raise AssertionError("v115 range was not completed before its sweep")

    _LAST_DIAGNOSTICS = {
        "summary": {
            "candidate": "candidate-02-v115-completed-session-range-sweep",
            "liquidity_source": config.liquidity_source,
            "detector": "immutable high/low of a fully completed session or prior day",
            "scenario": "sweep rejection -> displacement/CHoCH/FVG -> retest -> opposite boundary",
            "completed_ranges_available": len(ranges),
            "counts": dict(sorted(counts.items())),
            "signals_emitted": len(result),
            "future_information_used": False,
        },
        "examples": dict(examples),
    }
    return result
