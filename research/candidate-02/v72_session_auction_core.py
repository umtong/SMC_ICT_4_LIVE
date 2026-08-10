"""Session-opening auction sweep/acceptance state machine for candidate-02 v72.

At four fixed crypto activity transitions (00:00, 08:00, 13:30 and 16:00
UTC), freeze the completed pre-session auction. During the first 20 minutes:

* replenished failed sweep: aggressive flow consumes one external boundary,
  same-side depth rebuilds, impact is inefficient and the completed minute
  closes back inside. Rest a post-only limit at the consumed boundary and
  target the pre-session equilibrium.
* withdrawn accepted break: aggressive flow closes outside a boundary while
  front-side depth is withdrawn and impact is efficient; a second completed
  outside close confirms acceptance. Rest a post-only limit at the boundary and
  target a measured pre-session-range extension.

No performance is simulated here. NautilusTrader owns limits, fills,
contingent orders, fees, positions and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE = 60_000_000_000
MODE = "SESSION_OPENING_AUCTION_CLASSIFIER"


@dataclass(frozen=True, slots=True)
class SessionAuctionConfig:
    mode: str = MODE
    session_minutes_utc: tuple[int, ...] = (0, 480, 810, 960)
    pre_session_minutes: int = 30
    observation_minutes: int = 20
    prior_window_minutes: int = 720
    prior_minimum_minutes: int = 180
    flow_abs_quantile: float = 0.55
    turnover_quantile: float = 0.40
    depth_change_abs_quantile: float = 0.50
    impact_efficiency_low_quantile: float = 0.45
    impact_efficiency_high_quantile: float = 0.55
    vpin_quantile: float = 0.70
    flow_floor: float = 0.05
    maximum_pre_session_efficiency: float = 0.55
    minimum_range_atr: float = 0.75
    maximum_range_atr: float = 8.0
    touch_tolerance_atr: float = 0.20
    minimum_touches_each_side: int = 1
    minimum_directional_flow: float = 0.08
    sweep_buffer_atr: float = 0.05
    rejection_reclaim_atr: float = 0.00
    rejection_minimum_replenishment: float = 0.0
    rejection_stop_buffer_atr: float = 0.10
    acceptance_close_buffer_atr: float = 0.05
    acceptance_opposite_depth_floor: float = -0.10
    acceptance_stop_inside_atr: float = 0.15
    acceptance_range_extension: float = 0.75
    entry_expiry_minutes: int = 5
    maximum_holding_minutes: int = 120
    atr_lookback_minutes: int = 60
    minimum_cost_after_rr: float = 0.75
    maximum_cost_after_rr: float = 5.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "SessionAuctionConfig":
        data = dict(values)
        if "session_minutes_utc" in data:
            data["session_minutes_utc"] = tuple(int(value) for value in data["session_minutes_utc"])
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v72 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        if self.mode != MODE:
            raise ValueError("v72 mode changed")
        if self.session_minutes_utc != (0, 480, 810, 960):
            raise ValueError("v72 session schedule changed")
        if self.pre_session_minutes not in {20, 30, 45}:
            raise ValueError("pre-session horizon must be 20, 30 or 45 minutes")
        if not 5 <= self.observation_minutes <= 30:
            raise ValueError("invalid observation window")
        if self.prior_minimum_minutes >= self.prior_window_minutes:
            raise ValueError("prior minimum must be below prior window")
        for name in (
            "flow_abs_quantile",
            "turnover_quantile",
            "depth_change_abs_quantile",
            "impact_efficiency_low_quantile",
            "impact_efficiency_high_quantile",
            "vpin_quantile",
        ):
            if not 0 < float(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be in (0,1)")
        if self.impact_efficiency_low_quantile >= self.impact_efficiency_high_quantile:
            raise ValueError("impact thresholds inverted")
        if not 0 < self.minimum_range_atr < self.maximum_range_atr:
            raise ValueError("invalid range bounds")
        if self.minimum_touches_each_side < 1:
            raise ValueError("invalid touch count")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after RR band")


def _prior_quantile(series: pd.Series, config: SessionAuctionConfig, q: float) -> pd.Series:
    return series.shift(1).rolling(
        config.prior_window_minutes,
        min_periods=config.prior_minimum_minutes,
    ).quantile(q)


def build_state(features: pd.DataFrame, config: SessionAuctionConfig) -> pd.DataFrame:
    x = features.copy()
    one_return = np.log(x["close"] / x["close"].shift(1))
    x["impact_efficiency_1m"] = one_return.abs() / (
        x["signed_flow_ratio_1m"].abs() + config.flow_floor
    )
    x["flow_abs_threshold"] = _prior_quantile(
        x["signed_flow_ratio_1m"].abs(), config, config.flow_abs_quantile
    )
    x["turnover_threshold"] = _prior_quantile(
        x["aggressive_total_quote_1m"], config, config.turnover_quantile
    )
    depth_abs = pd.concat(
        [x["ask_depth_change_1m"].abs(), x["bid_depth_change_1m"].abs()], axis=1
    ).max(axis=1)
    x["depth_change_threshold"] = _prior_quantile(
        depth_abs, config, config.depth_change_abs_quantile
    )
    x["impact_efficiency_low_threshold"] = _prior_quantile(
        x["impact_efficiency_1m"], config, config.impact_efficiency_low_quantile
    )
    x["impact_efficiency_high_threshold"] = _prior_quantile(
        x["impact_efficiency_1m"], config, config.impact_efficiency_high_quantile
    )
    x["vpin_threshold"] = _prior_quantile(x["vpin_50"], config, config.vpin_quantile)
    return x


def _atr(raw: pd.DataFrame, lookback: int) -> pd.Series:
    previous = raw["close"].shift(1)
    true_range = pd.concat(
        [
            (raw["high"] - raw["low"]).abs(),
            (raw["high"] - previous).abs(),
            (raw["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(lookback, min_periods=max(30, lookback // 2)).median()


def _passive_costs(costs: CostConfig) -> CostConfig:
    return CostConfig(
        entry_fee_rate=costs.target_fee_rate,
        target_fee_rate=costs.target_fee_rate,
        stop_fee_rate=costs.stop_fee_rate,
        entry_slippage_rate=Decimal("0"),
        stop_slippage_rate=costs.stop_slippage_rate,
        market_impact_rate=costs.market_impact_rate,
        funding_rate_allowance=costs.funding_rate_allowance,
    )


def _append(
    signals: list[RotationSignal],
    *,
    config: SessionAuctionConfig,
    costs: CostConfig,
    observed: pd.Timestamp,
    side: str,
    entry: float,
    stop: float,
    target: float,
    score: float,
    subtype: str,
    details: Mapping[str, Any],
) -> bool:
    valid = stop < entry < target if side == "BUY" else target < entry < stop
    if not valid:
        return False
    reward_risk = cost_after_reward_risk(
        entry=entry,
        stop=stop,
        target=target,
        side=side,
        costs=_passive_costs(costs),
    )
    if not math.isfinite(reward_risk) or not (
        config.minimum_cost_after_rr <= reward_risk <= config.maximum_cost_after_rr
    ):
        return False
    observed_ns = int(observed.value)
    signals.append(
        RotationSignal(
            scenario_id=f"v72-{subtype.lower()}-{config.pre_session_minutes}m-{observed_ns}",
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=reward_risk,
            score=float(score),
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_open_time_ns=observed_ns - NS_MINUTE,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
            details={
                "mode": MODE,
                "session_subtype": subtype,
                "entry_expiry_minutes": config.entry_expiry_minutes,
                "entry_order_type": "POST_ONLY_LIMIT",
                **dict(details),
            },
        )
    )
    return True


def _session_anchors(start: pd.Timestamp, end: pd.Timestamp, minutes: tuple[int, ...]):
    day = start.floor("D")
    while day < end:
        for minute in minutes:
            anchor = day + pd.Timedelta(minutes=minute)
            if start <= anchor < end:
                yield anchor, minute
        day += pd.Timedelta(days=1)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: SessionAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    if start.tz is None:
        start = start.tz_localize("UTC")
    if end.tz is None:
        end = end.tz_localize("UTC")
    x = state.join(
        raw[["open", "high", "low"]].rename(
            columns={"open": "open_1m", "high": "high_1m", "low": "low_1m"}
        ),
        how="inner",
    )
    atr_series = _atr(raw, config.atr_lookback_minutes)
    signals: list[RotationSignal] = []

    for anchor, session_minute in _session_anchors(start, end, config.session_minutes_utc):
        formation = x.loc[
            (x.index >= anchor - pd.Timedelta(minutes=config.pre_session_minutes))
            & (x.index < anchor)
        ]
        if len(formation) < config.pre_session_minutes - 1:
            continue
        atr_value = float(atr_series.asof(anchor))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        high = float(formation["high_1m"].max())
        low = float(formation["low_1m"].min())
        width = high - low
        if not (
            config.minimum_range_atr * atr_value
            <= width
            <= config.maximum_range_atr * atr_value
        ):
            continue
        path = float(formation["close"].diff().abs().sum())
        movement = abs(float(formation["close"].iloc[-1]) - float(formation["open_1m"].iloc[0]))
        efficiency = movement / path if path > 0 else 0.0
        if efficiency > config.maximum_pre_session_efficiency:
            continue
        tolerance = config.touch_tolerance_atr * atr_value
        upper_touches = int((formation["high_1m"] >= high - tolerance).sum())
        lower_touches = int((formation["low_1m"] <= low + tolerance).sum())
        if (
            upper_touches < config.minimum_touches_each_side
            or lower_touches < config.minimum_touches_each_side
        ):
            continue
        weights = formation["aggressive_total_quote_1m"].clip(lower=1e-12)
        center = float((formation["close"] * weights).sum() / weights.sum())
        if not low < center < high:
            center = (high + low) / 2.0
        observation_end = min(end, anchor + pd.Timedelta(minutes=config.observation_minutes))
        observation = x.loc[(x.index >= anchor) & (x.index < observation_end)]
        accepted_candidate: dict[str, Any] | None = None

        for observed_time, row in observation.iterrows():
            required = (
                "flow_abs_threshold",
                "turnover_threshold",
                "depth_change_threshold",
                "impact_efficiency_1m",
                "impact_efficiency_low_threshold",
                "impact_efficiency_high_threshold",
                "vpin_50",
                "vpin_threshold",
                "signed_flow_ratio_1m",
                "ask_depth_change_1m",
                "bid_depth_change_1m",
                "depth_imbalance_1pct",
            )
            if any(not math.isfinite(float(row[name])) for name in required):
                continue
            current_high = float(row["high_1m"])
            current_low = float(row["low_1m"])
            current_close = float(row["close"])
            upper_sweep = current_high >= high + config.sweep_buffer_atr * atr_value
            lower_sweep = current_low <= low - config.sweep_buffer_atr * atr_value
            if upper_sweep == lower_sweep:
                if accepted_candidate is not None:
                    direction = int(accepted_candidate["direction"])
                    boundary = float(accepted_candidate["boundary"])
                    still_outside = current_close > boundary if direction > 0 else current_close < boundary
                    if still_outside:
                        if direction > 0:
                            side = "BUY"
                            entry = high
                            stop = high - config.acceptance_stop_inside_atr * atr_value
                            target = high + config.acceptance_range_extension * width
                        else:
                            side = "SELL"
                            entry = low
                            stop = low + config.acceptance_stop_inside_atr * atr_value
                            target = low - config.acceptance_range_extension * width
                        if _append(
                            signals,
                            config=config,
                            costs=costs,
                            observed=pd.Timestamp(observed_time),
                            side=side,
                            entry=entry,
                            stop=stop,
                            target=target,
                            score=float(accepted_candidate["score"]),
                            subtype="WITHDRAWN_ACCEPTED_BREAK",
                            details={
                                "session_anchor_utc": anchor.isoformat(),
                                "session_minute_utc": session_minute,
                                "pre_session_minutes": config.pre_session_minutes,
                                "auction_high": high,
                                "auction_low": low,
                                "auction_center": center,
                                "auction_width": width,
                                "auction_efficiency": efficiency,
                                "upper_touches": upper_touches,
                                "lower_touches": lower_touches,
                                "first_acceptance_utc": accepted_candidate["time"],
                                "acceptance_direction": direction,
                            },
                        ):
                            break
                    else:
                        accepted_candidate = None
                continue

            direction = 1 if upper_sweep else -1
            directional_flow = direction * float(row["signed_flow_ratio_1m"])
            if directional_flow < max(
                config.minimum_directional_flow,
                float(row["flow_abs_threshold"]),
            ):
                continue
            if float(row["aggressive_total_quote_1m"]) < float(row["turnover_threshold"]):
                continue
            same_depth_change = float(
                row["ask_depth_change_1m"] if direction > 0 else row["bid_depth_change_1m"]
            )
            opposite_depth_change = float(
                row["bid_depth_change_1m"] if direction > 0 else row["ask_depth_change_1m"]
            )
            boundary = high if direction > 0 else low
            extreme = current_high if direction > 0 else current_low
            inside = (
                current_close <= high - config.rejection_reclaim_atr * atr_value
                if direction > 0
                else current_close >= low + config.rejection_reclaim_atr * atr_value
            )
            rejection = (
                inside
                and same_depth_change >= config.rejection_minimum_replenishment
                and float(row["impact_efficiency_1m"])
                <= float(row["impact_efficiency_low_threshold"])
                and float(row["vpin_50"]) <= float(row["vpin_threshold"])
            )
            if rejection:
                if direction > 0:
                    side = "SELL"
                    entry = high
                    stop = extreme + config.rejection_stop_buffer_atr * atr_value
                    target = center
                else:
                    side = "BUY"
                    entry = low
                    stop = extreme - config.rejection_stop_buffer_atr * atr_value
                    target = center
                if _append(
                    signals,
                    config=config,
                    costs=costs,
                    observed=pd.Timestamp(observed_time),
                    side=side,
                    entry=entry,
                    stop=stop,
                    target=target,
                    score=directional_flow * (1.0 + max(0.0, same_depth_change)),
                    subtype="REPLENISHED_FAILED_SWEEP",
                    details={
                        "session_anchor_utc": anchor.isoformat(),
                        "session_minute_utc": session_minute,
                        "pre_session_minutes": config.pre_session_minutes,
                        "auction_high": high,
                        "auction_low": low,
                        "auction_center": center,
                        "auction_width": width,
                        "auction_efficiency": efficiency,
                        "upper_touches": upper_touches,
                        "lower_touches": lower_touches,
                        "sweep_direction": direction,
                        "sweep_extreme": extreme,
                        "directional_flow": directional_flow,
                        "same_side_depth_change": same_depth_change,
                        "opposite_side_depth_change": opposite_depth_change,
                        "impact_efficiency_1m": float(row["impact_efficiency_1m"]),
                        "vpin": float(row["vpin_50"]),
                    },
                ):
                    break

            outside = (
                current_close >= high + config.acceptance_close_buffer_atr * atr_value
                if direction > 0
                else current_close <= low - config.acceptance_close_buffer_atr * atr_value
            )
            withdrawn = same_depth_change <= -float(row["depth_change_threshold"])
            efficient = float(row["impact_efficiency_1m"]) >= float(
                row["impact_efficiency_high_threshold"]
            )
            opposite_stable = opposite_depth_change >= config.acceptance_opposite_depth_floor
            if outside and withdrawn and efficient and opposite_stable:
                accepted_candidate = {
                    "time": pd.Timestamp(observed_time).isoformat(),
                    "direction": direction,
                    "boundary": boundary,
                    "score": directional_flow * (1.0 + abs(same_depth_change)),
                }
            else:
                accepted_candidate = None

    result: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in sorted(signals, key=lambda item: item.observed_time_ns):
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        result.append(signal)
    for signal in result:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
        if signal.source_feature_open_time_ns + NS_MINUTE != signal.observed_time_ns:
            raise AssertionError("session confirmation availability mismatch")
    return result
