"""Causal persistent-liquidity-pool scenario router for candidate-02 v113.

This candidate replaces rolling-window extrema with liquidity pools that must be
formed by multiple *confirmed* swing points.  A swing at time ``t`` is not known
until ``pivot_span_minutes`` completed bars later.  Pools remain latent until
the required independent touches are known, persist until consumed or expired,
and can be swept only after formation.

After a pool is swept, the market—not a fixed entry rule—selects one of two
mutually exclusive state paths:

    REJECTION -> displacement/CHoCH/FVG -> FVG retest -> reversal entry
    ACCEPTANCE -> displacement/BOS/FVG  -> FVG retest -> continuation entry

Every objective is another pool already known at sweep time.  Signal
construction never simulates execution, fills, fees, positions, or NAV;
NautilusTrader remains the sole engine for those responsibilities.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import (
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
class PersistentPoolRouterConfig(LiquiditySweepConfig):
    pivot_span_minutes: int = 3
    pool_min_touch_separation_minutes: int = 5
    pool_max_age_minutes: int = 720
    pool_min_age_after_formation_minutes: int = 3
    acceptance_closes: int = 2
    acceptance_buffer_atr: float = 0.05
    bos_beyond_sweep_atr: float = 0.05

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "PersistentPoolRouterConfig":
        data = dict(values)
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v113 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        LiquiditySweepConfig.__post_init__(self)
        if self.pivot_span_minutes < 1:
            raise ValueError("v113 pivot span must be positive")
        if self.pool_min_touch_separation_minutes <= 0:
            raise ValueError("v113 pool touches must be separated")
        if self.pool_max_age_minutes <= self.pool_min_touch_separation_minutes:
            raise ValueError("v113 pool lifetime is too short")
        if self.pool_min_age_after_formation_minutes < 0:
            raise ValueError("v113 formation age cannot be negative")
        if self.acceptance_closes <= 0:
            raise ValueError("v113 acceptance closes must be positive")
        if self.acceptance_buffer_atr < 0 or self.bos_beyond_sweep_atr < 0:
            raise ValueError("v113 acceptance/BOS buffers cannot be negative")


@dataclass(slots=True)
class _Pool:
    pool_id: int
    kind: str
    touch_prices: list[float] = field(default_factory=list)
    touch_pivot_indices: list[int] = field(default_factory=list)
    touch_confirmation_indices: list[int] = field(default_factory=list)
    active: bool = False
    activated_index: int | None = None
    consumed_index: int | None = None
    expired_index: int | None = None

    @property
    def center(self) -> float:
        return sum(self.touch_prices) / len(self.touch_prices)

    @property
    def boundary(self) -> float:
        if self.kind == "HIGH":
            return max(self.touch_prices)
        return min(self.touch_prices)

    @property
    def width(self) -> float:
        return max(self.touch_prices) - min(self.touch_prices)

    @property
    def touches(self) -> int:
        return len(self.touch_prices)

    @property
    def last_confirmation_index(self) -> int:
        return self.touch_confirmation_indices[-1]

    @property
    def available(self) -> bool:
        return self.active and self.consumed_index is None and self.expired_index is None

    def snapshot(self, raw: pd.DataFrame) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "kind": self.kind,
            "center": self.center,
            "boundary": self.boundary,
            "width": self.width,
            "touches": self.touches,
            "first_touch_time_ns": int(raw.index[self.touch_pivot_indices[0]].value),
            "last_touch_time_ns": int(raw.index[self.touch_pivot_indices[-1]].value),
            "formed_time_ns": (
                int(raw.index[self.activated_index].value)
                if self.activated_index is not None
                else None
            ),
        }


_LAST_DIAGNOSTICS: dict[str, Any] = {"summary": {}, "examples": {}}


def get_last_scenario_diagnostics() -> dict[str, Any]:
    return {
        "summary": dict(_LAST_DIAGNOSTICS.get("summary", {})),
        "examples": {
            str(key): list(values)
            for key, values in dict(_LAST_DIAGNOSTICS.get("examples", {})).items()
        },
    }


def _confirmed_pivots(
    raw: pd.DataFrame,
    *,
    confirmation_index: int,
    span: int,
) -> list[tuple[str, int, float]]:
    """Return pivots that become knowable on this completed bar."""

    pivot_index = confirmation_index - span
    left = pivot_index - span
    right = confirmation_index
    if left < 0:
        return []
    window = raw.iloc[left : right + 1]
    pivot = raw.iloc[pivot_index]
    result: list[tuple[str, int, float]] = []

    high = float(pivot["high"])
    high_max = float(window["high"].max())
    # Keep a deterministic plateau convention: the final equal extreme in the
    # confirmation window owns the pivot.  This prevents duplicate pivots from
    # a single flat top while preserving causality.
    if math.isclose(high, high_max, rel_tol=0.0, abs_tol=1e-12):
        equal_positions = [
            int(position)
            for position, value in enumerate(window["high"].tolist())
            if math.isclose(float(value), high_max, rel_tol=0.0, abs_tol=1e-12)
        ]
        if equal_positions and equal_positions[-1] == span:
            result.append(("HIGH", pivot_index, high))

    low = float(pivot["low"])
    low_min = float(window["low"].min())
    if math.isclose(low, low_min, rel_tol=0.0, abs_tol=1e-12):
        equal_positions = [
            int(position)
            for position, value in enumerate(window["low"].tolist())
            if math.isclose(float(value), low_min, rel_tol=0.0, abs_tol=1e-12)
        ]
        if equal_positions and equal_positions[-1] == span:
            result.append(("LOW", pivot_index, low))
    return result


def _select_target_pool(
    *,
    snapshots: list[dict[str, Any]],
    side: str,
    route: str,
    entry: float,
) -> dict[str, Any] | None:
    if route == "REVERSAL":
        wanted = "HIGH" if side == "BUY" else "LOW"
    elif route == "CONTINUATION":
        wanted = "HIGH" if side == "BUY" else "LOW"
    else:
        raise ValueError(f"unknown v113 route: {route}")

    candidates = [value for value in snapshots if value["kind"] == wanted]
    if side == "BUY":
        candidates = [value for value in candidates if float(value["boundary"]) > entry]
        return min(candidates, key=lambda value: float(value["boundary"])) if candidates else None
    candidates = [value for value in candidates if float(value["boundary"]) < entry]
    return max(candidates, key=lambda value: float(value["boundary"])) if candidates else None


def _directional_fvg(
    *,
    raw: pd.DataFrame,
    third_index: int,
    side: str,
    body: pd.Series,
    bar_range: pd.Series,
    body_threshold: pd.Series,
    range_threshold: pd.Series,
    atr_value: float,
    minimum_gap_atr: float,
) -> dict[str, Any] | None:
    first_index = third_index - 2
    middle_index = third_index - 1
    first = raw.iloc[first_index]
    middle = raw.iloc[middle_index]
    third = raw.iloc[third_index]
    body_limit = float(body_threshold.iloc[middle_index])
    range_limit = float(range_threshold.iloc[middle_index])
    if not all(math.isfinite(value) for value in (body_limit, range_limit)):
        return None
    displaced = (
        float(body.iloc[middle_index]) >= body_limit
        and float(bar_range.iloc[middle_index]) >= range_limit
    )
    if not displaced:
        return None

    if side == "BUY":
        directional = float(middle["close"]) > float(middle["open"])
        gap_lower = float(first["high"])
        gap_upper = float(third["low"])
    elif side == "SELL":
        directional = float(middle["close"]) < float(middle["open"])
        gap_lower = float(third["high"])
        gap_upper = float(first["low"])
    else:
        raise ValueError(f"unknown side: {side}")
    gap_size = gap_upper - gap_lower
    if not directional or gap_size < minimum_gap_atr * atr_value:
        return None
    return {
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


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: PersistentPoolRouterConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    global _LAST_DIAGNOSTICS

    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    if raw.index.has_duplicates or not raw.index.is_monotonic_increasing:
        raise ValueError("v113 raw bars must be unique and increasing")

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

    pools: list[_Pool] = []
    next_pool_id = 1
    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_signals: list[RotationSignal] = []
    end_position = int(raw.index.searchsorted(end, side="left"))

    def record(
        stage: str,
        *,
        side: str | None = None,
        route: str | None = None,
        session: str | None = None,
        event_time: pd.Timestamp | None = None,
        reason: str,
        **values: Any,
    ) -> None:
        counts[stage] += 1
        if side is not None:
            counts[f"{stage}:{side}"] += 1
        if route is not None:
            counts[f"{stage}:{route}"] += 1
        if session is not None:
            counts[f"{stage}:{session}"] += 1
        if len(examples[stage]) < 5:
            examples[stage].append(
                {
                    "side": side,
                    "route": route,
                    "session": session,
                    "event_time_utc": event_time.isoformat() if event_time is not None else None,
                    "reason": reason,
                    **values,
                }
            )

    def add_pivot(
        *,
        kind: str,
        pivot_index: int,
        confirmation_index: int,
        price: float,
        atr_value: float,
    ) -> None:
        nonlocal next_pool_id
        tolerance = config.liquidity_touch_tolerance_atr * atr_value
        matches = [
            pool
            for pool in pools
            if pool.kind == kind
            and pool.consumed_index is None
            and pool.expired_index is None
            and confirmation_index - pool.last_confirmation_index <= config.pool_max_age_minutes
            and pivot_index - pool.touch_pivot_indices[-1] >= config.pool_min_touch_separation_minutes
            and abs(price - pool.center) <= tolerance
        ]
        if matches:
            pool = min(matches, key=lambda value: abs(price - value.center))
            pool.touch_prices.append(price)
            pool.touch_pivot_indices.append(pivot_index)
            pool.touch_confirmation_indices.append(confirmation_index)
        else:
            pool = _Pool(
                pool_id=next_pool_id,
                kind=kind,
                touch_prices=[price],
                touch_pivot_indices=[pivot_index],
                touch_confirmation_indices=[confirmation_index],
            )
            pools.append(pool)
            next_pool_id += 1

        if not pool.active and pool.touches >= config.minimum_liquidity_touches:
            pool.active = True
            pool.activated_index = confirmation_index
            record(
                "LIQUIDITY_POOL_FORMED",
                event_time=raw.index[confirmation_index],
                reason="multiple causally confirmed swing points clustered into a persistent pool",
                pool_id=pool.pool_id,
                kind=pool.kind,
                touches=pool.touches,
                center=pool.center,
                boundary=pool.boundary,
                width_atr=pool.width / max(atr_value, 1e-12),
            )
        elif pool.active:
            record(
                "LIQUIDITY_POOL_REINFORCED",
                event_time=raw.index[confirmation_index],
                reason="an additional independent confirmed swing touched the live pool",
                pool_id=pool.pool_id,
                kind=pool.kind,
                touches=pool.touches,
                boundary=pool.boundary,
            )

    for index in range(len(raw)):
        timestamp = raw.index[index]
        atr_value = float(atr.iloc[index])
        if math.isfinite(atr_value) and atr_value > 0:
            for kind, pivot_index, price in _confirmed_pivots(
                raw,
                confirmation_index=index,
                span=config.pivot_span_minutes,
            ):
                add_pivot(
                    kind=kind,
                    pivot_index=pivot_index,
                    confirmation_index=index,
                    price=price,
                    atr_value=atr_value,
                )

        for pool in pools:
            if (
                pool.consumed_index is None
                and pool.expired_index is None
                and index - pool.last_confirmation_index > config.pool_max_age_minutes
            ):
                pool.expired_index = index
                if pool.active:
                    record(
                        "LIQUIDITY_POOL_EXPIRED",
                        event_time=timestamp,
                        reason="pool aged beyond the fixed structural lifetime without being consumed",
                        pool_id=pool.pool_id,
                        kind=pool.kind,
                        touches=pool.touches,
                    )

        if not start <= timestamp < end or index == 0:
            continue
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue

        eligible = [
            pool
            for pool in pools
            if pool.available
            and pool.activated_index is not None
            and index - pool.activated_index >= config.pool_min_age_after_formation_minutes
        ]
        current = raw.iloc[index]
        previous_close = float(raw.iloc[index - 1]["close"])
        high_crossed = [
            pool
            for pool in eligible
            if pool.kind == "HIGH"
            and previous_close <= pool.boundary + config.reacceptance_buffer_atr * atr_value
            and float(current["high"]) >= pool.boundary + config.sweep_min_atr * atr_value
        ]
        low_crossed = [
            pool
            for pool in eligible
            if pool.kind == "LOW"
            and previous_close >= pool.boundary - config.reacceptance_buffer_atr * atr_value
            and float(current["low"]) <= pool.boundary - config.sweep_min_atr * atr_value
        ]
        if not high_crossed and not low_crossed:
            continue

        for pool in high_crossed + low_crossed:
            pool.consumed_index = index
        if high_crossed and low_crossed:
            record(
                "AMBIGUOUS_TWO_SIDED_POOL_SWEEP",
                session=_session_label(timestamp),
                event_time=timestamp,
                reason="one completed minute consumed both high-side and low-side pools; intrabar sequence is unknowable",
                high_pool_ids=[pool.pool_id for pool in high_crossed],
                low_pool_ids=[pool.pool_id for pool in low_crossed],
            )
            continue

        if high_crossed:
            swept = min(high_crossed, key=lambda pool: pool.boundary)
            pool_kind = "HIGH"
            sweep_extreme = float(current["high"])
            sweep_excursion = (sweep_extreme - swept.boundary) / atr_value
        else:
            swept = max(low_crossed, key=lambda pool: pool.boundary)
            pool_kind = "LOW"
            sweep_extreme = float(current["low"])
            sweep_excursion = (swept.boundary - sweep_extreme) / atr_value
        if len(high_crossed) + len(low_crossed) > 1:
            record(
                "MULTI_POOL_SWEEP",
                session=_session_label(timestamp),
                event_time=timestamp,
                reason="one directional bar consumed more than one pre-observed pool; nearest pool owns the scenario",
                selected_pool_id=swept.pool_id,
                consumed_pool_ids=[pool.pool_id for pool in high_crossed + low_crossed],
            )
        if sweep_excursion > config.sweep_max_atr:
            record(
                "POOL_OVERRUN_WITHOUT_SCENARIO",
                session=_session_label(timestamp),
                event_time=timestamp,
                reason="price traversed too far beyond the pool for a controlled retest scenario",
                pool_id=swept.pool_id,
                pool_kind=pool_kind,
                sweep_excursion_atr=sweep_excursion,
            )
            continue

        session = _session_label(timestamp)
        pool_snapshot = swept.snapshot(raw)
        target_snapshots = [
            pool.snapshot(raw)
            for pool in eligible
            if pool.pool_id != swept.pool_id and pool.consumed_index is None
        ]
        record(
            "PERSISTENT_POOL_SWEPT",
            session=session,
            event_time=timestamp,
            reason="price consumed a pool formed by multiple confirmed independent swings",
            pool_id=swept.pool_id,
            pool_kind=pool_kind,
            touches=swept.touches,
            boundary=swept.boundary,
            width_atr=swept.width / atr_value,
            sweep_excursion_atr=sweep_excursion,
            preobserved_target_pool_count=len(target_snapshots),
        )

        internal = raw.iloc[
            max(0, index - config.internal_structure_minutes) : index
        ]
        if len(internal) < config.internal_structure_minutes:
            continue
        internal_low = float(internal["low"].min())
        internal_high = float(internal["high"].max())
        total_range = max(float(current["high"] - current["low"]), 1e-12)
        upper_wick = float(current["high"] - max(current["open"], current["close"])) / total_range
        lower_wick = float(min(current["open"], current["close"]) - current["low"]) / total_range
        reversal_eligible = (
            float(current["close"]) < swept.boundary
            and upper_wick >= config.minimum_rejection_wick_fraction
            if pool_kind == "HIGH"
            else float(current["close"]) > swept.boundary
            and lower_wick >= config.minimum_rejection_wick_fraction
        )

        resolution: dict[str, Any] | None = None
        ambiguous_resolution = False
        scan_end = min(
            len(raw) - 1,
            end_position - 1,
            index + config.displacement_window_minutes,
        )
        for third_index in range(index + 2, scan_end + 1):
            reversal_side = "SELL" if pool_kind == "HIGH" else "BUY"
            continuation_side = "BUY" if pool_kind == "HIGH" else "SELL"

            reversal_fvg = None
            if reversal_eligible:
                candidate = _directional_fvg(
                    raw=raw,
                    third_index=third_index,
                    side=reversal_side,
                    body=body,
                    bar_range=bar_range,
                    body_threshold=body_threshold,
                    range_threshold=range_threshold,
                    atr_value=atr_value,
                    minimum_gap_atr=config.fvg_min_atr,
                )
                if candidate is not None:
                    third_close = float(raw.iloc[third_index]["close"])
                    choch = (
                        third_close < internal_low
                        if reversal_side == "SELL"
                        else third_close > internal_high
                    )
                    held_inside = (
                        third_close < swept.boundary
                        if reversal_side == "SELL"
                        else third_close > swept.boundary
                    )
                    if choch and held_inside:
                        reversal_fvg = candidate

            closes = raw.iloc[index + 1 : third_index + 1]["close"]
            if pool_kind == "HIGH":
                accepted_mask = closes > (
                    swept.boundary + config.acceptance_buffer_atr * atr_value
                )
                bos = float(closes.max()) > (
                    sweep_extreme + config.bos_beyond_sweep_atr * atr_value
                )
            else:
                accepted_mask = closes < (
                    swept.boundary - config.acceptance_buffer_atr * atr_value
                )
                bos = float(closes.min()) < (
                    sweep_extreme - config.bos_beyond_sweep_atr * atr_value
                )
            consecutive = 0
            for accepted in reversed(accepted_mask.tolist()):
                if bool(accepted):
                    consecutive += 1
                else:
                    break
            continuation_fvg = None
            if consecutive >= config.acceptance_closes and bos:
                candidate = _directional_fvg(
                    raw=raw,
                    third_index=third_index,
                    side=continuation_side,
                    body=body,
                    bar_range=bar_range,
                    body_threshold=body_threshold,
                    range_threshold=range_threshold,
                    atr_value=atr_value,
                    minimum_gap_atr=config.fvg_min_atr,
                )
                if candidate is not None:
                    third_close = float(raw.iloc[third_index]["close"])
                    held_outside = (
                        third_close > swept.boundary
                        if continuation_side == "BUY"
                        else third_close < swept.boundary
                    )
                    if held_outside:
                        continuation_fvg = candidate

            if reversal_fvg is not None and continuation_fvg is not None:
                ambiguous_resolution = True
                break
            if reversal_fvg is not None:
                resolution = {
                    "route": "REVERSAL",
                    "side": reversal_side,
                    "fvg": reversal_fvg,
                    "resolved_time": raw.index[third_index],
                }
                break
            if continuation_fvg is not None:
                resolution = {
                    "route": "CONTINUATION",
                    "side": continuation_side,
                    "fvg": continuation_fvg,
                    "resolved_time": raw.index[third_index],
                }
                break

        if ambiguous_resolution:
            record(
                "AMBIGUOUS_ROUTE_RESOLUTION",
                session=session,
                event_time=timestamp,
                reason="reversal and continuation paths resolved on the same completed bar",
                pool_id=swept.pool_id,
            )
            continue
        if resolution is None:
            record(
                "NO_ROUTE_RESOLUTION",
                session=session,
                event_time=timestamp,
                reason="swept pool produced neither causal reversal transfer nor accepted continuation transfer",
                pool_id=swept.pool_id,
                reversal_eligible=reversal_eligible,
            )
            continue

        route = str(resolution["route"])
        side = str(resolution["side"])
        fvg = dict(resolution["fvg"])
        record(
            "ROUTE_RESOLVED",
            side=side,
            route=route,
            session=session,
            event_time=timestamp,
            reason=(
                "rejection transferred structure through CHoCH and displacement"
                if route == "REVERSAL"
                else "acceptance transferred structure through BOS and displacement"
            ),
            pool_id=swept.pool_id,
            resolved_time_utc=resolution["resolved_time"].isoformat(),
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
            if route == "REVERSAL" and side == "SELL":
                if float(candidate["close"]) > swept.boundary + config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "reversal closed back above consumed high-side pool"
                    break
                if float(candidate["high"]) > sweep_extreme + config.stop_buffer_atr * atr_value:
                    invalidation_reason = "reversal invalidation beyond sweep extreme occurred before entry"
                    break
                touched = float(candidate["high"]) >= retrace_level
                rejected = float(candidate["close"]) < retrace_level and float(candidate["close"]) < float(candidate["open"])
            elif route == "REVERSAL" and side == "BUY":
                if float(candidate["close"]) < swept.boundary - config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "reversal closed back below consumed low-side pool"
                    break
                if float(candidate["low"]) < sweep_extreme - config.stop_buffer_atr * atr_value:
                    invalidation_reason = "reversal invalidation beyond sweep extreme occurred before entry"
                    break
                touched = float(candidate["low"]) <= retrace_level
                rejected = float(candidate["close"]) > retrace_level and float(candidate["close"]) > float(candidate["open"])
            elif route == "CONTINUATION" and side == "BUY":
                if float(candidate["close"]) < swept.boundary - config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "accepted high-side discovery closed back inside the pool"
                    break
                touched = float(candidate["low"]) <= retrace_level
                rejected = (
                    float(candidate["close"]) > retrace_level
                    and float(candidate["close"]) > float(candidate["open"])
                    and float(candidate["close"]) > swept.boundary
                )
            elif route == "CONTINUATION" and side == "SELL":
                if float(candidate["close"]) > swept.boundary + config.reacceptance_buffer_atr * atr_value:
                    invalidation_reason = "accepted low-side discovery closed back inside the pool"
                    break
                touched = float(candidate["high"]) >= retrace_level
                rejected = (
                    float(candidate["close"]) < retrace_level
                    and float(candidate["close"]) < float(candidate["open"])
                    and float(candidate["close"]) < swept.boundary
                )
            else:
                raise AssertionError("unreachable v113 route/side")
            if touched and rejected:
                retest_index = candidate_index
                break

        if retest_index is None:
            record(
                "ROUTE_INVALIDATED_BEFORE_RETEST" if invalidation_reason else "NO_FVG_RETEST",
                side=side,
                route=route,
                session=session,
                event_time=timestamp,
                reason=invalidation_reason or "resolved route never returned to and rejected its causal FVG",
                pool_id=swept.pool_id,
                retrace_level=retrace_level,
            )
            continue

        activation_index = retest_index + config.activation_delay_minutes
        activation_time = raw.index[activation_index]
        if not start <= activation_time < end:
            continue
        entry = float(raw.iloc[activation_index]["close"])
        if route == "REVERSAL":
            stop = (
                sweep_extreme - config.stop_buffer_atr * atr_value
                if side == "BUY"
                else sweep_extreme + config.stop_buffer_atr * atr_value
            )
        else:
            stop = (
                swept.boundary - config.stop_buffer_atr * atr_value
                if side == "BUY"
                else swept.boundary + config.stop_buffer_atr * atr_value
            )
        target_pool = _select_target_pool(
            snapshots=target_snapshots,
            side=side,
            route=route,
            entry=entry,
        )
        if target_pool is None:
            record(
                "NO_PREOBSERVED_POOL_OBJECTIVE",
                side=side,
                route=route,
                session=session,
                event_time=timestamp,
                reason="no still-unconsumed pool known at sweep time remained ahead of entry",
                pool_id=swept.pool_id,
                entry=entry,
                available_target_pools=len(target_snapshots),
            )
            continue
        target = float(target_pool["boundary"])
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            record(
                "ENTRY_GEOMETRY_REJECTED",
                side=side,
                route=route,
                session=session,
                event_time=timestamp,
                reason="entry was no longer between scenario invalidation and pre-observed pool objective",
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
                route=route,
                session=session,
                event_time=timestamp,
                reason="pre-observed pool path could not pay realistic entry and failure costs",
                entry=entry,
                stop=stop,
                target=target,
                cost_after_reward_risk=rr,
            )
            continue

        retest_time = raw.index[retest_index]
        feature_time, feature_details = _latest_feature(state, retest_time)
        activation_ns = int(activation_time.value)
        details: dict[str, Any] = {
            "scenario": "PERSISTENT_LIQUIDITY_POOL_ROUTER",
            "route": route,
            "state_sequence": (
                [
                    "CONFIRMED_SWINGS_CLUSTERED",
                    "PERSISTENT_POOL_FORMED",
                    "POOL_SWEPT_AND_REJECTED",
                    "DISPLACEMENT_AND_CHOCH",
                    "REVERSAL_FVG",
                    "FVG_RETEST_HELD",
                    "ENTRY_ARMED",
                ]
                if route == "REVERSAL"
                else [
                    "CONFIRMED_SWINGS_CLUSTERED",
                    "PERSISTENT_POOL_FORMED",
                    "POOL_SWEPT",
                    "ACCEPTANCE_BEYOND_POOL",
                    "DISPLACEMENT_AND_BOS",
                    "CONTINUATION_FVG",
                    "FVG_RETEST_HELD",
                    "ENTRY_ARMED",
                ]
            ),
            "swept_pool": pool_snapshot,
            "target_pool": target_pool,
            "sweep_time_ns": int(timestamp.value),
            "sweep_extreme": sweep_extreme,
            "sweep_excursion_atr": sweep_excursion,
            "internal_structure_low": internal_low,
            "internal_structure_high": internal_high,
            "route_resolved_time_ns": int(resolution["resolved_time"].value),
            "displacement_time_ns": int(fvg["displacement_time"].value),
            "fvg_time_ns": int(fvg["fvg_time"].value),
            "fvg_lower": fvg["gap_lower"],
            "fvg_upper": fvg["gap_upper"],
            "fvg_retrace_level": retrace_level,
            "fvg_retest_time_ns": int(retest_time.value),
            "activation_time_ns": activation_ns,
            "structural_invalidation": stop,
            "preobserved_pool_objective": target,
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
            + 0.10 * swept.touches
            + 0.05 * int(target_pool["touches"])
        )
        raw_signals.append(
            RotationSignal(
                scenario_id=(
                    f"v113-persistent-pool-{route.lower()}-{side.lower()}-{activation_ns}"
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
            route=route,
            session=session,
            event_time=timestamp,
            reason="persistent pool, route resolution, FVG retest, invalidation, and target all aligned",
            pool_id=swept.pool_id,
            target_pool_id=int(target_pool["pool_id"]),
            activation_time_utc=activation_time.isoformat(),
            cost_after_reward_risk=rr,
        )

    by_activation: dict[int, RotationSignal] = {}
    for signal in raw_signals:
        prior = by_activation.get(signal.observed_time_ns)
        if prior is None or signal.score > prior.score:
            by_activation[signal.observed_time_ns] = signal
    result = sorted(by_activation.values(), key=lambda value: value.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v113 entry used an incomplete activation bar")
        if signal.observed_time_ns - signal.source_max_market_time_ns != 60_000_000_000:
            raise AssertionError("v113 activation delay is not one completed minute")
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("v113 feature availability contract mismatch")

    pool_counts = Counter(pool.kind for pool in pools if pool.active)
    _LAST_DIAGNOSTICS = {
        "summary": {
            "candidate": "candidate-02-v113-persistent-liquidity-pool-router",
            "detector": "multiple causally confirmed swing points clustered into persistent pools",
            "router": "mutually exclusive rejection-reversal versus acceptance-continuation",
            "counts": dict(sorted(counts.items())),
            "pools_ever_formed": int(sum(pool_counts.values())),
            "high_pools_ever_formed": int(pool_counts.get("HIGH", 0)),
            "low_pools_ever_formed": int(pool_counts.get("LOW", 0)),
            "pool_registry_size": len(pools),
            "signals_emitted": len(result),
            "future_information_used": False,
        },
        "examples": dict(examples),
    }
    return result
