"""Multi-level common spot-perpetual accepted breakout for candidate-02 v94.

The candidate applies the one v93 state which worked to a causal registry of
pre-existing structural liquidity levels:

* highs/lows of every completed four-hour range,
* highs/lows of every completed eight-hour range,
* the previous completed UTC-day high/low.

A level is available only after its source range closes, expires after a fixed
structural lifetime, and is consumed on its first breach.  A trade requires
perpetual and basis-adjusted spot acceptance beyond the breached level, limited
basis expansion, same-direction displacement with a three-candle fair-value
gap, and a later retest which remains on the accepted side.  The objective is
the nearest intact external pivot known before the event.

This module emits deterministic trade intents only. NautilusTrader owns every
order, fill, fee, position and account-NAV transition.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

UTC = "UTC"
NS_MINUTE = 60_000_000_000
LEVEL_MODES = {"ALL", "FOUR_HOUR", "EIGHT_HOUR", "PREVIOUS_DAY"}
SOURCE_SPEC = {
    "FOUR_HOUR": (240, 24 * 60),
    "EIGHT_HOUR": (480, 48 * 60),
    "PREVIOUS_DAY": (1440, 72 * 60),
}


@dataclass(frozen=True, slots=True)
class MultiLevelBreakoutConfig:
    level_mode: str = "ALL"
    prior_window_minutes: int = 2880
    prior_minimum_minutes: int = 720
    turnover_quantile: float = 0.50
    atr_lookback_minutes: int = 60
    level_merge_atr: float = 0.10
    minimum_level_breach_atr: float = 0.02
    maximum_event_extension_atr: float = 1.50
    classification_minutes: int = 3
    minimum_outside_closes: int = 2
    minimum_acceptance_atr: float = 0.03
    minimum_spot_acceptance_ratio: float = 0.25
    maximum_basis_expansion_share: float = 0.75
    displacement_minutes: int = 6
    displacement_body_quantile: float = 0.55
    minimum_displacement_body_atr: float = 0.20
    minimum_fvg_atr: float = 0.01
    retrace_minutes: int = 20
    minimum_retrace_flow_alignment: float = -0.15
    invalidation_inside_atr: float = 0.10
    pivot_bar_minutes: int = 5
    pivot_radius_bars: int = 2
    target_lookback_minutes: int = 2880
    minimum_target_cost_after_rr: float = 1.10
    maximum_target_cost_after_rr: float = 1000.0
    cooldown_minutes: int = 20
    maximum_holding_minutes: int = 180

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "MultiLevelBreakoutConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v94 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.level_mode not in LEVEL_MODES:
            raise ValueError(f"unknown v94 level mode: {self.level_mode}")
        if self.prior_window_minutes < 1440 or self.prior_minimum_minutes < 360:
            raise ValueError("insufficient shifted history")
        if not 0.0 < self.turnover_quantile < 1.0:
            raise ValueError("invalid turnover quantile")
        if self.atr_lookback_minutes < 30:
            raise ValueError("ATR history too short")
        if not 0.0 <= self.level_merge_atr <= 0.5:
            raise ValueError("invalid level merge tolerance")
        if not 0.0 <= self.minimum_level_breach_atr < self.maximum_event_extension_atr:
            raise ValueError("invalid level-break geometry")
        if self.classification_minutes not in {2, 3, 4, 5}:
            raise ValueError("invalid acceptance horizon")
        if not 1 <= self.minimum_outside_closes <= self.classification_minutes + 1:
            raise ValueError("invalid outside-close count")
        if self.minimum_acceptance_atr < 0.0:
            raise ValueError("negative acceptance floor")
        if not 0.0 <= self.minimum_spot_acceptance_ratio <= 2.0:
            raise ValueError("invalid spot acceptance ratio")
        if not 0.0 <= self.maximum_basis_expansion_share <= 2.0:
            raise ValueError("invalid basis expansion share")
        if self.displacement_minutes not in {3, 4, 5, 6, 7, 8}:
            raise ValueError("invalid displacement horizon")
        if not 0.0 < self.displacement_body_quantile < 1.0:
            raise ValueError("invalid body quantile")
        if self.minimum_displacement_body_atr <= 0.0:
            raise ValueError("invalid displacement floor")
        if not 0.0 <= self.minimum_fvg_atr <= 0.25:
            raise ValueError("invalid FVG floor")
        if not 5 <= self.retrace_minutes <= 60:
            raise ValueError("invalid retest window")
        if not -1.0 < self.minimum_retrace_flow_alignment < 1.0:
            raise ValueError("invalid retest flow floor")
        if self.invalidation_inside_atr <= 0.0:
            raise ValueError("invalid old-level invalidation")
        if self.pivot_bar_minutes not in {3, 5, 10, 15}:
            raise ValueError("invalid pivot bar")
        if self.pivot_radius_bars not in {1, 2, 3}:
            raise ValueError("invalid pivot radius")
        if self.target_lookback_minutes < 1440:
            raise ValueError("target history too short")
        if not 0.0 < self.minimum_target_cost_after_rr <= self.maximum_target_cost_after_rr:
            raise ValueError("invalid target reward/risk band")
        if self.cooldown_minutes < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid timing")


@dataclass(frozen=True, slots=True)
class StructuralLevel:
    level_id: str
    source: str
    side: str
    price: float
    available_time_ns: int
    expiry_time_ns: int


def _normalise_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_index()
    if result.index.tz is None:
        result.index = result.index.tz_localize(UTC)
    else:
        result.index = result.index.tz_convert(UTC)
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and increasing")
    return result


def _normalise_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


def build_state(features: pd.DataFrame, config: MultiLevelBreakoutConfig) -> pd.DataFrame:
    required = {
        "close",
        "aggressive_total_quote_1m",
        "signed_flow_ratio_1m",
        "spot_open",
        "spot_high",
        "spot_low",
        "spot_close",
        "spot_signed_flow_ratio_1m",
        "perp_spot_log_basis",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v94 missing completed-minute features: {missing}")
    x = _normalise_index(features)
    x["turnover_threshold"] = (
        x["aggressive_total_quote_1m"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.turnover_quantile)
        .shift(1)
    )
    return x


def _true_range(raw: pd.DataFrame) -> pd.Series:
    previous_close = raw["close"].shift(1)
    return pd.concat(
        [
            raw["high"] - raw["low"],
            (raw["high"] - previous_close).abs(),
            (raw["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _finite(row: pd.Series, names: Sequence[str]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _enabled_sources(mode: str) -> set[str]:
    if mode == "ALL":
        return set(SOURCE_SPEC)
    return {mode}


def _generate_levels(raw: pd.DataFrame, config: MultiLevelBreakoutConfig) -> list[StructuralLevel]:
    output: list[StructuralLevel] = []
    enabled = _enabled_sources(config.level_mode)
    for source, (minutes, expiry_minutes) in SOURCE_SPEC.items():
        if source not in enabled:
            continue
        bars = raw.resample(
            f"{minutes}min",
            origin="start_day",
            label="right",
            closed="right",
        ).agg({"high": "max", "low": "min", "close": "count"})
        bars.rename(columns={"close": "count"}, inplace=True)
        bars.dropna(subset=["high", "low"], inplace=True)
        bars = bars.loc[bars["count"] >= minutes - 2]
        for anchor, row in bars.iterrows():
            available_ns = int(pd.Timestamp(anchor).value)
            expiry_ns = int((pd.Timestamp(anchor) + pd.Timedelta(minutes=expiry_minutes)).value)
            high = float(row["high"])
            low = float(row["low"])
            if not (math.isfinite(high) and math.isfinite(low) and high > low > 0.0):
                continue
            token = pd.Timestamp(anchor).isoformat()
            output.append(
                StructuralLevel(
                    level_id=f"{source}:{token}:HIGH",
                    source=source,
                    side="HIGH",
                    price=high,
                    available_time_ns=available_ns,
                    expiry_time_ns=expiry_ns,
                )
            )
            output.append(
                StructuralLevel(
                    level_id=f"{source}:{token}:LOW",
                    source=source,
                    side="LOW",
                    price=low,
                    available_time_ns=available_ns,
                    expiry_time_ns=expiry_ns,
                )
            )
    output.sort(key=lambda level: (level.available_time_ns, level.source, level.side, level.price))
    return output


def _cluster_breached(
    levels: Sequence[StructuralLevel],
    *,
    direction: int,
    tolerance: float,
) -> list[list[StructuralLevel]]:
    ordered = sorted(levels, key=lambda level: level.price)
    clusters: list[list[StructuralLevel]] = []
    for level in ordered:
        if not clusters or abs(level.price - clusters[-1][-1].price) > tolerance:
            clusters.append([level])
        else:
            clusters[-1].append(level)
    return clusters


def _completed_bars(
    raw: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    minutes: int,
) -> pd.DataFrame:
    view = raw.loc[(raw.index > start) & (raw.index <= end), ["open", "high", "low", "close"]]
    if view.empty:
        return view
    bars = view.resample(
        f"{minutes}min",
        origin="start_day",
        label="right",
        closed="right",
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    return bars.dropna()


def _intact_pivots(bars: pd.DataFrame, radius: int) -> tuple[list[float], list[float]]:
    if len(bars) < 2 * radius + 3:
        return [], []
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    intact_highs: list[float] = []
    intact_lows: list[float] = []
    for i in range(radius, len(bars) - radius):
        high = highs[i]
        low = lows[i]
        left_high = float(np.max(highs[i - radius : i]))
        right_high = float(np.max(highs[i + 1 : i + radius + 1]))
        left_low = float(np.min(lows[i - radius : i]))
        right_low = float(np.min(lows[i + 1 : i + radius + 1]))
        confirmation = i + radius
        later_closes = closes[confirmation + 1 :]
        is_high = high >= left_high and high >= right_high and (high > left_high or high > right_high)
        is_low = low <= left_low and low <= right_low and (low < left_low or low < right_low)
        if is_high and not np.any(later_closes > high):
            intact_highs.append(float(high))
        if is_low and not np.any(later_closes < low):
            intact_lows.append(float(low))
    return sorted(set(intact_highs)), sorted(set(intact_lows))


def _select_target(
    *,
    levels: Sequence[float],
    side: str,
    entry: float,
    stop: float,
    costs: CostConfig,
    minimum_rr: float,
    maximum_rr: float,
) -> tuple[float, float] | None:
    if side == "BUY":
        candidates = sorted({float(level) for level in levels if float(level) > entry})
    else:
        candidates = sorted({float(level) for level in levels if float(level) < entry}, reverse=True)
    for target in candidates:
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            continue
        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if math.isfinite(rr) and minimum_rr <= rr <= maximum_rr:
            return target, rr
    return None


def _find_displacement(
    *,
    x: pd.DataFrame,
    event_position: int,
    boundary: float,
    direction: int,
    config: MultiLevelBreakoutConfig,
) -> tuple[int, float, float] | None:
    end_position = min(event_position + config.displacement_minutes, len(x) - 1)
    for position in range(max(event_position, 2), end_position + 1):
        row = x.iloc[position]
        if not _finite(row, ("raw_open", "raw_high", "raw_low", "raw_close", "body", "body_threshold", "atr")):
            continue
        atr = float(row["atr"])
        body_floor = max(float(row["body_threshold"]), config.minimum_displacement_body_atr * atr)
        directional_body = direction * (float(row["raw_close"]) - float(row["raw_open"]))
        if directional_body <= 0.0 or float(row["body"]) < body_floor:
            continue
        if direction > 0 and float(row["raw_close"]) <= boundary:
            continue
        if direction < 0 and float(row["raw_close"]) >= boundary:
            continue
        two_back = x.iloc[position - 2]
        minimum_gap = config.minimum_fvg_atr * atr
        if direction > 0:
            fvg_low = float(two_back["raw_high"])
            fvg_high = float(row["raw_low"])
        else:
            fvg_low = float(row["raw_high"])
            fvg_high = float(two_back["raw_low"])
        if fvg_high - fvg_low < minimum_gap:
            continue
        return position, fvg_low, fvg_high
    return None


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: MultiLevelBreakoutConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalise_timestamp(pd.Timestamp(evaluation_start))
    end = _normalise_timestamp(pd.Timestamp(evaluation_end))
    if end <= start:
        raise ValueError("evaluation end must be after start")

    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
    x = state.join(
        raw_view.rename(
            columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
        ),
        how="inner",
    )
    x["atr"] = _true_range(raw_view).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median().shift(1).reindex(x.index)
    x["body"] = (x["raw_close"] - x["raw_open"]).abs()
    x["body_threshold"] = (
        x["body"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.displacement_body_quantile)
        .shift(1)
    )
    levels = _generate_levels(raw_view, config)
    consumed: set[str] = set()
    # Levels created during warm-up remain eligible only when their liquidity
    # has not already been touched before the evaluation begins.
    start_ns = int(start.value)
    for level in levels:
        if not (level.available_time_ns < start_ns <= level.expiry_time_ns):
            continue
        history = raw_view.loc[
            (raw_view.index.asi8 > level.available_time_ns)
            & (raw_view.index.asi8 < start_ns)
        ]
        if history.empty:
            continue
        touched = (
            float(history["high"].max()) >= level.price
            if level.side == "HIGH"
            else float(history["low"].min()) <= level.price
        )
        if touched:
            consumed.add(level.level_id)
    signals: list[RotationSignal] = []
    used_observed_times: set[int] = set()
    cooldown_until = -1
    event_fields = (
        "raw_high", "raw_low", "raw_close", "atr",
        "aggressive_total_quote_1m", "turnover_threshold",
        "spot_close", "perp_spot_log_basis",
    )

    evaluation_positions = [
        int(x.index.get_loc(ts))
        for ts in x.loc[(x.index >= start) & (x.index < end)].index
    ]
    for event_position in evaluation_positions:
        if event_position < 1:
            continue
        event_ts = pd.Timestamp(x.index[event_position])
        event_ns = int(event_ts.value)
        event = x.iloc[event_position]
        previous = x.iloc[event_position - 1]
        if not _finite(event, event_fields) or not _finite(previous, ("raw_close", "perp_spot_log_basis")):
            continue
        atr = float(event["atr"])
        if atr <= 0.0:
            continue
        active = [
            level
            for level in levels
            if level.level_id not in consumed
            and level.available_time_ns < event_ns <= level.expiry_time_ns
        ]
        if not active:
            continue
        previous_close = float(previous["raw_close"])
        upper = [
            level for level in active
            if level.side == "HIGH"
            and previous_close <= level.price
            and float(event["raw_high"]) >= level.price + config.minimum_level_breach_atr * atr
        ]
        lower = [
            level for level in active
            if level.side == "LOW"
            and previous_close >= level.price
            and float(event["raw_low"]) <= level.price - config.minimum_level_breach_atr * atr
        ]
        if upper and lower:
            consumed.update(level.level_id for level in upper + lower)
            continue
        breached = upper or lower
        if not breached:
            continue
        direction = 1 if upper else -1
        consumed.update(level.level_id for level in breached)
        clusters = _cluster_breached(
            breached,
            direction=direction,
            tolerance=config.level_merge_atr * atr,
        )
        cluster = clusters[-1] if direction > 0 else clusters[0]
        boundary = max(level.price for level in cluster) if direction > 0 else min(level.price for level in cluster)
        event_extreme = float(event["raw_high"] if direction > 0 else event["raw_low"])
        extension = direction * (event_extreme - boundary)
        if extension > config.maximum_event_extension_atr * atr:
            continue
        if float(event["aggressive_total_quote_1m"]) < float(event["turnover_threshold"]):
            continue

        classification_end = min(event_position + config.classification_minutes, len(x) - 1)
        segment = x.iloc[event_position : classification_end + 1]
        segment = segment.loc[segment.index < end]
        if len(segment) < config.classification_minutes:
            continue
        outside = segment["raw_close"] > boundary if direction > 0 else segment["raw_close"] < boundary
        last = segment.iloc[-1]
        last_ts = pd.Timestamp(segment.index[-1])
        final_perp = float(last["raw_close"])
        final_spot = float(last["spot_close"])
        pre_basis = float(previous["perp_spot_log_basis"])
        final_basis = float(last["perp_spot_log_basis"])
        spot_boundary = boundary / math.exp(pre_basis)
        final_outside_distance = direction * (final_perp - boundary)
        spot_outside_distance = direction * (final_spot - spot_boundary)
        perp_excess_fraction = max(direction * (final_perp / boundary - 1.0), 1e-12)
        spot_excess_fraction = direction * (final_spot / spot_boundary - 1.0)
        spot_ratio = spot_excess_fraction / perp_excess_fraction
        basis_expansion_share = max(direction * (final_basis - pre_basis), 0.0) / perp_excess_fraction
        accepted = (
            int(outside.sum()) >= config.minimum_outside_closes
            and final_outside_distance >= config.minimum_acceptance_atr * atr
            and spot_outside_distance > 0.0
            and spot_ratio >= config.minimum_spot_acceptance_ratio
            and basis_expansion_share <= config.maximum_basis_expansion_share
        )
        if not accepted:
            continue

        displacement = _find_displacement(
            x=x,
            event_position=event_position,
            boundary=boundary,
            direction=direction,
            config=config,
        )
        if displacement is None:
            continue
        displacement_position, fvg_low, fvg_high = displacement
        displacement_row = x.iloc[displacement_position]
        displacement_ts = pd.Timestamp(x.index[displacement_position])
        retrace_end = min(displacement_position + config.retrace_minutes, len(x) - 1)

        pivot_bars = _completed_bars(
            raw_view,
            start=event_ts - pd.Timedelta(minutes=config.target_lookback_minutes),
            end=event_ts - pd.Timedelta(minutes=1),
            minutes=config.pivot_bar_minutes,
        )
        pivot_highs, pivot_lows = _intact_pivots(pivot_bars, config.pivot_radius_bars)

        for position in range(displacement_position + 1, retrace_end + 1):
            observed = pd.Timestamp(x.index[position])
            if observed >= end:
                break
            row = x.iloc[position]
            if not _finite(row, ("raw_high", "raw_low", "raw_close", "signed_flow_ratio_1m", "atr")):
                continue
            invalidation_level = (
                boundary - config.invalidation_inside_atr * float(row["atr"])
                if direction > 0
                else boundary + config.invalidation_inside_atr * float(row["atr"])
            )
            invalidated = (
                float(row["raw_low"]) <= invalidation_level
                if direction > 0
                else float(row["raw_high"]) >= invalidation_level
            )
            if invalidated:
                break
            touched = float(row["raw_high"]) >= fvg_low and float(row["raw_low"]) <= fvg_high
            if not touched:
                continue
            midpoint = 0.5 * (fvg_low + fvg_high)
            rejected = float(row["raw_close"]) >= midpoint if direction > 0 else float(row["raw_close"]) <= midpoint
            if not rejected:
                continue
            held_outside = float(row["raw_close"]) > boundary if direction > 0 else float(row["raw_close"]) < boundary
            if not held_outside:
                continue
            retrace_flow = direction * float(row["signed_flow_ratio_1m"])
            if retrace_flow < config.minimum_retrace_flow_alignment:
                continue
            observed_ns = int(observed.value)
            if observed_ns <= cooldown_until or observed_ns in used_observed_times:
                continue
            entry = float(row["raw_close"])
            side = "BUY" if direction > 0 else "SELL"
            stop = invalidation_level
            target_levels = pivot_highs if side == "BUY" else pivot_lows
            selected = _select_target(
                levels=target_levels,
                side=side,
                entry=entry,
                stop=stop,
                costs=costs,
                minimum_rr=config.minimum_target_cost_after_rr,
                maximum_rr=config.maximum_target_cost_after_rr,
            )
            if selected is None:
                continue
            target, rr = selected
            displacement_strength = float(displacement_row["body"]) / max(float(displacement_row["atr"]), 1e-12)
            turnover_ratio = float(event["aggressive_total_quote_1m"]) / max(float(event["turnover_threshold"]), 1e-12)
            level_sources = sorted({level.source for level in cluster})
            score = (
                rr
                * max(displacement_strength, 0.0)
                * max(turnover_ratio, 1.0)
                * max(spot_ratio, 0.0)
                / (1.0 + max(basis_expansion_share, 0.0))
            )
            details = {
                "state": "COMMON_SPOT_PERPETUAL_ACCEPTED_BREAKOUT_RETEST",
                "level_mode": config.level_mode,
                "level_boundary": boundary,
                "level_sources": level_sources,
                "level_ids": sorted(level.level_id for level in cluster),
                "level_cluster_size": len(cluster),
                "breakout_direction": "UP" if direction > 0 else "DOWN",
                "event_close_utc": event_ts.isoformat(),
                "event_extension_atr": extension / atr,
                "classification_close_utc": last_ts.isoformat(),
                "outside_close_count": int(outside.sum()),
                "spot_boundary": spot_boundary,
                "spot_acceptance_ratio": spot_ratio,
                "basis_expansion_share": basis_expansion_share,
                "displacement_close_utc": displacement_ts.isoformat(),
                "displacement_body_atr": displacement_strength,
                "fvg_low": fvg_low,
                "fvg_high": fvg_high,
                "fvg_midpoint": midpoint,
                "retest_close_utc": observed.isoformat(),
                "retest_flow_alignment": retrace_flow,
                "invalidation_level": stop,
                "selected_external_pivot": target,
                "selected_target_cost_after_rr": rr,
                "causal_interpretation": "pre-existing structural liquidity was consumed once, spot and perpetual accepted beyond it, and an imbalance retest held the old level as support or resistance before the next intact external pivot",
            }
            signals.append(
                RotationSignal(
                    scenario_id=f"v94-common-breakout-{observed_ns}",
                    observed_time_ns=observed_ns,
                    side=side,
                    entry_reference=entry,
                    stop_price=stop,
                    target_price=target,
                    cost_after_reward_risk=rr,
                    score=float(score),
                    max_hold_minutes=config.maximum_holding_minutes,
                    source_feature_open_time_ns=event_ns - NS_MINUTE,
                    source_feature_available_time_ns=observed_ns,
                    source_max_market_time_ns=observed_ns,
                    details=details,
                )
            )
            used_observed_times.add(observed_ns)
            cooldown_until = observed_ns + config.cooldown_minutes * NS_MINUTE
            break

    signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    unique: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in signals:
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v94")
        unique.append(signal)
    return unique


__all__ = ["MultiLevelBreakoutConfig", "build_state", "build_rotation_signals"]
