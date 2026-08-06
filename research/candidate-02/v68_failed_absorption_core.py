"""Failed passive absorption continuation for candidate-02 v68.

The v66 replenishment/inefficient-impact setup and first boundary reclaim are
unchanged. A trade is released only if price subsequently closes through the
original impact extreme within 30 minutes, proving that replenished passive
liquidity was consumed. This is not a simple direction flip: the failed
absorption state is explicitly confirmed before entering with the original
aggressive flow.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any,Mapping
import numpy as np
import pandas as pd
import v66_depth_flow_core as parent
from v53_nt_core import CostConfig,RotationSignal

MODE="FAILED_REPLENISHMENT_PRICE_DISCOVERY"
parent.VALID_MODES.add(MODE)

@dataclass(frozen=True,slots=True)
class FailedAbsorptionConfig(parent.DepthFlowConfig):
    failure_confirmation_minutes:int=30
    failure_break_buffer_atr:float=0.05
    @classmethod
    def from_mapping(cls,values:Mapping[str,Any])->"FailedAbsorptionConfig":
        unknown=sorted(set(values)-set(cls.__dataclass_fields__))
        if unknown:raise ValueError(f"unknown v68 keys: {unknown}")
        return cls(**dict(values))
    def __post_init__(self)->None:
        parent.DepthFlowConfig.__post_init__(self)
        if self.mode!=MODE:raise ValueError("v68 mode changed")
        if not 5<=self.failure_confirmation_minutes<=60:raise ValueError("invalid failure window")
        if self.failure_break_buffer_atr<0:raise ValueError("invalid failure buffer")

def build_state(features:pd.DataFrame,config:FailedAbsorptionConfig)->pd.DataFrame:
    return parent.build_state(features,config)

def build_rotation_signals(*,state:pd.DataFrame,raw:pd.DataFrame,
        evaluation_start:pd.Timestamp,evaluation_end:pd.Timestamp,
        config:FailedAbsorptionConfig,costs:CostConfig)->list[RotationSignal]:
    start,end=pd.Timestamp(evaluation_start),pd.Timestamp(evaluation_end)
    if start.tz is None:start=start.tz_localize("UTC")
    if end.tz is None:end=end.tz_localize("UTC")
    x=state.join(raw[["high","low"]].rename(columns={"high":"high_1m","low":"low_1m"}),how="inner")
    atr_series=parent._atr(raw,config.atr_lookback_minutes)
    signals=[];cooldown=-1
    for ts,row in x.loc[(x.index>=start)&(x.index<end)].iterrows():
        if int(ts.value)<=cooldown:continue
        names=("flow_ratio_window","flow_abs_threshold","quote_volume_window","volume_threshold","impact_efficiency","impact_efficiency_low_threshold","depth_change_threshold")
        if any(not math.isfinite(float(row[n])) for n in names):continue
        direction=int(np.sign(float(row["flow_ratio_window"])))
        if direction==0 or abs(float(row["flow_ratio_window"]))<float(row["flow_abs_threshold"]):continue
        if float(row["quote_volume_window"])<float(row["volume_threshold"]):continue
        same=float(row["ask_depth_change_window"] if direction>0 else row["bid_depth_change_window"])
        if same<float(row["depth_change_threshold"]):continue
        if float(row["impact_efficiency"])>float(row["impact_efficiency_low_threshold"]):continue
        prior=raw.loc[(raw.index>=ts-pd.Timedelta(minutes=config.boundary_minutes))&(raw.index<ts)]
        window=raw.loc[(raw.index>ts-pd.Timedelta(minutes=config.impact_window_minutes))&(raw.index<=ts)]
        if len(prior)<config.boundary_minutes-1 or window.empty:continue
        high,low=float(prior["high"].max()),float(prior["low"].min());width=high-low;atr=float(atr_series.asof(ts))
        if not math.isfinite(width) or width<=0 or not math.isfinite(atr) or atr<=0:continue
        boundary=high if direction>0 else low;extreme=float(window["high"].max() if direction>0 else window["low"].min())
        swept=extreme>=high+config.boundary_break_atr*atr if direction>0 else extreme<=low-config.boundary_break_atr*atr
        if not swept:continue
        reclaim=parent._confirm_absorption(x,setup=ts,end=end,direction=direction,boundary=boundary,config=config)
        if reclaim is None:continue
        reclaim_time,_=reclaim
        future=x.loc[(x.index>reclaim_time)&(x.index<=min(end,reclaim_time+pd.Timedelta(minutes=config.failure_confirmation_minutes)))]
        confirmation=None
        threshold=extreme+config.failure_break_buffer_atr*atr if direction>0 else extreme-config.failure_break_buffer_atr*atr
        for observed,r in future.iterrows():
            failed=float(r["close"])>threshold if direction>0 else float(r["close"])<threshold
            if failed:
                confirmation=(pd.Timestamp(observed),float(r["close"]));break
        if confirmation is None:continue
        observed,entry=confirmation
        if direction>0:
            side,stop,target="BUY",high-config.stop_buffer_atr*atr,high+config.target_range_extension*width
        else:
            side,stop,target="SELL",low+config.stop_buffer_atr*atr,low-config.target_range_extension*width
        if parent._append(signals,config=config,costs=costs,observed=observed,side=side,entry=entry,stop=stop,target=target,
                score=abs(float(row["flow_ratio_window"]))*(1+same),
                details={"setup_available_utc":pd.Timestamp(ts).isoformat(),"reclaim_utc":reclaim_time.isoformat(),
                         "formation_high":high,"formation_low":low,"external_boundary":boundary,
                         "impact_extreme":extreme,"failure_threshold":threshold,
                         "flow_ratio_window":float(row["flow_ratio_window"]),
                         "impact_efficiency":float(row["impact_efficiency"]),
                         "same_side_depth_change":same,
                         "failure_lag_minutes":int((observed-reclaim_time)/pd.Timedelta(minutes=1))}):
            cooldown=int(observed.value)+config.cooldown_minutes*parent.NS_MINUTE
    result=[];seen=set()
    for s in sorted(signals,key=lambda q:q.observed_time_ns):
        if s.observed_time_ns not in seen:seen.add(s.observed_time_ns);result.append(s)
    return result
