"""Causal SMC/ICT liquidity-sweep scenario state machine for candidate-02 v111.

This module deliberately separates market-event detection from the trade
scenario.  It never simulates fills, positions, fees, or NAV.  NautilusTrader
remains the only execution and accounting engine.

Scenario sequence
-----------------
RANGE_DEFINED -> EXTERNAL_LIQUIDITY_SWEPT -> DISPLACEMENT_AND_CHOCH
-> FVG_FORMED -> FVG_RETRACE_REJECTED -> ENTRY_ARMED

Every transition is based only on completed bars.  Entry activation is one
additional completed minute after the retrace rejection.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import (
    NS_MINUTE,
    CostConfig,
    RotationConfig,
    RotationSignal,
    _true_range,
    cost_after_reward_risk,
)

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class LiquiditySweepConfig(RotationConfig):
    dealing_range_minutes: int = 90
    internal_structure_minutes: int = 15
    minimum_liquidity_touches: int = 1
    liquidity_touch_tolerance_atr: float = 0.10
    sweep_min_atr: float = 0.02
    sweep_max_atr: float = 1.25
    minimum_rejection_wick_fraction: float = 0.20
    displacement_window_minutes: int = 15
    displacement_history_minutes: int = 1440
    displacement_min_history_minutes: int = 360
    displacement_body_quantile: float = 0.75
    displacement_range_quantile: float = 0.70
    fvg_min_atr: float = 0.02
    fvg_retrace_fraction: float = 0.50
    retrace_window_minutes: int = 30
    reacceptance_buffer_atr: float = 0.05
    activation_delay_minutes: int = 1
    scenario_cooldown_minutes: int = 20

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "LiquiditySweepConfig":
        data = dict(values)
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v111 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        RotationConfig.__post_init__(self)
        if self.dealing_range_minutes < 30:
            raise ValueError("v111 dealing range must be at least 30 minutes")
        if not 3 <= self.internal_structure_minutes < self.dealing_range_minutes:
            raise ValueError("v111 internal structure horizon is invalid")
        if self.minimum_liquidity_touches <= 0:
            raise ValueError("v111 requires at least one prior liquidity touch")
        if self.liquidity_touch_tolerance_atr < 0:
            raise ValueError("v111 liquidity tolerance cannot be negative")
        if not 0 <= self.sweep_min_atr < self.sweep_max_atr:
            raise ValueError("v111 sweep excursion bounds are invalid")
        if not 0 <= self.minimum_rejection_wick_fraction < 1:
            raise ValueError("v111 wick fraction must be in [0,1)")
        if self.displacement_window_minutes < 3:
            raise ValueError("v111 displacement window is too short")
        if self.displacement_history_minutes <= self.displacement_min_history_minutes:
            raise ValueError("v111 displacement history must exceed minimum history")
        if not 0 < self.displacement_body_quantile < 1:
            raise ValueError("v111 body quantile must be in (0,1)")
        if not 0 < self.displacement_range_quantile < 1:
            raise ValueError("v111 range quantile must be in (0,1)")
        if self.fvg_min_atr < 0 or not 0 < self.fvg_retrace_fraction < 1:
            raise ValueError("v111 FVG settings are invalid")
        if self.retrace_window_minutes <= 0:
            raise ValueError("v111 retrace window must be positive")
        if self.reacceptance_buffer_atr < 0:
            raise ValueError("v111 reacceptance buffer cannot be negative")
        if self.activation_delay_minutes != 1:
            raise ValueError("v111 requires exactly one completed activation minute")
        if self.scenario_cooldown_minutes < 0:
            raise ValueError("v111 cooldown cannot be negative")


_LAST_DIAGNOSTICS: dict[str, Any] = {"summary": {}, "records": []}


def build_state(features: pd.DataFrame, config: LiquiditySweepConfig) -> pd.DataFrame:
    """Keep the feature matrix causal and available for scenario diagnostics.

    The structural state machine is built from completed one-minute bars.  The
    latest fully available five-minute microstructure row is attached only as
    context; no forward columns or future labels participate in transitions.
    """

    del config
    x = features.copy()
    x["feature_available_time"] = x.index + pd.Timedelta(minutes=5)
    return x


def _normalize(value: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(value)
    return value.tz_localize(UTC) if value.tzinfo is None else value.tz_convert(UTC)


def _session_label(timestamp: pd.Timestamp) -> str:
    hour = int(timestamp.hour)
    if hour < 6:
        return "ASIA"
    if hour < 12:
        return "LONDON"
    if hour < 20:
        return "NEW_YORK"
    return "OFF_HOURS"


def _latest_feature(state: pd.DataFrame, known_time: pd.Timestamp) -> tuple[pd.Timestamp | None, Mapping[str, Any]]:
    """Return the latest five-minute row whose close is known at known_time."""

    cutoff = known_time - pd.Timedelta(minutes=5)
    position = int(state.index.searchsorted(cutoff, side="right")) - 1
    if position < 0:
        return None, {}
    timestamp = state.index[position]
    row = state.iloc[position]
    keys = (
        "taker_buy_ratio_5m",
        "depth_imbalance_1pct",
        "vpin_50",
        "hawkes_net",
        "oi_change_1h",
        "log_ret_5m",
        "realized_vol_30m",
    )
    details: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        if value is not None and math.isfinite(float(value)):
            details[key] = float(value)
    return timestamp, details


def _record(
    records: list[dict[str, Any]],
    counts: Counter[str],
    *,
    stage: str,
    side: str,
    session: str,
    sweep_time: pd.Timestamp,
    reason: str,
    **values: Any,
) -> None:
    counts[stage] += 1
    counts[f"{stage}:{side}"] += 1
    counts[f"{stage}:{session}"] += 1
    records.append(
        {
            "stage": stage,
            "side": side,
            "session": session,
            "sweep_time_utc": sweep_time.isoformat(),
            "reason": reason,
            **values,
        }
    )


def get_last_scenario_diagnostics() -> dict[str, Any]:
    return {
        "summary": dict(_LAST_DIAGNOSTICS.get("summary", {})),
        "records": list(_LAST_DIAGNOSTICS.get("records", [])),
    }


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: LiquiditySweepConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    """Resolve completed SMC/ICT state transitions into causal trade intents."""

    global _LAST_DIAGNOSTICS

    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError("v111 raw bars must be unique and increasing")

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
    prior_high = raw["high"].shift(1).rolling(
        config.dealing_range_minutes,
        min_periods=config.dealing_range_minutes,
    ).max()
    prior_low = raw["low"].shift(1).rolling(
        config.dealing_range_minutes,
        min_periods=config.dealing_range_minutes,
    ).min()

    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    selected: dict[int, RotationSignal] = {}
    last_emitted_ns: int | None = None

    evaluation_positions = raw.index.get_indexer_for(
        raw.index[(raw.index >= start) & (raw.index < end)]
    )
    for index in evaluation_positions:
        if index < config.dealing_range_minutes:
            continue
        sweep_time = raw.index[index]
        row = raw.iloc[index]
        atr_value = float(atr.iloc[index])
        range_high = float(prior_high.iloc[index])
        range_low = float(prior_low.iloc[index])
        if not all(math.isfinite(value) for value in (atr_value, range_high, range_low)):
            continue
        if atr_value <= 0 or range_high <= range_low:
            continue

        history = raw.iloc[index - config.dealing_range_minutes : index]
        tolerance = config.liquidity_touch_tolerance_atr * atr_value
        high_touches = int((history["high"] >= range_high - tolerance).sum())
        low_touches = int((history["low"] <= range_low + tolerance).sum())

        total_range = max(float(row["high"] - row["low"]), 1e-12)
        upper_wick_fraction = float(row["high"] - max(row["open"], row["close"])) / total_range
        lower_wick_fraction = float(min(row["open"], row["close"]) - row["low"]) / total_range
        buy_side_excursion = float(row["high"] - range_high) / atr_value
        sell_side_excursion = float(range_low - row["low"]) / atr_value

        bearish_sweep = (
            high_touches >= config.minimum_liquidity_touches
            and config.sweep_min_atr <= buy_side_excursion <= config.sweep_max_atr
            and float(row["close"]) < range_high
            and upper_wick_fraction >= config.minimum_rejection_wick_fraction
        )
        bullish_sweep = (
            low_touches >= config.minimum_liquidity_touches
            and config.sweep_min_atr <= sell_side_excursion <= config.sweep_max_atr
            and float(row["close"]) > range_low
            and lower_wick_fraction >= config.minimum_rejection_wick_fraction
        )
        if bearish_sweep and bullish_sweep:
            counts["AMBIGUOUS_TWO_SIDED_SWEEP"] += 1
            continue
        if not bearish_sweep and not bullish_sweep:
            continue

        side = "SELL" if bearish_sweep else "BUY"
        session = _session_label(sweep_time)
        sweep_level = range_high if bearish_sweep else range_low
        sweep_extreme = float(row["high"] if bearish_sweep else row["low"])
        touch_count = high_touches if bearish_sweep else low_touches
        excursion_atr = buy_side_excursion if bearish_sweep else sell_side_excursion
        internal = raw.iloc[max(0, index - config.internal_structure_minutes) : index]
        if len(internal) < config.internal_structure_minutes:
            continue
        choch_level = float(internal["low"].min() if bearish_sweep else internal["high"].max())

        _record(
            records,
            counts,
            stage="EXTERNAL_LIQUIDITY_SWEPT",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="completed bar traded beyond prior external liquidity and closed back inside",
            dealing_range_high=range_high,
            dealing_range_low=range_low,
            liquidity_touch_count=touch_count,
            sweep_level=sweep_level,
            sweep_extreme=sweep_extreme,
            sweep_excursion_atr=excursion_atr,
            choch_level=choch_level,
        )

        fvg: dict[str, Any] | None = None
        scan_end = min(
            len(raw) - 1,
            index + config.displacement_window_minutes,
            int(raw.index.searchsorted(end, side="left")) - 1,
        )
        invalid_before_fvg = False
        for third_index in range(index + 2, scan_end + 1):
            first_index = third_index - 2
            middle_index = third_index - 1
            decision_slice = raw.iloc[index + 1 : third_index + 1]
            reacceptance = config.reacceptance_buffer_atr * atr_value
            if bearish_sweep:
                if (decision_slice["close"] > sweep_level + reacceptance).any():
                    invalid_before_fvg = True
                    break
            else:
                if (decision_slice["close"] < sweep_level - reacceptance).any():
                    invalid_before_fvg = True
                    break

            middle = raw.iloc[middle_index]
            third = raw.iloc[third_index]
            first = raw.iloc[first_index]
            body_limit = float(body_threshold.iloc[middle_index])
            range_limit = float(range_threshold.iloc[middle_index])
            if not all(math.isfinite(value) for value in (body_limit, range_limit)):
                continue
            displaced = (
                float(body.iloc[middle_index]) >= body_limit
                and float(bar_range.iloc[middle_index]) >= range_limit
            )
            if not displaced:
                continue

            if bearish_sweep:
                directional = float(middle["close"]) < float(middle["open"])
                choch = min(float(middle["close"]), float(third["close"])) < choch_level
                gap_lower = float(third["high"])
                gap_upper = float(first["low"])
            else:
                directional = float(middle["close"]) > float(middle["open"])
                choch = max(float(middle["close"]), float(third["close"])) > choch_level
                gap_lower = float(first["high"])
                gap_upper = float(third["low"])
            gap_size = gap_upper - gap_lower
            fvg_valid = gap_size >= config.fvg_min_atr * atr_value
            if not (directional and choch and fvg_valid):
                continue

            fvg = {
                "first_index": first_index,
                "middle_index": middle_index,
                "third_index": third_index,
                "displacement_time": raw.index[middle_index],
                "fvg_time": raw.index[third_index],
                "gap_lower": gap_lower,
                "gap_upper": gap_upper,
                "gap_size": gap_size,
                "body_ratio": float(body.iloc[middle_index]) / max(body_limit, 1e-12),
                "range_ratio": float(bar_range.iloc[middle_index]) / max(range_limit, 1e-12),
            }
            break

        if fvg is None:
            _record(
                records,
                counts,
                stage="INVALIDATED_BEFORE_FVG" if invalid_before_fvg else "NO_DISPLACEMENT_CHOCH_FVG",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason=(
                    "price re-accepted beyond swept liquidity before structure transfer"
                    if invalid_before_fvg
                    else "no displacement bar simultaneously produced CHoCH and a causal FVG"
                ),
            )
            continue

        _record(
            records,
            counts,
            stage="DISPLACEMENT_CHOCH_FVG_CONFIRMED",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="displacement crossed internal structure and left a three-candle imbalance",
            displacement_time_utc=fvg["displacement_time"].isoformat(),
            fvg_formed_time_utc=fvg["fvg_time"].isoformat(),
            fvg_lower=fvg["gap_lower"],
            fvg_upper=fvg["gap_upper"],
            fvg_size_atr=fvg["gap_size"] / atr_value,
            displacement_body_threshold_ratio=fvg["body_ratio"],
            displacement_range_threshold_ratio=fvg["range_ratio"],
        )

        retrace_level = float(
            fvg["gap_lower"]
            + config.fvg_retrace_fraction * (fvg["gap_upper"] - fvg["gap_lower"])
        )
        retrace_start = int(fvg["third_index"]) + 1
        retrace_end = min(
            len(raw) - 2,
            retrace_start + config.retrace_window_minutes - 1,
            int(raw.index.searchsorted(end, side="left")) - 2,
        )
        retrace_index: int | None = None
        invalidation_reason: str | None = None
        for candidate_index in range(retrace_start, retrace_end + 1):
            candidate = raw.iloc[candidate_index]
            if bearish_sweep:
                if float(candidate["close"]) > sweep_level + config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "close re-accepted above swept buy-side liquidity"
                    break
                if float(candidate["high"]) > sweep_extreme + config.stop_buffer_atr * atr_value:
                    invalidation_reason = "sweep extreme was exceeded before entry"
                    break
                if float(candidate["close"]) > float(fvg["gap_upper"]):
                    invalidation_reason = "bearish FVG was fully closed through"
                    break
                touched = float(candidate["high"]) >= retrace_level
                rejected = (
                    float(candidate["close"]) < retrace_level
                    and float(candidate["close"]) < float(candidate["open"])
                )
            else:
                if float(candidate["close"]) < sweep_level - config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "close re-accepted below swept sell-side liquidity"
                    break
                if float(candidate["low"]) < sweep_extreme - config.stop_buffer_atr * atr_value:
                    invalidation_reason = "sweep extreme was exceeded before entry"
                    break
                if float(candidate["close"]) < float(fvg["gap_lower"]):
                    invalidation_reason = "bullish FVG was fully closed through"
                    break
                touched = float(candidate["low"]) <= retrace_level
                rejected = (
                    float(candidate["close"]) > retrace_level
                    and float(candidate["close"]) > float(candidate["open"])
                )
            if touched and rejected:
                retrace_index = candidate_index
                break

        if retrace_index is None:
            _record(
                records,
                counts,
                stage="INVALIDATED_BEFORE_RETRACE" if invalidation_reason else "NO_FVG_RETRACE",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason=invalidation_reason or "FVG was not retraced and rejected inside the allowed state lifetime",
                fvg_retrace_level=retrace_level,
            )
            continue

        retrace_time = raw.index[retrace_index]
        activation_index = retrace_index + config.activation_delay_minutes
        activation_time = raw.index[activation_index]
        if not start <= activation_time < end:
            continue
        entry = float(raw.iloc[activation_index]["close"])
        if bearish_sweep:
            stop = sweep_extreme + config.stop_buffer_atr * atr_value
            target = range_low
            geometry = target < entry < stop
        else:
            stop = sweep_extreme - config.stop_buffer_atr * atr_value
            target = range_high
            geometry = stop < entry < target
        if not geometry:
            _record(
                records,
                counts,
                stage="ENTRY_GEOMETRY_REJECTED",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="activation price no longer lay between structural invalidation and opposing liquidity",
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
            _record(
                records,
                counts,
                stage="COST_AFTER_RR_REJECTED",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="structural path existed but target could not pay realistic entry and stop costs",
                cost_after_reward_risk=rr,
                minimum=config.minimum_cost_after_rr,
                maximum=config.maximum_cost_after_rr,
            )
            continue

        activation_ns = int(activation_time.value)
        if (
            last_emitted_ns is not None
            and activation_ns - last_emitted_ns < config.scenario_cooldown_minutes * NS_MINUTE
        ):
            _record(
                records,
                counts,
                stage="DUPLICATE_SCENARIO_COOLDOWN",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="same local auction transfer already emitted a recent trade intent",
            )
            continue

        feature_time, feature_details = _latest_feature(state, retrace_time)
        details: dict[str, Any] = {
            "scenario": "EXTERNAL_LIQUIDITY_SWEEP_DISPLACEMENT_CHOCH_FVG_RETRACE",
            "state_sequence": [
                "RANGE_DEFINED",
                "EXTERNAL_LIQUIDITY_SWEPT",
                "DISPLACEMENT_AND_CHOCH",
                "FVG_FORMED",
                "FVG_RETRACE_REJECTED",
                "ENTRY_ARMED",
            ],
            "dealing_range_minutes": config.dealing_range_minutes,
            "dealing_range_high": range_high,
            "dealing_range_low": range_low,
            "dealing_range_equilibrium": (range_high + range_low) / 2.0,
            "liquidity_touch_count": touch_count,
            "sweep_time_ns": int(sweep_time.value),
            "sweep_level": sweep_level,
            "sweep_extreme": sweep_extreme,
            "sweep_excursion_atr": excursion_atr,
            "choch_level": choch_level,
            "displacement_time_ns": int(fvg["displacement_time"].value),
            "displacement_body_threshold_ratio": fvg["body_ratio"],
            "displacement_range_threshold_ratio": fvg["range_ratio"],
            "fvg_formed_time_ns": int(fvg["fvg_time"].value),
            "fvg_lower": fvg["gap_lower"],
            "fvg_upper": fvg["gap_upper"],
            "fvg_retrace_level": retrace_level,
            "retrace_rejection_time_ns": int(retrace_time.value),
            "activation_time_ns": activation_ns,
            "structural_invalidation": stop,
            "opposing_external_liquidity_target": target,
            "session": session,
            "atr_1m_prior_to_sweep": atr_value,
            "latest_available_feature_open_time_ns": int(feature_time.value) if feature_time is not None else None,
            "microstructure_context": feature_details,
        }
        score = (
            rr
            + float(fvg["body_ratio"])
            + float(fvg["range_ratio"])
            + float(fvg["gap_size"]) / atr_value
            + 0.05 * touch_count
        )
        signal = RotationSignal(
            scenario_id=f"v111-liquidity-sweep-{side.lower()}-{activation_ns}",
            observed_time_ns=activation_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=score,
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_open_time_ns=(
                int(feature_time.value) if feature_time is not None else int(sweep_time.value)
            ),
            source_feature_available_time_ns=activation_ns,
            source_max_market_time_ns=int(retrace_time.value),
            details=details,
        )
        prior = selected.get(activation_ns)
        if prior is None or signal.score > prior.score:
            selected[activation_ns] = signal
        last_emitted_ns = activation_ns
        _record(
            records,
            counts,
            stage="ENTRY_ARMED",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="all causal SMC/ICT state transitions completed",
            activation_time_utc=activation_time.isoformat(),
            cost_after_reward_risk=rr,
            target=target,
            stop=stop,
        )

    result = sorted(selected.values(), key=lambda signal: signal.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v111 entry used an incomplete activation bar")
        if signal.observed_time_ns - signal.source_max_market_time_ns != NS_MINUTE:
            raise AssertionError("v111 activation delay is not exactly one completed minute")
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v111 feature availability contract mismatch")

    _LAST_DIAGNOSTICS = {
        "summary": {
            "candidate": "candidate-02-v111-liquidity-sweep-state-machine",
            "event_detector": "prior external dealing-range liquidity sweep",
            "scenario_state_machine": (
                "RANGE_DEFINED -> EXTERNAL_LIQUIDITY_SWEPT -> "
                "DISPLACEMENT_AND_CHOCH -> FVG_FORMED -> "
                "FVG_RETRACE_REJECTED -> ENTRY_ARMED"
            ),
            "counts": dict(sorted(counts.items())),
            "signals_emitted": len(result),
            "records": len(records),
            "future_information_used": False,
        },
        "records": records,
    }
    return result
