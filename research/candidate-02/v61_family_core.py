"""Causal scenario family for candidate-02 v61.

Each mode defines a different market mechanism and emits deterministic trade
intents from completed observations.  The module deliberately contains no fill,
position, commission or NAV simulation: those remain exclusively owned by the
shared NautilusTrader runner.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE = 60_000_000_000
VALID_MODES = {
    "TREND_PULLBACK_REACCELERATION",
    "TREND_TERMINAL_SWEEP_REVERSAL",
    "LOW_TOXICITY_AUCTION_ROTATION",
    "COMPRESSION_BREAKOUT_RETEST",
    "LIQUIDATION_DISTORTION_RECLAIM",
}


@dataclass(frozen=True, slots=True)
class ScenarioFamilyConfig:
    mode: str
    prior_quantile_days: int = 30
    prior_quantile_min_rows: int = 1000
    atr_lookback_minutes: int = 60
    stop_buffer_atr: float = 0.15
    minimum_cost_after_rr: float = 1.0
    maximum_cost_after_rr: float = 5.0
    maximum_holding_minutes: int = 180
    cooldown_bars: int = 6

    trend_abs_return_quantile: float = 0.60
    terminal_trend_quantile: float = 0.80
    vpin_low_quantile: float = 0.55
    vpin_high_quantile: float = 0.70
    oi_abs_quantile: float = 0.70
    efficiency_low_quantile: float = 0.40
    efficiency_mid_quantile: float = 0.50
    realized_vol_low_quantile: float = 0.35
    displacement_quantile: float = 0.60
    liquidation_return_quantile: float = 0.80

    minimum_confirmation_flow: float = 0.0
    minimum_confirmation_depth: float = -0.10
    pullback_minimum_fraction: float = 0.20
    pullback_maximum_fraction: float = 0.60
    target_pullback_extension: float = 0.50

    minimum_sweep_atr: float = 0.10
    minimum_reclaim_atr: float = 0.10

    auction_z_min: float = 1.50
    auction_target_opposite_sigma: float = 0.50
    auction_stop_sigma: float = 2.00

    breakout_minimum_atr: float = 0.10
    retest_tolerance_atr: float = 0.25
    breakout_target_range_extension: float = 1.00

    liquidation_target_origin_fraction: float = 1.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ScenarioFamilyConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v61 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"unknown v61 mode: {self.mode}")
        if self.prior_quantile_days <= 0 or self.prior_quantile_min_rows <= 0:
            raise ValueError("prior history must be positive")
        if self.atr_lookback_minutes < 30:
            raise ValueError("ATR lookback must be at least 30 minutes")
        if self.stop_buffer_atr < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid stop buffer or holding period")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown cannot be negative")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk bounds")
        for name in (
            "trend_abs_return_quantile",
            "terminal_trend_quantile",
            "vpin_low_quantile",
            "vpin_high_quantile",
            "oi_abs_quantile",
            "efficiency_low_quantile",
            "efficiency_mid_quantile",
            "realized_vol_low_quantile",
            "displacement_quantile",
            "liquidation_return_quantile",
        ):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if not 0 < self.pullback_minimum_fraction < self.pullback_maximum_fraction < 1:
            raise ValueError("invalid pullback fraction bounds")
        if self.auction_z_min <= 0 or self.auction_stop_sigma <= self.auction_z_min:
            raise ValueError("invalid auction z geometry")
        if self.breakout_target_range_extension <= 0:
            raise ValueError("breakout target extension must be positive")


def _prior_quantile(
    series: pd.Series,
    *,
    config: ScenarioFamilyConfig,
    quantile: float,
) -> pd.Series:
    return series.shift(1).rolling(
        config.prior_quantile_days * 288,
        min_periods=config.prior_quantile_min_rows,
    ).quantile(quantile)


def build_state(features: pd.DataFrame, config: ScenarioFamilyConfig) -> pd.DataFrame:
    x = features.copy()
    signed_flow = 2.0 * x["taker_buy_ratio_5m"] - 1.0
    weight = x["vol_5m"].clip(lower=1e-12)
    x["signed_flow"] = signed_flow

    x["abs_return_60m"] = x["log_ret_60m"].abs()
    x["trend_threshold"] = _prior_quantile(
        x["abs_return_60m"], config=config, quantile=config.trend_abs_return_quantile
    )
    x["terminal_trend_threshold"] = _prior_quantile(
        x["abs_return_60m"], config=config, quantile=config.terminal_trend_quantile
    )
    x["vpin_low_threshold"] = _prior_quantile(
        x["vpin_50"], config=config, quantile=config.vpin_low_quantile
    )
    x["vpin_high_threshold"] = _prior_quantile(
        x["vpin_50"], config=config, quantile=config.vpin_high_quantile
    )
    x["oi_abs_threshold"] = _prior_quantile(
        x["oi_change_1h"].abs(), config=config, quantile=config.oi_abs_quantile
    )
    x["realized_vol_low_threshold"] = _prior_quantile(
        x["realized_vol_30m"], config=config, quantile=config.realized_vol_low_quantile
    )
    x["displacement_threshold"] = _prior_quantile(
        x["log_ret_5m"].abs(), config=config, quantile=config.displacement_quantile
    )
    x["liquidation_return_threshold"] = _prior_quantile(
        x["log_ret_15m"].abs(), config=config, quantile=config.liquidation_return_quantile
    )

    path_60 = x["close"].diff().abs().rolling(12, min_periods=12).sum()
    x["efficiency_60m"] = (
        (x["close"] - x["close"].shift(12)).abs()
        / path_60.replace(0.0, np.nan)
    )
    x["efficiency_low_threshold"] = _prior_quantile(
        x["efficiency_60m"], config=config, quantile=config.efficiency_low_quantile
    )
    x["efficiency_mid_threshold"] = _prior_quantile(
        x["efficiency_60m"], config=config, quantile=config.efficiency_mid_quantile
    )

    weighted = (x["close"] * weight).rolling(12, min_periods=12).sum()
    weight_sum = weight.rolling(12, min_periods=12).sum()
    x["vwap_60m"] = weighted / weight_sum
    x["std_60m"] = x["close"].rolling(12, min_periods=12).std(ddof=0)
    x["z_60m"] = (
        (x["close"] - x["vwap_60m"])
        / x["std_60m"].replace(0.0, np.nan)
    )

    x["flow_15m"] = (
        (signed_flow * weight).rolling(3, min_periods=3).sum()
        / weight.rolling(3, min_periods=3).sum()
    )
    return x


def _aggregate_raw_five_minute(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("raw one-minute data are empty")
    frame = raw.copy()
    frame["bucket_open"] = (frame.index - pd.Timedelta(nanoseconds=1)).floor("5min")
    bars = frame.groupby("bucket_open", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        minute_count=("close", "size"),
    )
    bars.index = pd.DatetimeIndex(bars.index, tz="UTC")
    bars.index.name = "bar_open_utc"
    return bars.loc[bars["minute_count"] == 5].copy()


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


def _utc(value: pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def _weighted_center(frame: pd.DataFrame) -> float:
    weights = np.maximum(frame["vol_5m"].to_numpy(dtype=np.float64), 1e-12)
    return float(np.average(frame["raw_close_5m"].to_numpy(dtype=np.float64), weights=weights))


def _emit(
    *,
    signals: list[RotationSignal],
    mode: str,
    observed_time: pd.Timestamp,
    feature_open: pd.Timestamp,
    side: str,
    entry: float,
    stop: float,
    target: float,
    max_hold: int,
    score: float,
    details: Mapping[str, Any],
    costs: CostConfig,
    config: ScenarioFamilyConfig,
) -> bool:
    geometry_valid = stop < entry < target if side == "BUY" else target < entry < stop
    if not geometry_valid:
        return False
    reward_risk = cost_after_reward_risk(
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
    source_ns = int(feature_open.value)
    signals.append(
        RotationSignal(
            scenario_id=f"v61-{mode.lower()}-{observed_ns}",
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=reward_risk,
            score=float(score),
            max_hold_minutes=max_hold,
            source_feature_open_time_ns=source_ns,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
            details={"mode": mode, **dict(details)},
        )
    )
    return True


def _trend_pullback(
    *,
    i: int,
    aligned: pd.DataFrame,
    raw: pd.DataFrame,
    atr_value: float,
    observed_time: pd.Timestamp,
    costs: CostConfig,
    config: ScenarioFamilyConfig,
    signals: list[RotationSignal],
) -> bool:
    row = aligned.iloc[i]
    prior = aligned.iloc[i - 1]
    trend_return = float(prior["log_ret_60m"])
    direction = int(np.sign(trend_return))
    if direction == 0 or abs(trend_return) < float(prior["trend_threshold"]):
        return False
    pullback_start = float(aligned.iloc[i - 4]["raw_close_5m"])
    pullback_end = float(prior["raw_close_5m"])
    pullback_return = math.log(pullback_end / pullback_start)
    pullback_fraction = abs(pullback_return) / max(abs(trend_return), 1e-12)
    if direction * pullback_return >= 0 or not (
        config.pullback_minimum_fraction
        <= pullback_fraction
        <= config.pullback_maximum_fraction
    ):
        return False
    if (
        direction * float(row["log_ret_5m"]) <= 0
        or direction * float(row["signed_flow"]) < config.minimum_confirmation_flow
        or direction * float(row["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
        or float(row["oi_change_1h"]) <= 0
        or float(row["vpin_50"]) > float(row["vpin_high_threshold"])
    ):
        return False
    entry = float(raw.at[observed_time, "close"])
    pullback_window = aligned.iloc[i - 3 : i + 1]
    prior_trend_window = aligned.iloc[max(0, i - 15) : i]
    pullback_distance = abs(pullback_end - pullback_start)
    if direction > 0:
        stop = float(pullback_window["raw_low_5m"].min() - config.stop_buffer_atr * atr_value)
        external = float(prior_trend_window["raw_high_5m"].max())
        target = external + config.target_pullback_extension * pullback_distance
        side = "BUY"
    else:
        stop = float(pullback_window["raw_high_5m"].max() + config.stop_buffer_atr * atr_value)
        external = float(prior_trend_window["raw_low_5m"].min())
        target = external - config.target_pullback_extension * pullback_distance
        side = "SELL"
    return _emit(
        signals=signals,
        mode=config.mode,
        observed_time=observed_time,
        feature_open=aligned.index[i],
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        max_hold=config.maximum_holding_minutes,
        score=abs(trend_return) * (1.0 + abs(float(row["signed_flow"]))),
        details={
            "trend_return_60m": trend_return,
            "trend_threshold": float(prior["trend_threshold"]),
            "pullback_return_15m": pullback_return,
            "pullback_fraction": pullback_fraction,
            "prior_external_liquidity": external,
        },
        costs=costs,
        config=config,
    )


def _terminal_reversal(
    *,
    i: int,
    aligned: pd.DataFrame,
    raw: pd.DataFrame,
    atr_value: float,
    observed_time: pd.Timestamp,
    costs: CostConfig,
    config: ScenarioFamilyConfig,
    signals: list[RotationSignal],
) -> bool:
    row = aligned.iloc[i]
    prior = aligned.iloc[i - 1]
    trend_return = float(prior["log_ret_60m"])
    direction = int(np.sign(trend_return))
    if direction == 0 or abs(trend_return) < float(prior["terminal_trend_threshold"]):
        return False
    toxic_or_closing = (
        float(prior["vpin_50"]) >= float(prior["vpin_high_threshold"])
        or float(prior["oi_change_1h"]) < 0
    )
    if not toxic_or_closing:
        return False
    history = aligned.iloc[i - 12 : i]
    high = float(history["raw_high_5m"].max())
    low = float(history["raw_low_5m"].min())
    midpoint = (high + low) / 2.0
    if direction > 0:
        swept = float(row["raw_high_5m"]) >= high + config.minimum_sweep_atr * atr_value
        reclaimed = float(row["raw_close_5m"]) <= high - config.minimum_reclaim_atr * atr_value
        extreme = float(row["raw_high_5m"])
    else:
        swept = float(row["raw_low_5m"]) <= low - config.minimum_sweep_atr * atr_value
        reclaimed = float(row["raw_close_5m"]) >= low + config.minimum_reclaim_atr * atr_value
        extreme = float(row["raw_low_5m"])
    if not swept or not reclaimed:
        return False
    if (
        -direction * float(row["signed_flow"]) < config.minimum_confirmation_flow
        or -direction * float(row["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
    ):
        return False
    entry = float(raw.at[observed_time, "close"])
    if direction > 0:
        side = "SELL"
        stop = extreme + config.stop_buffer_atr * atr_value
    else:
        side = "BUY"
        stop = extreme - config.stop_buffer_atr * atr_value
    return _emit(
        signals=signals,
        mode=config.mode,
        observed_time=observed_time,
        feature_open=aligned.index[i],
        side=side,
        entry=entry,
        stop=stop,
        target=midpoint,
        max_hold=config.maximum_holding_minutes,
        score=abs(trend_return) * (1.0 + float(prior["vpin_50"])),
        details={
            "trend_return_60m": trend_return,
            "terminal_threshold": float(prior["terminal_trend_threshold"]),
            "prior_range_low": low,
            "prior_range_high": high,
            "prior_range_midpoint": midpoint,
            "toxic_vpin": float(prior["vpin_50"]),
            "closing_oi_change": float(prior["oi_change_1h"]),
        },
        costs=costs,
        config=config,
    )


def _auction_rotation(
    *,
    i: int,
    aligned: pd.DataFrame,
    raw: pd.DataFrame,
    atr_value: float,
    observed_time: pd.Timestamp,
    costs: CostConfig,
    config: ScenarioFamilyConfig,
    signals: list[RotationSignal],
) -> bool:
    # The frozen source auction excludes the excursion bar i-1.
    formation = aligned.iloc[i - 13 : i - 1]
    excursion = aligned.iloc[i - 1]
    row = aligned.iloc[i]
    formation_state = aligned.iloc[i - 2]
    if len(formation) != 12:
        return False
    if (
        float(formation_state["efficiency_60m"])
        > float(formation_state["efficiency_low_threshold"])
        or float(formation_state["vpin_50"])
        > float(formation_state["vpin_low_threshold"])
        or abs(float(formation_state["oi_change_1h"]))
        > float(formation_state["oi_abs_threshold"])
    ):
        return False
    center = _weighted_center(formation)
    std = float(formation["raw_close_5m"].std(ddof=0))
    if not math.isfinite(std) or std <= 0:
        return False
    excursion_z = (float(excursion["raw_close_5m"]) - center) / std
    direction = int(np.sign(excursion_z))
    if direction == 0 or abs(excursion_z) < config.auction_z_min:
        return False
    if direction * float(excursion["signed_flow"]) < config.minimum_confirmation_flow:
        return False
    current_z = (float(row["raw_close_5m"]) - center) / std
    if (
        abs(current_z) >= abs(excursion_z)
        or -direction * float(row["log_ret_5m"]) <= 0
        or -direction * float(row["signed_flow"]) < config.minimum_confirmation_flow
        or -direction * float(row["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
    ):
        return False
    entry = float(raw.at[observed_time, "close"])
    stop = center + direction * config.auction_stop_sigma * std
    target = center - direction * config.auction_target_opposite_sigma * std
    side = "SELL" if direction > 0 else "BUY"
    return _emit(
        signals=signals,
        mode=config.mode,
        observed_time=observed_time,
        feature_open=aligned.index[i],
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        max_hold=config.maximum_holding_minutes,
        score=abs(excursion_z) * (1.0 + abs(float(row["signed_flow"]))),
        details={
            "frozen_auction_center": center,
            "frozen_auction_std": std,
            "excursion_z": excursion_z,
            "confirmation_z": current_z,
            "formation_efficiency": float(formation_state["efficiency_60m"]),
            "formation_vpin": float(formation_state["vpin_50"]),
            "formation_oi_change": float(formation_state["oi_change_1h"]),
        },
        costs=costs,
        config=config,
    )


def _compression_breakout(
    *,
    i: int,
    aligned: pd.DataFrame,
    raw: pd.DataFrame,
    atr_value: float,
    observed_time: pd.Timestamp,
    costs: CostConfig,
    config: ScenarioFamilyConfig,
    signals: list[RotationSignal],
) -> bool:
    formation = aligned.iloc[i - 13 : i - 1]
    breakout = aligned.iloc[i - 1]
    row = aligned.iloc[i]
    formation_state = aligned.iloc[i - 2]
    if len(formation) != 12:
        return False
    if (
        float(formation_state["realized_vol_30m"])
        > float(formation_state["realized_vol_low_threshold"])
        or float(formation_state["efficiency_60m"])
        > float(formation_state["efficiency_mid_threshold"])
        or float(breakout["oi_change_1h"]) <= 0
        or abs(float(breakout["log_ret_5m"]))
        < float(breakout["displacement_threshold"])
    ):
        return False
    high = float(formation["raw_high_5m"].max())
    low = float(formation["raw_low_5m"].min())
    range_width = high - low
    if range_width <= 0:
        return False
    up = float(breakout["raw_close_5m"]) >= high + config.breakout_minimum_atr * atr_value
    down = float(breakout["raw_close_5m"]) <= low - config.breakout_minimum_atr * atr_value
    if up == down:
        return False
    direction = 1 if up else -1
    if (
        direction * float(breakout["signed_flow"]) < config.minimum_confirmation_flow
        or direction * float(breakout["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
    ):
        return False
    boundary = high if direction > 0 else low
    touched = (
        float(row["raw_low_5m"]) <= boundary + config.retest_tolerance_atr * atr_value
        if direction > 0
        else float(row["raw_high_5m"]) >= boundary - config.retest_tolerance_atr * atr_value
    )
    held = float(row["raw_close_5m"]) > boundary if direction > 0 else float(row["raw_close_5m"]) < boundary
    if (
        not touched
        or not held
        or direction * float(row["log_ret_5m"]) <= 0
        or direction * float(row["signed_flow"]) < config.minimum_confirmation_flow
        or direction * float(row["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
    ):
        return False
    entry = float(raw.at[observed_time, "close"])
    stop = boundary - direction * config.stop_buffer_atr * atr_value
    target = boundary + direction * config.breakout_target_range_extension * range_width
    side = "BUY" if direction > 0 else "SELL"
    return _emit(
        signals=signals,
        mode=config.mode,
        observed_time=observed_time,
        feature_open=aligned.index[i],
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        max_hold=config.maximum_holding_minutes,
        score=abs(float(breakout["log_ret_5m"])) * (1.0 + abs(float(row["signed_flow"]))),
        details={
            "frozen_range_low": low,
            "frozen_range_high": high,
            "frozen_range_width": range_width,
            "breakout_direction": direction,
            "breakout_return": float(breakout["log_ret_5m"]),
            "breakout_oi_change": float(breakout["oi_change_1h"]),
            "formation_realized_vol": float(formation_state["realized_vol_30m"]),
            "formation_efficiency": float(formation_state["efficiency_60m"]),
        },
        costs=costs,
        config=config,
    )


def _liquidation_reclaim(
    *,
    i: int,
    aligned: pd.DataFrame,
    raw: pd.DataFrame,
    atr_value: float,
    observed_time: pd.Timestamp,
    costs: CostConfig,
    config: ScenarioFamilyConfig,
    signals: list[RotationSignal],
) -> bool:
    row = aligned.iloc[i]
    event_end = aligned.iloc[i - 1]
    event_origin = aligned.iloc[i - 4]
    event_return = math.log(float(event_end["raw_close_5m"]) / float(event_origin["raw_close_5m"]))
    direction = int(np.sign(event_return))
    if (
        direction == 0
        or abs(event_return) < float(event_end["liquidation_return_threshold"])
        or float(event_end["oi_change_1h"]) >= 0
        or direction * float(event_end["flow_15m"]) < config.minimum_confirmation_flow
    ):
        return False
    origin_price = float(event_origin["raw_close_5m"])
    end_price = float(event_end["raw_close_5m"])
    midpoint = (origin_price + end_price) / 2.0
    reclaimed = float(row["raw_close_5m"]) <= midpoint if direction > 0 else float(row["raw_close_5m"]) >= midpoint
    if (
        not reclaimed
        or -direction * float(row["log_ret_5m"]) <= 0
        or -direction * float(row["signed_flow"]) < config.minimum_confirmation_flow
        or -direction * float(row["depth_imbalance_1pct"]) < config.minimum_confirmation_depth
    ):
        return False
    event_window = aligned.iloc[i - 3 : i + 1]
    entry = float(raw.at[observed_time, "close"])
    target = end_price + config.liquidation_target_origin_fraction * (origin_price - end_price)
    if direction > 0:
        side = "SELL"
        stop = float(event_window["raw_high_5m"].max() + config.stop_buffer_atr * atr_value)
    else:
        side = "BUY"
        stop = float(event_window["raw_low_5m"].min() - config.stop_buffer_atr * atr_value)
    return _emit(
        signals=signals,
        mode=config.mode,
        observed_time=observed_time,
        feature_open=aligned.index[i],
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        max_hold=config.maximum_holding_minutes,
        score=abs(event_return) * (1.0 + abs(float(event_end["flow_15m"]))),
        details={
            "event_return_15m": event_return,
            "liquidation_threshold": float(event_end["liquidation_return_threshold"]),
            "event_oi_change": float(event_end["oi_change_1h"]),
            "event_flow_15m": float(event_end["flow_15m"]),
            "event_origin": origin_price,
            "event_end": end_price,
            "event_midpoint": midpoint,
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
    config: ScenarioFamilyConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _utc(evaluation_start)
    end = _utc(evaluation_end)
    five = _aggregate_raw_five_minute(raw)
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
    atr = _true_range(raw).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    signals: list[RotationSignal] = []
    cooldown_until_ns = -1
    minimum_history = 16
    for i in range(minimum_history, len(aligned)):
        feature_open = aligned.index[i]
        observed_time = feature_open + pd.Timedelta(minutes=5)
        if not start <= observed_time < end:
            continue
        observed_ns = int(observed_time.value)
        if observed_ns <= cooldown_until_ns or observed_time not in raw.index:
            continue
        atr_value = float(atr.asof(observed_time))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        kwargs = {
            "i": i,
            "aligned": aligned,
            "raw": raw,
            "atr_value": atr_value,
            "observed_time": observed_time,
            "costs": costs,
            "config": config,
            "signals": signals,
        }
        before = len(signals)
        if config.mode == "TREND_PULLBACK_REACCELERATION":
            _trend_pullback(**kwargs)
        elif config.mode == "TREND_TERMINAL_SWEEP_REVERSAL":
            _terminal_reversal(**kwargs)
        elif config.mode == "LOW_TOXICITY_AUCTION_ROTATION":
            _auction_rotation(**kwargs)
        elif config.mode == "COMPRESSION_BREAKOUT_RETEST":
            _compression_breakout(**kwargs)
        elif config.mode == "LIQUIDATION_DISTORTION_RECLAIM":
            _liquidation_reclaim(**kwargs)
        else:  # pragma: no cover - constructor guards this
            raise AssertionError(config.mode)
        if len(signals) > before:
            cooldown_until_ns = observed_ns + config.cooldown_bars * 5 * NS_MINUTE

    signals.sort(key=lambda item: item.observed_time_ns)
    if len({item.observed_time_ns for item in signals}) != len(signals):
        raise ValueError("duplicate v61 signal timestamps")
    for item in signals:
        if item.source_max_market_time_ns > item.observed_time_ns:
            raise AssertionError("future information detected")
        if item.source_feature_open_time_ns + 5 * NS_MINUTE != item.observed_time_ns:
            raise AssertionError("feature availability mismatch")
    return signals
