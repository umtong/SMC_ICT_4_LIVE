"""Structural new-inventory price discovery for candidate-02 v74.

The v73 new-inventory setup is unchanged: completed 3-minute aggressive flow,
positive OI, efficient impact and front-depth withdrawal must close outside a
frozen short-horizon boundary, followed by a completed outside hold. The
controlled changes are causal and structural:

* enter at the confirmed market price instead of waiting for an adverse passive
  fill at the old boundary;
* invalidate beyond the completed 3-minute event origin, not a 0.15 ATR wick
  inside the boundary;
* target one full frozen-range extension, the next measured external liquidity.

NautilusTrader remains the only performance and execution engine.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any,Mapping
import numpy as np
import pandas as pd
from v53_nt_core import CostConfig,RotationSignal,cost_after_reward_risk
from v73_inventory_depth_core import InventoryDepthConfig,build_state,_atr,NS_MINUTE

@dataclass(frozen=True,slots=True)
class StructuralDiscoveryConfig(InventoryDepthConfig):
    structural_target_range_extension:float=1.0
    event_origin_stop_buffer_atr:float=0.10
    @classmethod
    def from_mapping(cls,values:Mapping[str,Any])->"StructuralDiscoveryConfig":
        unknown=sorted(set(values)-set(cls.__dataclass_fields__))
        if unknown:raise ValueError(f"unknown v74 keys: {unknown}")
        return cls(**dict(values))
    def __post_init__(self)->None:
        InventoryDepthConfig.__post_init__(self)
        if self.structural_target_range_extension<=0 or self.event_origin_stop_buffer_atr<0:raise ValueError("invalid v74 geometry")

def build_rotation_signals(*,state:pd.DataFrame,raw:pd.DataFrame,evaluation_start:pd.Timestamp,evaluation_end:pd.Timestamp,config:StructuralDiscoveryConfig,costs:CostConfig)->list[RotationSignal]:
    start,end=pd.Timestamp(evaluation_start),pd.Timestamp(evaluation_end)
    if start.tz is None:start=start.tz_localize("UTC")
    if end.tz is None:end=end.tz_localize("UTC")
    x=state.join(raw[["open","high","low"]].rename(columns={"open":"open_1m","high":"high_1m","low":"low_1m"}),how="inner");atr_series=_atr(raw,config.atr_lookback_minutes);signals=[];cooldown=-1;n=config.event_minutes
    for ts,row in x.loc[(x.index>=start)&(x.index<end)].iterrows():
        if int(ts.value)<=cooldown:continue
        req=("event_flow","flow_threshold","event_turnover","turnover_threshold","event_return","event_efficiency","efficiency_high","depth_threshold","oi_change_1h","oi_threshold")
        if any(not math.isfinite(float(row[k])) for k in req):continue
        direction=int(np.sign(float(row["event_flow"])))
        if direction==0 or direction*float(row["event_return"])<=0 or abs(float(row["event_flow"]))<max(config.minimum_directional_flow,float(row["flow_threshold"])) or float(row["event_turnover"])<float(row["turnover_threshold"]):continue
        if float(row["oi_change_1h"])<float(row["oi_threshold"]):continue
        formation=raw.loc[(raw.index>=ts-pd.Timedelta(minutes=n+config.boundary_minutes))&(raw.index<ts-pd.Timedelta(minutes=n))];window=raw.loc[(raw.index>ts-pd.Timedelta(minutes=n))&(raw.index<=ts)]
        if len(formation)<config.boundary_minutes-1 or window.empty:continue
        high,low=float(formation["high"].max()),float(formation["low"].min());width=high-low;atr=float(atr_series.asof(ts))
        if not math.isfinite(width) or width<=0 or not math.isfinite(atr) or atr<=0:continue
        boundary=high if direction>0 else low;extreme=float(window["high"].max() if direction>0 else window["low"].min());front_change=float(row["ask_depth_change_event"] if direction>0 else row["bid_depth_change_event"])
        swept=extreme>=high+config.boundary_break_atr*atr if direction>0 else extreme<=low-config.boundary_break_atr*atr;outside=float(row["close"])>high if direction>0 else float(row["close"])<low
        if not (swept and outside and front_change<=-float(row["depth_threshold"]) and float(row["event_efficiency"])>=float(row["efficiency_high"])):continue
        future=x.loc[(x.index>ts)&(x.index<=min(end,ts+pd.Timedelta(minutes=config.discovery_confirmation_minutes)))];confirmation=None
        for observed,r in future.iterrows():
            held=float(r["close"])>boundary if direction>0 else float(r["close"])<boundary
            if held and direction*float(r["signed_flow_ratio_1m"])>=-0.05:confirmation=(pd.Timestamp(observed),float(r["close"]));break
        if confirmation is None:continue
        observed,entry=confirmation;event_origin=float(window["open"].iloc[0])
        if direction>0:side,stop,target="BUY",event_origin-config.event_origin_stop_buffer_atr*atr,high+config.structural_target_range_extension*width
        else:side,stop,target="SELL",event_origin+config.event_origin_stop_buffer_atr*atr,low-config.structural_target_range_extension*width
        valid=stop<entry<target if side=="BUY" else target<entry<stop
        if not valid:continue
        rr=cost_after_reward_risk(entry=entry,stop=stop,target=target,side=side,costs=costs)
        if not math.isfinite(rr) or not config.minimum_cost_after_rr<=rr<=config.maximum_cost_after_rr:continue
        ns=int(observed.value);signals.append(RotationSignal(scenario_id=f"v74-structural-discovery-{config.boundary_minutes}m-{ns}",observed_time_ns=ns,side=side,entry_reference=entry,stop_price=stop,target_price=target,cost_after_reward_risk=rr,score=abs(float(row["event_flow"]))*(1+float(row["oi_change_1h"])/max(float(row["oi_threshold"]),1e-12)),max_hold_minutes=config.maximum_holding_minutes,source_feature_open_time_ns=ns-NS_MINUTE,source_feature_available_time_ns=ns,source_max_market_time_ns=ns,details={"mode":"STRUCTURAL_NEW_INVENTORY_DISCOVERY","setup_utc":pd.Timestamp(ts).isoformat(),"boundary_high":high,"boundary_low":low,"event_origin":event_origin,"event_extreme":extreme,"event_flow":float(row["event_flow"]),"event_efficiency":float(row["event_efficiency"]),"front_depth_change":front_change,"oi_change_1h":float(row["oi_change_1h"]),"entry_order_type":"MARKET","structural_target_range_extension":config.structural_target_range_extension}));cooldown=ns+config.cooldown_minutes*NS_MINUTE
    result=[];seen=set()
    for s in sorted(signals,key=lambda q:q.observed_time_ns):
        if s.observed_time_ns not in seen:seen.add(s.observed_time_ns);result.append(s)
    return result
