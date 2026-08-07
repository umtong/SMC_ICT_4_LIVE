"""Candidate-02 v104: causal external-liquidity acceptance/retest continuation.

This is a signal-state module, not a backtest engine.  It maintains an
already-known liquidity registry and emits deterministic trade intents.
NautilusTrader remains the sole owner of orders, fills, costs, positions,
liquidation and account NAV.

Narrative:
    pre-existing external liquidity -> first breach during meaningful activity
    -> common spot/perpetual acceptance -> post-acceptance displacement/FVG
    -> later defended FVG/old-boundary retest -> delayed activation toward the
    nearest still-intact external-liquidity pool.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

UTC = "UTC"
NS_MINUTE = 60_000_000_000
LEVEL_FAMILIES = (
    "PREVIOUS_DAY",
    "PREVIOUS_WEEK",
    "MATURE_SWING",
    "EQUAL_SWING_CLUSTER",
)


@dataclass(frozen=True, slots=True)
class ExternalLiquidityConfig:
    prior_window_minutes: int = 2880
    prior_minimum_minutes: int = 720
    turnover_quantile: float = 0.50
    atr_lookback_minutes: int = 60

    swing_bar_minutes: int = 15
    swing_radius_bars: int = 2
    mature_minimum_age_minutes: int = 480
    mature_maximum_age_minutes: int = 4320
    require_mature_defense_memory: bool = True
    defense_approach_atr: float = 0.15
    defense_rejection_atr: float = 0.25
    defense_confirmation_minutes: int = 5
    previous_day_lifetime_minutes: int = 4320
    previous_week_lifetime_minutes: int = 20160
    equal_minimum_separation_minutes: int = 30
    equal_maximum_span_minutes: int = 1440
    equal_tolerance_atr: float = 0.10
    equal_lifetime_minutes: int = 2880
    level_merge_atr: float = 0.08
    level_families: tuple[str, ...] = LEVEL_FAMILIES

    minimum_level_breach_atr: float = 0.02
    maximum_event_extension_atr: float = 1.50
    classification_minutes: int = 3
    minimum_outside_closes: int = 2
    minimum_acceptance_atr: float = 0.03
    minimum_spot_acceptance_ratio: float = 0.25
    maximum_basis_expansion_share: float = 0.75

    displacement_search_minutes: int = 6
    displacement_body_quantile: float = 0.55
    minimum_displacement_body_atr: float = 0.20
    minimum_fvg_atr: float = 0.01
    retest_minutes: int = 20
    maximum_retest_boundary_distance_atr: float = 0.20
    invalidation_inside_atr: float = 0.10
    stop_buffer_atr: float = 0.05
    minimum_retrace_flow_alignment: float = -0.15

    maximum_delivery_fraction: float = 0.50
    minimum_target_cost_after_rr: float = 1.00
    cooldown_minutes: int = 20
    activation_delay_minutes: int = 1
    maximum_holding_minutes: int = 180

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ExternalLiquidityConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v104 config keys: {unknown}")
        payload = dict(values)
        if "level_families" in payload:
            payload["level_families"] = tuple(str(x) for x in payload["level_families"])
        return cls(**payload)

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
        if self.mature_minimum_age_minutes < self.swing_bar_minutes * self.swing_radius_bars:
            raise ValueError("maturity must exceed causal confirmation")
        if self.mature_maximum_age_minutes <= self.mature_minimum_age_minutes:
            raise ValueError("invalid mature lifetime")
        if not 0.0 <= self.defense_approach_atr <= 1.0:
            raise ValueError("invalid defense approach")
        if not 0.0 < self.defense_rejection_atr <= 2.0:
            raise ValueError("invalid defense rejection")
        if not 1 <= self.defense_confirmation_minutes <= 30:
            raise ValueError("invalid defense horizon")
        if self.previous_day_lifetime_minutes < 1440:
            raise ValueError("previous-day lifetime too short")
        if self.previous_week_lifetime_minutes < 10080:
            raise ValueError("previous-week lifetime too short")
        if self.equal_minimum_separation_minutes < self.swing_bar_minutes:
            raise ValueError("equal pivots insufficiently separated")
        if self.equal_maximum_span_minutes <= self.equal_minimum_separation_minutes:
            raise ValueError("invalid equal-pivot span")
        if not 0.0 < self.equal_tolerance_atr <= 0.50:
            raise ValueError("invalid equal tolerance")
        if self.equal_lifetime_minutes < 1440:
            raise ValueError("equal-level lifetime too short")
        if not 0.0 <= self.level_merge_atr <= 0.50:
            raise ValueError("invalid level merge tolerance")
        unknown_families = sorted(set(self.level_families) - set(LEVEL_FAMILIES))
        if unknown_families or not self.level_families:
            raise ValueError(f"invalid level families: {unknown_families}")
        if not 0.0 <= self.minimum_level_breach_atr < self.maximum_event_extension_atr:
            raise ValueError("invalid breach geometry")
        if self.classification_minutes not in {2, 3, 4, 5}:
            raise ValueError("invalid classification horizon")
        if not 1 <= self.minimum_outside_closes <= self.classification_minutes:
            raise ValueError("invalid outside closes")
        if self.minimum_acceptance_atr < 0.0:
            raise ValueError("negative acceptance")
        if not 0.0 <= self.minimum_spot_acceptance_ratio <= 2.0:
            raise ValueError("invalid spot ratio")
        if not 0.0 <= self.maximum_basis_expansion_share <= 2.0:
            raise ValueError("invalid basis share")
        if self.displacement_search_minutes not in {3, 4, 5, 6, 7, 8, 10}:
            raise ValueError("invalid displacement horizon")
        if not 0.0 < self.displacement_body_quantile < 1.0:
            raise ValueError("invalid displacement quantile")
        if self.minimum_displacement_body_atr <= 0.0:
            raise ValueError("invalid displacement floor")
        if not 0.0 <= self.minimum_fvg_atr <= 0.25:
            raise ValueError("invalid FVG floor")
        if not 5 <= self.retest_minutes <= 60:
            raise ValueError("invalid retest horizon")
        if not 0.0 < self.maximum_retest_boundary_distance_atr <= 1.0:
            raise ValueError("invalid retest distance")
        if self.invalidation_inside_atr <= 0.0 or self.stop_buffer_atr < 0.0:
            raise ValueError("invalid invalidation")
        if not -1.0 < self.minimum_retrace_flow_alignment < 1.0:
            raise ValueError("invalid retest flow")
        if not 0.0 < self.maximum_delivery_fraction <= 1.0:
            raise ValueError("invalid valuation fraction")
        if self.minimum_target_cost_after_rr < 0.0:
            raise ValueError("negative target RR")
        if self.cooldown_minutes < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid timing")
        if self.activation_delay_minutes != 1:
            raise ValueError("v104 activation delay is locked to exactly one completed minute")


@dataclass(frozen=True, slots=True)
class ConfirmedSwing:
    swing_id: str
    side: str
    price: float
    pivot_close_ns: int
    confirmation_ns: int


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    level_id: str
    family: str
    side: str
    price: float
    formed_ns: int
    confirmation_ns: int
    eligibility_ns: int
    expiry_ns: int
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DisplacementEvent:
    position: int
    fvg_low: float
    fvg_high: float
    body_atr: float


@dataclass(frozen=True, slots=True)
class ScenarioBuildResult:
    signals: tuple[RotationSignal, ...]
    diagnostics: Mapping[str, int]
    level_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ActivationValidation:
    """Structural and economic validation at the actual activation close."""

    accepted: bool
    reason: str
    cost_after_reward_risk: float
    delivery_fraction: float


def _normalise_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy().sort_index()
    out.index = out.index.tz_localize(UTC) if out.index.tz is None else out.index.tz_convert(UTC)
    if out.index.has_duplicates or not out.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and increasing")
    return out


def _normalise_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


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


def _finite(row: pd.Series, names: Iterable[str]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def build_state(features: pd.DataFrame, config: ExternalLiquidityConfig) -> pd.DataFrame:
    required = {
        "close",
        "aggressive_total_quote_1m",
        "signed_flow_ratio_1m",
        "spot_close",
        "spot_signed_flow_ratio_1m",
        "perp_spot_log_basis",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v104 missing completed-minute features: {missing}")
    x = _normalise_index(features)
    x["turnover_threshold"] = (
        x["aggressive_total_quote_1m"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.turnover_quantile)
        .shift(1)
    )
    return x


def _completed_bars(raw: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp, minutes: int) -> pd.DataFrame:
    view = raw.loc[(raw.index > start) & (raw.index <= end), ["open", "high", "low", "close"]]
    if view.empty:
        return view
    bars = view.resample(
        f"{minutes}min", origin="start_day", label="right", closed="right"
    ).agg({"open": "first", "high": "max", "low": "min", "close": ["last", "count"]})
    bars.columns = ["open", "high", "low", "close", "count"]
    bars.dropna(subset=["open", "high", "low", "close"], inplace=True)
    return bars.loc[bars["count"] == minutes, ["open", "high", "low", "close"]]


def _confirmed_swings(raw: pd.DataFrame, config: ExternalLiquidityConfig) -> list[ConfirmedSwing]:
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
    output: list[ConfirmedSwing] = []
    for i in range(radius, len(bars) - radius):
        high, low = float(highs[i]), float(lows[i])
        left_high = float(np.max(highs[i - radius : i]))
        right_high = float(np.max(highs[i + 1 : i + radius + 1]))
        left_low = float(np.min(lows[i - radius : i]))
        right_low = float(np.min(lows[i + 1 : i + radius + 1]))
        pivot_ts = pd.Timestamp(bars.index[i])
        confirmation_ts = pd.Timestamp(bars.index[i + radius])
        token = pivot_ts.isoformat()
        is_high = high >= left_high and high >= right_high and (high > left_high or high > right_high)
        is_low = low <= left_low and low <= right_low and (low < left_low or low < right_low)
        if is_high and math.isfinite(high) and high > 0.0:
            output.append(
                ConfirmedSwing(
                    swing_id=f"SWING:{token}:HIGH",
                    side="HIGH",
                    price=high,
                    pivot_close_ns=int(pivot_ts.value),
                    confirmation_ns=int(confirmation_ts.value),
                )
            )
        if is_low and math.isfinite(low) and low > 0.0:
            output.append(
                ConfirmedSwing(
                    swing_id=f"SWING:{token}:LOW",
                    side="LOW",
                    price=low,
                    pivot_close_ns=int(pivot_ts.value),
                    confirmation_ns=int(confirmation_ts.value),
                )
            )
    output.sort(key=lambda x: (x.confirmation_ns, x.side, x.price))
    return output


def _wick_breaches(side: str, price: float, row: pd.Series, atr: float, breach_atr: float) -> bool:
    high_name = "high" if "high" in row.index else "raw_high"
    low_name = "low" if "low" in row.index else "raw_low"
    return (
        float(row[high_name]) >= price + breach_atr * atr
        if side == "HIGH"
        else float(row[low_name]) <= price - breach_atr * atr
    )


def _mature_swing_levels(
    raw: pd.DataFrame,
    *,
    swings: Sequence[ConfirmedSwing],
    atr: pd.Series,
    config: ExternalLiquidityConfig,
) -> list[LiquidityLevel]:
    frame = raw[["open", "high", "low", "close"]].copy()
    frame["atr"] = atr.reindex(frame.index)
    index_ns = frame.index.asi8
    output: list[LiquidityLevel] = []
    for swing in swings:
        age_ns = swing.confirmation_ns + config.mature_minimum_age_minutes * NS_MINUTE
        expiry_ns = swing.confirmation_ns + config.mature_maximum_age_minutes * NS_MINUTE
        left = int(np.searchsorted(index_ns, swing.confirmation_ns, side="right"))
        age_end = int(np.searchsorted(index_ns, age_ns, side="right"))
        right = int(np.searchsorted(index_ns, expiry_ns, side="right"))
        dead = False
        for position in range(left, min(age_end, right)):
            row = frame.iloc[position]
            atr_value = float(row["atr"])
            if math.isfinite(atr_value) and atr_value > 0.0 and _wick_breaches(
                swing.side,
                swing.price,
                row,
                atr_value,
                config.minimum_level_breach_atr,
            ):
                dead = True
                break
        if dead:
            continue
        if not config.require_mature_defense_memory:
            output.append(
                LiquidityLevel(
                    level_id=f"MATURE:{swing.swing_id}",
                    family="MATURE_SWING",
                    side=swing.side,
                    price=swing.price,
                    formed_ns=swing.pivot_close_ns,
                    confirmation_ns=swing.confirmation_ns,
                    eligibility_ns=age_ns,
                    expiry_ns=expiry_ns,
                    metadata={"defense_touch_ns": None, "defense_confirmation_ns": None},
                )
            )
            continue
        qualified: LiquidityLevel | None = None
        position = age_end
        while position < right:
            row = frame.iloc[position]
            atr_value = float(row["atr"])
            if not math.isfinite(atr_value) or atr_value <= 0.0:
                position += 1
                continue
            if _wick_breaches(
                swing.side,
                swing.price,
                row,
                atr_value,
                config.minimum_level_breach_atr,
            ):
                break
            approached = (
                float(row["high"]) >= swing.price - config.defense_approach_atr * atr_value
                and float(row["close"]) <= swing.price
                if swing.side == "HIGH"
                else float(row["low"]) <= swing.price + config.defense_approach_atr * atr_value
                and float(row["close"]) >= swing.price
            )
            if not approached:
                position += 1
                continue
            confirm_end = min(position + config.defense_confirmation_minutes - 1, right - 1)
            invalidated = False
            for confirm_position in range(position, confirm_end + 1):
                confirm = frame.iloc[confirm_position]
                confirm_atr = float(confirm["atr"])
                if not math.isfinite(confirm_atr) or confirm_atr <= 0.0:
                    continue
                if _wick_breaches(
                    swing.side,
                    swing.price,
                    confirm,
                    confirm_atr,
                    config.minimum_level_breach_atr,
                ):
                    invalidated = True
                    break
                rejected = (
                    float(confirm["close"]) <= swing.price - config.defense_rejection_atr * confirm_atr
                    if swing.side == "HIGH"
                    else float(confirm["close"]) >= swing.price + config.defense_rejection_atr * confirm_atr
                )
                if rejected:
                    confirmation_ns = int(index_ns[confirm_position])
                    qualified = LiquidityLevel(
                        level_id=f"MATURE:{swing.swing_id}",
                        family="MATURE_SWING",
                        side=swing.side,
                        price=swing.price,
                        formed_ns=swing.pivot_close_ns,
                        confirmation_ns=swing.confirmation_ns,
                        eligibility_ns=confirmation_ns,
                        expiry_ns=expiry_ns,
                        metadata={
                            "defense_touch_ns": int(index_ns[position]),
                            "defense_confirmation_ns": confirmation_ns,
                        },
                    )
                    break
            if qualified is not None or invalidated:
                break
            position = confirm_end + 1
        if qualified is not None:
            output.append(qualified)
    return output


def _period_levels(raw: pd.DataFrame, config: ExternalLiquidityConfig) -> list[LiquidityLevel]:
    open_time = raw.index - pd.Timedelta(minutes=1)
    work = raw[["high", "low"]].copy()
    work["day_start"] = open_time.floor("D")
    weekdays = pd.Series(open_time.weekday, index=work.index)
    work["week_start"] = (open_time - pd.to_timedelta(weekdays, unit="D")).dt.floor("D")
    output: list[LiquidityLevel] = []
    for day_start, group in work.groupby("day_start", sort=True):
        if len(group) != 1440:
            continue
        eligibility = pd.Timestamp(day_start) + pd.Timedelta(days=1)
        for side, price in (("HIGH", float(group["high"].max())), ("LOW", float(group["low"].min()))):
            output.append(
                LiquidityLevel(
                    level_id=f"PD:{pd.Timestamp(day_start).isoformat()}:{side}",
                    family="PREVIOUS_DAY",
                    side=side,
                    price=price,
                    formed_ns=int(pd.Timestamp(day_start).value),
                    confirmation_ns=int(eligibility.value),
                    eligibility_ns=int(eligibility.value),
                    expiry_ns=int((eligibility + pd.Timedelta(minutes=config.previous_day_lifetime_minutes)).value),
                    metadata={"period_start_utc": pd.Timestamp(day_start).isoformat()},
                )
            )
    for week_start, group in work.groupby("week_start", sort=True):
        if len(group) != 10080:
            continue
        eligibility = pd.Timestamp(week_start) + pd.Timedelta(days=7)
        for side, price in (("HIGH", float(group["high"].max())), ("LOW", float(group["low"].min()))):
            output.append(
                LiquidityLevel(
                    level_id=f"PW:{pd.Timestamp(week_start).isoformat()}:{side}",
                    family="PREVIOUS_WEEK",
                    side=side,
                    price=price,
                    formed_ns=int(pd.Timestamp(week_start).value),
                    confirmation_ns=int(eligibility.value),
                    eligibility_ns=int(eligibility.value),
                    expiry_ns=int((eligibility + pd.Timedelta(minutes=config.previous_week_lifetime_minutes)).value),
                    metadata={"period_start_utc": pd.Timestamp(week_start).isoformat()},
                )
            )
    return output


def _equal_swing_levels(
    raw: pd.DataFrame,
    *,
    swings: Sequence[ConfirmedSwing],
    atr: pd.Series,
    config: ExternalLiquidityConfig,
) -> list[LiquidityLevel]:
    output: list[LiquidityLevel] = []
    index_ns = raw.index.asi8
    for side in ("HIGH", "LOW"):
        candidates = [x for x in swings if x.side == side]
        for i, current in enumerate(candidates):
            current_ts = pd.Timestamp(current.confirmation_ns, unit="ns", tz=UTC)
            atr_value = float(atr.asof(current_ts)) if not atr.empty else math.nan
            if not math.isfinite(atr_value) or atr_value <= 0.0:
                continue
            tolerance = config.equal_tolerance_atr * atr_value
            chosen: ConfirmedSwing | None = None
            for prior in reversed(candidates[:i]):
                separation = (current.pivot_close_ns - prior.pivot_close_ns) / NS_MINUTE
                if separation > config.equal_maximum_span_minutes:
                    break
                if separation >= config.equal_minimum_separation_minutes and abs(current.price - prior.price) <= tolerance:
                    chosen = prior
                    break
            if chosen is None:
                continue
            price = max(current.price, chosen.price) if side == "HIGH" else min(current.price, chosen.price)
            left = int(np.searchsorted(index_ns, chosen.confirmation_ns, side="right"))
            right = int(np.searchsorted(index_ns, current.confirmation_ns, side="right"))
            dead = False
            for position in range(left, right):
                row = raw.iloc[position]
                row_atr = float(atr.asof(raw.index[position]))
                if math.isfinite(row_atr) and row_atr > 0.0 and _wick_breaches(
                    side,
                    price,
                    row,
                    row_atr,
                    config.minimum_level_breach_atr,
                ):
                    dead = True
                    break
            if dead:
                continue
            first, second = sorted((chosen.pivot_close_ns, current.pivot_close_ns))
            output.append(
                LiquidityLevel(
                    level_id=f"EQ:{side}:{first}:{second}",
                    family="EQUAL_SWING_CLUSTER",
                    side=side,
                    price=price,
                    formed_ns=first,
                    confirmation_ns=current.confirmation_ns,
                    eligibility_ns=current.confirmation_ns,
                    expiry_ns=current.confirmation_ns + config.equal_lifetime_minutes * NS_MINUTE,
                    metadata={
                        "member_swing_ids": [chosen.swing_id, current.swing_id],
                        "member_prices": [chosen.price, current.price],
                        "tolerance": tolerance,
                    },
                )
            )
    return output


def build_liquidity_registry(
    raw: pd.DataFrame,
    *,
    atr: pd.Series,
    config: ExternalLiquidityConfig,
) -> list[LiquidityLevel]:
    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
    swings = _confirmed_swings(raw_view, config)
    levels: list[LiquidityLevel] = []
    if {"PREVIOUS_DAY", "PREVIOUS_WEEK"} & set(config.level_families):
        levels.extend(_period_levels(raw_view, config))
    if "MATURE_SWING" in config.level_families:
        levels.extend(_mature_swing_levels(raw_view, swings=swings, atr=atr, config=config))
    if "EQUAL_SWING_CLUSTER" in config.level_families:
        levels.extend(_equal_swing_levels(raw_view, swings=swings, atr=atr, config=config))
    levels = [x for x in levels if x.family in config.level_families]
    levels.sort(key=lambda x: (x.eligibility_ns, x.side, x.price, x.level_id))
    unique: list[LiquidityLevel] = []
    seen: set[str] = set()
    for level in levels:
        if level.level_id not in seen:
            unique.append(level)
            seen.add(level.level_id)
    return unique


def _cluster_breaches(levels: Sequence[LiquidityLevel], tolerance: float) -> list[list[LiquidityLevel]]:
    clusters: list[list[LiquidityLevel]] = []
    for level in sorted(levels, key=lambda x: x.price):
        if not clusters or abs(level.price - clusters[-1][-1].price) > tolerance:
            clusters.append([level])
        else:
            clusters[-1].append(level)
    return clusters


def _find_displacement(
    *,
    x: pd.DataFrame,
    start_position: int,
    boundary: float,
    direction: int,
    config: ExternalLiquidityConfig,
) -> DisplacementEvent | None:
    """Search only after the common-acceptance close is fully known."""
    first_position = start_position + 1
    end_position = min(start_position + config.displacement_search_minutes, len(x) - 1)
    for position in range(max(first_position, 2), end_position + 1):
        row = x.iloc[position]
        fields = ("raw_open", "raw_high", "raw_low", "raw_close", "body", "body_threshold", "atr")
        if not _finite(row, fields):
            continue
        atr_value = float(row["atr"])
        if atr_value <= 0.0:
            continue
        body_floor = max(float(row["body_threshold"]), config.minimum_displacement_body_atr * atr_value)
        directional_body = direction * (float(row["raw_close"]) - float(row["raw_open"]))
        if directional_body <= 0.0 or float(row["body"]) < body_floor:
            continue
        if direction > 0 and float(row["raw_close"]) <= boundary:
            continue
        if direction < 0 and float(row["raw_close"]) >= boundary:
            continue
        two_back = x.iloc[position - 2]
        minimum_gap = config.minimum_fvg_atr * atr_value
        if direction > 0:
            fvg_low, fvg_high = float(two_back["raw_high"]), float(row["raw_low"])
        else:
            fvg_low, fvg_high = float(row["raw_high"]), float(two_back["raw_low"])
        if not (math.isfinite(fvg_low) and math.isfinite(fvg_high)):
            continue
        if fvg_high - fvg_low < minimum_gap:
            continue
        return DisplacementEvent(
            position=position,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
            body_atr=float(row["body"]) / max(atr_value, 1e-12),
        )
    return None


def _target_candidates(
    *,
    levels: Sequence[LiquidityLevel],
    consumed: set[str],
    decision_ns: int,
    activation_ns: int,
    side: str,
    entry: float,
    path_extreme: float,
) -> list[LiquidityLevel]:
    """Return targets known by decision and still active at activation.

    A target whose confirmation becomes available only on the activation bar is
    future information relative to the completed-minute decision.  Eligibility
    is therefore bounded by ``decision_ns`` while expiry must cover the later
    activation timestamp.
    """
    level_side = "HIGH" if side == "BUY" else "LOW"
    output = [
        level
        for level in levels
        if level.side == level_side
        and level.level_id not in consumed
        and level.eligibility_ns <= decision_ns
        and activation_ns <= level.expiry_ns
        and (
            level.price > max(entry, path_extreme)
            if side == "BUY"
            else level.price < min(entry, path_extreme)
        )
    ]
    return sorted(output, key=lambda x: x.price, reverse=side == "SELL")


def validate_activation(
    *,
    side: str,
    entry: float,
    boundary: float,
    stop: float,
    target: float,
    costs: CostConfig,
    minimum_cost_after_rr: float,
    maximum_delivery_fraction: float,
    activation_high: float | None = None,
    activation_low: float | None = None,
    structural_invalidation: float | None = None,
) -> ActivationValidation:
    """Repeat locked setup checks with the actual activation bar and close.

    A scheduled signal was decided one completed minute earlier.  At activation,
    the just-completed bar may already have invalidated the stop, consumed the
    target, returned into the old range, or degraded the cost-after-RR.  Such a
    signal must be rejected before NautilusTrader receives an entry order.
    """
    numeric = (entry, boundary, stop, target, minimum_cost_after_rr, maximum_delivery_fraction)
    if structural_invalidation is not None:
        numeric = (*numeric, structural_invalidation)
    if side not in {"BUY", "SELL"}:
        return ActivationValidation(False, "UNKNOWN_SIDE", math.nan, math.nan)
    if not all(math.isfinite(float(value)) for value in numeric):
        return ActivationValidation(False, "NONFINITE_ACTIVATION_INPUT", math.nan, math.nan)
    if minimum_cost_after_rr < 0.0 or not 0.0 < maximum_delivery_fraction <= 1.0:
        return ActivationValidation(False, "INVALID_ACTIVATION_THRESHOLDS", math.nan, math.nan)

    geometry = (
        stop < boundary < entry < target
        if side == "BUY"
        else target < entry < boundary < stop
    )
    if not geometry:
        return ActivationValidation(
            False,
            "ACTIVATION_STRUCTURE_GEOMETRY_INVALID",
            math.nan,
            math.nan,
        )

    if structural_invalidation is not None:
        invalidation = float(structural_invalidation)
        invalidation_geometry = (
            stop <= invalidation < boundary
            if side == "BUY"
            else boundary < invalidation <= stop
        )
        if not invalidation_geometry:
            return ActivationValidation(
                False,
                "ACTIVATION_STRUCTURAL_INVALIDATION_GEOMETRY_INVALID",
                math.nan,
                math.nan,
            )

    if (activation_high is None) != (activation_low is None):
        return ActivationValidation(False, "INCOMPLETE_ACTIVATION_BAR_RANGE", math.nan, math.nan)
    if activation_high is not None and activation_low is not None:
        high = float(activation_high)
        low = float(activation_low)
        if not (math.isfinite(high) and math.isfinite(low) and high >= low):
            return ActivationValidation(False, "INVALID_ACTIVATION_BAR_RANGE", math.nan, math.nan)
        stop_traversed = low <= stop if side == "BUY" else high >= stop
        target_traversed = high >= target if side == "BUY" else low <= target
        structural_invalidation_traversed = (
            False
            if structural_invalidation is None
            else (
                low <= float(structural_invalidation)
                if side == "BUY"
                else high >= float(structural_invalidation)
            )
        )
        if stop_traversed and target_traversed:
            return ActivationValidation(
                False,
                "ACTIVATION_BAR_PRETRAVERSED_STOP_AND_TARGET",
                math.nan,
                math.nan,
            )
        if stop_traversed:
            return ActivationValidation(False, "ACTIVATION_BAR_PRETRAVERSED_STOP", math.nan, math.nan)
        if target_traversed:
            return ActivationValidation(False, "ACTIVATION_BAR_PRETRAVERSED_TARGET", math.nan, math.nan)
        if structural_invalidation_traversed:
            return ActivationValidation(
                False,
                "ACTIVATION_BAR_PRETRAVERSED_STRUCTURAL_INVALIDATION",
                math.nan,
                math.nan,
            )

    denominator = target - boundary if side == "BUY" else boundary - target
    numerator = entry - boundary if side == "BUY" else boundary - entry
    if denominator <= 0.0:
        return ActivationValidation(False, "ACTIVATION_TARGET_NOT_EXTERNAL", math.nan, math.nan)
    delivery_fraction = numerator / denominator
    if not math.isfinite(delivery_fraction) or not 0.0 <= delivery_fraction <= maximum_delivery_fraction:
        return ActivationValidation(
            False,
            "ACTIVATION_DELIVERY_FRACTION_EXCEEDED",
            math.nan,
            float(delivery_fraction),
        )

    rr = cost_after_reward_risk(
        entry=entry,
        stop=stop,
        target=target,
        side=side,
        costs=costs,
    )
    if not math.isfinite(rr) or rr < minimum_cost_after_rr:
        return ActivationValidation(
            False,
            "ACTIVATION_COST_AFTER_RR_BELOW_FLOOR",
            float(rr),
            float(delivery_fraction),
        )
    return ActivationValidation(True, "ACCEPTED", float(rr), float(delivery_fraction))

def _select_natural_target(
    *,
    candidates: Sequence[LiquidityLevel],
    side: str,
    boundary: float,
    entry: float,
    stop: float,
    costs: CostConfig,
    config: ExternalLiquidityConfig,
) -> tuple[LiquidityLevel, float, float] | None:
    """The nearest level is decisive; a bad nearest level is no-trade."""
    if not candidates:
        return None
    target = candidates[0]
    validation = validate_activation(
        side=side,
        entry=entry,
        boundary=boundary,
        stop=stop,
        target=target.price,
        costs=costs,
        minimum_cost_after_rr=config.minimum_target_cost_after_rr,
        maximum_delivery_fraction=config.maximum_delivery_fraction,
    )
    if not validation.accepted:
        return None
    return target, validation.cost_after_reward_risk, validation.delivery_fraction


def _session_label(ts: pd.Timestamp) -> str:
    if ts.hour < 8:
        return "ASIA"
    if ts.hour < 13:
        return "LONDON"
    if ts.hour < 21:
        return "NEW_YORK"
    return "LATE_US"


def _volatility_regime(atr_value: float, atr: pd.Series, ts: pd.Timestamp) -> str:
    history = atr.loc[atr.index < ts].tail(1440).dropna()
    if history.empty or not math.isfinite(atr_value):
        return "UNKNOWN"
    low, high = float(history.quantile(0.33)), float(history.quantile(0.67))
    if atr_value <= low:
        return "LOW"
    if atr_value >= high:
        return "HIGH"
    return "MID"


def build_scenario_result(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: ExternalLiquidityConfig,
    costs: CostConfig,
) -> ScenarioBuildResult:
    start, end = _normalise_timestamp(evaluation_start), _normalise_timestamp(evaluation_end)
    if end <= start:
        raise ValueError("evaluation end must be after start")
    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
    x = state.join(
        raw_view.rename(
            columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
        ),
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
    levels = build_liquidity_registry(raw_view, atr=atr, config=config)
    diagnostics: Counter[str] = Counter()
    level_counts = Counter(x.family for x in levels)
    consumed: set[str] = set()
    index_ns = x.index.asi8
    index_ns_set = set(int(value) for value in index_ns)
    start_ns = int(start.value)

    # A target/boundary is dead if a wick already traversed it before evaluation.
    for level in levels:
        if not (level.eligibility_ns < start_ns <= level.expiry_ns):
            continue
        left = int(np.searchsorted(index_ns, level.eligibility_ns, side="right"))
        right = int(np.searchsorted(index_ns, start_ns, side="left"))
        for position in range(left, right):
            row = x.iloc[position]
            atr_value = float(row["atr"])
            if math.isfinite(atr_value) and atr_value > 0.0 and _wick_breaches(
                level.side, level.price, row, atr_value, config.minimum_level_breach_atr
            ):
                consumed.add(level.level_id)
                diagnostics["PRE_EVALUATION_LEVEL_CONSUMED"] += 1
                break

    candidate_signals: list[RotationSignal] = []
    evaluation_positions = [
        int(x.index.get_loc(ts))
        for ts in x.loc[(x.index >= start) & (x.index < end)].index
    ]
    event_fields = (
        "raw_high",
        "raw_low",
        "raw_close",
        "atr",
        "aggressive_total_quote_1m",
        "turnover_threshold",
        "spot_close",
        "perp_spot_log_basis",
    )

    for event_position in evaluation_positions:
        if event_position < 1:
            diagnostics["EVENT_SKIPPED_NO_PREVIOUS_BAR"] += 1
            continue
        event_ts = pd.Timestamp(x.index[event_position])
        event_ns = int(event_ts.value)
        event = x.iloc[event_position]
        previous = x.iloc[event_position - 1]
        if not _finite(event, event_fields) or not _finite(
            previous, ("raw_close", "perp_spot_log_basis")
        ):
            diagnostics["EVENT_SKIPPED_NONFINITE"] += 1
            continue
        atr_value = float(event["atr"])
        if atr_value <= 0.0:
            diagnostics["EVENT_SKIPPED_INVALID_ATR"] += 1
            continue

        active = [
            level
            for level in levels
            if level.level_id not in consumed
            and level.eligibility_ns < event_ns <= level.expiry_ns
        ]
        if not active:
            diagnostics["EVENT_NO_ACTIVE_EXTERNAL_LIQUIDITY"] += 1
            continue

        previous_close = float(previous["raw_close"])
        upper = [
            level
            for level in active
            if level.side == "HIGH"
            and previous_close <= level.price
            and float(event["raw_high"])
            >= level.price + config.minimum_level_breach_atr * atr_value
        ]
        lower = [
            level
            for level in active
            if level.side == "LOW"
            and previous_close >= level.price
            and float(event["raw_low"])
            <= level.price - config.minimum_level_breach_atr * atr_value
        ]
        if not upper and not lower:
            diagnostics["EVENT_NO_FIRST_EXTERNAL_BREACH"] += 1
            continue

        # Liquidity is consumed by the wick traversal whether the later scenario
        # qualifies or not.  A failed confirmation must not resurrect it.
        consumed.update(level.level_id for level in upper + lower)
        diagnostics["EXTERNAL_LEVELS_CONSUMED"] += len(upper) + len(lower)
        if upper and lower:
            diagnostics["EVENT_BOTH_SIDES_CONSUMED_NO_TRADE"] += 1
            continue

        direction = 1 if upper else -1
        breached = upper or lower
        clusters = _cluster_breaches(
            breached,
            tolerance=config.level_merge_atr * atr_value,
        )
        cluster = clusters[-1] if direction > 0 else clusters[0]
        boundary = (
            max(level.price for level in cluster)
            if direction > 0
            else min(level.price for level in cluster)
        )
        event_extreme = float(event["raw_high"] if direction > 0 else event["raw_low"])
        event_extension = direction * (event_extreme - boundary)
        if event_extension > config.maximum_event_extension_atr * atr_value:
            diagnostics["EVENT_OVEREXTENDED_BEFORE_CONFIRMATION"] += 1
            continue
        if float(event["aggressive_total_quote_1m"]) < float(event["turnover_threshold"]):
            diagnostics["EVENT_BELOW_CAUSAL_TURNOVER_REGIME"] += 1
            continue

        classification_end = event_position + config.classification_minutes - 1
        if classification_end >= len(x):
            diagnostics["CLASSIFICATION_INCOMPLETE_DATA_END"] += 1
            continue
        classification_ts = pd.Timestamp(x.index[classification_end])
        if classification_ts >= end:
            diagnostics["CLASSIFICATION_OUTSIDE_EVALUATION"] += 1
            continue
        segment = x.iloc[event_position : classification_end + 1]
        if len(segment) != config.classification_minutes:
            diagnostics["CLASSIFICATION_INCOMPLETE_MINUTES"] += 1
            continue
        required_segment_fields = (
            "raw_close",
            "spot_close",
            "perp_spot_log_basis",
        )
        if any(not _finite(row, required_segment_fields) for _, row in segment.iterrows()):
            diagnostics["CLASSIFICATION_NONFINITE"] += 1
            continue

        outside = (
            segment["raw_close"] > boundary
            if direction > 0
            else segment["raw_close"] < boundary
        )
        last = segment.iloc[-1]
        final_perp = float(last["raw_close"])
        final_spot = float(last["spot_close"])
        pre_basis = float(previous["perp_spot_log_basis"])
        final_basis = float(last["perp_spot_log_basis"])
        spot_boundary = boundary / math.exp(pre_basis)
        final_outside_distance = direction * (final_perp - boundary)
        spot_outside_distance = direction * (final_spot - spot_boundary)
        perp_excess_fraction = max(
            direction * (final_perp / boundary - 1.0),
            1e-12,
        )
        spot_excess_fraction = direction * (final_spot / spot_boundary - 1.0)
        spot_ratio = spot_excess_fraction / perp_excess_fraction
        basis_expansion_share = (
            max(direction * (final_basis - pre_basis), 0.0) / perp_excess_fraction
        )
        accepted = (
            int(outside.sum()) >= config.minimum_outside_closes
            and final_outside_distance >= config.minimum_acceptance_atr * atr_value
            and spot_outside_distance > 0.0
            and spot_ratio >= config.minimum_spot_acceptance_ratio
            and basis_expansion_share <= config.maximum_basis_expansion_share
        )
        if not accepted:
            diagnostics["COMMON_ACCEPTANCE_FAILED"] += 1
            continue
        diagnostics["COMMON_ACCEPTANCE_CONFIRMED"] += 1

        displacement = _find_displacement(
            x=x,
            start_position=classification_end,
            boundary=boundary,
            direction=direction,
            config=config,
        )
        if displacement is None:
            diagnostics["POST_ACCEPTANCE_DISPLACEMENT_OR_FVG_MISSING"] += 1
            continue
        displacement_ts = pd.Timestamp(x.index[displacement.position])
        retrace_end = min(displacement.position + config.retest_minutes, len(x) - 1)

        for position in range(displacement.position + 1, retrace_end + 1):
            decision_ts = pd.Timestamp(x.index[position])
            if decision_ts >= end:
                diagnostics["RETEST_OUTSIDE_EVALUATION"] += 1
                break
            row = x.iloc[position]
            retest_fields = (
                "raw_high",
                "raw_low",
                "raw_close",
                "spot_close",
                "signed_flow_ratio_1m",
                "spot_signed_flow_ratio_1m",
                "atr",
            )
            if not _finite(row, retest_fields):
                diagnostics["RETEST_NONFINITE"] += 1
                continue
            row_atr = float(row["atr"])
            if row_atr <= 0.0:
                diagnostics["RETEST_INVALID_ATR"] += 1
                continue

            old_range_invalidation = (
                boundary - config.invalidation_inside_atr * row_atr
                if direction > 0
                else boundary + config.invalidation_inside_atr * row_atr
            )
            invalidated = (
                float(row["raw_low"]) <= old_range_invalidation
                if direction > 0
                else float(row["raw_high"]) >= old_range_invalidation
            )
            if invalidated:
                diagnostics["RETEST_INVALIDATED_BACK_INSIDE_OLD_RANGE"] += 1
                break

            touched_fvg = (
                float(row["raw_high"]) >= displacement.fvg_low
                and float(row["raw_low"]) <= displacement.fvg_high
            )
            if not touched_fvg:
                diagnostics["RETEST_NO_FVG_TOUCH"] += 1
                continue
            boundary_near = (
                float(row["raw_low"])
                <= boundary + config.maximum_retest_boundary_distance_atr * row_atr
                if direction > 0
                else float(row["raw_high"])
                >= boundary - config.maximum_retest_boundary_distance_atr * row_atr
            )
            if not boundary_near:
                diagnostics["RETEST_FVG_NOT_NEAR_OLD_BOUNDARY"] += 1
                continue

            fvg_midpoint = 0.5 * (displacement.fvg_low + displacement.fvg_high)
            rejected = (
                float(row["raw_close"]) >= fvg_midpoint
                if direction > 0
                else float(row["raw_close"]) <= fvg_midpoint
            )
            held_outside = (
                float(row["raw_close"]) > boundary
                if direction > 0
                else float(row["raw_close"]) < boundary
            )
            spot_held_outside = (
                float(row["spot_close"]) > spot_boundary
                if direction > 0
                else float(row["spot_close"]) < spot_boundary
            )
            if not (rejected and held_outside and spot_held_outside):
                diagnostics["RETEST_NOT_DEFENDED_BY_COMMON_MARKET"] += 1
                continue

            combined_retrace_flow = direction * 0.5 * (
                float(row["signed_flow_ratio_1m"])
                + float(row["spot_signed_flow_ratio_1m"])
            )
            if combined_retrace_flow < config.minimum_retrace_flow_alignment:
                diagnostics["RETEST_STRONGLY_OPPOSED_BY_FLOW"] += 1
                continue

            decision_ns = int(decision_ts.value)
            activation_ns = decision_ns + config.activation_delay_minutes * NS_MINUTE
            activation_ts = pd.Timestamp(activation_ns, unit="ns", tz=UTC)
            if activation_ts >= end or activation_ns not in index_ns_set:
                diagnostics["DELAYED_ACTIVATION_UNAVAILABLE"] += 1
                continue

            entry = float(row["raw_close"])
            side = "BUY" if direction > 0 else "SELL"
            stop = (
                min(
                    float(row["raw_low"]) - config.stop_buffer_atr * row_atr,
                    old_range_invalidation,
                )
                if direction > 0
                else max(
                    float(row["raw_high"]) + config.stop_buffer_atr * row_atr,
                    old_range_invalidation,
                )
            )
            path = x.iloc[event_position : position + 1]
            path_extreme = (
                float(path["raw_high"].max())
                if direction > 0
                else float(path["raw_low"].min())
            )
            targets = _target_candidates(
                levels=levels,
                consumed=consumed,
                decision_ns=decision_ns,
                activation_ns=activation_ns,
                side=side,
                entry=entry,
                path_extreme=path_extreme,
            )
            natural = _select_natural_target(
                candidates=targets,
                side=side,
                boundary=boundary,
                entry=entry,
                stop=stop,
                costs=costs,
                config=config,
            )
            if natural is None:
                diagnostics["NO_TRADABLE_NEAREST_EXTERNAL_TARGET"] += 1
                continue
            target, cost_after_rr, delivery_fraction = natural

            turnover_ratio = float(event["aggressive_total_quote_1m"]) / max(
                float(event["turnover_threshold"]),
                1e-12,
            )
            score = (
                max(displacement.body_atr, 0.0)
                * max(turnover_ratio, 1.0)
                * max(spot_ratio, 0.0)
                * max(float(len(cluster)), 1.0)
                / (1.0 + max(basis_expansion_share, 0.0))
            )
            details = {
                "state": "EXTERNAL_LIQUIDITY_COMMON_ACCEPTANCE_FVG_RETEST_CONTINUATION",
                "liquidity_boundary": boundary,
                "liquidity_level_ids": sorted(level.level_id for level in cluster),
                "liquidity_families": sorted({level.family for level in cluster}),
                "liquidity_cluster_size": len(cluster),
                "liquidity_metadata": [dict(level.metadata) for level in cluster],
                "breakout_direction": "UP" if direction > 0 else "DOWN",
                "event_close_utc": event_ts.isoformat(),
                "event_extension_atr": event_extension / max(atr_value, 1e-12),
                "turnover_ratio": turnover_ratio,
                "classification_close_utc": classification_ts.isoformat(),
                "classification_minutes": config.classification_minutes,
                "outside_close_count": int(outside.sum()),
                "spot_boundary": spot_boundary,
                "spot_acceptance_ratio": spot_ratio,
                "basis_expansion_share": basis_expansion_share,
                "displacement_close_utc": displacement_ts.isoformat(),
                "displacement_body_atr": displacement.body_atr,
                "fvg_low": displacement.fvg_low,
                "fvg_high": displacement.fvg_high,
                "fvg_midpoint": fvg_midpoint,
                "decision_close_utc": decision_ts.isoformat(),
                "activation_close_utc": activation_ts.isoformat(),
                "activation_delay_minutes": config.activation_delay_minutes,
                "combined_retrace_flow_alignment": combined_retrace_flow,
                "old_range_invalidation": old_range_invalidation,
                "structural_stop": stop,
                "decision_entry_reference": entry,
                "entry_to_decision_path_extreme": path_extreme,
                "minimum_target_cost_after_rr": config.minimum_target_cost_after_rr,
                "maximum_delivery_fraction": config.maximum_delivery_fraction,
                "activation_validation_costs": {
                    name: str(getattr(costs, name))
                    for name in costs.__dataclass_fields__
                },
                "selected_nearest_external_target_id": target.level_id,
                "selected_nearest_external_target_family": target.family,
                "selected_nearest_external_target": target.price,
                "selected_target_eligibility_ns": target.eligibility_ns,
                "selected_target_expiry_ns": target.expiry_ns,
                "selected_target_known_by_decision": target.eligibility_ns <= decision_ns,
                "selected_target_active_at_activation": activation_ns <= target.expiry_ns,
                "selected_target_eligibility_utc": pd.Timestamp(
                    target.eligibility_ns, unit="ns", tz=UTC
                ).isoformat(),
                "selected_target_expiry_utc": pd.Timestamp(
                    target.expiry_ns, unit="ns", tz=UTC
                ).isoformat(),
                "selected_target_cost_after_rr_at_decision": cost_after_rr,
                "delivery_fraction_boundary_to_target": delivery_fraction,
                "target_skip_rule": "NEAREST_ONLY_NO_RR_SKIPPING",
                "session_diagnostic_only": _session_label(decision_ts),
                "volatility_regime_diagnostic_only": _volatility_regime(
                    row_atr, atr, decision_ts
                ),
                "risk_multiplier_from_score": False,
                "causal_interpretation": (
                    "already-known external liquidity was first traversed, spot and perpetual "
                    "accepted beyond it without basis-only leadership, post-acceptance "
                    "displacement left an imbalance, and a later common-market retest defended "
                    "the old boundary before delayed activation toward the nearest still-intact "
                    "external liquidity pool"
                ),
            }
            candidate_signals.append(
                RotationSignal(
                    scenario_id=f"v104-external-liquidity-{activation_ns}",
                    observed_time_ns=activation_ns,
                    side=side,
                    entry_reference=entry,
                    stop_price=stop,
                    target_price=target.price,
                    cost_after_reward_risk=cost_after_rr,
                    score=float(score),
                    max_hold_minutes=config.maximum_holding_minutes,
                    source_feature_open_time_ns=event_ns - NS_MINUTE,
                    source_feature_available_time_ns=activation_ns,
                    source_max_market_time_ns=decision_ns,
                    details=details,
                )
            )
            diagnostics["SCENARIO_QUALIFIED_BEFORE_GLOBAL_SCHEDULING"] += 1
            break

    candidate_signals.sort(
        key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id)
    )
    selected: list[RotationSignal] = []
    seen_activation: set[int] = set()
    cooldown_until = -1
    for signal in candidate_signals:
        if signal.observed_time_ns in seen_activation:
            diagnostics["GLOBAL_SAME_MINUTE_SIGNAL_SUPPRESSED"] += 1
            continue
        if signal.observed_time_ns <= cooldown_until:
            diagnostics["GLOBAL_COOLDOWN_SIGNAL_SUPPRESSED"] += 1
            continue
        if signal.source_max_market_time_ns >= signal.observed_time_ns:
            raise AssertionError("v104 delayed activation causality failed")
        seen_activation.add(signal.observed_time_ns)
        selected.append(signal)
        cooldown_until = signal.observed_time_ns + config.cooldown_minutes * NS_MINUTE
    diagnostics["SCENARIO_SCHEDULED"] = len(selected)
    return ScenarioBuildResult(
        signals=tuple(selected),
        diagnostics=dict(sorted(diagnostics.items())),
        level_counts=dict(sorted(level_counts.items())),
    )


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: ExternalLiquidityConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    return list(
        build_scenario_result(
            state=state,
            raw=raw,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            config=config,
            costs=costs,
        ).signals
    )


__all__ = [
    "ActivationValidation",
    "ExternalLiquidityConfig",
    "ConfirmedSwing",
    "LiquidityLevel",
    "DisplacementEvent",
    "ScenarioBuildResult",
    "build_state",
    "build_liquidity_registry",
    "build_scenario_result",
    "build_rotation_signals",
    "validate_activation",
]
