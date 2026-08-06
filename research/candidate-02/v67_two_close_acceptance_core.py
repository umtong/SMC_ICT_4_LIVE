"""Controlled v67: depth withdrawal followed by two-close acceptance.

The v66 setup is unchanged. Only the confirmation changes from a physical
boundary retest plus flow/book reconfirmation to two consecutive completed
one-minute closes outside the frozen boundary. This represents a liquidity gap
where price discovery need not revisit the consumed boundary.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Mapping
import numpy as np
import pandas as pd
import v66_depth_flow_core as parent
from v53_nt_core import CostConfig, RotationSignal

MODE="WITHDRAWAL_TWO_CLOSE_ACCEPTANCE"
parent.VALID_MODES.add(MODE)

@dataclass(frozen=True, slots=True)
class TwoCloseConfig(parent.DepthFlowConfig):
    acceptance_closes: int = 2
    @classmethod
    def from_mapping(cls, values: Mapping[str,Any]) -> "TwoCloseConfig":
        unknown=sorted(set(values)-set(cls.__dataclass_fields__))
        if unknown: raise ValueError(f"unknown v67 keys: {unknown}")
        return cls(**dict(values))
    def __post_init__(self) -> None:
        parent.DepthFlowConfig.__post_init__(self)
        if self.mode!=MODE or self.acceptance_closes!=2:
            raise ValueError("v67 confirmation must remain two closes")

def build_state(features:pd.DataFrame, config:TwoCloseConfig)->pd.DataFrame:
    return parent.build_state(features,config)

def _confirmation(state:pd.DataFrame, *, setup:pd.Timestamp, end:pd.Timestamp,
                  direction:int, boundary:float, config:TwoCloseConfig):
    rows=state.loc[(state.index>setup)&(state.index<=min(end,setup+pd.Timedelta(minutes=config.confirmation_window_minutes)))]
    run=0
    for ts,row in rows.iterrows():
        outside=float(row["close"])>boundary if direction>0 else float(row["close"])<boundary
        run=run+1 if outside else 0
        if run>=config.acceptance_closes:
            return pd.Timestamp(ts),float(row["close"])
    return None

def build_rotation_signals(*,state:pd.DataFrame,raw:pd.DataFrame,
        evaluation_start:pd.Timestamp,evaluation_end:pd.Timestamp,
        config:TwoCloseConfig,costs:CostConfig)->list[RotationSignal]:
    start,end=pd.Timestamp(evaluation_start),pd.Timestamp(evaluation_end)
    if start.tz is None:start=start.tz_localize("UTC")
    if end.tz is None:end=end.tz_localize("UTC")
    x=state.join(raw[["high","low"]].rename(columns={"high":"high_1m","low":"low_1m"}),how="inner")
    atr_series=parent._atr(raw,config.atr_lookback_minutes)
    signals=[]; cooldown=-1
    for ts,row in x.loc[(x.index>=start)&(x.index<end)].iterrows():
        if int(ts.value)<=cooldown:continue
        names=("flow_ratio_window","flow_abs_threshold","quote_volume_window","volume_threshold",
               "impact_efficiency","impact_efficiency_high_threshold","depth_change_threshold")
        if any(not math.isfinite(float(row[n])) for n in names):continue
        direction=int(np.sign(float(row["flow_ratio_window"])))
        if direction==0 or abs(float(row["flow_ratio_window"]))<float(row["flow_abs_threshold"]):continue
        if float(row["quote_volume_window"])<float(row["volume_threshold"]):continue
        prior=raw.loc[(raw.index>=ts-pd.Timedelta(minutes=config.boundary_minutes))&(raw.index<ts)]
        window=raw.loc[(raw.index>ts-pd.Timedelta(minutes=config.impact_window_minutes))&(raw.index<=ts)]
        if len(prior)<config.boundary_minutes-1 or window.empty:continue
        high,low=float(prior["high"].max()),float(prior["low"].min()); width=high-low
        atr=float(atr_series.asof(ts))
        if not math.isfinite(width) or width<=0 or not math.isfinite(atr) or atr<=0:continue
        same=float(row["ask_depth_change_window"] if direction>0 else row["bid_depth_change_window"])
        opposite=float(row["bid_depth_change_window"] if direction>0 else row["ask_depth_change_window"])
        boundary=high if direction>0 else low
        extreme=float(window["high"].max() if direction>0 else window["low"].min())
        swept=extreme>=high+config.boundary_break_atr*atr if direction>0 else extreme<=low-config.boundary_break_atr*atr
        closed_outside=float(row["close"])>high if direction>0 else float(row["close"])<low
        if not (swept and closed_outside and same<=-float(row["depth_change_threshold"])
                and opposite>=-0.05 and float(row["impact_efficiency"])>=float(row["impact_efficiency_high_threshold"])):
            continue
        confirmation=_confirmation(x,setup=ts,end=end,direction=direction,boundary=boundary,config=config)
        if confirmation is None:continue
        observed,entry=confirmation
        if direction>0:
            side,stop,target="BUY",high-config.stop_buffer_atr*atr,high+config.target_range_extension*width
        else:
            side,stop,target="SELL",low+config.stop_buffer_atr*atr,low-config.target_range_extension*width
        if parent._append(signals,config=config,costs=costs,observed=observed,side=side,
                entry=entry,stop=stop,target=target,
                score=abs(float(row["flow_ratio_window"]))*(1+abs(same)),
                details={"setup_available_utc":pd.Timestamp(ts).isoformat(),"formation_high":high,
                         "formation_low":low,"external_boundary":boundary,"impact_extreme":extreme,
                         "flow_ratio_window":float(row["flow_ratio_window"]),
                         "impact_efficiency":float(row["impact_efficiency"]),
                         "same_side_depth_change":same,"opposite_side_depth_change":opposite,
                         "acceptance_lag_minutes":int((observed-ts)/pd.Timedelta(minutes=1))}):
            cooldown=int(observed.value)+config.cooldown_minutes*parent.NS_MINUTE
    result=[];seen=set()
    for signal in sorted(signals,key=lambda s:s.observed_time_ns):
        if signal.observed_time_ns not in seen:
            seen.add(signal.observed_time_ns);result.append(signal)
    return result
