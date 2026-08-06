#!/usr/bin/env python3
"""Volume-clock price-impact residual and inventory-resolution candidate.

Fixed clock time mixes very different information intensities.  This compiler
freezes a past-only median five-minute notional target at the start of each
bucket and closes the bucket only after that amount of completed trading has
arrived.  Every threshold for the current bucket is computed from earlier
completed buckets only.

An informed continuation requires:

1. a high-imbalance bucket whose directional price response, path efficiency,
   futures-index basis change and material OI creation all agree;
2. a separate weak counter-flow bucket that retains at least half of the
   displacement and most of the newly created OI; and
3. a later completed resumption bucket that breaks the pullback structure with
   renewed flow, return and basis alignment.

An absorption reversal requires:

1. a high-imbalance bucket taking a causal external pivot pool while realized
   price impact is in the lower tail of its own past volume-clock distribution;
2. material OI expansion or contraction to identify trapped new inventory or
   forced liquidation; and
3. a separate completed bucket reclaiming the exact pool with opposite flow,
   return and basis while the routed inventory unwinds or remains depleted.

Stops lie beyond the complete multi-bucket excursion.  The compiler emits
completed-data intents only.  NautilusTrader remains the sole owner of causal
target selection, orders, fills, fees, positions, margin, liquidation,
current-NAV 3% all-in risk sizing, PnL and NAV.
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


Intent = v22.Intent

INFORMED_CONTINUATION = "VOLUME_CLOCK_INFORMED_INVENTORY_PULLBACK_CONTINUATION"
TRAPPED_REVERSAL = "VOLUME_CLOCK_TRAPPED_INVENTORY_ABSORPTION_REVERSAL"
LIQUIDATION_REVERSAL = "VOLUME_CLOCK_LIQUIDATION_ABSORPTION_REVERSAL"

TARGET_NOTIONAL_BARS = 5
TARGET_WINDOW_MINUTES = 720
TARGET_MINIMUM_MINUTES = 240
MAX_BUCKET_BARS = 15
HISTORY_BUCKETS = 144
MIN_HISTORY_BUCKETS = 36
PULLBACK_RETAIN_FRACTION = 0.50
MAX_COUNTER_IMBALANCE_FRACTION = 0.65
OI_RETENTION = 0.999
OI_REBUILD_TOLERANCE = 1.001
RECLAIM_BUCKETS = 2
RESUMPTION_BUCKETS = 2


@dataclass(frozen=True, slots=True)
class VolumeBucket:
    bucket_id: int
    start_index: int
    end_index: int
    target_notional: float
    notional: float
    signed_effort: float
    imbalance: float
    side: int
    start_price: float
    close: float
    high: float
    low: float
    return_bps: float
    directional_return_bps: float
    path_bps: float
    efficiency: float
    impact_ratio: float
    oi_before: float
    oi_end: float
    oi_change: float
    basis_before: float
    basis_end: float
    directional_basis_change_bps: float
    external_takes: tuple[v24.PoolTake, ...]


@dataclass(frozen=True, slots=True)
class BucketThresholds:
    imbalance_q75: float
    imbalance_q50: float
    absolute_return_q65: float
    efficiency_q60: float
    impact_q25: float
    impact_q60: float
    positive_oi_median: float


@dataclass(frozen=True, slots=True)
class BucketState:
    bucket: VolumeBucket
    thresholds: BucketThresholds
    state: str
    pool_take: v24.PoolTake | None = None
    inventory_route: str | None = None


def finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def past_only_notional_target(data: pd.DataFrame) -> pd.Series:
    five_minute = (
        data["notional_60s"].astype(float)
        .clip(lower=0.0)
        .rolling(TARGET_NOTIONAL_BARS, min_periods=TARGET_NOTIONAL_BARS)
        .sum()
    )
    return (
        five_minute.shift(1)
        .rolling(
            TARGET_WINDOW_MINUTES,
            min_periods=TARGET_MINIMUM_MINUTES,
        )
        .median()
    )


def nearest_quantile(values: Iterable[float], quantile: float) -> float:
    clean = sorted(
        finite(value)
        for value in values
        if math.isfinite(finite(value))
    )
    if not clean:
        return float("nan")
    rank = max(1, math.ceil(quantile * len(clean)))
    return clean[min(rank - 1, len(clean) - 1)]


def bucket_thresholds(history: list[VolumeBucket]) -> BucketThresholds | None:
    prior = history[-HISTORY_BUCKETS:]
    if len(prior) < MIN_HISTORY_BUCKETS:
        return None
    positive_oi = [
        bucket.oi_change
        for bucket in prior
        if math.isfinite(bucket.oi_change) and bucket.oi_change > 0.0
    ]
    if len(positive_oi) < max(12, MIN_HISTORY_BUCKETS // 3):
        return None
    return BucketThresholds(
        imbalance_q75=nearest_quantile(
            (abs(bucket.imbalance) for bucket in prior),
            0.75,
        ),
        imbalance_q50=nearest_quantile(
            (abs(bucket.imbalance) for bucket in prior),
            0.50,
        ),
        absolute_return_q65=nearest_quantile(
            (abs(bucket.return_bps) for bucket in prior),
            0.65,
        ),
        efficiency_q60=nearest_quantile(
            (bucket.efficiency for bucket in prior),
            0.60,
        ),
        impact_q25=nearest_quantile(
            (bucket.impact_ratio for bucket in prior),
            0.25,
        ),
        impact_q60=nearest_quantile(
            (bucket.impact_ratio for bucket in prior),
            0.60,
        ),
        positive_oi_median=float(median(positive_oi)),
    )


def _oi(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return finite(data["metric_sum_open_interest"].iloc[index])


def _basis(data: pd.DataFrame, index: int) -> float:
    if not 0 <= index < len(data):
        return float("nan")
    return finite(data["trade_index_basis_bps"].iloc[index])


def build_volume_buckets(
    data: pd.DataFrame,
    external_takes: dict[int, list[v24.PoolTake]],
) -> list[VolumeBucket]:
    targets = past_only_notional_target(data)
    close = data["close"].astype(float)
    one_minute_path = close.pct_change(fill_method=None).abs() * 10_000.0
    buckets: list[VolumeBucket] = []
    index = TARGET_MINIMUM_MINUTES
    bucket_id = 0
    while index < len(data):
        target = finite(targets.iloc[index])
        if not math.isfinite(target) or target <= 0.0:
            index += 1
            continue
        start = index
        end = start
        notional = 0.0
        signed_effort = 0.0
        takes: list[v24.PoolTake] = []
        while end < len(data) and end < start + MAX_BUCKET_BARS:
            row = data.iloc[end]
            minute_notional = max(finite(row["notional_60s"]), 0.0)
            minute_flow = finite(row["flow_60s"])
            if math.isfinite(minute_notional):
                notional += minute_notional
            if math.isfinite(minute_flow) and math.isfinite(minute_notional):
                signed_effort += minute_flow * minute_notional
            takes.extend(external_takes.get(end, ()))
            if notional >= target:
                break
            end += 1
        if end >= len(data):
            break
        if notional <= 0.0:
            index = end + 1
            continue
        start_price_index = max(start - 1, 0)
        start_price = finite(close.iloc[start_price_index])
        end_price = finite(close.iloc[end])
        high = finite(data["high"].iloc[start : end + 1].max())
        low = finite(data["low"].iloc[start : end + 1].min())
        path = finite(one_minute_path.iloc[start : end + 1].sum())
        imbalance = signed_effort / notional
        side = 1 if imbalance > 0.0 else -1 if imbalance < 0.0 else 0
        if all(
            math.isfinite(value)
            for value in (start_price, end_price, high, low, path, imbalance)
        ) and start_price > 0.0 and side != 0:
            return_bps = (end_price / start_price - 1.0) * 10_000.0
            directional_return = side * return_bps
            efficiency = abs(return_bps) / path if path > 0.0 else 0.0
            impact_ratio = abs(return_bps) / max(abs(imbalance), 1e-9)
            oi_before = _oi(data, start_price_index)
            oi_end = _oi(data, end)
            oi_change = (
                oi_end / oi_before - 1.0
                if oi_before > 0.0 and oi_end > 0.0
                else float("nan")
            )
            basis_before = _basis(data, start_price_index)
            basis_end = _basis(data, end)
            directional_basis = (
                side * (basis_end - basis_before)
                if math.isfinite(basis_before) and math.isfinite(basis_end)
                else float("nan")
            )
            bucket_id += 1
            buckets.append(
                VolumeBucket(
                    bucket_id=bucket_id,
                    start_index=start,
                    end_index=end,
                    target_notional=target,
                    notional=notional,
                    signed_effort=signed_effort,
                    imbalance=imbalance,
                    side=side,
                    start_price=start_price,
                    close=end_price,
                    high=high,
                    low=low,
                    return_bps=return_bps,
                    directional_return_bps=directional_return,
                    path_bps=path,
                    efficiency=efficiency,
                    impact_ratio=impact_ratio,
                    oi_before=oi_before,
                    oi_end=oi_end,
                    oi_change=oi_change,
                    basis_before=basis_before,
                    basis_end=basis_end,
                    directional_basis_change_bps=directional_basis,
                    external_takes=tuple(takes),
                )
            )
        index = end + 1
    return buckets


def material_inventory_route(
    oi_change: float,
    cutoff: float,
) -> str | None:
    if not all(math.isfinite(value) for value in (oi_change, cutoff)):
        return None
    if cutoff <= 0.0:
        return None
    if oi_change >= cutoff:
        return "NEW_INVENTORY"
    if oi_change <= -cutoff:
        return "LIQUIDATION"
    return None


def matching_external_take(bucket: VolumeBucket) -> v24.PoolTake | None:
    matches = [
        take
        for take in bucket.external_takes
        if int(take.pool_side) == int(bucket.side)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: (
            item.age_bars,
            item.prominence_atr,
            item.touches,
        ),
    )


def classify_bucket(
    bucket: VolumeBucket,
    thresholds: BucketThresholds,
) -> BucketState | None:
    values = (
        abs(bucket.imbalance),
        bucket.directional_return_bps,
        bucket.efficiency,
        bucket.impact_ratio,
        bucket.directional_basis_change_bps,
        bucket.oi_change,
        thresholds.imbalance_q75,
        thresholds.absolute_return_q65,
        thresholds.efficiency_q60,
        thresholds.impact_q25,
        thresholds.impact_q60,
        thresholds.positive_oi_median,
    )
    if not all(math.isfinite(value) for value in values):
        return None
    route = material_inventory_route(
        bucket.oi_change,
        thresholds.positive_oi_median,
    )
    if route is None:
        return None
    high_imbalance = abs(bucket.imbalance) >= thresholds.imbalance_q75
    if not high_imbalance:
        return None
    informed = (
        bucket.directional_return_bps >= thresholds.absolute_return_q65
        and bucket.efficiency >= thresholds.efficiency_q60
        and bucket.impact_ratio >= thresholds.impact_q60
        and bucket.directional_basis_change_bps > 0.0
        and route == "NEW_INVENTORY"
    )
    if informed:
        return BucketState(
            bucket=bucket,
            thresholds=thresholds,
            state="INFORMED_NEW_INVENTORY",
            inventory_route=route,
        )
    take = matching_external_take(bucket)
    absorbed = (
        take is not None
        and bucket.impact_ratio <= thresholds.impact_q25
        and bucket.directional_return_bps >= 0.0
    )
    if absorbed:
        return BucketState(
            bucket=bucket,
            thresholds=thresholds,
            state="EXTERNAL_POOL_ABSORPTION",
            pool_take=take,
            inventory_route=route,
        )
    return None


def pullback_retains_displacement(
    shock: VolumeBucket,
    pullback: VolumeBucket,
) -> bool:
    displacement = shock.close - shock.start_price
    if shock.side * displacement <= 0.0:
        return False
    retained = pullback.close - shock.start_price
    return shock.side * retained >= PULLBACK_RETAIN_FRACTION * abs(displacement)


def weak_counter_flow(
    shock: VolumeBucket,
    pullback: VolumeBucket,
) -> bool:
    counter = -shock.side * pullback.imbalance
    if counter <= 0.0:
        return abs(pullback.imbalance) <= abs(shock.imbalance)
    return counter <= MAX_COUNTER_IMBALANCE_FRACTION * abs(shock.imbalance)


def oi_creation_retained(shock: VolumeBucket, later: VolumeBucket) -> bool:
    return bool(
        math.isfinite(later.oi_end)
        and math.isfinite(shock.oi_end)
        and later.oi_end >= OI_RETENTION * shock.oi_end
    )


def exact_pool_reclaimed(take: v24.PoolTake, close: float) -> bool:
    return v24.pool_is_reclaimed(take, close)


def route_inventory_resolved(
    route: str,
    shock: VolumeBucket,
    later: VolumeBucket,
) -> bool:
    if not all(math.isfinite(value) for value in (shock.oi_end, later.oi_end)):
        return False
    if route == "NEW_INVENTORY":
        return later.oi_end < shock.oi_end
    if route == "LIQUIDATION":
        return later.oi_end <= OI_REBUILD_TOLERANCE * shock.oi_end
    return False


def _stop_buffer(impact_parameters: Any) -> float:
    value = getattr(impact_parameters, "stop_buffer_atr", None)
    if value is None:
        value = getattr(impact_parameters, "sweep_stop_buffer_atr", None)
    if value is None:
        raise AttributeError("impact configuration has no stop buffer")
    return float(value)


def structural_stop(
    data: pd.DataFrame,
    start_index: int,
    end_index: int,
    side: int,
    impact_parameters: Any,
) -> float:
    segment = data.iloc[start_index : end_index + 1]
    atr = finite(data["atr"].iloc[end_index])
    if segment.empty or not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    extreme = float(
        segment["low"].min()
        if side > 0
        else segment["high"].max()
    )
    return extreme - side * _stop_buffer(impact_parameters) * atr


def _common_details(
    state: BucketState,
    confirmation: VolumeBucket,
    outcome: str,
) -> dict[str, Any]:
    shock = state.bucket
    details: dict[str, Any] = {
        "shock_bucket_id": shock.bucket_id,
        "shock_start_index": shock.start_index,
        "shock_end_index": shock.end_index,
        "shock_target_notional": shock.target_notional,
        "shock_realized_notional": shock.notional,
        "shock_imbalance": shock.imbalance,
        "shock_directional_return_bps": shock.directional_return_bps,
        "shock_path_bps": shock.path_bps,
        "shock_efficiency": shock.efficiency,
        "shock_impact_ratio": shock.impact_ratio,
        "shock_oi_change": shock.oi_change,
        "shock_directional_basis_change_bps": shock.directional_basis_change_bps,
        "past_only_imbalance_q75": state.thresholds.imbalance_q75,
        "past_only_absolute_return_q65": state.thresholds.absolute_return_q65,
        "past_only_efficiency_q60": state.thresholds.efficiency_q60,
        "past_only_impact_q25": state.thresholds.impact_q25,
        "past_only_impact_q60": state.thresholds.impact_q60,
        "past_only_positive_oi_median": state.thresholds.positive_oi_median,
        "inventory_route": state.inventory_route,
        "confirmation_bucket_id": confirmation.bucket_id,
        "confirmation_start_index": confirmation.start_index,
        "confirmation_end_index": confirmation.end_index,
        "confirmation_imbalance": confirmation.imbalance,
        "confirmation_return_bps": confirmation.return_bps,
        "confirmation_directional_basis_change_bps": (
            confirmation.directional_basis_change_bps
        ),
        "auction_outcome": outcome,
        "compiler": "candidate-04-volume-clock-impact-residual",
    }
    if state.pool_take is not None:
        details.update(
            {
                "external_pool_id": state.pool_take.pool_id,
                "external_pool_side": state.pool_take.pool_side,
                "external_pool_level": state.pool_take.level,
                "external_pool_age_bars": state.pool_take.age_bars,
                "external_pool_prominence_atr": (
                    state.pool_take.prominence_atr
                ),
            }
        )
    return details


def resolve_informed_continuation(
    data: pd.DataFrame,
    buckets: list[VolumeBucket],
    position: int,
    state: BucketState,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[Intent | None, int]:
    shock = state.bucket
    if position + 2 >= len(buckets):
        return None, position
    pullback = buckets[position + 1]
    if data.index[pullback.end_index] > evaluation_end:
        return None, position + 1
    if not (
        weak_counter_flow(shock, pullback)
        and pullback_retains_displacement(shock, pullback)
        and oi_creation_retained(shock, pullback)
    ):
        return None, position + 1
    structure = pullback.high if shock.side > 0 else pullback.low
    upper = min(position + 1 + RESUMPTION_BUCKETS, len(buckets) - 1)
    for candidate_position in range(position + 2, upper + 1):
        resume = buckets[candidate_position]
        if data.index[resume.end_index] > evaluation_end:
            return None, candidate_position
        structure_broken = (
            resume.close > structure
            if shock.side > 0
            else resume.close < structure
        )
        aligned = (
            shock.side * resume.imbalance
            >= max(0.0, state.thresholds.imbalance_q50)
            and shock.side * resume.return_bps > 0.0
            and shock.side * (resume.basis_end - resume.basis_before) > 0.0
        )
        if not (
            structure_broken
            and aligned
            and oi_creation_retained(shock, resume)
        ):
            continue
        stop = structural_stop(
            data,
            pullback.start_index,
            resume.end_index,
            shock.side,
            impact_parameters,
        )
        if not math.isfinite(stop) or shock.side * (resume.close - stop) <= 0.0:
            continue
        details = {
            **_common_details(
                state,
                resume,
                "INFORMED_INVENTORY_PULLBACK_HELD_AND_RESUMED",
            ),
            "pullback_bucket_id": pullback.bucket_id,
            "pullback_start_index": pullback.start_index,
            "pullback_end_index": pullback.end_index,
            "pullback_imbalance": pullback.imbalance,
            "pullback_close": pullback.close,
            "pullback_structure": structure,
            "oi_at_pullback": pullback.oi_end,
            "oi_at_resumption": resume.oi_end,
        }
        return (
            Intent(
                scenario=INFORMED_CONTINUATION,
                side=shock.side,
                signal_index=resume.end_index,
                entry_index=resume.end_index + 1,
                stop_level=stop,
                event_indices=(
                    shock.start_index,
                    shock.end_index,
                    pullback.start_index,
                    pullback.end_index,
                    resume.start_index,
                    resume.end_index,
                ),
                details=details,
            ),
            candidate_position,
        )
    return None, upper


def resolve_absorption_reversal(
    data: pd.DataFrame,
    buckets: list[VolumeBucket],
    position: int,
    state: BucketState,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[Intent | None, int]:
    shock = state.bucket
    take = state.pool_take
    if take is None:
        return None, position
    upper = min(position + RECLAIM_BUCKETS, len(buckets) - 1)
    for candidate_position in range(position + 1, upper + 1):
        reclaim = buckets[candidate_position]
        if data.index[reclaim.end_index] > evaluation_end:
            return None, candidate_position
        trade_side = -shock.side
        aligned = (
            trade_side * reclaim.imbalance > 0.0
            and trade_side * reclaim.return_bps > 0.0
            and trade_side * (reclaim.basis_end - reclaim.basis_before) > 0.0
        )
        if not (
            exact_pool_reclaimed(take, reclaim.close)
            and aligned
            and route_inventory_resolved(
                str(state.inventory_route),
                shock,
                reclaim,
            )
        ):
            continue
        stop = structural_stop(
            data,
            shock.start_index,
            reclaim.end_index,
            trade_side,
            impact_parameters,
        )
        if not math.isfinite(stop) or trade_side * (reclaim.close - stop) <= 0.0:
            continue
        if state.inventory_route == "NEW_INVENTORY":
            scenario = TRAPPED_REVERSAL
            outcome = "LOW_IMPACT_NEW_INVENTORY_TRAPPED_AND_UNWOUND"
        else:
            scenario = LIQUIDATION_REVERSAL
            outcome = "LOW_IMPACT_LIQUIDATION_EXHAUSTED_AND_RECLAIMED"
        details = {
            **_common_details(state, reclaim, outcome),
            "oi_at_reclaim": reclaim.oi_end,
            "reclaim_close": reclaim.close,
        }
        return (
            Intent(
                scenario=scenario,
                side=trade_side,
                signal_index=reclaim.end_index,
                entry_index=reclaim.end_index + 1,
                stop_level=stop,
                event_indices=(
                    shock.start_index,
                    shock.end_index,
                    reclaim.start_index,
                    reclaim.end_index,
                ),
                details=details,
            ),
            candidate_position,
        )
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
    buckets = build_volume_buckets(data, takes)
    intents: list[Intent] = []
    history: list[VolumeBucket] = []
    counts = {
        "volume_buckets": len(buckets),
        "buckets_before_history_ready": 0,
        "unclassified_buckets": 0,
        "informed_new_inventory_states": 0,
        "external_pool_absorption_states": 0,
        "informed_continuations": 0,
        "trapped_inventory_reversals": 0,
        "liquidation_reversals": 0,
        "unresolved_informed_states": 0,
        "unresolved_absorption_states": 0,
    }
    position = 0
    while position < len(buckets):
        bucket = buckets[position]
        timestamp = data.index[bucket.end_index]
        thresholds = bucket_thresholds(history)
        history.append(bucket)
        if timestamp < evaluation_start:
            position += 1
            continue
        if timestamp > evaluation_end:
            break
        if thresholds is None:
            counts["buckets_before_history_ready"] += 1
            position += 1
            continue
        state = classify_bucket(bucket, thresholds)
        if state is None:
            counts["unclassified_buckets"] += 1
            position += 1
            continue
        if state.state == "INFORMED_NEW_INVENTORY":
            counts["informed_new_inventory_states"] += 1
            intent, resolved_position = resolve_informed_continuation(
                data,
                buckets,
                position,
                state,
                evaluation_end,
                impact_parameters,
            )
            if intent is None:
                counts["unresolved_informed_states"] += 1
            else:
                intents.append(intent)
                counts["informed_continuations"] += 1
        else:
            counts["external_pool_absorption_states"] += 1
            intent, resolved_position = resolve_absorption_reversal(
                data,
                buckets,
                position,
                state,
                evaluation_end,
                impact_parameters,
            )
            if intent is None:
                counts["unresolved_absorption_states"] += 1
            else:
                intents.append(intent)
                if intent.scenario == TRAPPED_REVERSAL:
                    counts["trapped_inventory_reversals"] += 1
                else:
                    counts["liquidation_reversals"] += 1
        position = max(position + 1, resolved_position + 1)

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
        "candidate": "candidate-04-v37-volume-clock-impact-residual",
        "compiler": "candidate-04-volume-clock-impact-residual",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicates,
        "route_counts": counts,
        "scenario_contract": {
            "clock": (
                "bucket notional target frozen from shifted past-only median "
                "completed five-minute notional"
            ),
            "informed": (
                "tail imbalance, efficient high impact, basis alignment and "
                "material OI creation"
            ),
            "continuation": (
                "separate weak pullback bucket plus later independent resumption "
                "bucket with retained OI"
            ),
            "absorption": (
                "causal external pool take with lower-tail realized impact and "
                "material OI route"
            ),
            "reversal": (
                "separate exact-pool reclaim bucket with opposite flow return "
                "basis and inventory resolution"
            ),
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
