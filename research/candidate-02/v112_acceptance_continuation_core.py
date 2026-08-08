"""Causal failed-reversal acceptance continuation for candidate-02 v112.

The v111 reversal study showed that most apparent sweeps were re-accepted in the
original excursion direction before a reversal FVG formed.  v112 does not loosen
that failed reversal.  It treats re-acceptance as a separate price-discovery
scenario:

RANGE_DEFINED -> LIQUIDITY_SWEPT_AND_REJECTED -> REVERSAL_FAILED
-> ACCEPTANCE_BEYOND_LIQUIDITY -> CONTINUATION_DISPLACEMENT_AND_BOS
-> CONTINUATION_FVG -> FVG_RETEST_HELD -> ENTRY_ARMED

Signal construction only.  NautilusTrader remains the sole execution, fee,
position, and NAV engine.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import (
    NS_MINUTE,
    CostConfig,
    RotationSignal,
    _true_range,
    cost_after_reward_risk,
)
from v111_liquidity_sweep_core import (
    LiquiditySweepConfig,
    _latest_feature,
    _normalize,
    _session_label,
    build_state,
)

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class AcceptanceContinuationConfig(LiquiditySweepConfig):
    higher_dealing_range_minutes: int = 360
    acceptance_window_minutes: int = 15
    acceptance_closes: int = 2
    acceptance_buffer_atr: float = 0.05
    bos_beyond_sweep_atr: float = 0.05
    continuation_fvg_window_minutes: int = 10
    target_swing_span_minutes: int = 3

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AcceptanceContinuationConfig":
        data = dict(values)
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v112 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        LiquiditySweepConfig.__post_init__(self)
        if self.higher_dealing_range_minutes <= self.dealing_range_minutes:
            raise ValueError("v112 higher range must exceed local dealing range")
        if self.acceptance_window_minutes < self.acceptance_closes:
            raise ValueError("v112 acceptance window is shorter than required closes")
        if self.acceptance_closes <= 0:
            raise ValueError("v112 acceptance closes must be positive")
        if self.acceptance_buffer_atr < 0 or self.bos_beyond_sweep_atr < 0:
            raise ValueError("v112 acceptance/BOS buffers cannot be negative")
        if self.continuation_fvg_window_minutes < 3:
            raise ValueError("v112 continuation FVG window is too short")
        if self.target_swing_span_minutes < 1:
            raise ValueError("v112 target swing span must be positive")


_LAST_DIAGNOSTICS: dict[str, Any] = {"summary": {}, "examples": {}}


def get_last_scenario_diagnostics() -> dict[str, Any]:
    return {
        "summary": dict(_LAST_DIAGNOSTICS.get("summary", {})),
        "examples": {
            str(key): list(values)
            for key, values in dict(_LAST_DIAGNOSTICS.get("examples", {})).items()
        },
    }


def _prior_swing_target(
    *,
    raw: pd.DataFrame,
    sweep_index: int,
    entry: float,
    side: str,
    lookback: int,
    span: int,
) -> tuple[float | None, int]:
    """Nearest pre-sweep confirmed swing liquidity beyond the entry."""

    start = max(0, sweep_index - lookback)
    history = raw.iloc[start:sweep_index]
    if len(history) < 2 * span + 1:
        return None, 0
    window = 2 * span + 1
    if side == "BUY":
        extreme = history["high"].rolling(window, center=True, min_periods=window).max()
        mask = history["high"].eq(extreme)
        levels = [float(value) for value in history.loc[mask, "high"] if float(value) > entry]
        return (min(levels), len(levels)) if levels else (None, 0)
    extreme = history["low"].rolling(window, center=True, min_periods=window).min()
    mask = history["low"].eq(extreme)
    levels = [float(value) for value in history.loc[mask, "low"] if float(value) < entry]
    return (max(levels), len(levels)) if levels else (None, 0)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: AcceptanceContinuationConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    global _LAST_DIAGNOSTICS

    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError("v112 raw bars must be unique and increasing")

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
    local_high = raw["high"].shift(1).rolling(
        config.dealing_range_minutes,
        min_periods=config.dealing_range_minutes,
    ).max()
    local_low = raw["low"].shift(1).rolling(
        config.dealing_range_minutes,
        min_periods=config.dealing_range_minutes,
    ).min()
    higher_high = raw["high"].shift(1).rolling(
        config.higher_dealing_range_minutes,
        min_periods=config.higher_dealing_range_minutes,
    ).max()
    higher_low = raw["low"].shift(1).rolling(
        config.higher_dealing_range_minutes,
        min_periods=config.higher_dealing_range_minutes,
    ).min()

    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    selected: dict[int, RotationSignal] = {}
    last_emitted_ns: int | None = None

    def record(
        stage: str,
        *,
        side: str,
        session: str,
        sweep_time: pd.Timestamp,
        reason: str,
        **values: Any,
    ) -> None:
        counts[stage] += 1
        counts[f"{stage}:{side}"] += 1
        counts[f"{stage}:{session}"] += 1
        if len(examples[stage]) < 5:
            examples[stage].append(
                {
                    "side": side,
                    "session": session,
                    "sweep_time_utc": sweep_time.isoformat(),
                    "reason": reason,
                    **values,
                }
            )

    evaluation_indices = raw.index.get_indexer_for(
        raw.index[(raw.index >= start) & (raw.index < end)]
    )
    end_position = int(raw.index.searchsorted(end, side="left"))

    for sweep_index in evaluation_indices:
        if sweep_index < config.higher_dealing_range_minutes:
            continue
        sweep_time = raw.index[sweep_index]
        row = raw.iloc[sweep_index]
        atr_value = float(atr.iloc[sweep_index])
        range_high = float(local_high.iloc[sweep_index])
        range_low = float(local_low.iloc[sweep_index])
        htf_high = float(higher_high.iloc[sweep_index])
        htf_low = float(higher_low.iloc[sweep_index])
        if not all(
            math.isfinite(value)
            for value in (atr_value, range_high, range_low, htf_high, htf_low)
        ):
            continue
        if atr_value <= 0 or range_high <= range_low or htf_high <= htf_low:
            continue

        history = raw.iloc[
            sweep_index - config.dealing_range_minutes : sweep_index
        ]
        tolerance = config.liquidity_touch_tolerance_atr * atr_value
        high_touches = int((history["high"] >= range_high - tolerance).sum())
        low_touches = int((history["low"] <= range_low + tolerance).sum())
        total_range = max(float(row["high"] - row["low"]), 1e-12)
        upper_wick = float(row["high"] - max(row["open"], row["close"])) / total_range
        lower_wick = float(min(row["open"], row["close"]) - row["low"]) / total_range
        high_excursion = float(row["high"] - range_high) / atr_value
        low_excursion = float(range_low - row["low"]) / atr_value

        swept_high = (
            high_touches >= config.minimum_liquidity_touches
            and config.sweep_min_atr <= high_excursion <= config.sweep_max_atr
            and float(row["close"]) < range_high
            and upper_wick >= config.minimum_rejection_wick_fraction
        )
        swept_low = (
            low_touches >= config.minimum_liquidity_touches
            and config.sweep_min_atr <= low_excursion <= config.sweep_max_atr
            and float(row["close"]) > range_low
            and lower_wick >= config.minimum_rejection_wick_fraction
        )
        if swept_high and swept_low:
            counts["AMBIGUOUS_TWO_SIDED_SWEEP"] += 1
            continue
        if not swept_high and not swept_low:
            continue

        side = "BUY" if swept_high else "SELL"
        session = _session_label(sweep_time)
        level = range_high if swept_high else range_low
        sweep_extreme = float(row["high"] if swept_high else row["low"])
        touch_count = high_touches if swept_high else low_touches
        excursion = high_excursion if swept_high else low_excursion
        htf_equilibrium = (htf_high + htf_low) / 2.0
        in_price_discovery_half = (
            level >= htf_equilibrium if side == "BUY" else level <= htf_equilibrium
        )
        if not in_price_discovery_half:
            record(
                "HTF_PREMIUM_DISCOUNT_REJECTED",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="local swept liquidity was not in the continuation half of the higher dealing range",
                level=level,
                higher_equilibrium=htf_equilibrium,
            )
            continue

        record(
            "LIQUIDITY_SWEPT_AND_REJECTED",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="external local liquidity was swept and the sweep bar initially closed back inside",
            level=level,
            sweep_extreme=sweep_extreme,
            sweep_excursion_atr=excursion,
            liquidity_touch_count=touch_count,
        )

        acceptance_index: int | None = None
        consecutive = 0
        displacement_seen = False
        acceptance_scan_end = min(
            end_position - 1,
            len(raw) - 1,
            sweep_index + config.acceptance_window_minutes,
        )
        for index in range(sweep_index + 1, acceptance_scan_end + 1):
            current = raw.iloc[index]
            body_limit = float(body_threshold.iloc[index])
            range_limit = float(range_threshold.iloc[index])
            if not all(math.isfinite(value) for value in (body_limit, range_limit)):
                continue
            directional_displacement = (
                float(body.iloc[index]) >= body_limit
                and float(bar_range.iloc[index]) >= range_limit
                and (
                    float(current["close"]) > float(current["open"])
                    if side == "BUY"
                    else float(current["close"]) < float(current["open"])
                )
            )
            if side == "BUY":
                accepted = float(current["close"]) > level + config.acceptance_buffer_atr * atr_value
                bos = float(current["close"]) > sweep_extreme + config.bos_beyond_sweep_atr * atr_value
            else:
                accepted = float(current["close"]) < level - config.acceptance_buffer_atr * atr_value
                bos = float(current["close"]) < sweep_extreme - config.bos_beyond_sweep_atr * atr_value

            if accepted:
                consecutive += 1
                displacement_seen = displacement_seen or directional_displacement
            else:
                consecutive = 0
                displacement_seen = False
            if consecutive >= config.acceptance_closes and displacement_seen and bos:
                acceptance_index = index
                break

        if acceptance_index is None:
            record(
                "NO_CAUSAL_REACCEPTANCE",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="failed reversal did not achieve sustained closes, displacement, and BOS beyond the sweep extreme",
            )
            continue

        acceptance_time = raw.index[acceptance_index]
        record(
            "REVERSAL_FAILED_AND_ACCEPTED",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="market re-accepted beyond swept liquidity with displacement and BOS",
            acceptance_time_utc=acceptance_time.isoformat(),
            required_acceptance_closes=config.acceptance_closes,
        )

        fvg: dict[str, Any] | None = None
        fvg_scan_start = max(sweep_index + 2, acceptance_index)
        fvg_scan_end = min(
            end_position - 1,
            len(raw) - 1,
            acceptance_index + config.continuation_fvg_window_minutes,
        )
        for third_index in range(fvg_scan_start, fvg_scan_end + 1):
            first_index = third_index - 2
            middle_index = third_index - 1
            first = raw.iloc[first_index]
            middle = raw.iloc[middle_index]
            third = raw.iloc[third_index]
            body_limit = float(body_threshold.iloc[middle_index])
            range_limit = float(range_threshold.iloc[middle_index])
            if not all(math.isfinite(value) for value in (body_limit, range_limit)):
                continue
            displaced = (
                float(body.iloc[middle_index]) >= body_limit
                and float(bar_range.iloc[middle_index]) >= range_limit
            )
            if side == "BUY":
                directional = float(middle["close"]) > float(middle["open"])
                gap_lower = float(first["high"])
                gap_upper = float(third["low"])
                held_beyond = float(third["close"]) > level
            else:
                directional = float(middle["close"]) < float(middle["open"])
                gap_lower = float(third["high"])
                gap_upper = float(first["low"])
                held_beyond = float(third["close"]) < level
            gap_size = gap_upper - gap_lower
            if (
                displaced
                and directional
                and held_beyond
                and gap_size >= config.fvg_min_atr * atr_value
            ):
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
            record(
                "NO_CONTINUATION_FVG",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="accepted breakout did not leave a causal continuation imbalance",
                acceptance_time_utc=acceptance_time.isoformat(),
            )
            continue

        record(
            "CONTINUATION_DISPLACEMENT_FVG_CONFIRMED",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="accepted price discovery left a directional three-candle imbalance",
            fvg_time_utc=fvg["fvg_time"].isoformat(),
            fvg_size_atr=float(fvg["gap_size"]) / atr_value,
        )

        retrace_level = float(
            fvg["gap_lower"]
            + config.fvg_retrace_fraction * (fvg["gap_upper"] - fvg["gap_lower"])
        )
        retest_index: int | None = None
        invalidation_reason: str | None = None
        retest_start = max(int(fvg["third_index"]) + 1, acceptance_index + 1)
        retest_end = min(
            end_position - 2,
            len(raw) - 2,
            retest_start + config.retrace_window_minutes - 1,
        )
        for index in range(retest_start, retest_end + 1):
            candidate = raw.iloc[index]
            if side == "BUY":
                if float(candidate["close"]) < level - config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "accepted buy-side price discovery closed back inside the old range"
                    break
                touched = float(candidate["low"]) <= retrace_level
                rejected = (
                    float(candidate["close"]) > retrace_level
                    and float(candidate["close"]) > float(candidate["open"])
                    and float(candidate["close"]) > level
                )
            else:
                if float(candidate["close"]) > level + config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "accepted sell-side price discovery closed back inside the old range"
                    break
                touched = float(candidate["high"]) >= retrace_level
                rejected = (
                    float(candidate["close"]) < retrace_level
                    and float(candidate["close"]) < float(candidate["open"])
                    and float(candidate["close"]) < level
                )
            if touched and rejected:
                retest_index = index
                break

        if retest_index is None:
            record(
                "ACCEPTANCE_INVALIDATED" if invalidation_reason else "NO_FVG_RETEST",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason=invalidation_reason or "continuation FVG was not retested and held within its state lifetime",
                retrace_level=retrace_level,
            )
            continue

        retest_time = raw.index[retest_index]
        activation_index = retest_index + config.activation_delay_minutes
        activation_time = raw.index[activation_index]
        if not start <= activation_time < end:
            continue
        entry = float(raw.iloc[activation_index]["close"])
        stop = (
            level - config.stop_buffer_atr * atr_value
            if side == "BUY"
            else level + config.stop_buffer_atr * atr_value
        )
        target, target_candidates = _prior_swing_target(
            raw=raw,
            sweep_index=sweep_index,
            entry=entry,
            side=side,
            lookback=config.higher_dealing_range_minutes,
            span=config.target_swing_span_minutes,
        )
        if target is None:
            record(
                "NO_PREOBSERVED_LIQUIDITY_OBJECTIVE",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="no pre-sweep confirmed swing liquidity remained beyond the activation price",
                entry=entry,
            )
            continue
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            record(
                "ENTRY_GEOMETRY_REJECTED",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="activation price was not between failed-acceptance invalidation and next liquidity",
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
                sweep_time=sweep_time,
                reason="next pre-observed liquidity did not compensate realistic entry/stop costs",
                cost_after_reward_risk=rr,
                entry=entry,
                stop=stop,
                target=target,
            )
            continue

        activation_ns = int(activation_time.value)
        if (
            last_emitted_ns is not None
            and activation_ns - last_emitted_ns < config.scenario_cooldown_minutes * NS_MINUTE
        ):
            record(
                "DUPLICATE_SCENARIO_COOLDOWN",
                side=side,
                session=session,
                sweep_time=sweep_time,
                reason="recent intent already represented this local price-discovery transfer",
            )
            continue

        feature_time, feature_details = _latest_feature(state, retest_time)
        score = (
            rr
            + float(fvg["body_ratio"])
            + float(fvg["range_ratio"])
            + float(fvg["gap_size"]) / atr_value
            + 0.05 * touch_count
        )
        details: dict[str, Any] = {
            "scenario": "FAILED_REVERSAL_ACCEPTANCE_CONTINUATION",
            "state_sequence": [
                "RANGE_DEFINED",
                "LIQUIDITY_SWEPT_AND_REJECTED",
                "REVERSAL_FAILED",
                "ACCEPTANCE_BEYOND_LIQUIDITY",
                "CONTINUATION_DISPLACEMENT_AND_BOS",
                "CONTINUATION_FVG",
                "FVG_RETEST_HELD",
                "ENTRY_ARMED",
            ],
            "dealing_range_minutes": config.dealing_range_minutes,
            "higher_dealing_range_minutes": config.higher_dealing_range_minutes,
            "local_range_high": range_high,
            "local_range_low": range_low,
            "higher_range_high": htf_high,
            "higher_range_low": htf_low,
            "higher_range_equilibrium": htf_equilibrium,
            "liquidity_touch_count": touch_count,
            "sweep_time_ns": int(sweep_time.value),
            "swept_liquidity_level": level,
            "sweep_extreme": sweep_extreme,
            "sweep_excursion_atr": excursion,
            "acceptance_time_ns": int(acceptance_time.value),
            "acceptance_closes": config.acceptance_closes,
            "continuation_displacement_time_ns": int(fvg["displacement_time"].value),
            "continuation_fvg_time_ns": int(fvg["fvg_time"].value),
            "fvg_lower": fvg["gap_lower"],
            "fvg_upper": fvg["gap_upper"],
            "fvg_retest_level": retrace_level,
            "fvg_retest_time_ns": int(retest_time.value),
            "activation_time_ns": activation_ns,
            "failed_acceptance_invalidation": stop,
            "next_preobserved_swing_liquidity_target": target,
            "target_candidate_count": target_candidates,
            "session": session,
            "atr_1m_prior_to_sweep": atr_value,
            "latest_available_feature_open_time_ns": (
                int(feature_time.value) if feature_time is not None else None
            ),
            "microstructure_context": feature_details,
        }
        signal = RotationSignal(
            scenario_id=f"v112-acceptance-continuation-{side.lower()}-{activation_ns}",
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
            source_max_market_time_ns=int(retest_time.value),
            details=details,
        )
        prior = selected.get(activation_ns)
        if prior is None or signal.score > prior.score:
            selected[activation_ns] = signal
        last_emitted_ns = activation_ns
        record(
            "ENTRY_ARMED",
            side=side,
            session=session,
            sweep_time=sweep_time,
            reason="failed reversal resolved into accepted price-discovery continuation",
            activation_time_utc=activation_time.isoformat(),
            cost_after_reward_risk=rr,
            stop=stop,
            target=target,
        )

    result = sorted(selected.values(), key=lambda value: value.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v112 entry used an incomplete activation bar")
        if signal.observed_time_ns - signal.source_max_market_time_ns != NS_MINUTE:
            raise AssertionError("v112 activation delay is not one completed minute")
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v112 feature availability contract mismatch")

    _LAST_DIAGNOSTICS = {
        "summary": {
            "candidate": "candidate-02-v112-failed-reversal-acceptance-continuation",
            "event_detector": "initial local liquidity sweep and rejection",
            "scenario_state_machine": (
                "RANGE_DEFINED -> LIQUIDITY_SWEPT_AND_REJECTED -> REVERSAL_FAILED "
                "-> ACCEPTANCE_BEYOND_LIQUIDITY -> CONTINUATION_DISPLACEMENT_AND_BOS "
                "-> CONTINUATION_FVG -> FVG_RETEST_HELD -> ENTRY_ARMED"
            ),
            "counts": dict(sorted(counts.items())),
            "signals_emitted": len(result),
            "future_information_used": False,
        },
        "examples": dict(examples),
    }
    return result
