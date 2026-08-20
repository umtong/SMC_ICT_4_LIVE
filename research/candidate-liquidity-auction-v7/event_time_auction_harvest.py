#!/usr/bin/env python3
"""Hierarchical event-time auction plans from causal directional-change structure.

Large directional-change pivots define public external liquidity and the current
auction leg. Small directional-change pivots define pullbacks and control transfer.
Two mechanisms share one grammar:

* EXTERNAL_FAILED_AUCTION: a fresh large-scale pivot is raided, rejected and left;
* HIERARCHICAL_CONTINUATION: a small counter-leg completes inside a live large leg.

OB/FVG-like zones refine the first-return entry. Stops invalidate the causal event;
targets are pre-existing fresh directional-change liquidity. Filled positions leave
only at TP or SL. Unfilled first-return orders cancel when the event dies or the route
is consumed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import argparse, hashlib, json, math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import departure_first_return_harvest as loader

SYMBOLS=("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT")
SCALES=(0.75,1.5,3.0)
RR_VARIANTS=(1.0,1.25,1.5,1.75,2.0)
ENTRY_FEE=0.0005; STOP_FEE=0.0005; TARGET_FEE=0.0002
ENTRY_SLIPPAGE_TICKS=2; STOP_SLIPPAGE_TICKS=2; LIMIT_TRADE_THROUGH_TICKS=1
EPS=1e-12

@dataclass(frozen=True, slots=True)
class DCNode:
    scale: float; side: str; extreme_index: int; observed_index: int; price: float; threshold: float; leg_start_index: int

@dataclass(frozen=True, slots=True)
class OrderLabel:
    fill_state: str; outcome: str; fill_index: int|None; fill_time_ns: int|None; resolution_index: int|None; resolution_time_ns: int|None; order_terminal_index: int; order_terminal_time_ns: int; entry_wait_minutes: float|None; holding_minutes: float|None; actual_entry: float|None; actual_target_net_r: float|None; actual_stop_net_r: float|None; actual_gross_rr: float|None; net_r: float|None; mfe_r: float|None; mae_r: float|None

def _stable(*values:Any)->str:return hashlib.sha1("|".join(map(str,values)).encode()).hexdigest()[:16]
def _sign(side:str)->float:return 1.0 if side=="LONG" else -1.0
def _num(row:pd.Series,*names:str,default:float=0.0)->float:
    for name in names:
        try:v=float(row.get(name,np.nan))
        except (TypeError,ValueError):continue
        if math.isfinite(v):return v
    return default

def _atr(data:pd.DataFrame)->np.ndarray:
    prev=data.close.shift(1);tr=pd.concat([data.high-data.low,(data.high-prev).abs(),(data.low-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(90,min_periods=30).median().shift(1).bfill().to_numpy(float)

def directional_change(data:pd.DataFrame,scale:float,atr:np.ndarray)->list[DCNode]:
    high=data.high.to_numpy(float);low=data.low.to_numpy(float);nodes=[];mode=None;extreme_i=0;extreme_p=float(high[0]);leg_start=0
    running_high=float(high[0]);running_low=float(low[0]);hi_i=lo_i=0;start=1
    for i in range(1,len(data)):
        if high[i]>running_high:running_high=float(high[i]);hi_i=i
        if low[i]<running_low:running_low=float(low[i]);lo_i=i
        threshold=scale*max(float(atr[i]),EPS)
        if running_high-running_low>=threshold:
            if hi_i>lo_i:mode="UP";extreme_i=hi_i;extreme_p=running_high;leg_start=lo_i
            else:mode="DOWN";extreme_i=lo_i;extreme_p=running_low;leg_start=hi_i
            start=i+1;break
    if mode is None:return nodes
    for i in range(start,len(data)):
        if mode=="UP":
            if high[i]>=extreme_p:extreme_p=float(high[i]);extreme_i=i
            threshold=scale*max(float(atr[extreme_i]),EPS)
            if low[i]<=extreme_p-threshold:
                nodes.append(DCNode(scale,"HIGH",extreme_i,i,extreme_p,threshold,leg_start));mode="DOWN";leg_start=extreme_i;extreme_i=i;extreme_p=float(low[i])
        else:
            if low[i]<=extreme_p:extreme_p=float(low[i]);extreme_i=i
            threshold=scale*max(float(atr[extreme_i]),EPS)
            if high[i]>=extreme_p+threshold:
                nodes.append(DCNode(scale,"LOW",extreme_i,i,extreme_p,threshold,leg_start));mode="UP";leg_start=extreme_i;extreme_i=i;extreme_p=float(high[i])
    return nodes

def _first_penetration(data,node,atr):
    for i in range(node.observed_index+1,len(data)):
        threshold=max(0.08*atr[i],EPS)
        if node.side=="HIGH" and float(data.high.iloc[i])>=node.price+threshold:return i
        if node.side=="LOW" and float(data.low.iloc[i])<=node.price-threshold:return i
    return None

def _zone(data,start,end,side,atr_price):
    segment=data.iloc[max(0,start):end+1];opposite=(segment.close<segment.open) if side=="LONG" else (segment.close>segment.open)
    if opposite.any():
        row=segment.iloc[np.flatnonzero(opposite.to_numpy())[-1]];return float(row.low),float(row.high),"LAST_OPPOSITE_CANDLE"
    boundary=float(data.close.iloc[start]);width=max(0.10*atr_price,EPS);return boundary-width,boundary+width,"BOUNDARY_BAND"

def _flow_features(data,start,end,side,atr_price):
    sign=_sign(side);segment=data.iloc[start:end+1];quote=pd.to_numeric(segment.get("quote_volume",pd.Series(0.0,index=segment.index)),errors="coerce").fillna(0.0)
    if "signed_quote_flow" in segment:signed=pd.to_numeric(segment.signed_quote_flow,errors="coerce").fillna(0.0)
    elif "delta_share" in segment:signed=pd.to_numeric(segment.delta_share,errors="coerce").fillna(0.0)*quote
    else:signed=pd.Series(0.0,index=segment.index)
    close=segment.close.to_numpy(float);travel=float(np.abs(np.diff(close)).sum()) if len(close)>1 else 0.0;move=sign*(close[-1]-close[0]);prior=pd.to_numeric(data.quote_volume.iloc[max(0,start-60):start],errors="coerce").median() if "quote_volume" in data else np.nan;activity=float(quote.mean())/max(float(prior) if np.isfinite(prior) else float(quote.mean()),EPS)
    share=float(signed.sum())/max(float(quote.sum()),EPS)
    return {"event_move_atr":move/max(atr_price,EPS),"event_path_efficiency":move/max(travel,EPS),"event_flow_share_signed":sign*share,"event_activity_ratio":activity,"event_effort_result":move/max(atr_price,EPS)/max(0.08,activity*(abs(share)+0.08))}

def _route(data,atr,nodes,time_index,entry,side):
    wanted="HIGH" if side=="LONG" else "LOW";candidates=[n for n in nodes if n.side==wanted and n.observed_index<time_index and ((side=="LONG" and n.price>entry) or (side=="SHORT" and n.price<entry))];available=[]
    for n in candidates:
        part=data.iloc[n.observed_index+1:time_index];buffer=max(0.05*float(atr[n.observed_index]),EPS)
        consumed=(float(part.high.max())>=n.price+buffer) if (len(part) and n.side=="HIGH") else (float(part.low.min())<=n.price-buffer) if len(part) else False
        if not consumed:available.append(n)
    return min(available,key=lambda n:(abs(n.price-entry),-n.scale,n.observed_index)) if available else None

def _economics(side,entry,stop,target,tick):
    sign=_sign(side);actual_entry=entry+sign*ENTRY_SLIPPAGE_TICKS*tick;stop_fill=stop-sign*STOP_SLIPPAGE_TICKS*tick;risk=abs(actual_entry-stop_fill)
    if risk<=EPS:return None
    stop_raw=sign*(stop_fill-actual_entry)/risk-(ENTRY_FEE*abs(actual_entry)+STOP_FEE*abs(stop_fill))/risk;norm=max(abs(stop_raw),EPS);target_raw=sign*(target-actual_entry)/risk-(ENTRY_FEE*abs(actual_entry)+TARGET_FEE*abs(target))/risk
    return actual_entry,stop_fill,target_raw/norm,-1.0,norm

def _resolve(data,order_index,expiry,entry,stop,target,side,tick):
    sign=_sign(side)
    for i in range(order_index+1,min(expiry,len(data)-1)+1):
        row=data.iloc[i];invalid=float(row.low)<=stop if side=="LONG" else float(row.high)>=stop;spent=float(row.high)>=target if side=="LONG" else float(row.low)<=target;through=float(row.low)<=entry-LIMIT_TRADE_THROUGH_TICKS*tick if side=="LONG" else float(row.high)>=entry+LIMIT_TRADE_THROUGH_TICKS*tick
        if through:
            eco=_economics(side,entry,stop,target,tick)
            if eco is None:return OrderLabel("CANCELED_INVALID_GEOMETRY","UNFILLED",None,None,None,None,i,int(data.index[i].value),None,None,None,None,None,None,None,None,None)
            actual,stop_fill,target_r,stop_r,norm=eco;risk=abs(actual-stop_fill);best=worst=0.0
            for j in range(i,len(data)):
                bar=data.iloc[j];th=float(bar.high)>=target if side=="LONG" else float(bar.low)<=target;sh=float(bar.low)<=stop if side=="LONG" else float(bar.high)>=stop;favorable=((float(bar.high)-actual) if side=="LONG" else (actual-float(bar.low)))/risk/norm;adverse=((float(bar.low)-actual) if side=="LONG" else (actual-float(bar.high)))/risk/norm;best=max(best,favorable);worst=min(worst,adverse)
                if sh or th:
                    outcome="STOP_FIRST" if sh else "TARGET_FIRST";result=stop_r if sh else target_r
                    return OrderLabel("FILLED_LIMIT",outcome,i,int(data.index[i].value),j,int(data.index[j].value),j,int(data.index[j].value),float(i-order_index),float(j-i+1),actual,target_r,stop_r,abs(target-actual)/max(abs(actual-stop),EPS),result,best,worst)
            end=len(data)-1;return OrderLabel("FILLED_LIMIT","CENSORED_OPEN",i,int(data.index[i].value),None,None,end,int(data.index[end].value),float(i-order_index),None,actual,target_r,stop_r,abs(target-actual)/max(abs(actual-stop),EPS),None,best,worst)
        if invalid:return OrderLabel("CANCELED_PRE_FILL_INVALIDATED","UNFILLED",None,None,None,None,i,int(data.index[i].value),None,None,None,None,None,None,None,None,None)
        if spent:return OrderLabel("CANCELED_PRE_FILL_TARGET_SPENT","UNFILLED",None,None,None,None,i,int(data.index[i].value),None,None,None,None,None,None,None,None,None)
    end=min(expiry,len(data)-1);return OrderLabel("EXPIRED_UNFILLED","UNFILLED",None,None,None,None,end,int(data.index[end].value),None,None,None,None,None,None,None,None,None)

def _common_features(row,side):
    sign=_sign(side);return {"common_factor_signed":sign*_num(row,"factor_return","common_return_5m"),"common_breadth_signed":sign*_num(row,"breadth","common_breadth"),"relative_return_signed":sign*_num(row,"relative_return_5m","residual_return"),"oi_log_change":_num(row,"metric_oi_log_change_1","oi_log_change_1"),"basis_change_signed_bps":sign*_num(row,"basis_change_3m_bps","basis_change_bps")}

def _plans(data,symbol,family,node,interaction,departure,lower,upper,zone_kind,stop,all_nodes,atr,tick):
    side="SHORT" if (family=="EXTERNAL_FAILED_AUCTION" and node.side=="HIGH") else "LONG" if (family=="EXTERNAL_FAILED_AUCTION" and node.side=="LOW") else "LONG" if node.side=="LOW" else "SHORT";sign=_sign(side);decision=float(data.close.iloc[departure]);entries=[("ZONE_PROXIMAL_LIMIT",lower if side=="LONG" else upper),("ZONE_MID_LIMIT",0.5*(lower+upper))];out=[];episode=f"ET:{symbol}:{int(data.index[interaction].value)}:{family}:{node.scale}"
    for entry_kind,entry in entries:
        if not(entry<decision-tick if side=="LONG" else entry>decision+tick) or not(stop<entry if side=="LONG" else stop>entry):continue
        route=_route(data,atr,all_nodes,departure,entry,side)
        if route is None:continue
        risk=abs(entry-stop);expiry=min(len(data)-1,departure+max(12,int(20*node.scale)));base={"symbol":symbol,"side":side,"family":family,"episode_id":episode,"order_time_ns":int(data.index[departure].value),"entry_geometry":entry_kind,"setup_kind":family,"location_kind":"EXTERNAL_DC" if node.scale>=1.5 else "HIERARCHICAL_PULLBACK","source_pool_kind":f"DC_{node.scale:.2f}","source_scale":node.scale,"source_age_minutes":float(interaction-node.observed_index),"node_threshold_atr":node.threshold/max(atr[node.extreme_index],EPS),"zone_kind":zone_kind,"zone_width_atr":(upper-lower)/max(atr[departure],EPS),**_flow_features(data,interaction,departure,side,atr[interaction]),**_common_features(data.iloc[departure],side)}
        for rr in RR_VARIANTS:
            target=entry+sign*rr*risk;clear=target<=route.price if side=="LONG" else target>=route.price
            if not clear:continue
            eco=_economics(side,entry,stop,target,tick)
            if eco is None or eco[2]<=0:continue
            label=_resolve(data,departure,expiry,entry,stop,target,side,tick);state_id=f"ETSTATE:{_stable(episode,departure)}";action_id=f"{state_id}:{entry_kind}:{rr:.2f}"
            out.append({"action_id":action_id,"state_id":state_id,"episode_id":episode,"entry":entry,"stop":stop,"target":target,"gross_rr":rr,"risk_bps":risk/max(abs(entry),EPS)*10000,"route_kind":f"DC_{route.scale:.2f}_{route.side}","route_price":route.price,"route_rr":abs(route.price-entry)/max(risk,EPS),"planned_target_net_r":eco[2],"actual_target_net_r":label.actual_target_net_r,"actual_stop_net_r":label.actual_stop_net_r,**base,**asdict(label)})
    return out

def generate_symbol(symbol,data,trading_start,trading_end,tick):
    atr=_atr(data);nodes_by={scale:directional_change(data,scale,atr) for scale in SCALES};all_nodes=sum(nodes_by.values(),[]);records=[];used=set();start_ns=int(pd.Timestamp(trading_start,tz="UTC").value);end_ns=int(pd.Timestamp(trading_end,tz="UTC").value)
    for node in nodes_by[1.5]+nodes_by[3.0]:
        interaction=_first_penetration(data,node,atr)
        if interaction is None or not(start_ns<=int(data.index[interaction].value)<end_ns):continue
        side="SHORT" if node.side=="HIGH" else "LONG";sign=_sign(side);key=(interaction,node.side)
        if key in used:continue
        used.add(key);departure=None
        for j in range(interaction,min(len(data)-1,interaction+10)+1):
            if sign*(float(data.close.iloc[j])-node.price)/max(atr[interaction],EPS)>=0.30:departure=j;break
        if departure is None:continue
        lower,upper,zone_kind=_zone(data,interaction,departure,side,atr[interaction]);event_extreme=float(data.low.iloc[interaction:departure+1].min()) if side=="LONG" else float(data.high.iloc[interaction:departure+1].max());buffer=max(2*tick,0.08*atr[interaction]);stop=event_extreme-buffer if side=="LONG" else event_extreme+buffer;records.extend(_plans(data,symbol,"EXTERNAL_FAILED_AUCTION",node,interaction,departure,lower,upper,zone_kind,stop,all_nodes,atr,tick))
    macro=nodes_by[3.0]
    for event in nodes_by[0.75]:
        departure=event.observed_index
        if not(start_ns<=int(data.index[departure].value)<end_ns):continue
        prior_macro=[n for n in macro if n.observed_index<departure]
        if not prior_macro:continue
        last=prior_macro[-1];macro_side="LONG" if last.side=="LOW" else "SHORT";micro_side="LONG" if event.side=="LOW" else "SHORT"
        if micro_side!=macro_side:continue
        interaction=event.extreme_index;lower,upper,zone_kind=_zone(data,event.leg_start_index,departure,macro_side,atr[interaction]);buffer=max(2*tick,0.08*atr[interaction]);stop=float(data.low.iloc[event.leg_start_index:departure+1].min())-buffer if macro_side=="LONG" else float(data.high.iloc[event.leg_start_index:departure+1].max())+buffer;records.extend(_plans(data,symbol,"HIERARCHICAL_CONTINUATION",event,interaction,departure,lower,upper,zone_kind,stop,all_nodes,atr,tick))
    return pd.DataFrame(records)

def run_research(start,end,warmup_days,symbols,cache,output):
    start_d=date.fromisoformat(start);end_d=date.fromisoformat(end);raw=loader._load_universe(start_d-timedelta(days=warmup_days),end_d+timedelta(days=3),symbols,cache);prepared=loader._prepare_state(raw);output.mkdir(parents=True,exist_ok=True);frames=[];summary={}
    for symbol in symbols:
        frame=generate_symbol(symbol,prepared[symbol],start,end,loader.CONTRACTS[symbol].tick_size);frames.append(frame);summary[symbol]={"plans":int(len(frame)),"states":int(frame.state_id.nunique()) if not frame.empty else 0}
    actions=pd.concat(frames,ignore_index=True,sort=False) if frames else pd.DataFrame();actions.to_csv(output/"event_time_actions.csv.gz",index=False,compression="gzip");summary["total"]={"plans":int(len(actions)),"states":int(actions.state_id.nunique()) if not actions.empty else 0};(output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n");return summary

def main():
    p=argparse.ArgumentParser();p.add_argument("--start",required=True);p.add_argument("--end",required=True);p.add_argument("--warmup-days",type=int,default=60);p.add_argument("--symbols",nargs="+",default=list(SYMBOLS));p.add_argument("--cache",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args();print(json.dumps(run_research(a.start,a.end,a.warmup_days,a.symbols,a.cache,a.output),indent=2))
if __name__=="__main__":main()
