"""Causal absorbed-inventory pullback continuation; NT owns performance."""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Mapping
import numpy as np
import pandas as pd
import v61_family_core as family
import v63_impact_state_core as impact
from v53_nt_core import CostConfig, RotationSignal
from v63_impact_state_core import ImpactStateConfig, NS_MINUTE, _append_signal, _first_reclaim, _formation, build_state

MODE = "ABSORBED_INVENTORY_PULLBACK_CONTINUATION"
family.VALID_MODES.add(MODE)
impact.VALID_MODES.add(MODE)

@dataclass(frozen=True, slots=True)
class AbsorbedPullbackConfig(ImpactStateConfig):
    target_range_extension: float = 0.50
    local_stop_buffer_atr: float = 0.10
    maximum_reclaim_depth_fraction: float = 0.50
    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AbsorbedPullbackConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v64 config keys: {unknown}")
        return cls(**dict(values))
    def __post_init__(self) -> None:
        ImpactStateConfig.__post_init__(self)
        if self.mode != MODE:
            raise ValueError("v64 mode changed")
        if self.target_range_extension <= 0 or self.local_stop_buffer_atr < 0:
            raise ValueError("invalid v64 target/stop")
        if not 0 < self.maximum_reclaim_depth_fraction <= 1:
            raise ValueError("invalid reclaim depth")

def build_rotation_signals(*, state: pd.DataFrame, raw: pd.DataFrame,
    evaluation_start: pd.Timestamp, evaluation_end: pd.Timestamp,
    config: AbsorbedPullbackConfig, costs: CostConfig) -> list[RotationSignal]:
    start, end = family._utc(evaluation_start), family._utc(evaluation_end)
    five = family._aggregate_raw_five_minute(raw)
    aligned = state.join(five[["open","high","low","close","minute_count"]].rename(columns={
        "open":"raw_open_5m","high":"raw_high_5m","low":"raw_low_5m","close":"raw_close_5m"}), how="inner")
    if aligned.empty:
        raise ValueError("no aligned bars")
    atr = family._true_range(raw).rolling(config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2)).median()
    signals: list[RotationSignal] = []
    cooldown_until_ns = -1
    for i in range(12 + config.flow_window_bars_5m, len(aligned)):
        setup_open = aligned.index[i]
        setup_available = setup_open + pd.Timedelta(minutes=5)
        if setup_available >= end or int(setup_available.value) <= cooldown_until_ns:
            continue
        row = aligned.iloc[i].copy(); row.name = setup_open
        atr_value = float(atr.asof(setup_available))
        if not math.isfinite(atr_value) or atr_value <= 0:
            continue
        _, window, center, high, low = _formation(aligned, i=i, flow_bars=config.flow_window_bars_5m)
        direction = int(np.sign(float(row["impact_return"])))
        if (direction == 0 or direction != int(np.sign(float(row["impact_flow"])))
            or abs(float(row["impact_flow"])) < float(row["impact_flow_threshold"])
            or direction * float(row["z_60m"]) < config.edge_z_min
            or float(row["oi_change_1h"]) <= 0
            or float(row["impact_efficiency"]) > float(row["impact_efficiency_low_threshold"])):
            continue
        extreme = float(window["raw_high_5m"].max()) if direction > 0 else float(window["raw_low_5m"].min())
        boundary = high if direction > 0 else low
        swept = extreme >= high + config.sweep_buffer_atr * atr_value if direction > 0 else extreme <= low - config.sweep_buffer_atr * atr_value
        if not swept:
            continue
        reclaim = _first_reclaim(raw=raw, setup_available=setup_available,
            evaluation_end=end, direction=direction, boundary=boundary, center=center,
            window_minutes=config.confirmation_window_minutes)
        if reclaim is None:
            continue
        observed_time, entry = reclaim
        width = high - low
        if not math.isfinite(width) or width <= 0:
            continue
        max_depth = config.maximum_reclaim_depth_fraction * abs(boundary - center)
        minute = raw.loc[observed_time]
        if direction > 0:
            if entry < boundary - max_depth: continue
            side, stop, target = "BUY", float(minute["low"]) - config.local_stop_buffer_atr * atr_value, high + config.target_range_extension * width
        else:
            if entry > boundary + max_depth: continue
            side, stop, target = "SELL", float(minute["high"]) + config.local_stop_buffer_atr * atr_value, low - config.target_range_extension * width
        if _append_signal(signals=signals, mode=config.mode, observed_time=observed_time,
            side=side, entry=entry, stop=stop, target=target,
            score=abs(float(row["impact_flow"])) * (1 + abs(float(row["z_60m"]))),
            max_hold=config.maximum_holding_minutes, costs=costs, config=config,
            details={"setup_feature_open_utc":str(setup_open),"formation_center":center,
            "formation_high":high,"formation_low":low,"external_boundary":boundary,
            "impact_extreme":extreme,"impact_flow":float(row["impact_flow"]),
            "impact_return":float(row["impact_return"]),"impact_efficiency":float(row["impact_efficiency"]),
            "oi_change_1h":float(row["oi_change_1h"]),"reclaim_lag_minutes":int((observed_time-setup_available)/pd.Timedelta(minutes=1))}):
            cooldown_until_ns = signals[-1].observed_time_ns + config.cooldown_bars * 5 * NS_MINUTE
    result, seen = [], set()
    for signal in sorted((s for s in signals if int(start.value) <= s.observed_time_ns < int(end.value)), key=lambda x:x.observed_time_ns):
        if signal.observed_time_ns not in seen:
            seen.add(signal.observed_time_ns); result.append(signal)
    for signal in result:
        if signal.source_max_market_time_ns > signal.observed_time_ns or signal.source_feature_open_time_ns + NS_MINUTE != signal.observed_time_ns:
            raise AssertionError("future information detected")
    return result
