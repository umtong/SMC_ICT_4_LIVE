#!/usr/bin/env python3
"""Depth-conditioned volume-clock impact-innovation SMC/ICT candidate.

This candidate treats an external liquidity raid or directional displacement as
an event whose meaning depends on the price response expected from executed
order flow at the contemporaneous opposing depth.

For each *completed* equal-notional bucket, a robust past-only model estimates::

    realized_return_bps ~= beta * signed_effort / opposing_depth

The current bucket's directional residual, scaled by prior residual MAD, is its
impact innovation. Positive innovation means price moved farther than prior
flow/depth relations predicted; negative innovation means flow was absorbed.

Continuation:
  positive innovation + material new OI + basis alignment, followed by a real
  counter-flow/counter-price pullback retaining created inventory, then an
  independent positive-innovation structure resumption.

Reversal:
  a causal external pool take with negative innovation, followed by exact pool
  reclaim, opposite positive innovation and cause-specific inventory resolution.

The compiler emits intents only. NautilusTrader owns external-liquidity target
selection, orders, fills, fees, positions, current-NAV 3% all-in sizing, PnL and
NAV. No measured-move target is created here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Any, Iterable

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v24 as v24
import volume_clock_impact_residual_compiler as v37

Intent = v22.Intent

CONTINUATION = "DEPTH_NORMALIZED_POSITIVE_INNOVATION_PULLBACK_CONTINUATION"
TRAPPED_REVERSAL = "EXTERNAL_POOL_NEGATIVE_INNOVATION_TRAPPED_REVERSAL"
LIQUIDATION_REVERSAL = "EXTERNAL_POOL_NEGATIVE_INNOVATION_LIQUIDATION_REVERSAL"

DEPTH_BAND = 1
DEPTH_LOOKBACK_MINUTES = 3
MAX_DEPTH_AGE_SECONDS = 45.0
MODEL_BUCKETS = 144
MIN_MODEL_BUCKETS = 36
MIN_SLOPE_BUCKETS = 24
POSITIVE_INNOVATION_Z = 1.50
RESUMPTION_INNOVATION_Z = 0.50
NEGATIVE_INNOVATION_Z = -1.00
MIN_RETRACE_FRACTION = 0.20
MAX_RETRACE_FRACTION = 0.70
MAX_RESOLUTION_BUCKETS = 2
MAD_SCALE = 1.4826
MIN_MAD_BPS = 0.25


@dataclass(frozen=True, slots=True)
class ImpactModel:
    beta: float
    residual_median: float
    residual_scale: float
    pressure_q75: float
    sample_size: int


@dataclass(frozen=True, slots=True)
class Innovation:
    signed_pressure: float
    directional_expected_bps: float
    directional_actual_bps: float
    residual_bps: float
    z_score: float


@dataclass(frozen=True, slots=True)
class InnovationState:
    bucket: v37.VolumeBucket
    model: ImpactModel
    innovation: Innovation
    state: str
    inventory_route: str
    external_take: v24.PoolTake | None = None


def finite(value: Any) -> float:
    return v37.finite(value)


def nearest_quantile(values: Iterable[float], q: float) -> float:
    clean = sorted(finite(value) for value in values if math.isfinite(finite(value)))
    if not clean:
        return float("nan")
    rank = max(1, math.ceil(q * len(clean)))
    return clean[min(rank - 1, len(clean) - 1)]


def opposing_depth(data: pd.DataFrame, bucket: v37.VolumeBucket) -> float:
    """Past-only opposing depth immediately before the event-time bucket.

    Binance ``bookDepth`` bands are cumulative percentage bands. Averaging the
    1%-5% bands double counts the same resting liquidity and lets post-event
    replenishment affect the expected-impact denominator. Use a short median of
    fully observed 1% opposing depth ending one minute before the bucket starts.
    """

    if bucket.side not in (-1, 1):
        return float("nan")
    end = int(bucket.start_index) - 1
    start = max(0, end - DEPTH_LOOKBACK_MINUTES + 1)
    if end < start:
        return float("nan")
    column = "ask_depth_1" if bucket.side > 0 else "bid_depth_1"
    observations: list[float] = []
    for index in range(start, end + 1):
        age = finite(data["depth_snapshot_age_seconds"].iloc[index])
        value = finite(data[column].iloc[index])
        if (
            math.isfinite(age)
            and age <= MAX_DEPTH_AGE_SECONDS
            and math.isfinite(value)
            and value > 0.0
        ):
            observations.append(value)
    if len(observations) < DEPTH_LOOKBACK_MINUTES:
        return float("nan")
    return float(median(observations))


def signed_depth_pressure(data: pd.DataFrame, bucket: v37.VolumeBucket) -> float:
    depth = opposing_depth(data, bucket)
    if not math.isfinite(depth) or depth <= 0.0:
        return float("nan")
    return bucket.signed_effort / depth


def robust_impact_model(
    data: pd.DataFrame,
    history: list[v37.VolumeBucket],
) -> ImpactModel | None:
    prior = history[-MODEL_BUCKETS:]
    if len(prior) < MIN_MODEL_BUCKETS:
        return None
    pairs: list[tuple[float, float]] = []
    for bucket in prior:
        pressure = signed_depth_pressure(data, bucket)
        realized = finite(bucket.return_bps)
        if not (math.isfinite(pressure) and math.isfinite(realized)):
            continue
        if abs(pressure) <= 1e-12:
            continue
        # Include aligned, absorbed and adverse responses. Conditioning the
        # model only on aligned returns would censor the negative residuals that
        # define absorption and bias expected impact upward.
        pairs.append((pressure, realized))
    if len(pairs) < MIN_SLOPE_BUCKETS:
        return None
    slopes = [realized / pressure for pressure, realized in pairs]
    beta = float(median(slopes))
    if not math.isfinite(beta) or beta <= 0.0:
        return None
    residuals = [
        math.copysign(1.0, pressure) * realized - beta * abs(pressure)
        for pressure, realized in pairs
    ]
    center = float(median(residuals))
    mad = float(median(abs(value - center) for value in residuals))
    scale = max(MAD_SCALE * mad, MIN_MAD_BPS)
    return ImpactModel(
        beta=beta,
        residual_median=center,
        residual_scale=scale,
        pressure_q75=nearest_quantile((abs(value[0]) for value in pairs), 0.75),
        sample_size=len(pairs),
    )


def impact_innovation(
    data: pd.DataFrame,
    bucket: v37.VolumeBucket,
    model: ImpactModel,
) -> Innovation | None:
    pressure = signed_depth_pressure(data, bucket)
    if not math.isfinite(pressure) or abs(pressure) <= 1e-12:
        return None
    side = 1 if pressure > 0.0 else -1
    actual = side * finite(bucket.return_bps)
    expected = model.beta * abs(pressure)
    if not all(math.isfinite(value) for value in (actual, expected)):
        return None
    residual = actual - expected
    z_score = (residual - model.residual_median) / model.residual_scale
    return Innovation(
        signed_pressure=pressure,
        directional_expected_bps=expected,
        directional_actual_bps=actual,
        residual_bps=residual,
        z_score=z_score,
    )


def matching_external_take(bucket: v37.VolumeBucket) -> v24.PoolTake | None:
    candidates = [
        take for take in bucket.external_takes
        if int(take.pool_side) == int(bucket.side)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda take: (take.age_bars, take.prominence_atr, take.touches),
    )


def classify_state(
    data: pd.DataFrame,
    bucket: v37.VolumeBucket,
    model: ImpactModel,
    thresholds: v37.BucketThresholds,
) -> InnovationState | None:
    innovation = impact_innovation(data, bucket, model)
    if innovation is None:
        return None
    route = v37.material_inventory_route(
        bucket.oi_change,
        thresholds.positive_oi_median,
    )
    if route is None:
        return None
    if (
        abs(bucket.imbalance) < thresholds.imbalance_q75
        or abs(innovation.signed_pressure) < model.pressure_q75
    ):
        return None

    take = matching_external_take(bucket)
    # A direct external-liquidity cause always precedes generic continuation.
    if take is not None:
        if (
            innovation.directional_actual_bps >= 0.0
            and innovation.z_score <= NEGATIVE_INNOVATION_Z
        ):
            return InnovationState(
                bucket=bucket,
                model=model,
                innovation=innovation,
                state="EXTERNAL_POOL_NEGATIVE_INNOVATION",
                inventory_route=route,
                external_take=take,
            )
        return None

    if (
        route == "NEW_INVENTORY"
        and bucket.directional_basis_change_bps > 0.0
        and innovation.z_score >= POSITIVE_INNOVATION_Z
    ):
        return InnovationState(
            bucket=bucket,
            model=model,
            innovation=innovation,
            state="POSITIVE_INNOVATION_NEW_INVENTORY",
            inventory_route=route,
        )
    return None


def actual_counter_pullback(
    shock: v37.VolumeBucket,
    pullback: v37.VolumeBucket,
) -> tuple[bool, float]:
    displacement = shock.side * (shock.close - shock.start_price)
    if displacement <= 0.0:
        return False, float("nan")
    counter_price = -shock.side * (pullback.close - shock.close)
    counter_flow = -shock.side * pullback.imbalance
    if counter_price <= 0.0 or counter_flow <= 0.0:
        return False, float("nan")
    fraction = counter_price / displacement
    return (
        MIN_RETRACE_FRACTION <= fraction <= MAX_RETRACE_FRACTION,
        fraction,
    )


def structure_level(bucket: v37.VolumeBucket, side: int) -> float:
    return bucket.high if side > 0 else bucket.low


def structure_broken(bucket: v37.VolumeBucket, level: float, side: int) -> bool:
    return bucket.close > level if side > 0 else bucket.close < level


def exact_pool_reclaimed(take: v24.PoolTake, close: float) -> bool:
    return v24.pool_is_reclaimed(take, close)


def _details(state: InnovationState, confirmation: v37.VolumeBucket) -> dict[str, Any]:
    bucket = state.bucket
    details: dict[str, Any] = {
        "shock_bucket_id": bucket.bucket_id,
        "shock_start_index": bucket.start_index,
        "shock_end_index": bucket.end_index,
        "shock_imbalance": bucket.imbalance,
        "shock_oi_before": bucket.oi_before,
        "shock_oi_end": bucket.oi_end,
        "shock_oi_change": bucket.oi_change,
        "shock_basis_change_bps": bucket.directional_basis_change_bps,
        "opposing_depth_pressure": state.innovation.signed_pressure,
        "past_only_impact_beta": state.model.beta,
        "past_only_residual_median": state.model.residual_median,
        "past_only_residual_scale": state.model.residual_scale,
        "past_only_pressure_q75": state.model.pressure_q75,
        "impact_model_sample_size": state.model.sample_size,
        "directional_expected_return_bps": state.innovation.directional_expected_bps,
        "directional_actual_return_bps": state.innovation.directional_actual_bps,
        "impact_residual_bps": state.innovation.residual_bps,
        "impact_innovation_z": state.innovation.z_score,
        "inventory_route": state.inventory_route,
        "confirmation_bucket_id": confirmation.bucket_id,
        "confirmation_start_index": confirmation.start_index,
        "confirmation_end_index": confirmation.end_index,
        "compiler": "candidate-04-depth-conditioned-impact-innovation",
    }
    if state.external_take is not None:
        details.update({
            "external_pool_id": state.external_take.pool_id,
            "external_pool_side": state.external_take.pool_side,
            "external_pool_level": state.external_take.level,
            "external_pool_age_bars": state.external_take.age_bars,
            "external_pool_prominence_atr": state.external_take.prominence_atr,
        })
    return details


def resolve_continuation(
    data: pd.DataFrame,
    buckets: list[v37.VolumeBucket],
    models: list[ImpactModel | None],
    position: int,
    state: InnovationState,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[Intent | None, int]:
    shock = state.bucket
    upper_pullback = min(position + MAX_RESOLUTION_BUCKETS, len(buckets) - 2)
    for pullback_position in range(position + 1, upper_pullback + 1):
        pullback = buckets[pullback_position]
        valid, fraction = actual_counter_pullback(shock, pullback)
        if not valid or not v37.oi_creation_retained(shock, pullback):
            continue
        level = structure_level(pullback, shock.side)
        upper_resume = min(pullback_position + MAX_RESOLUTION_BUCKETS, len(buckets) - 1)
        for resume_position in range(pullback_position + 1, upper_resume + 1):
            resume = buckets[resume_position]
            if data.index[resume.end_index] > evaluation_end:
                return None, resume_position
            if resume.end_index + 1 >= len(data):
                continue
            model = models[resume_position]
            if model is None:
                continue
            innovation = impact_innovation(data, resume, model)
            if innovation is None:
                continue
            aligned = (
                shock.side * resume.imbalance > 0.0
                and shock.side * resume.return_bps > 0.0
                and shock.side * (resume.basis_end - resume.basis_before) > 0.0
                and innovation.z_score >= RESUMPTION_INNOVATION_Z
            )
            if not (
                aligned
                and structure_broken(resume, level, shock.side)
                and v37.oi_creation_retained(shock, resume)
            ):
                continue
            stop = v37.structural_stop(
                data, shock.start_index, resume.end_index,
                shock.side, impact_parameters,
            )
            if not math.isfinite(stop) or shock.side * (resume.close - stop) <= 0.0:
                continue
            details = {
                **_details(state, resume),
                "pullback_bucket_id": pullback.bucket_id,
                "pullback_fraction": fraction,
                "pullback_structure": level,
                "resumption_impact_innovation_z": innovation.z_score,
            }
            return Intent(
                scenario=CONTINUATION,
                side=shock.side,
                signal_index=resume.end_index,
                entry_index=resume.end_index + 1,
                stop_level=stop,
                event_indices=(
                    shock.start_index, shock.end_index,
                    pullback.start_index, pullback.end_index,
                    resume.start_index, resume.end_index,
                ),
                details=details,
            ), resume_position
    return None, upper_pullback


def resolve_reversal(
    data: pd.DataFrame,
    buckets: list[v37.VolumeBucket],
    models: list[ImpactModel | None],
    position: int,
    state: InnovationState,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[Intent | None, int]:
    shock = state.bucket
    take = state.external_take
    if take is None:
        return None, position
    trade_side = -shock.side
    upper = min(position + MAX_RESOLUTION_BUCKETS, len(buckets) - 1)
    for confirm_position in range(position + 1, upper + 1):
        confirm = buckets[confirm_position]
        if data.index[confirm.end_index] > evaluation_end:
            return None, confirm_position
        if confirm.end_index + 1 >= len(data):
            continue
        model = models[confirm_position]
        if model is None:
            continue
        innovation = impact_innovation(data, confirm, model)
        if innovation is None:
            continue
        aligned = (
            trade_side * confirm.imbalance > 0.0
            and trade_side * confirm.return_bps > 0.0
            and trade_side * (confirm.basis_end - confirm.basis_before) > 0.0
            and innovation.z_score >= RESUMPTION_INNOVATION_Z
        )
        if not (
            exact_pool_reclaimed(take, confirm.close)
            and aligned
            and v37.route_inventory_resolved(
                state.inventory_route, shock, confirm,
            )
        ):
            continue
        stop = v37.structural_stop(
            data, shock.start_index, confirm.end_index,
            trade_side, impact_parameters,
        )
        if not math.isfinite(stop) or trade_side * (confirm.close - stop) <= 0.0:
            continue
        scenario = (
            TRAPPED_REVERSAL
            if state.inventory_route == "NEW_INVENTORY"
            else LIQUIDATION_REVERSAL
        )
        details = {
            **_details(state, confirm),
            "reclaim_impact_innovation_z": innovation.z_score,
            "reclaim_close": confirm.close,
        }
        return Intent(
            scenario=scenario,
            side=trade_side,
            signal_index=confirm.end_index,
            entry_index=confirm.end_index + 1,
            stop_level=stop,
            event_indices=(
                shock.start_index, shock.end_index,
                confirm.start_index, confirm.end_index,
            ),
            details=details,
        ), confirm_position
    return None, upper


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    takes = v24.detect_external_pool_takes(data, config)
    buckets = v37.build_volume_buckets(data, takes)
    if any(bucket.notional + 1e-9 < bucket.target_notional for bucket in buckets):
        raise RuntimeError("incomplete volume-clock bucket reached V41")

    models: list[ImpactModel | None] = []
    thresholds: list[v37.BucketThresholds | None] = []
    history: list[v37.VolumeBucket] = []
    for bucket in buckets:
        models.append(robust_impact_model(data, history))
        thresholds.append(v37.bucket_thresholds(history))
        history.append(bucket)

    states: list[InnovationState | None] = []
    for bucket, model, threshold in zip(buckets, models, thresholds):
        states.append(
            classify_state(data, bucket, model, threshold)
            if model is not None and threshold is not None
            else None
        )

    intents: list[Intent] = []
    counts = {
        "volume_buckets": len(buckets),
        "models_ready": sum(model is not None for model in models),
        "positive_innovation_states": 0,
        "negative_innovation_external_states": 0,
        "continuations": 0,
        "trapped_reversals": 0,
        "liquidation_reversals": 0,
        "unresolved_states": 0,
    }
    position = 0
    while position < len(buckets):
        bucket = buckets[position]
        timestamp = data.index[bucket.end_index]
        if timestamp < evaluation_start:
            position += 1
            continue
        if timestamp > evaluation_end:
            break
        state = states[position]
        if state is None:
            position += 1
            continue
        if state.state == "POSITIVE_INNOVATION_NEW_INVENTORY":
            counts["positive_innovation_states"] += 1
            intent, resolved = resolve_continuation(
                data, buckets, models, position, state,
                evaluation_end, impact_parameters,
            )
        else:
            counts["negative_innovation_external_states"] += 1
            intent, resolved = resolve_reversal(
                data, buckets, models, position, state,
                evaluation_end, impact_parameters,
            )
        if intent is None:
            counts["unresolved_states"] += 1
        else:
            intents.append(intent)
            if intent.scenario == CONTINUATION:
                counts["continuations"] += 1
            elif intent.scenario == TRAPPED_REVERSAL:
                counts["trapped_reversals"] += 1
            else:
                counts["liquidation_reversals"] += 1
        if intent is None:
            # Unresolved states do not consume later completed buckets; those
            # buckets can form independent scenarios on subsequent iterations.
            position += 1
        else:
            position = max(position + 1, resolved + 1)

    intents.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicates = 0
    for intent in intents:
        if int(intent.signal_index) in seen:
            duplicates += 1
            continue
        seen.add(int(intent.signal_index))
        unique.append(intent)
    return unique, {
        "candidate": "candidate-04-v41-depth-conditioned-impact-innovation",
        "compiler": "candidate-04-depth-conditioned-impact-innovation",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicates,
        "route_counts": counts,
        "scenario_contract": {
            "clock": "completed frozen-notional event-time buckets",
            "impact": "robust past-only return response to signed effort divided by opposing depth",
            "continuation": "positive innovation, created OI, real counter-auction, independent positive-innovation resumption",
            "reversal": "external pool raid, negative innovation, exact reclaim, opposite positive innovation and inventory resolution",
            "target": "pre-existing external liquidity only; no measured-move projection",
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals

if __name__ == "__main__":
    v22.main()
