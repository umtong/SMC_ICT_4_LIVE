"""Horizon-correct coherent system with one active trendline/channel state per scale."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

import coherent_policy as core
import coherent_system_v4 as v4
import coherent_system_v5 as v5  # installs prior/posterior feature hierarchy
import hierarchical_liquidity_bpr as hl
from auction_episode_research import CONTRACTS
from derivatives_dislocation import prepare_market_state
from liquidity_displacement import _add_common_state
from market_data import load_range as load_reference_range
from metrics_state import load_range_metrics
from semantic_liquidity_v4 import PoolMeta, build_semantic_liquidity


POLICY = v5.POLICY + ":LATEST_ACTIVE_TRENDLINE_CHANNEL_AND_FULL_FORWARD_LABEL_BUFFER"
_ORIGINAL_STRUCTURE = core._active_structure_features


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value=float(value)
    except (TypeError,ValueError):
        return default
    return value if math.isfinite(value) else default


def _position(data: pd.DataFrame, level: hl.LiquidityLevel) -> int:
    timestamp=pd.Timestamp(int(level.event_time_ns),unit='ns',tz='UTC')
    return int(data.index.searchsorted(timestamp,side='left'))


def _latest(levels, index, timeframe, side, count=3):
    candidates=[level for level in levels if int(level.timeframe_minutes)==timeframe and level.side==side and int(level.observed_index_1m)<index]
    candidates.sort(key=lambda level:(level.event_time_ns,level.observed_time_ns,level.level_id))
    return candidates[-count:]


def _project_line(data, first, second, index):
    p0,p1=_position(data,first),_position(data,second)
    if p1<=p0:return None
    slope=(float(second.price)-float(first.price))/(p1-p0)
    current=float(second.price)+slope*(index-p1)
    return p0,p1,slope,current


def active_structure_features(data:pd.DataFrame,levels:Sequence[hl.LiquidityLevel],index:int):
    output=_ORIGINAL_STRUCTURE(data,levels,index);atr=core._atr_price(data,index);price=float(data.iloc[index].close)
    phase_votes=[]
    for timeframe in (15,60,240):
        highs=_latest(levels,index,timeframe,'HIGH',3);lows=_latest(levels,index,timeframe,'LOW',3)
        support=_project_line(data,lows[-2],lows[-1],index) if len(lows)>=2 else None
        resistance=_project_line(data,highs[-2],highs[-1],index) if len(highs)>=2 else None
        prefix=f'active_{timeframe}m'
        support_value=support[3] if support else float('nan');resistance_value=resistance[3] if resistance else float('nan')
        output[f'{prefix}_support_slope_atr_per_bar']=support[2]/atr if support else 0.0
        output[f'{prefix}_resistance_slope_atr_per_bar']=resistance[2]/atr if resistance else 0.0
        output[f'{prefix}_support_distance_atr']=(price-support_value)/atr if support else 0.0
        output[f'{prefix}_resistance_distance_atr']=(resistance_value-price)/atr if resistance else 0.0
        for name,line,field in (('support',support,'low'),('resistance',resistance,'high')):
            if line is None:
                output[f'{prefix}_{name}_touches']=0.0;output[f'{prefix}_{name}_break_hold']=0.0;continue
            _,anchor,slope,_=line;start=max(anchor,index-240);positions=np.arange(start,index+1);projected=float((lows if name=='support' else highs)[-1].price)+slope*(positions-anchor);observed=data.iloc[start:index+1][field].to_numpy(float);distance=np.abs(observed-projected);touches=int((distance<=.18*atr).sum());output[f'{prefix}_{name}_touches']=float(touches)
            closes=data.iloc[max(0,index-1):index+1].close.to_numpy(float);close_positions=np.arange(max(0,index-1),index+1);projected_close=float((lows if name=='support' else highs)[-1].price)+slope*(close_positions-anchor)
            if len(closes)==2:
                broken=bool(np.all(closes<projected_close)) if name=='support' else bool(np.all(closes>projected_close))
            else:broken=False
            output[f'{prefix}_{name}_break_hold']=float(broken)
        if support and resistance and resistance_value>support_value:
            width=resistance_value-support_value;location=(price-support_value)/width
            output[f'{prefix}_channel_present']=1.0;output[f'{prefix}_channel_width_atr']=width/atr;output[f'{prefix}_channel_location']=location;output[f'{prefix}_channel_slope_agreement']=1.0-abs(support[2]-resistance[2])/max(abs(support[2])+abs(resistance[2]),atr*1e-6)
            output[f'{prefix}_midline_distance_atr']=(price-.5*(support_value+resistance_value))/atr
        else:
            output[f'{prefix}_channel_present']=0.0;output[f'{prefix}_channel_width_atr']=0.0;output[f'{prefix}_channel_location']=.5;output[f'{prefix}_channel_slope_agreement']=0.0;output[f'{prefix}_midline_distance_atr']=0.0
        high_state=np.sign(float(highs[-1].price-highs[-2].price)) if len(highs)>=2 else 0.0;low_state=np.sign(float(lows[-1].price-lows[-2].price)) if len(lows)>=2 else 0.0
        phase=1.0 if high_state>0 and low_state>0 else -1.0 if high_state<0 and low_state<0 else 0.0;phase_votes.append(phase);output[f'{prefix}_swing_phase']=phase
    output['active_structure_phase_vote']=float(np.mean(phase_votes)) if phase_votes else 0.0;output['active_structure_phase_agreement']=abs(output['active_structure_phase_vote'])
    return output


core._active_structure_features=active_structure_features
v4.core._active_structure_features=active_structure_features


def run_research(*,start:date,end:date,warmup_days:int,symbols:Sequence[str],cache:Path,output:Path):
    from data_re1_flow import load_range_flow
    output.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True);load_start=start-timedelta(days=warmup_days);load_end=end+timedelta(days=1);cutoff_ns=int(pd.Timestamp(end+timedelta(days=1),tz='UTC').value)
    prepared={};levels_by={};meta_by={}
    for symbol in symbols:
        tick=CONTRACTS[symbol].tick_size;raw=load_range_flow(symbol,load_start,load_end,cache);index_price=load_reference_range('indexPriceKlines',symbol,load_start,load_end,cache);mark_price=load_reference_range('markPriceKlines',symbol,load_start,load_end,cache);metrics=load_range_metrics(symbol,load_start,load_end,cache);state=prepare_market_state(raw,index_price,mark_price,metrics,tick);levels,metadata=build_semantic_liquidity(symbol,state,raw,tick);prepared[symbol]=state;levels_by[symbol]=levels;meta_by[symbol]=metadata
    prepared=_add_common_state(prepared);action_frames=[];state_frames=[];by_symbol={}
    for symbol in symbols:
        actions,states,summary=v4.generate_symbol(symbol,prepared[symbol],levels_by[symbol],meta_by[symbol],start)
        if not actions.empty:actions=actions[pd.to_numeric(actions.emission_time_ns,errors='coerce')<cutoff_ns].copy()
        if not states.empty:states=states[pd.to_numeric(states.emission_time_ns,errors='coerce')<cutoff_ns].copy()
        summary={**summary,'evaluation_actions':len(actions),'evaluation_states':len(states),'label_buffer_days':1};by_symbol[symbol]=summary
        if not actions.empty:actions.to_csv(output/f'{symbol}_coherent_actions.csv',index=False);action_frames.append(actions)
        if not states.empty:states.to_csv(output/f'{symbol}_destination_states.csv',index=False);state_frames.append(states)
    actions=pd.concat(action_frames,ignore_index=True,sort=False) if action_frames else pd.DataFrame();states=pd.concat(state_frames,ignore_index=True,sort=False) if state_frames else pd.DataFrame();actions.to_csv(output/'coherent_actions.csv',index=False);states.to_csv(output/'destination_states.csv',index=False)
    resolved=actions[actions.outcome.astype(str).isin(['TARGET_FIRST','STOP_FIRST','AMBIGUOUS_SAME_MINUTE','AMBIGUOUS_FILL_BARRIER_SAME_MINUTE','TIME_EXIT'])] if not actions.empty else actions
    summary={'start':start.isoformat(),'end':end.isoformat(),'label_data_end':load_end.isoformat(),'symbols':list(symbols),'actions':len(actions),'destination_states':len(states),'resolved_actions':len(resolved),'wins':int(resolved.outcome.astype(str).eq('TARGET_FIRST').sum()) if len(resolved) else 0,'win_rate':float(resolved.outcome.astype(str).eq('TARGET_FIRST').mean()) if len(resolved) else None,'mean_account_r':float(pd.to_numeric(resolved.net_r,errors='coerce').mean()) if len(resolved) else None,'by_symbol':by_symbol,'policy':POLICY,'future_information_in_features':False,'future_information_in_labels_only':True,'complete_forward_horizon_for_all_emissions':True}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8');return summary


MAX_HOLD_MINUTES=v4.MAX_HOLD_MINUTES;LIMIT_EXPIRY_MINUTES=v4.LIMIT_EXPIRY_MINUTES
__all__=['POLICY','run_research','MAX_HOLD_MINUTES','LIMIT_EXPIRY_MINUTES']
