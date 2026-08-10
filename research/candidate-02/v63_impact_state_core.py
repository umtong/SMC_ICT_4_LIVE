"""Order-flow impact and auction-boundary state machine for candidate-02 v63.

The same persistent aggressive-flow setup is classified by observed price
impact and inventory:

- aggressive flow + rising OI + inefficient impact + external sweep:
  liquidity absorbed new positions; after the first causal one-minute reclaim,
  rotate toward the opposite side of the frozen auction;
- aggressive flow + falling OI + toxic flow + external sweep:
  forced liquidation exhausted; after the first one-minute reclaim, rotate
  toward the opposite side of the frozen auction;
- aggressive flow + rising OI + efficient impact + two-stage acceptance:
  price discovery is accepted; after a one-minute boundary hold/retest, continue
  toward a measured range extension.

Signals are precomputed from completed market observations, but every order,
fill, position, fee and NAV transition is handled only by NautilusTrader.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

import v61_family_core as family
from v53_nt_core import CostConfig, RotationSignal

NS_MINUTE = 60_000_000_000

VALID_MODES = {
    "ABSORBED_NEW_POSITION_ROTATION",
    "ACCEPTED_FLOW_PRICE_DISCOVERY",
    "LIQUIDATION_EXHAUSTION_ROTATION",
}
family.VALID_MODES.update(VALID_MODES)


@dataclass(frozen=True, slots=True)
class ImpactStateConfig(family.ScenarioFamilyConfig):
    flow_window_bars_5m: int = 3
    flow_abs_quantile: float = 0.70
    impact_efficiency_low_quantile: float = 0.40
    impact_efficiency_high_quantile: float = 0.60
    edge_z_min: float = 1.00
    sweep_buffer_atr: float = 0.05
    confirmation_window_minutes: int = 5
    rotation_target: str = "OPPOSITE_FORMATION_BOUNDARY"
    accepted_target_range_extension: float = 0.50
    accepted_stop_inside_atr: float = 0.20
    accepted_retest_tolerance_atr: float = 0.20
    impact_flow_floor: float = 0.05

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ImpactStateConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v63 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        family.ScenarioFamilyConfig.__post_init__(self)
        if self.mode not in VALID_MODES:
            raise ValueError(f"unknown v63 mode: {self.mode}")
        if not 2 <= self.flow_window_bars_5m <= 6:
            raise ValueError("flow window must be between 10 and 30 minutes")
        for name in (
            "flow_abs_quantile",
            "impact_efficiency_low_quantile",
            "impact_efficiency_high_quantile",
        ):
            value = float(getattr(self, name))
            if not 0 < value < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if self.impact_efficiency_low_quantile >= self.impact_efficiency_high_quantile:
            raise ValueError("low impact threshold must be below high impact threshold")
        if self.edge_z_min <= 0 or self.sweep_buffer_atr < 0:
            raise ValueError("invalid boundary thresholds")
        if not 1 <= self.confirmation_window_minutes <= 15:
            raise ValueError("confirmation window must be between one and fifteen minutes")
        if self.rotation_target != "OPPOSITE_FORMATION_BOUNDARY":
            raise ValueError("v63 rotation target must remain the opposite frozen boundary")
        if self.accepted_target_range_extension <= 0:
            raise ValueError("accepted range extension must be positive")
        if self.accepted_stop_inside_atr < 0 or self.accepted_retest_tolerance_atr < 0:
            raise ValueError("accepted boundary buffers must be non-negative")
        if self.impact_flow_floor <= 0:
            raise ValueError("impact flow floor must be positive")


def build_state(features: pd.DataFrame, config: ImpactStateConfig) -> pd.DataFrame:
    x = family.build_state(features, config)
    bars = config.flow_window_bars_5m
    weight = x["vol_5m"].clip(lower=1e-12)
    x["impact_flow"] = (
        (x["signed_flow"] * weight).rolling(bars, min_periods=bars).sum()
        / weight.rolling(bars, min_periods=bars).sum()
    )
    x["impact_return"] = np.log(x["close"] / x["close"].shift(bars))
    x["impact_efficiency"] = (
        x["impact_return"].abs()
        / (x["impact_flow"].abs() + config.impact_flow_floor)
    )
    x["impact_flow_threshold"] = family._prior_quantile(
        x["impact_flow"].abs(),
        config=config,
        quantile=config.flow_abs_quantile,
    )
    x["impact_efficiency_low_threshold"] = family._prior_quantile(
        x["impact_efficiency"],
        config=config,
        quantile=config.impact_efficiency_low_quantile,
    )
    x["impact_efficiency_high_threshold"] = family._prior_quantile(
        x["impact_efficiency"],
        config=config,
        quantile=config.impact_efficiency_high_quantile,
    )
    return x


def _formation(
    aligned: pd.DataFrame,
    *,
    i: int,
    flow_bars: int,
) -> tuple[pd.DataFrame, pd.DataFrame, float, float, float]:
    end = i - flow_bars + 1
    start = end - 12
    formation = aligned.iloc[start:end]
    impact = aligned.iloc[end : i + 1]
    if len(formation) != 12 or len(impact) != flow_bars:
        raise ValueError("insufficient formation or impact history")
    center = family._weighted_center(formation)
    high = float(formation["raw_high_5m"].max())
    low = float(formation["raw_low_5m"].min())
    return formation, impact, center, high, low


def _first_reclaim(
    *,
    raw: pd.DataFrame,
    setup_available: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    direction: int,
    boundary: float,
    center: float,
    window_minutes: int,
) -> tuple[pd.Timestamp, float] | None:
    end = min(
        setup_available + pd.Timedelta(minutes=window_minutes),
        evaluation_end,
    )
    minutes = raw.loc[(raw.index >= setup_available) & (raw.index <= end)]
    if minutes.empty:
        return None
    previous_close: float | None = None
    for observed_time, minute in minutes.iterrows():
        close = float(minute["close"])
        moving_back = previous_close is None or -direction * (close - previous_close) > 0
        inside = close < boundary if direction > 0 else close > boundary
        closer = abs(close - center) < abs(boundary - center)
        if inside and moving_back and closer:
            return pd.Timestamp(observed_time), close
        previous_close = close
    return None


def _first_acceptance_hold(
    *,
    raw: pd.DataFrame,
    setup_available: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    direction: int,
    boundary: float,
    atr_value: float,
    config: ImpactStateConfig,
) -> tuple[pd.Timestamp, float] | None:
    end = min(
        setup_available + pd.Timedelta(minutes=config.confirmation_window_minutes),
        evaluation_end,
    )
    minutes = raw.loc[(raw.index >= setup_available) & (raw.index <= end)]
    if minutes.empty:
        return None
    tolerance = config.accepted_retest_tolerance_atr * atr_value
    previous_close: float | None = None
    touched = False
    for observed_time, minute in minutes.iterrows():
        low = float(minute["low"])
        high = float(minute["high"])
        close = float(minute["close"])
        if direction > 0:
            touched = touched or low <= boundary + tolerance
            held = close > boundary
            resumed = previous_close is None or close >= previous_close
        else:
            touched = touched or high >= boundary - tolerance
            held = close < boundary
            resumed = previous_close is None or close <= previous_close
        if touched and held and resumed:
            return pd.Timestamp(observed_time), close
        previous_close = close
    return None


def _append_signal(
    *,
    signals: list[RotationSignal],
    mode: str,
    observed_time: pd.Timestamp,
    side: str,
    entry: float,
    stop: float,
    target: float,
    score: float,
    max_hold: int,
    details: Mapping[str, Any],
    costs: CostConfig,
    config: ImpactStateConfig,
) -> bool:
    geometry_valid = stop < entry < target if side == "BUY" else target < entry < stop
    if not geometry_valid:
        return False
    reward_risk = family.cost_after_reward_risk(
        entry=entry,
        stop=stop,
        target=target,
        side=side,
        costs=costs,
    )
    if not math.isfinite(reward_risk) or not (
        config.minimum_cost_after_rr <= reward_risk <= config.maximum_cost_after_rr
    ):
        return False
    observed_ns = int(observed_time.value)
    signals.append(
        RotationSignal(
            scenario_id=f"v63-{mode.lower()}-{observed_ns}",
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=reward_risk,
            score=float(score),
            max_hold_minutes=max_hold,
            source_feature_open_time_ns=observed_ns - NS_MINUTE,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
            details={"mode": mode, **dict(details)},
        )
    )
    return True


def _rotation_setup(
    *,
    mode: str,
    row: pd.Series,
    impact: pd.DataFrame,
    center: float,
    high: float,
    low: float,
    atr_value: float,
    setup_available: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    raw: pd.DataFrame,
    config: ImpactStateConfig,
    costs: CostConfig,
    signals: list[RotationSignal],
) -> bool:
    direction = int(np.sign(float(row["impact_return"])))
    flow_direction = int(np.sign(float(row["impact_flow"])))
    if (
        direction == 0
        or direction != flow_direction
        or abs(float(row["impact_flow"])) < float(row["impact_flow_threshold"])
        or direction * float(row["z_60m"]) < config.edge_z_min
    ):
        return False
    if direction > 0:
        extreme = float(impact["raw_high_5m"].max())
        boundary = high
        swept = extreme >= high + config.sweep_buffer_atr * atr_value
    else:
        extreme = float(impact["raw_low_5m"].min())
        boundary = low
        swept = extreme <= low - config.sweep_buffer_atr * atr_value
    if not swept:
        return False

    if mode == "ABSORBED_NEW_POSITION_ROTATION":
        inventory_valid = (
            float(row["oi_change_1h"]) > 0
            and float(row["impact_efficiency"])
            <= float(row["impact_efficiency_low_threshold"])
        )
    else:
        inventory_valid = (
            float(row["oi_change_1h"]) <= -float(row["oi_abs_threshold"])
            and float(row["vpin_50"]) >= float(row["vpin_high_threshold"])
        )
    if not inventory_valid:
        return False
    confirmation = _first_reclaim(
        raw=raw,
        setup_available=setup_available,
        evaluation_end=evaluation_end,
        direction=direction,
        boundary=boundary,
        center=center,
        window_minutes=config.confirmation_window_minutes,
    )
    if confirmation is None:
        return False
    observed_time, entry = confirmation
    if direction > 0:
        side = "SELL"
        stop = extreme + config.stop_buffer_atr * atr_value
        target = low
    else:
        side = "BUY"
        stop = extreme - config.stop_buffer_atr * atr_value
        target = high
    return _append_signal(
        signals=signals,
        mode=mode,
        observed_time=observed_time,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        score=abs(float(row["impact_flow"])) * (1.0 + abs(float(row["z_60m"]))),
        max_hold=config.maximum_holding_minutes,
        details={
            "setup_feature_open_utc": str(row.name),
            "setup_feature_available_utc": setup_available.isoformat(),
            "formation_center": center,
            "formation_high": high,
            "formation_low": low,
            "external_boundary": boundary,
            "impact_extreme": extreme,
            "impact_flow": float(row["impact_flow"]),
            "impact_return": float(row["impact_return"]),
            "impact_efficiency": float(row["impact_efficiency"]),
            "oi_change_1h": float(row["oi_change_1h"]),
            "vpin": float(row["vpin_50"]),
            "confirmation_lag_minutes": int(
                (observed_time - setup_available) / pd.Timedelta(minutes=1)
            ),
        },
        costs=costs,
        config=config,
    )


def _accepted_setup(
    *,
    row: pd.Series,
    impact: pd.DataFrame,
    center: float,
    high: float,
    low: float,
    atr_value: float,
    setup_available: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    raw: pd.DataFrame,
    config: ImpactStateConfig,
    costs: CostConfig,
    signals: list[RotationSignal],
) -> bool:
    direction = int(np.sign(float(row["impact_flow"])))
    if (
        direction == 0
        or direction * float(row["impact_return"]) <= 0
        or abs(float(row["impact_flow"])) < float(row["impact_flow_threshold"])
        or float(row["impact_efficiency"])
        < float(row["impact_efficiency_high_threshold"])
        or float(row["oi_change_1h"]) <= 0
        or float(row["vpin_50"]) > float(row["vpin_high_threshold"])
        or direction * float(row["depth_imbalance_1pct"])
        < config.minimum_confirmation_depth
    ):
        return False
    range_width = high - low
    if not math.isfinite(range_width) or range_width <= 0:
        return False
    boundary = high if direction > 0 else low
    accepted = (
        float(row["raw_close_5m"]) >= high + config.sweep_buffer_atr * atr_value
        if direction > 0
        else float(row["raw_close_5m"]) <= low - config.sweep_buffer_atr * atr_value
    )
    if not accepted:
        return False
    confirmation = _first_acceptance_hold(
        raw=raw,
        setup_available=setup_available,
        evaluation_end=evaluation_end,
        direction=direction,
        boundary=boundary,
        atr_value=atr_value,
        config=config,
    )
    if confirmation is None:
        return False
    observed_time, entry = confirmation
    if direction > 0:
        side = "BUY"
        stop = high - config.accepted_stop_inside_atr * atr_value
        target = high + config.accepted_target_range_extension * range_width
    else:
        side = "SELL"
        stop = low + config.accepted_stop_inside_atr * atr_value
        target = low - config.accepted_target_range_extension * range_width
    return _append_signal(
        signals=signals,
        mode=config.mode,
        observed_time=observed_time,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        score=abs(float(row["impact_return"]))
        * (1.0 + abs(float(row["impact_flow"]))),
        max_hold=config.maximum_holding_minutes,
        details={
            "setup_feature_open_utc": str(row.name),
            "setup_feature_available_utc": setup_available.isoformat(),
            "formation_center": center,
            "formation_high": high,
            "formation_low": low,
            "accepted_boundary": boundary,
            "formation_range": range_width,
            "impact_flow": float(row["impact_flow"]),
            "impact_return": float(row["impact_return"]),
            "impact_efficiency": float(row["impact_efficiency"]),
            "oi_change_1h": float(row["oi_change_1h"]),
            "vpin": float(row["vpin_50"]),
            "confirmation_lag_minutes": int(
                (observed_time - setup_available) / pd.Timedelta(minutes=1)
            ),
        },
        costs=costs,
        config=config,
    )


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: ImpactStateConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = family._utc(evaluation_start)
    end = family._utc(evaluation_end)
    five = family._aggregate_raw_five_minute(raw)
    aligned = state.join(
        five[["open", "high", "low", "close", "minute_count"]].rename(
            columns={
                "open": "raw_open_5m",
                "high": "raw_high_5m",
                "low": "raw_low_5m",
                "close": "raw_close_5m",
            }
        ),
        how="inner",
    )
    if aligned.empty:
        raise ValueError("no aligned feature and raw five-minute bars")
    atr = family._true_range(raw).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()

    signals: list[RotationSignal] = []
    cooldown_until_ns = -1
    minimum_history = 12 + config.flow_window_bars_5m
    for i in range(minimum_history, len(aligned)):
        setup_open = aligned.index[i]
        setup_available = setup_open + pd.Timedelta(minutes=5)
        if setup_available >= end or setup_available + pd.Timedelta(
            minutes=config.confirmation_window_minutes
        ) < start:
            continue
        if int(setup_available.value) <= cooldown_until_ns:
            continue
        row = aligned.iloc[i].copy()
        row.name = setup_open
        atr_value = float(atr.asof(setup_available))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        formation, impact, center, high, low = _formation(
            aligned, i=i, flow_bars=config.flow_window_bars_5m
        )
        if config.mode == "ACCEPTED_FLOW_PRICE_DISCOVERY":
            emitted = _accepted_setup(
                row=row,
                impact=impact,
                center=center,
                high=high,
                low=low,
                atr_value=atr_value,
                setup_available=setup_available,
                evaluation_end=end,
                raw=raw,
                config=config,
                costs=costs,
                signals=signals,
            )
        else:
            emitted = _rotation_setup(
                mode=config.mode,
                row=row,
                impact=impact,
                center=center,
                high=high,
                low=low,
                atr_value=atr_value,
                setup_available=setup_available,
                evaluation_end=end,
                raw=raw,
                config=config,
                costs=costs,
                signals=signals,
            )
        if emitted:
            cooldown_until_ns = (
                signals[-1].observed_time_ns + config.cooldown_bars * 5 * NS_MINUTE
            )

    signals = [
        signal
        for signal in signals
        if int(start.value) <= signal.observed_time_ns < int(end.value)
    ]
    signals.sort(key=lambda item: item.observed_time_ns)
    deduped: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in signals:
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        deduped.append(signal)
    for item in deduped:
        if item.source_max_market_time_ns > item.observed_time_ns:
            raise AssertionError("future information detected")
        if item.source_feature_available_time_ns != item.observed_time_ns:
            raise AssertionError("one-minute confirmation availability mismatch")
        if item.source_feature_open_time_ns + NS_MINUTE != item.observed_time_ns:
            raise AssertionError("confirmation minute availability mismatch")
    return deduped
