"""Direct depth/flow auction states for candidate-02 v66.

A completed aggressive-flow impulse at a frozen 30-minute auction boundary is
classified by the actual one-percent book response:

* WITHDRAWAL_DISCOVERY: liquidity in front of the impulse is withdrawn while
  opposite-side depth is stable/replenishing and price impact is efficient.
  Enter only after the first completed one-minute boundary retest/hold.
* REPLENISHMENT_ABSORPTION: liquidity in front of the impulse replenishes while
  price impact is inefficient. Enter only after the first completed one-minute
  reclaim back into the frozen auction.

This module emits causal trade intents only. NautilusTrader is the exclusive
order, fill, fee, position, account and NAV engine.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Mapping
import numpy as np
import pandas as pd
from v53_nt_core import CostConfig, RotationSignal
from v61_family_core import cost_after_reward_risk

NS_MINUTE = 60_000_000_000
VALID_MODES = {"WITHDRAWAL_DISCOVERY", "REPLENISHMENT_ABSORPTION"}

@dataclass(frozen=True, slots=True)
class DepthFlowConfig:
    mode: str
    impact_window_minutes: int = 3
    prior_window_minutes: int = 720
    prior_minimum_minutes: int = 180
    flow_abs_quantile: float = 0.60
    volume_quantile: float = 0.50
    depth_change_abs_quantile: float = 0.60
    impact_efficiency_low_quantile: float = 0.40
    impact_efficiency_high_quantile: float = 0.60
    flow_floor: float = 0.05
    boundary_minutes: int = 30
    boundary_break_atr: float = 0.05
    retest_tolerance_atr: float = 0.15
    stop_buffer_atr: float = 0.15
    target_range_extension: float = 0.75
    confirmation_window_minutes: int = 3
    atr_lookback_minutes: int = 60
    cooldown_minutes: int = 5
    maximum_holding_minutes: int = 120
    minimum_confirmation_flow: float = 0.0
    minimum_confirmation_book: float = -0.10
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 5.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "DepthFlowConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v66 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"unknown v66 mode: {self.mode}")
        if not 2 <= self.impact_window_minutes <= 10:
            raise ValueError("impact window must be 2-10 minutes")
        if self.prior_minimum_minutes >= self.prior_window_minutes:
            raise ValueError("prior minimum must be below prior window")
        for name in ("flow_abs_quantile", "volume_quantile", "depth_change_abs_quantile",
                     "impact_efficiency_low_quantile", "impact_efficiency_high_quantile"):
            if not 0 < float(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be in (0,1)")
        if self.impact_efficiency_low_quantile >= self.impact_efficiency_high_quantile:
            raise ValueError("impact thresholds inverted")
        if self.boundary_minutes < 15 or self.confirmation_window_minutes < 1:
            raise ValueError("invalid boundary/confirmation window")
        if self.target_range_extension <= 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid target/holding period")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after RR band")

def _prior_quantile(series: pd.Series, config: DepthFlowConfig, q: float) -> pd.Series:
    return series.shift(1).rolling(config.prior_window_minutes,
        min_periods=config.prior_minimum_minutes).quantile(q)

def build_state(features: pd.DataFrame, config: DepthFlowConfig) -> pd.DataFrame:
    x = features.copy()
    n = config.impact_window_minutes
    signed = x["aggressive_signed_quote_1m"].rolling(n, min_periods=n).sum()
    total = x["aggressive_total_quote_1m"].rolling(n, min_periods=n).sum()
    x["flow_ratio_window"] = signed / total.replace(0.0, np.nan)
    x["quote_volume_window"] = total
    x["impact_return"] = np.log(x["close"] / x["close"].shift(n))
    x["impact_efficiency"] = x["impact_return"].abs() / (
        x["flow_ratio_window"].abs() + config.flow_floor)
    x["ask_depth_change_window"] = x["ask_depth_1pct_end"] / x["ask_depth_1pct_end"].shift(n) - 1.0
    x["bid_depth_change_window"] = x["bid_depth_1pct_end"] / x["bid_depth_1pct_end"].shift(n) - 1.0
    x["flow_abs_threshold"] = _prior_quantile(x["flow_ratio_window"].abs(), config, config.flow_abs_quantile)
    x["volume_threshold"] = _prior_quantile(x["quote_volume_window"], config, config.volume_quantile)
    depth_abs = pd.concat([x["ask_depth_change_window"].abs(), x["bid_depth_change_window"].abs()], axis=1).max(axis=1)
    x["depth_change_threshold"] = _prior_quantile(depth_abs, config, config.depth_change_abs_quantile)
    x["impact_efficiency_low_threshold"] = _prior_quantile(x["impact_efficiency"], config, config.impact_efficiency_low_quantile)
    x["impact_efficiency_high_threshold"] = _prior_quantile(x["impact_efficiency"], config, config.impact_efficiency_high_quantile)
    return x

def _atr(raw: pd.DataFrame, lookback: int) -> pd.Series:
    prev = raw["close"].shift(1)
    tr = pd.concat([(raw["high"]-raw["low"]).abs(), (raw["high"]-prev).abs(), (raw["low"]-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(lookback, min_periods=max(30, lookback//2)).median()

def _append(signals: list[RotationSignal], *, config: DepthFlowConfig, costs: CostConfig,
            observed: pd.Timestamp, side: str, entry: float, stop: float, target: float,
            score: float, details: Mapping[str, Any]) -> bool:
    valid = stop < entry < target if side == "BUY" else target < entry < stop
    if not valid:
        return False
    rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
    if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
        return False
    ns = int(observed.value)
    signals.append(RotationSignal(
        scenario_id=f"v66-{config.mode.lower()}-{ns}", observed_time_ns=ns,
        side=side, entry_reference=entry, stop_price=stop, target_price=target,
        cost_after_reward_risk=rr, score=float(score), max_hold_minutes=config.maximum_holding_minutes,
        source_feature_open_time_ns=ns-NS_MINUTE, source_feature_available_time_ns=ns,
        source_max_market_time_ns=ns, details={"mode":config.mode, **dict(details)}))
    return True

def _confirm_discovery(state: pd.DataFrame, *, setup: pd.Timestamp, end: pd.Timestamp,
                       direction: int, boundary: float, atr: float,
                       config: DepthFlowConfig) -> tuple[pd.Timestamp,float] | None:
    rows = state.loc[(state.index > setup) & (state.index <= min(end, setup+pd.Timedelta(minutes=config.confirmation_window_minutes)))]
    touched = False
    for ts, row in rows.iterrows():
        if direction > 0:
            touched = touched or float(row["low_1m"]) <= boundary + config.retest_tolerance_atr*atr
            held = float(row["close"]) > boundary
        else:
            touched = touched or float(row["high_1m"]) >= boundary - config.retest_tolerance_atr*atr
            held = float(row["close"]) < boundary
        flow_ok = direction * float(row["signed_flow_ratio_1m"]) >= config.minimum_confirmation_flow
        book_ok = direction * float(row["depth_imbalance_1pct"]) >= config.minimum_confirmation_book
        if touched and held and flow_ok and book_ok:
            return pd.Timestamp(ts), float(row["close"])
    return None

def _confirm_absorption(state: pd.DataFrame, *, setup: pd.Timestamp, end: pd.Timestamp,
                        direction: int, boundary: float, config: DepthFlowConfig) -> tuple[pd.Timestamp,float] | None:
    rows = state.loc[(state.index > setup) & (state.index <= min(end, setup+pd.Timedelta(minutes=config.confirmation_window_minutes)))]
    for ts, row in rows.iterrows():
        inside = float(row["close"]) < boundary if direction > 0 else float(row["close"]) > boundary
        flow_failed = direction * float(row["signed_flow_ratio_1m"]) <= config.minimum_confirmation_flow
        book_opposes = direction * float(row["depth_imbalance_1pct"]) <= -config.minimum_confirmation_book
        if inside and (flow_failed or book_opposes):
            return pd.Timestamp(ts), float(row["close"])
    return None

def build_rotation_signals(*, state: pd.DataFrame, raw: pd.DataFrame,
        evaluation_start: pd.Timestamp, evaluation_end: pd.Timestamp,
        config: DepthFlowConfig, costs: CostConfig) -> list[RotationSignal]:
    start, end = pd.Timestamp(evaluation_start), pd.Timestamp(evaluation_end)
    if start.tz is None: start = start.tz_localize("UTC")
    if end.tz is None: end = end.tz_localize("UTC")
    x = state.join(raw[["high","low"]].rename(columns={"high":"high_1m","low":"low_1m"}), how="inner")
    atr_series = _atr(raw, config.atr_lookback_minutes)
    signals: list[RotationSignal] = []
    cooldown_ns = -1
    for ts, row in x.loc[(x.index >= start) & (x.index < end)].iterrows():
        if int(ts.value) <= cooldown_ns:
            continue
        needed = ("flow_ratio_window","flow_abs_threshold","quote_volume_window","volume_threshold",
                  "impact_efficiency","impact_efficiency_low_threshold","impact_efficiency_high_threshold",
                  "depth_change_threshold")
        if any(not math.isfinite(float(row[name])) for name in needed):
            continue
        direction = int(np.sign(float(row["flow_ratio_window"])))
        if direction == 0 or abs(float(row["flow_ratio_window"])) < float(row["flow_abs_threshold"]):
            continue
        if float(row["quote_volume_window"]) < float(row["volume_threshold"]):
            continue
        prior = raw.loc[(raw.index >= ts-pd.Timedelta(minutes=config.boundary_minutes)) & (raw.index < ts)]
        impact_raw = raw.loc[(raw.index > ts-pd.Timedelta(minutes=config.impact_window_minutes)) & (raw.index <= ts)]
        if len(prior) < config.boundary_minutes-1 or impact_raw.empty:
            continue
        high, low = float(prior["high"].max()), float(prior["low"].min())
        width = high-low
        atr_value = float(atr_series.asof(ts))
        if not math.isfinite(width) or width <= 0 or not math.isfinite(atr_value) or atr_value <= 0:
            continue
        same_change = float(row["ask_depth_change_window"] if direction>0 else row["bid_depth_change_window"])
        opposite_change = float(row["bid_depth_change_window"] if direction>0 else row["ask_depth_change_window"])
        boundary = high if direction>0 else low
        extreme = float(impact_raw["high"].max() if direction>0 else impact_raw["low"].min())
        swept = extreme >= high+config.boundary_break_atr*atr_value if direction>0 else extreme <= low-config.boundary_break_atr*atr_value
        if not swept:
            continue
        if config.mode == "WITHDRAWAL_DISCOVERY":
            if not (same_change <= -float(row["depth_change_threshold"])
                    and opposite_change >= -0.05
                    and float(row["impact_efficiency"]) >= float(row["impact_efficiency_high_threshold"])
                    and (float(row["close"]) > high if direction>0 else float(row["close"]) < low)):
                continue
            confirmation = _confirm_discovery(x, setup=ts, end=end, direction=direction,
                boundary=boundary, atr=atr_value, config=config)
            if confirmation is None: continue
            observed, entry = confirmation
            if direction>0:
                side, stop, target = "BUY", high-config.stop_buffer_atr*atr_value, high+config.target_range_extension*width
            else:
                side, stop, target = "SELL", low+config.stop_buffer_atr*atr_value, low-config.target_range_extension*width
        else:
            if not (same_change >= float(row["depth_change_threshold"])
                    and float(row["impact_efficiency"]) <= float(row["impact_efficiency_low_threshold"])):
                continue
            confirmation = _confirm_absorption(x, setup=ts, end=end, direction=direction,
                boundary=boundary, config=config)
            if confirmation is None: continue
            observed, entry = confirmation
            if direction>0:
                side, stop, target = "SELL", extreme+config.stop_buffer_atr*atr_value, low
            else:
                side, stop, target = "BUY", extreme-config.stop_buffer_atr*atr_value, high
        if _append(signals, config=config, costs=costs, observed=observed, side=side,
                   entry=entry, stop=stop, target=target,
                   score=abs(float(row["flow_ratio_window"]))*(1+abs(same_change)),
                   details={"setup_available_utc":pd.Timestamp(ts).isoformat(),"formation_high":high,
                       "formation_low":low,"external_boundary":boundary,"impact_extreme":extreme,
                       "flow_ratio_window":float(row["flow_ratio_window"]),
                       "impact_efficiency":float(row["impact_efficiency"]),
                       "same_side_depth_change":same_change,"opposite_side_depth_change":opposite_change,
                       "confirmation_lag_minutes":int((observed-ts)/pd.Timedelta(minutes=1))}):
            cooldown_ns = int(observed.value)+config.cooldown_minutes*NS_MINUTE
    result=[]; seen=set()
    for signal in sorted(signals, key=lambda s:s.observed_time_ns):
        if signal.observed_time_ns not in seen:
            seen.add(signal.observed_time_ns); result.append(signal)
    for signal in result:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
    return result
