"""Inventory-aware direct-depth auction classifier for candidate-02 v73.

Completed 3-minute aggressive flow reaches a frozen short-horizon boundary.
Positive OI with front-depth withdrawal and efficient impact is treated as new
inventory price discovery. Material OI decline with a boundary sweep is treated
as forced liquidation; only causal depth recovery and range reclaim release a
rotation. Signals are passive boundary retests; NautilusTrader owns performance.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Mapping
import numpy as np
import pandas as pd
from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE=60_000_000_000
MODE="INVENTORY_DEPTH_AUCTION_CLASSIFIER"

@dataclass(frozen=True,slots=True)
class InventoryDepthConfig:
    mode:str=MODE
    event_minutes:int=3
    boundary_minutes:int=15
    prior_window_minutes:int=720
    prior_minimum_minutes:int=180
    flow_abs_quantile:float=0.60
    turnover_quantile:float=0.50
    depth_change_abs_quantile:float=0.55
    impact_efficiency_low_quantile:float=0.40
    impact_efficiency_high_quantile:float=0.60
    oi_abs_quantile:float=0.60
    vpin_quantile:float=0.60
    flow_floor:float=0.05
    minimum_directional_flow:float=0.08
    boundary_break_atr:float=0.05
    discovery_confirmation_minutes:int=2
    discovery_stop_inside_atr:float=0.15
    discovery_range_extension:float=0.75
    liquidation_confirmation_minutes:int=3
    liquidation_minimum_depth_recovery:float=0.0
    liquidation_stop_buffer_atr:float=0.10
    entry_expiry_minutes:int=5
    maximum_holding_minutes:int=120
    cooldown_minutes:int=5
    atr_lookback_minutes:int=60
    minimum_cost_after_rr:float=0.75
    maximum_cost_after_rr:float=5.0
    @classmethod
    def from_mapping(cls,values:Mapping[str,Any])->"InventoryDepthConfig":
        unknown=sorted(set(values)-set(cls.__dataclass_fields__))
        if unknown:raise ValueError(f"unknown v73 config keys: {unknown}")
        return cls(**dict(values))
    def __post_init__(self)->None:
        if self.mode!=MODE:raise ValueError("v73 mode changed")
        if self.event_minutes!=3 or self.boundary_minutes not in {10,15,20}:raise ValueError("invalid v73 horizons")
        if self.prior_minimum_minutes>=self.prior_window_minutes:raise ValueError("invalid prior window")
        for name in ("flow_abs_quantile","turnover_quantile","depth_change_abs_quantile","impact_efficiency_low_quantile","impact_efficiency_high_quantile","oi_abs_quantile","vpin_quantile"):
            if not 0<float(getattr(self,name))<1:raise ValueError(f"invalid {name}")
        if self.impact_efficiency_low_quantile>=self.impact_efficiency_high_quantile:raise ValueError("impact thresholds inverted")
        if not 0<self.minimum_cost_after_rr<=self.maximum_cost_after_rr:raise ValueError("invalid RR band")

def _pq(s:pd.Series,c:InventoryDepthConfig,q:float)->pd.Series:
    return s.shift(1).rolling(c.prior_window_minutes,min_periods=c.prior_minimum_minutes).quantile(q)

def build_state(features:pd.DataFrame,config:InventoryDepthConfig)->pd.DataFrame:
    x=features.copy();n=config.event_minutes
    signed=x["aggressive_signed_quote_1m"].rolling(n,min_periods=n).sum();total=x["aggressive_total_quote_1m"].rolling(n,min_periods=n).sum()
    x["event_flow"]=signed/total.replace(0,np.nan);x["event_turnover"]=total;x["event_return"]=np.log(x["close"]/x["close"].shift(n));x["event_efficiency"]=x["event_return"].abs()/(x["event_flow"].abs()+config.flow_floor)
    x["ask_depth_change_event"]=x["ask_depth_1pct_end"]/x["ask_depth_1pct_end"].shift(n)-1;x["bid_depth_change_event"]=x["bid_depth_1pct_end"]/x["bid_depth_1pct_end"].shift(n)-1
    x["flow_threshold"]=_pq(x["event_flow"].abs(),config,config.flow_abs_quantile);x["turnover_threshold"]=_pq(x["event_turnover"],config,config.turnover_quantile)
    depth=pd.concat([x["ask_depth_change_event"].abs(),x["bid_depth_change_event"].abs()],axis=1).max(axis=1);x["depth_threshold"]=_pq(depth,config,config.depth_change_abs_quantile)
    x["efficiency_low"]=_pq(x["event_efficiency"],config,config.impact_efficiency_low_quantile);x["efficiency_high"]=_pq(x["event_efficiency"],config,config.impact_efficiency_high_quantile)
    x["oi_threshold"]=_pq(x["oi_change_1h"].abs(),config,config.oi_abs_quantile);x["vpin_threshold"]=_pq(x["vpin_50"],config,config.vpin_quantile)
    return x

def _atr(raw:pd.DataFrame,n:int)->pd.Series:
    p=raw["close"].shift(1);tr=pd.concat([(raw["high"]-raw["low"]).abs(),(raw["high"]-p).abs(),(raw["low"]-p).abs()],axis=1).max(axis=1);return tr.rolling(n,min_periods=max(30,n//2)).median()

def _pc(costs:CostConfig)->CostConfig:
    return CostConfig(entry_fee_rate=costs.target_fee_rate,target_fee_rate=costs.target_fee_rate,stop_fee_rate=costs.stop_fee_rate,entry_slippage_rate=Decimal("0"),stop_slippage_rate=costs.stop_slippage_rate,market_impact_rate=costs.market_impact_rate,funding_rate_allowance=costs.funding_rate_allowance)

def _append(signals:list[RotationSignal],*,c:InventoryDepthConfig,costs:CostConfig,observed:pd.Timestamp,side:str,entry:float,stop:float,target:float,score:float,subtype:str,details:Mapping[str,Any])->bool:
    valid=stop<entry<target if side=="BUY" else target<entry<stop
    if not valid:return False
    rr=cost_after_reward_risk(entry=entry,stop=stop,target=target,side=side,costs=_pc(costs))
    if not math.isfinite(rr) or not c.minimum_cost_after_rr<=rr<=c.maximum_cost_after_rr:return False
    ns=int(observed.value);signals.append(RotationSignal(scenario_id=f"v73-{subtype.lower()}-{c.boundary_minutes}m-{ns}",observed_time_ns=ns,side=side,entry_reference=entry,stop_price=stop,target_price=target,cost_after_reward_risk=rr,score=float(score),max_hold_minutes=c.maximum_holding_minutes,source_feature_open_time_ns=ns-NS_MINUTE,source_feature_available_time_ns=ns,source_max_market_time_ns=ns,details={"mode":MODE,"inventory_subtype":subtype,"entry_expiry_minutes":c.entry_expiry_minutes,"entry_order_type":"POST_ONLY_LIMIT",**dict(details)}));return True

def build_rotation_signals(*,state:pd.DataFrame,raw:pd.DataFrame,evaluation_start:pd.Timestamp,evaluation_end:pd.Timestamp,config:InventoryDepthConfig,costs:CostConfig)->list[RotationSignal]:
    start,end=pd.Timestamp(evaluation_start),pd.Timestamp(evaluation_end)
    if start.tz is None:start=start.tz_localize("UTC")
    if end.tz is None:end=end.tz_localize("UTC")
    x=state.join(raw[["high","low"]].rename(columns={"high":"high_1m","low":"low_1m"}),how="inner");atr_series=_atr(raw,config.atr_lookback_minutes);signals=[];cooldown=-1;n=config.event_minutes
    for ts,row in x.loc[(x.index>=start)&(x.index<end)].iterrows():
        if int(ts.value)<=cooldown:continue
        req=("event_flow","flow_threshold","event_turnover","turnover_threshold","event_return","event_efficiency","efficiency_low","efficiency_high","depth_threshold","oi_change_1h","oi_threshold","vpin_50","vpin_threshold")
        if any(not math.isfinite(float(row[k])) for k in req):continue
        direction=int(np.sign(float(row["event_flow"])))
        if direction==0 or direction*float(row["event_return"])<=0 or abs(float(row["event_flow"]))<max(config.minimum_directional_flow,float(row["flow_threshold"])) or float(row["event_turnover"])<float(row["turnover_threshold"]):continue
        formation=raw.loc[(raw.index>=ts-pd.Timedelta(minutes=n+config.boundary_minutes))&(raw.index<ts-pd.Timedelta(minutes=n))]
        window=raw.loc[(raw.index>ts-pd.Timedelta(minutes=n))&(raw.index<=ts)]
        if len(formation)<config.boundary_minutes-1 or window.empty:continue
        high,low=float(formation["high"].max()),float(formation["low"].min());width=high-low;center=(high+low)/2;atr=float(atr_series.asof(ts))
        if not math.isfinite(width) or width<=0 or not math.isfinite(atr) or atr<=0:continue
        boundary=high if direction>0 else low;extreme=float(window["high"].max() if direction>0 else window["low"].min());front_col="ask_depth_1pct_end" if direction>0 else "bid_depth_1pct_end";front_change=float(row["ask_depth_change_event"] if direction>0 else row["bid_depth_change_event"])
        swept=extreme>=high+config.boundary_break_atr*atr if direction>0 else extreme<=low-config.boundary_break_atr*atr
        if not swept:continue
        close=float(row["close"]);outside=close>high if direction>0 else close<low
        new_inventory=float(row["oi_change_1h"])>=float(row["oi_threshold"])
        liquidation=float(row["oi_change_1h"])<=-float(row["oi_threshold"])
        if new_inventory and outside and front_change<=-float(row["depth_threshold"]) and float(row["event_efficiency"])>=float(row["efficiency_high"]):
            future=x.loc[(x.index>ts)&(x.index<=min(end,ts+pd.Timedelta(minutes=config.discovery_confirmation_minutes)))];confirmation=None
            for observed,r in future.iterrows():
                held=float(r["close"])>boundary if direction>0 else float(r["close"])<boundary
                flow_ok=direction*float(r["signed_flow_ratio_1m"])>=-0.05
                if held and flow_ok:confirmation=(pd.Timestamp(observed),float(r["close"]));break
            if confirmation is not None:
                observed,_=confirmation
                if direction>0:side,entry,stop,target="BUY",high,high-config.discovery_stop_inside_atr*atr,high+config.discovery_range_extension*width
                else:side,entry,stop,target="SELL",low,low+config.discovery_stop_inside_atr*atr,low-config.discovery_range_extension*width
                if _append(signals,c=config,costs=costs,observed=observed,side=side,entry=entry,stop=stop,target=target,score=abs(float(row["event_flow"]))*(1+float(row["oi_change_1h"])/max(float(row["oi_threshold"]),1e-12)),subtype="NEW_INVENTORY_DISCOVERY",details={"setup_utc":pd.Timestamp(ts).isoformat(),"boundary_high":high,"boundary_low":low,"event_extreme":extreme,"event_flow":float(row["event_flow"]),"event_efficiency":float(row["event_efficiency"]),"front_depth_change":front_change,"oi_change_1h":float(row["oi_change_1h"])}):cooldown=int(observed.value)+(config.entry_expiry_minutes+config.cooldown_minutes)*NS_MINUTE;continue
        if liquidation and float(row["vpin_50"])>=float(row["vpin_threshold"]):
            setup_depth=float(row[front_col]);future=x.loc[(x.index>ts)&(x.index<=min(end,ts+pd.Timedelta(minutes=config.liquidation_confirmation_minutes)))];confirmation=None
            for observed,r in future.iterrows():
                inside=float(r["close"])<boundary if direction>0 else float(r["close"])>boundary
                recovery=float(r[front_col])/setup_depth-1 if setup_depth>0 else -math.inf
                flow_failed=direction*float(r["signed_flow_ratio_1m"])<=0
                if inside and recovery>=config.liquidation_minimum_depth_recovery and flow_failed:confirmation=(pd.Timestamp(observed),float(r["close"]),recovery);break
            if confirmation is not None:
                observed,_,recovery=confirmation
                if direction>0:side,entry,stop,target="SELL",high,extreme+config.liquidation_stop_buffer_atr*atr,center
                else:side,entry,stop,target="BUY",low,extreme-config.liquidation_stop_buffer_atr*atr,center
                if _append(signals,c=config,costs=costs,observed=observed,side=side,entry=entry,stop=stop,target=target,score=abs(float(row["event_flow"]))*(1+abs(float(row["oi_change_1h"]))/max(float(row["oi_threshold"]),1e-12)),subtype="LIQUIDATION_EXHAUSTION",details={"setup_utc":pd.Timestamp(ts).isoformat(),"boundary_high":high,"boundary_low":low,"boundary_center":center,"event_extreme":extreme,"event_flow":float(row["event_flow"]),"event_efficiency":float(row["event_efficiency"]),"front_depth_change":front_change,"confirmation_depth_recovery":recovery,"oi_change_1h":float(row["oi_change_1h"]),"vpin":float(row["vpin_50"])}):cooldown=int(observed.value)+(config.entry_expiry_minutes+config.cooldown_minutes)*NS_MINUTE
    result=[];seen=set()
    for s in sorted(signals,key=lambda q:q.observed_time_ns):
        if s.observed_time_ns not in seen:seen.add(s.observed_time_ns);result.append(s)
    for s in result:
        if s.source_max_market_time_ns>s.observed_time_ns:raise AssertionError("future information detected")
    return result
