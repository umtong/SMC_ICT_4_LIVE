"""Mature defended swing breakout for candidate-02 v95.

The module deliberately separates level discovery from the trading scenario.
A price is not a tradable liquidity level merely because it is a recent high or
low.  It must pass a causal life-cycle:

1. a completed fifteen-minute swing is confirmed by two later completed bars;
2. the level survives for eight hours without an external breach;
3. price revisits its neighbourhood and is rejected away from it;
4. only the first later external breach may start a breakout scenario.

The breakout scenario then reuses the v93/v94 state which showed partial value:
basis-adjusted spot and perpetual must accept beyond the old level, a same-side
displacement candle must leave a causal three-candle fair-value gap, and a
later completed-minute retest must hold the old level.  The objective is the
nearest intact, already-confirmed fifteen-minute swing in the delivery
direction.  The nearest target is never skipped to manufacture a larger RR;
when that natural target cannot cover trading costs the state is no-trade.

This file emits deterministic trade intents only.  NautilusTrader owns orders,
fills, fees, positions, liquidation logic and account-NAV transitions.
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


@dataclass(frozen=True, slots=True)
class MatureSwingBreakoutConfig:
    prior_window_minutes: int = 2880
    prior_minimum_minutes: int = 720
    turnover_quantile: float = 0.50
    atr_lookback_minutes: int = 60

    swing_bar_minutes: int = 15
    swing_radius_bars: int = 2
    minimum_level_age_minutes: int = 480
    maximum_level_age_minutes: int = 2880
    defense_approach_atr: float = 0.15
    defense_rejection_atr: float = 0.25
    defense_confirmation_minutes: int = 5
    require_defense_memory: bool = True

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

    target_lookback_minutes: int = 2880
    minimum_target_cost_after_rr: float = 0.0
    maximum_target_cost_after_rr: float = 1000.0
    cooldown_minutes: int = 20
    maximum_holding_minutes: int = 180

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "MatureSwingBreakoutConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v95 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.prior_window_minutes < 1440 or self.prior_minimum_minutes < 360:
            raise ValueError("insufficient shifted history")
        if not 0.0 < self.turnover_quantile < 1.0:
            raise ValueError("invalid turnover quantile")
        if self.atr_lookback_minutes < 30:
            raise ValueError("ATR history too short")
        if self.swing_bar_minutes not in {5, 10, 15, 30}:
            raise ValueError("invalid swing bar")
        if self.swing_radius_bars not in {1, 2, 3}:
            raise ValueError("invalid swing radius")
        if self.minimum_level_age_minutes < self.swing_bar_minutes * self.swing_radius_bars:
            raise ValueError("level age must exceed causal swing confirmation")
        if self.maximum_level_age_minutes <= self.minimum_level_age_minutes:
            raise ValueError("invalid level lifetime")
        if not 0.0 <= self.defense_approach_atr <= 1.0:
            raise ValueError("invalid defense approach band")
        if not 0.0 < self.defense_rejection_atr <= 2.0:
            raise ValueError("invalid defense rejection distance")
        if not 1 <= self.defense_confirmation_minutes <= 30:
            raise ValueError("invalid defense confirmation window")
        if not 0.0 <= self.level_merge_atr <= 0.5:
            raise ValueError("invalid level merge tolerance")
        if not 0.0 <= self.minimum_level_breach_atr < self.maximum_event_extension_atr:
            raise ValueError("invalid level-break geometry")
        if self.classification_minutes not in {2, 3, 4, 5}:
            raise ValueError("invalid acceptance horizon")
        if not 1 <= self.minimum_outside_closes <= self.classification_minutes:
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
        if self.target_lookback_minutes < 1440:
            raise ValueError("target history too short")
        if self.minimum_target_cost_after_rr < 0.0:
            raise ValueError("target RR floor cannot be negative")
        if self.maximum_target_cost_after_rr <= self.minimum_target_cost_after_rr:
            raise ValueError("invalid target RR ceiling")
        if self.cooldown_minutes < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid timing")


@dataclass(frozen=True, slots=True)
class SwingLevelCandidate:
    level_id: str
    side: str
    price: float
    pivot_close_ns: int
    confirmation_ns: int
    expiry_ns: int


@dataclass(frozen=True, slots=True)
class MatureSwingLevel:
    level_id: str
    side: str
    price: float
    pivot_close_ns: int
    confirmation_ns: int
    eligibility_ns: int
    expiry_ns: int
    defense_touch_ns: int | None
    defense_confirmation_ns: int | None


def _normalise_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_index()
    if result.index.tz is None:
        result.index = result.index.tz_localize(UTC)
    else:
        result.index = result.index.tz_convert(UTC)
    # pandas may preserve second, millisecond or microsecond resolution.
    # All scenario timestamps are stored as integer nanoseconds for NautilusTrader,
    # so normalize the index resolution before any asi8 comparison.
    if hasattr(result.index, "as_unit"):
        result.index = result.index.as_unit("ns")
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and increasing")
    return result


def _normalise_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


def build_state(features: pd.DataFrame, config: MatureSwingBreakoutConfig) -> pd.DataFrame:
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
        raise ValueError(f"v95 missing completed-minute features: {missing}")
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
    ).agg({"open": "first", "high": "max", "low": "min", "close": ["last", "count"]})
    bars.columns = ["open", "high", "low", "close", "count"]
    bars.dropna(subset=["open", "high", "low", "close"], inplace=True)
    return bars.loc[bars["count"] == minutes, ["open", "high", "low", "close"]]


def _generate_swing_candidates(
    raw: pd.DataFrame,
    *,
    config: MatureSwingBreakoutConfig,
) -> list[SwingLevelCandidate]:
    bars = _completed_bars(
        raw,
        start=pd.Timestamp(raw.index.min()) - pd.Timedelta(minutes=1),
        end=pd.Timestamp(raw.index.max()),
        minutes=config.swing_bar_minutes,
    )
    radius = config.swing_radius_bars
    if len(bars) < 2 * radius + 3:
        return []
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    output: list[SwingLevelCandidate] = []
    for i in range(radius, len(bars) - radius):
        high = float(highs[i])
        low = float(lows[i])
        left_high = float(np.max(highs[i - radius : i]))
        right_high = float(np.max(highs[i + 1 : i + radius + 1]))
        left_low = float(np.min(lows[i - radius : i]))
        right_low = float(np.min(lows[i + 1 : i + radius + 1]))
        pivot_ts = pd.Timestamp(bars.index[i])
        confirmation_ts = pd.Timestamp(bars.index[i + radius])
        expiry_ts = confirmation_ts + pd.Timedelta(minutes=config.maximum_level_age_minutes)
        token = pivot_ts.isoformat()
        is_high = high >= left_high and high >= right_high and (high > left_high or high > right_high)
        is_low = low <= left_low and low <= right_low and (low < left_low or low < right_low)
        if is_high and math.isfinite(high) and high > 0.0:
            output.append(
                SwingLevelCandidate(
                    level_id=f"MATURE_SWING:{token}:HIGH",
                    side="HIGH",
                    price=high,
                    pivot_close_ns=int(pivot_ts.value),
                    confirmation_ns=int(confirmation_ts.value),
                    expiry_ns=int(expiry_ts.value),
                )
            )
        if is_low and math.isfinite(low) and low > 0.0:
            output.append(
                SwingLevelCandidate(
                    level_id=f"MATURE_SWING:{token}:LOW",
                    side="LOW",
                    price=low,
                    pivot_close_ns=int(pivot_ts.value),
                    confirmation_ns=int(confirmation_ts.value),
                    expiry_ns=int(expiry_ts.value),
                )
            )
    output.sort(key=lambda level: (level.confirmation_ns, level.side, level.price))
    return output


def _breached(level: SwingLevelCandidate | MatureSwingLevel, row: pd.Series, atr: float, breach_atr: float) -> bool:
    if level.side == "HIGH":
        return float(row["high"]) >= level.price + breach_atr * atr
    return float(row["low"]) <= level.price - breach_atr * atr


def _qualify_levels(
    raw: pd.DataFrame,
    *,
    candidates: Sequence[SwingLevelCandidate],
    atr: pd.Series,
    config: MatureSwingBreakoutConfig,
) -> list[MatureSwingLevel]:
    frame = _normalise_index(raw[["open", "high", "low", "close"]])
    frame["atr"] = atr.reindex(frame.index)
    index_ns = frame.index.asi8
    output: list[MatureSwingLevel] = []

    for level in candidates:
        earliest_ns = level.confirmation_ns + config.minimum_level_age_minutes * NS_MINUTE
        if earliest_ns >= level.expiry_ns:
            continue
        left = int(np.searchsorted(index_ns, level.confirmation_ns, side="right"))
        right = int(np.searchsorted(index_ns, level.expiry_ns, side="right"))
        earliest = int(np.searchsorted(index_ns, earliest_ns, side="right"))
        dead = False

        # A level cannot become mature if its external liquidity was already
        # taken while it was ageing.
        for position in range(left, min(earliest, right)):
            row = frame.iloc[position]
            atr_value = float(row["atr"])
            if not math.isfinite(atr_value) or atr_value <= 0.0:
                continue
            if _breached(level, row, atr_value, config.minimum_level_breach_atr):
                dead = True
                break
        if dead:
            continue

        if not config.require_defense_memory:
            output.append(
                MatureSwingLevel(
                    level_id=level.level_id,
                    side=level.side,
                    price=level.price,
                    pivot_close_ns=level.pivot_close_ns,
                    confirmation_ns=level.confirmation_ns,
                    eligibility_ns=earliest_ns,
                    expiry_ns=level.expiry_ns,
                    defense_touch_ns=None,
                    defense_confirmation_ns=None,
                )
            )
            continue

        qualified: MatureSwingLevel | None = None
        position = earliest
        while position < right:
            row = frame.iloc[position]
            atr_value = float(row["atr"])
            if not math.isfinite(atr_value) or atr_value <= 0.0:
                position += 1
                continue
            if _breached(level, row, atr_value, config.minimum_level_breach_atr):
                break
            if level.side == "HIGH":
                approached = (
                    float(row["high"]) >= level.price - config.defense_approach_atr * atr_value
                    and float(row["close"]) <= level.price
                )
            else:
                approached = (
                    float(row["low"]) <= level.price + config.defense_approach_atr * atr_value
                    and float(row["close"]) >= level.price
                )
            if not approached:
                position += 1
                continue

            confirm_end = min(position + config.defense_confirmation_minutes - 1, right - 1)
            invalidated = False
            for confirm_position in range(position, confirm_end + 1):
                confirm_row = frame.iloc[confirm_position]
                confirm_atr = float(confirm_row["atr"])
                if not math.isfinite(confirm_atr) or confirm_atr <= 0.0:
                    continue
                if _breached(level, confirm_row, confirm_atr, config.minimum_level_breach_atr):
                    invalidated = True
                    break
                rejected = (
                    float(confirm_row["close"]) <= level.price - config.defense_rejection_atr * confirm_atr
                    if level.side == "HIGH"
                    else float(confirm_row["close"]) >= level.price + config.defense_rejection_atr * confirm_atr
                )
                if rejected:
                    touch_ns = int(index_ns[position])
                    confirmation_ns = int(index_ns[confirm_position])
                    qualified = MatureSwingLevel(
                        level_id=level.level_id,
                        side=level.side,
                        price=level.price,
                        pivot_close_ns=level.pivot_close_ns,
                        confirmation_ns=level.confirmation_ns,
                        eligibility_ns=confirmation_ns,
                        expiry_ns=level.expiry_ns,
                        defense_touch_ns=touch_ns,
                        defense_confirmation_ns=confirmation_ns,
                    )
                    break
            if qualified is not None or invalidated:
                break
            position = confirm_end + 1

        if qualified is not None:
            output.append(qualified)

    output.sort(key=lambda level: (level.eligibility_ns, level.side, level.price))
    return output


def _cluster_breached(levels: Sequence[MatureSwingLevel], *, tolerance: float) -> list[list[MatureSwingLevel]]:
    ordered = sorted(levels, key=lambda level: level.price)
    clusters: list[list[MatureSwingLevel]] = []
    for level in ordered:
        if not clusters or abs(level.price - clusters[-1][-1].price) > tolerance:
            clusters.append([level])
        else:
            clusters[-1].append(level)
    return clusters


def _intact_pivots(bars: pd.DataFrame, radius: int) -> tuple[list[float], list[float]]:
    if len(bars) < 2 * radius + 3:
        return [], []
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    intact_highs: list[float] = []
    intact_lows: list[float] = []
    for i in range(radius, len(bars) - radius):
        high = float(highs[i])
        low = float(lows[i])
        left_high = float(np.max(highs[i - radius : i]))
        right_high = float(np.max(highs[i + 1 : i + radius + 1]))
        left_low = float(np.min(lows[i - radius : i]))
        right_low = float(np.min(lows[i + 1 : i + radius + 1]))
        confirmation = i + radius
        later_highs = highs[confirmation + 1 :]
        later_lows = lows[confirmation + 1 :]
        is_high = high >= left_high and high >= right_high and (high > left_high or high > right_high)
        is_low = low <= left_low and low <= right_low and (low < left_low or low < right_low)
        if is_high and not np.any(later_highs > high):
            intact_highs.append(high)
        if is_low and not np.any(later_lows < low):
            intact_lows.append(low)
    return sorted(set(intact_highs)), sorted(set(intact_lows))


def _select_nearest_target(
    *,
    levels: Sequence[float],
    side: str,
    entry: float,
    stop: float,
    costs: CostConfig,
    minimum_rr: float,
    maximum_rr: float,
) -> tuple[float, float] | None:
    candidates = (
        sorted({float(level) for level in levels if float(level) > entry})
        if side == "BUY"
        else sorted({float(level) for level in levels if float(level) < entry}, reverse=True)
    )
    if not candidates:
        return None
    target = candidates[0]
    geometry = stop < entry < target if side == "BUY" else target < entry < stop
    if not geometry:
        return None
    rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
    if not math.isfinite(rr) or not (rr > minimum_rr and rr <= maximum_rr):
        return None
    return target, rr


def _find_displacement(
    *,
    x: pd.DataFrame,
    event_position: int,
    boundary: float,
    direction: int,
    config: MatureSwingBreakoutConfig,
) -> tuple[int, float, float] | None:
    end_position = min(event_position + config.displacement_minutes - 1, len(x) - 1)
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
    config: MatureSwingBreakoutConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalise_timestamp(evaluation_start)
    end = _normalise_timestamp(evaluation_end)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
    x = state.join(
        raw_view.rename(columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}),
        how="inner",
    )
    atr = _true_range(raw_view).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median().shift(1)
    x["atr"] = atr.reindex(x.index)
    x["body"] = (x["raw_close"] - x["raw_open"]).abs()
    x["body_threshold"] = (
        x["body"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.displacement_body_quantile)
        .shift(1)
    )

    candidates = _generate_swing_candidates(raw_view, config=config)
    levels = _qualify_levels(raw_view, candidates=candidates, atr=atr, config=config)
    consumed: set[str] = set()
    start_ns = int(start.value)
    for level in levels:
        if not (level.eligibility_ns < start_ns <= level.expiry_ns):
            continue
        history = raw_view.loc[
            (raw_view.index.asi8 > level.eligibility_ns) & (raw_view.index.asi8 < start_ns)
        ]
        if history.empty:
            continue
        history_atr = atr.reindex(history.index)
        for position, (_, row) in enumerate(history.iterrows()):
            atr_value = float(history_atr.iloc[position])
            if math.isfinite(atr_value) and atr_value > 0.0 and _breached(
                level, row, atr_value, config.minimum_level_breach_atr
            ):
                consumed.add(level.level_id)
                break

    signals: list[RotationSignal] = []
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
        atr_value = float(event["atr"])
        if atr_value <= 0.0:
            continue
        active = [
            level for level in levels
            if level.level_id not in consumed
            and level.eligibility_ns < event_ns <= level.expiry_ns
        ]
        if not active:
            continue
        previous_close = float(previous["raw_close"])
        upper = [
            level for level in active
            if level.side == "HIGH"
            and previous_close <= level.price
            and float(event["raw_high"]) >= level.price + config.minimum_level_breach_atr * atr_value
        ]
        lower = [
            level for level in active
            if level.side == "LOW"
            and previous_close >= level.price
            and float(event["raw_low"]) <= level.price - config.minimum_level_breach_atr * atr_value
        ]
        if upper and lower:
            consumed.update(level.level_id for level in upper + lower)
            continue
        breached = upper or lower
        if not breached:
            continue
        direction = 1 if upper else -1
        consumed.update(level.level_id for level in breached)
        clusters = _cluster_breached(breached, tolerance=config.level_merge_atr * atr_value)
        cluster = clusters[-1] if direction > 0 else clusters[0]
        boundary = max(level.price for level in cluster) if direction > 0 else min(level.price for level in cluster)
        event_extreme = float(event["raw_high"] if direction > 0 else event["raw_low"])
        extension = direction * (event_extreme - boundary)
        if extension > config.maximum_event_extension_atr * atr_value:
            continue
        if float(event["aggressive_total_quote_1m"]) < float(event["turnover_threshold"]):
            continue

        classification_end = min(event_position + config.classification_minutes - 1, len(x) - 1)
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
            and final_outside_distance >= config.minimum_acceptance_atr * atr_value
            and spot_outside_distance > 0.0
            and spot_ratio >= config.minimum_spot_acceptance_ratio
            and basis_expansion_share <= config.maximum_basis_expansion_share
        )
        if not accepted:
            continue

        displacement = _find_displacement(
            x=x,
            event_position=classification_end,
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

        target_bars = _completed_bars(
            raw_view,
            start=event_ts - pd.Timedelta(minutes=config.target_lookback_minutes),
            end=event_ts - pd.Timedelta(minutes=1),
            minutes=config.swing_bar_minutes,
        )
        pivot_highs, pivot_lows = _intact_pivots(target_bars, config.swing_radius_bars)

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
            entry = float(row["raw_close"])
            side = "BUY" if direction > 0 else "SELL"
            stop = invalidation_level
            path = x.iloc[event_position : position + 1]
            path_extreme = (
                float(path["raw_high"].max()) if side == "BUY" else float(path["raw_low"].min())
            )
            intact_at_entry = (
                [level for level in pivot_highs if level > path_extreme]
                if side == "BUY"
                else [level for level in pivot_lows if level < path_extreme]
            )
            selected = _select_nearest_target(
                levels=intact_at_entry,
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
            score = (
                max(displacement_strength, 0.0)
                * max(turnover_ratio, 1.0)
                * max(spot_ratio, 0.0)
                * max(float(len(cluster)), 1.0)
                / (1.0 + max(basis_expansion_share, 0.0))
            )
            details = {
                "state": "MATURE_DEFENDED_SWING_COMMON_ACCEPTED_BREAKOUT_RETEST",
                "level_boundary": boundary,
                "level_ids": sorted(level.level_id for level in cluster),
                "level_cluster_size": len(cluster),
                "level_pivot_closes_utc": [pd.Timestamp(level.pivot_close_ns, unit="ns", tz=UTC).isoformat() for level in cluster],
                "level_confirmations_utc": [pd.Timestamp(level.confirmation_ns, unit="ns", tz=UTC).isoformat() for level in cluster],
                "level_eligibility_utc": [pd.Timestamp(level.eligibility_ns, unit="ns", tz=UTC).isoformat() for level in cluster],
                "defense_touch_utc": [
                    None if level.defense_touch_ns is None else pd.Timestamp(level.defense_touch_ns, unit="ns", tz=UTC).isoformat()
                    for level in cluster
                ],
                "defense_confirmation_utc": [
                    None if level.defense_confirmation_ns is None else pd.Timestamp(level.defense_confirmation_ns, unit="ns", tz=UTC).isoformat()
                    for level in cluster
                ],
                "require_defense_memory": config.require_defense_memory,
                "level_age_hours_at_break": [
                    (event_ns - level.confirmation_ns) / (60.0 * NS_MINUTE) for level in cluster
                ],
                "breakout_direction": "UP" if direction > 0 else "DOWN",
                "event_close_utc": event_ts.isoformat(),
                "event_extension_atr": extension / atr_value,
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
                "entry_path_extreme": path_extreme,
                "selected_nearest_intact_swing": target,
                "selected_target_cost_after_rr": rr,
                "target_skip_rule": "NEAREST_ONLY_NO_RR_SKIPPING",
                "causal_interpretation": "a causally confirmed swing survived eight hours, demonstrated market memory through rejection, then its first external breach was accepted jointly by spot and perpetual before an imbalance retest held the old level toward the nearest intact swing liquidity",
            }
            signals.append(
                RotationSignal(
                    scenario_id=f"v95-mature-swing-breakout-{observed_ns}",
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
            break

    signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    unique: list[RotationSignal] = []
    seen: set[int] = set()
    cooldown_until = -1
    for signal in signals:
        if signal.observed_time_ns in seen or signal.observed_time_ns <= cooldown_until:
            continue
        seen.add(signal.observed_time_ns)
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v95")
        unique.append(signal)
        cooldown_until = signal.observed_time_ns + config.cooldown_minutes * NS_MINUTE
    return unique


__all__ = ["MatureSwingBreakoutConfig", "build_state", "build_rotation_signals"]
