"""Causal multi-horizon high-dispersion auction rotation for v107.

Signal construction only. NautilusTrader remains the sole execution and
accounting engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import (
    NS_MINUTE,
    CostConfig,
    RotationConfig,
    RotationSignal,
    _true_range,
    cost_after_reward_risk,
)

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class RegimeRotationConfig(RotationConfig):
    auction_windows_5m: tuple[int, ...] = (6, 9, 12, 15, 18)
    flow_cap_quantile: float = 0.70

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RegimeRotationConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(v) for v in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v107 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        RotationConfig.__post_init__(self)
        if tuple(sorted(set(self.auction_windows_5m))) != self.auction_windows_5m:
            raise ValueError("v107 windows must be unique and increasing")
        if any(v < 6 for v in self.auction_windows_5m):
            raise ValueError("v107 windows must be at least 30 minutes")
        if not 0 < self.flow_cap_quantile < 1:
            raise ValueError("v107 flow cap quantile must be in (0,1)")


def _prior_q(series: pd.Series, *, days: int, q: float, minimum: int) -> pd.Series:
    return series.shift(1).rolling(days * 288, min_periods=minimum).quantile(q)


def build_state(features: pd.DataFrame, config: RegimeRotationConfig) -> pd.DataFrame:
    x = features.copy()
    x["vpinq"] = _prior_q(x["vpin_50"], days=config.prior_quantile_days, q=config.vpin_quantile, minimum=config.prior_quantile_min_rows)
    x["abs_oiq"] = _prior_q(x["oi_change_1h"].abs(), days=config.prior_quantile_days, q=config.oi_abs_quantile, minimum=config.prior_quantile_min_rows)
    raw_flow = (2.0 * x["taker_buy_ratio_5m"] - 1.0).abs()
    x["flow_cap"] = _prior_q(raw_flow, days=config.prior_quantile_days, q=config.flow_cap_quantile, minimum=config.prior_quantile_min_rows)
    weight = x["vol_5m"].replace(0.0, np.nan)
    for bars in config.auction_windows_5m:
        prefix = f"h{bars}"
        center = (x["close"] * weight).rolling(bars, min_periods=bars).sum() / weight.rolling(bars, min_periods=bars).sum()
        std = x["close"].rolling(bars, min_periods=bars).std(ddof=0)
        path = x["close"].diff().abs().rolling(bars, min_periods=bars).sum()
        efficiency = (x["close"] - x["close"].shift(bars)).abs() / path.replace(0.0, np.nan)
        width_pct = std / x["close"].replace(0.0, np.nan)
        z = (x["close"] - center) / std.replace(0.0, np.nan)
        x[f"{prefix}_center"] = center
        x[f"{prefix}_std"] = std
        x[f"{prefix}_eff"] = efficiency
        x[f"{prefix}_effq"] = _prior_q(efficiency, days=config.prior_quantile_days, q=config.efficiency_quantile, minimum=config.prior_quantile_min_rows)
        x[f"{prefix}_width_pct"] = width_pct
        x[f"{prefix}_widthq"] = _prior_q(width_pct, days=config.prior_quantile_days, q=config.volatility_quantile, minimum=config.prior_quantile_min_rows)
        x[f"{prefix}_z"] = z
        x[f"{prefix}_z_prev"] = z.shift(1)
    return x


def _normalize(value: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(value)
    return value.tz_localize(UTC) if value.tzinfo is None else value.tz_convert(UTC)


def build_rotation_signals(
    *, state: pd.DataFrame, raw: pd.DataFrame,
    evaluation_start: pd.Timestamp, evaluation_end: pd.Timestamp,
    config: RegimeRotationConfig, costs: CostConfig,
) -> list[RotationSignal]:
    start, end = _normalize(evaluation_start), _normalize(evaluation_end)
    tr = _true_range(raw)
    atr = tr.rolling(config.atr_lookback_minutes, min_periods=max(30, config.atr_lookback_minutes // 2)).median()
    selected: dict[int, RotationSignal] = {}
    for feature_time, row in state.loc[start - pd.Timedelta(days=2):end].iterrows():
        observed = feature_time + pd.Timedelta(minutes=5)
        if not start <= observed < end or observed not in raw.index:
            continue
        common = (
            math.isfinite(float(row.get("vpinq", math.nan)))
            and math.isfinite(float(row.get("abs_oiq", math.nan)))
            and math.isfinite(float(row.get("flow_cap", math.nan)))
            and float(row["vpin_50"]) <= float(row["vpinq"])
            and abs(float(row["oi_change_1h"])) <= float(row["abs_oiq"])
        )
        if not common:
            continue
        atr_value = float(atr.asof(observed))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        entry = float(raw.at[observed, "close"])
        candidates: list[RotationSignal] = []
        for bars in config.auction_windows_5m:
            p = f"h{bars}"
            required = [f"{p}_center", f"{p}_std", f"{p}_eff", f"{p}_effq", f"{p}_width_pct", f"{p}_widthq", f"{p}_z", f"{p}_z_prev"]
            if any(not math.isfinite(float(row.get(key, math.nan))) for key in required):
                continue
            if not (
                float(row[f"{p}_eff"]) <= float(row[f"{p}_effq"])
                and float(row[f"{p}_width_pct"]) >= float(row[f"{p}_widthq"])
            ):
                continue
            z_prev = float(row[f"{p}_z_prev"])
            direction = int(np.sign(z_prev))
            if direction == 0 or abs(z_prev) < config.z_min:
                continue
            excursion_flow = direction * (2.0 * float(state.at[feature_time - pd.Timedelta(minutes=5), "taker_buy_ratio_5m"]) - 1.0) if feature_time - pd.Timedelta(minutes=5) in state.index else math.nan
            if not math.isfinite(excursion_flow) or not (config.excursion_flow_min <= excursion_flow <= float(row["flow_cap"])):
                continue
            confirmation_return = -direction * float(row["log_ret_5m"])
            confirmation_depth = -direction * float(row["depth_imbalance_1pct"])
            if not (
                confirmation_return > config.confirmation_return_min
                and abs(float(row[f"{p}_z"])) < abs(z_prev)
                and confirmation_depth >= config.confirmation_depth_min
            ):
                continue
            side = "BUY" if direction < 0 else "SELL"
            center = float(row[f"{p}_center"])
            std = float(row[f"{p}_std"])
            deviation = abs(z_prev) * std
            target = center + (-direction) * config.target_extension * deviation
            history = raw.loc[(raw.index > observed - pd.Timedelta(minutes=config.stop_lookback_minutes)) & (raw.index <= observed)]
            if len(history) < config.stop_lookback_minutes:
                continue
            if side == "BUY":
                stop = float(history["low"].min() - config.stop_buffer_atr * atr_value)
                valid = stop < entry < target
            else:
                stop = float(history["high"].max() + config.stop_buffer_atr * atr_value)
                valid = target < entry < stop
            if not valid:
                continue
            rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
            if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
                continue
            observed_ns, feature_ns = int(observed.value), int(feature_time.value)
            score = rr + float(row[f"{p}_width_pct"]) / float(row[f"{p}_widthq"]) - excursion_flow
            candidates.append(RotationSignal(
                scenario_id=f"v107-regime-rotation-{bars}-{observed_ns}", observed_time_ns=observed_ns,
                side=side, entry_reference=entry, stop_price=stop, target_price=target,
                cost_after_reward_risk=rr, score=score, max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=feature_ns, source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details={"auction_window_minutes":bars*5,"z_previous":z_prev,"auction_center":center,"auction_std":std,
                         "auction_width_pct":float(row[f"{p}_width_pct"]),"width_threshold":float(row[f"{p}_widthq"]),
                         "auction_efficiency":float(row[f"{p}_eff"]),"efficiency_threshold":float(row[f"{p}_effq"]),
                         "excursion_flow":excursion_flow,"flow_cap":float(row["flow_cap"]),
                         "confirmation_return":confirmation_return,"confirmation_depth":confirmation_depth,
                         "vpin":float(row["vpin_50"]),"vpin_threshold":float(row["vpinq"]),
                         "oi_change_1h":float(row["oi_change_1h"]),"oi_abs_threshold":float(row["abs_oiq"]),"atr_1m":atr_value}
            ))
        if candidates:
            selected[int(observed.value)] = max(candidates, key=lambda s: (s.score, -s.details["auction_window_minutes"]))
    result = sorted(selected.values(), key=lambda s: s.observed_time_ns)
    for signal in result:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information in v107")
        if signal.observed_time_ns != signal.source_feature_open_time_ns + 5 * NS_MINUTE:
            raise AssertionError("v107 feature availability mismatch")
    return result
