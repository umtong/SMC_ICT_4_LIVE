"""Causal event-specific auction episodes with derivatives positioning state.

The policy universe is not generated from legacy SMC plans.  It detects two mutually
exclusive auction mechanisms around pre-existing liquidity boundaries:

* RECLAIM: price sweeps a known boundary and closes back inside.  The candidate action
  fades the failed auction, with invalidation beyond the sweep extreme.
* ACCEPTED_BREAK: price closes beyond a known boundary and the next completed minute
  holds outside.  The candidate action follows the initiative, with invalidation beyond
  the two-bar break/hold wave.

Every event uses only completed bars and five-minute Binance positioning metrics that
have been shifted by one native sample.  Entry is the next one-minute open.  Stops and
targets are immutable before submission and labels are conservative first passage.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
import json, math

import numpy as np
import pandas as pd

from direct_state_action_harvest import (
    _confirmed_pivot_state,
    _resample_ohlc,
    _first_passage,
    TICK_SIZE,
    TAKER_FEE,
    MAKER_FEE,
    ENTRY_SLIPPAGE_TICKS,
    STOP_SLIPPAGE_TICKS,
)
from metrics_state import load_range_metrics, metric_features

SYMBOLS=("BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT")
EPS=1e-12
MAX_HOLD_MINUTES=360

@dataclass(frozen=True)
class HarvestConfig:
    start: date
    end: date
    load_start: date
    symbols: tuple[str,...]
    cache: Path
    output: Path


def _safe(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0.0,np.nan)


def _mad_z(x: pd.Series, window: int=1440) -> pd.Series:
    med=x.shift(1).rolling(window,min_periods=max(120,window//4)).median()
    mad=(x.shift(1)-med).abs().rolling(window,min_periods=max(120,window//4)).median()
    return (x-med)/(1.4826*mad+EPS)


def _prepare(symbol: str, raw: pd.DataFrame, metrics_raw: pd.DataFrame) -> pd.DataFrame:
    f=raw.copy()
    f.index=pd.DatetimeIndex(f.pop('open_time_dt'))+pd.Timedelta(minutes=1)
    f=f.sort_index()
    cols=("open","high","low","close","volume","quote_volume","count","taker_buy_volume","taker_buy_quote_volume")
    for c in cols:f[c]=pd.to_numeric(f[c],errors='coerce')
    close=f.close
    log=np.log(close)
    r=log.diff()
    prev=close.shift(1)
    tr=pd.concat([(f.high-f.low),(f.high-prev).abs(),(f.low-prev).abs()],axis=1).max(axis=1)
    rng=(f.high-f.low).replace(0,np.nan)
    delta=2*f.taker_buy_quote_volume-f.quote_volume
    f['ret_1m']=r
    f['tr_bps']=tr/close*1e4
    f['body_bps']=(f.close-f.open)/close*1e4
    f['body_fraction']=(f.close-f.open).abs()/rng
    f['upper_wick_fraction']=(f.high-f[['open','close']].max(axis=1))/rng
    f['lower_wick_fraction']=(f[['open','close']].min(axis=1)-f.low)/rng
    f['close_location']=(f.close-f.low)/rng
    f['delta_share_1m']=delta/f.quote_volume.replace(0,np.nan)
    f['activity_z_1d']=_mad_z(np.log1p(f.quote_volume))
    f['trade_count_z_1d']=_mad_z(np.log1p(f['count']))
    f['range_z_1d']=_mad_z(f.tr_bps)
    f['delta_abs_z_1d']=_mad_z(f.delta_share_1m.abs())
    avg_trade=f.quote_volume/f['count'].replace(0,np.nan)
    f['trade_size_z_1d']=_mad_z(np.log1p(avg_trade))
    # Signed price response per signed taker imbalance. Low impact with high effort is
    # an absorption candidate; high impact is initiative.
    f['impact_1m']=r.abs()*1e4/(f.delta_share_1m.abs()+.02)
    f['impact_z_1d']=_mad_z(np.log1p(f.impact_1m))

    for w in (3,5,10,15,30,60,120,240):
        f[f'ret_{w}m']=log-log.shift(w)
        q=f.quote_volume.rolling(w,min_periods=w).sum()
        d=delta.rolling(w,min_periods=w).sum()
        hi=f.high.rolling(w,min_periods=w).max();lo=f.low.rolling(w,min_periods=w).min()
        f[f'delta_share_{w}m']=d/q.replace(0,np.nan)
        f[f'range_position_{w}m']=(close-lo)/(hi-lo).replace(0,np.nan)
        f[f'path_efficiency_{w}m']=r.rolling(w,min_periods=w).sum().abs()/r.abs().rolling(w,min_periods=w).sum().replace(0,np.nan)
        f[f'activity_ratio_{w}m']=f.quote_volume.rolling(w,min_periods=w).mean()/f.quote_volume.shift(1).rolling(240,min_periods=120).median().replace(0,np.nan)

    # Exact trajectory. Aggregates alone erased the sequence that distinguishes an
    # exhausted sweep from a still-expanding initiative auction.
    trajectory={}
    for lag in range(0,31):
        trajectory[f'lag{lag}_ret_bps']=r.shift(lag)*1e4
        trajectory[f'lag{lag}_delta']=f.delta_share_1m.shift(lag)
        trajectory[f'lag{lag}_range_z']=f.range_z_1d.shift(lag)
        trajectory[f'lag{lag}_activity_z']=f.activity_z_1d.shift(lag)
        trajectory[f'lag{lag}_close_location']=f.close_location.shift(lag)
        trajectory[f'lag{lag}_impact_z']=f.impact_z_1d.shift(lag)
    f=pd.concat([f,pd.DataFrame(trajectory,index=f.index)],axis=1)

    # Boundaries exclude the current bar. They exist before interaction.
    f['prior_high_60']=f.high.shift(1).rolling(60,min_periods=60).max()
    f['prior_low_60']=f.low.shift(1).rolling(60,min_periods=60).min()
    f['prior_high_240']=f.high.shift(1).rolling(240,min_periods=120).max()
    f['prior_low_240']=f.low.shift(1).rolling(240,min_periods=120).min()
    f['atr_price_60']=tr.shift(1).rolling(60,min_periods=30).median()

    for tf,span in ((5,2),(15,2),(60,2)):
        agg=_resample_ohlc(f,tf)
        piv=_confirmed_pivot_state(agg,span=span,prefix=f'p{tf}')
        f=pd.merge_asof(f.sort_index(),piv.sort_index(),left_index=True,right_index=True,direction='backward')

    mf=metric_features(metrics_raw)
    f=pd.merge_asof(f.sort_index(),mf.sort_index(),left_index=True,right_index=True,direction='backward')
    f['next_open']=f.open.shift(-1)
    return f


def _boundary_candidates(row: pd.Series, previous: pd.Series|None=None) -> list[tuple[str,str,float]]:
    # (family, side-of-boundary, value). HIGH means liquidity above, LOW below.
    src=row if previous is None else previous
    vals=[
      ('RANGE_60','HIGH',src.get('prior_high_60',np.nan)),('RANGE_60','LOW',src.get('prior_low_60',np.nan)),
      ('RANGE_240','HIGH',src.get('prior_high_240',np.nan)),('RANGE_240','LOW',src.get('prior_low_240',np.nan)),
      ('PIVOT_5','HIGH',src.get('p5_pivot_high',np.nan)),('PIVOT_5','LOW',src.get('p5_pivot_low',np.nan)),
      ('PIVOT_15','HIGH',src.get('p15_pivot_high',np.nan)),('PIVOT_15','LOW',src.get('p15_pivot_low',np.nan)),
      ('PIVOT_60','HIGH',src.get('p60_pivot_high',np.nan)),('PIVOT_60','LOW',src.get('p60_pivot_low',np.nan)),
    ]
    return [(a,b,float(v)) for a,b,v in vals if np.isfinite(v)]


def _merge_triggers(triggers: list[dict], tick: float) -> list[dict]:
    # At one bar, several levels may describe the same causal auction. Merge them by
    # style and direction; confluence is a feature, not multiple pseudo-trades.
    out=[]
    for (style,side), items in __import__('itertools').groupby(sorted(triggers,key=lambda x:(x['style'],x['side'])),key=lambda x:(x['style'],x['side'])):
        xs=list(items)
        # For continuation use the furthest accepted level. For reclaim use the most
        # deeply swept level. Both represent the hardest boundary that price resolved.
        if xs[0]['edge']=='HIGH':chosen=max(xs,key=lambda x:x['boundary'])
        else:chosen=min(xs,key=lambda x:x['boundary'])
        z=dict(chosen)
        z['boundary_families']='|'.join(sorted({x['family'] for x in xs}))
        z['boundary_count']=len(xs)
        z['boundary_span_bps']=(max(x['boundary'] for x in xs)-min(x['boundary'] for x in xs))/max(abs(chosen['boundary']),EPS)*1e4
        out.append(z)
    return out


def _detect_at(f: pd.DataFrame, i: int, tick: float) -> list[dict]:
    if i<2:return []
    r=f.iloc[i];p=f.iloc[i-1];pp=f.iloc[i-2]
    t=[]
    # sweep and close back inside on the current completed minute
    for fam,edge,level in _boundary_candidates(r):
        if edge=='HIGH' and r.high>=level+tick and r.close<level:
            t.append({'style':'RECLAIM','side':'SHORT','family':fam,'boundary':level,'edge':'HIGH','event_extreme':float(r.high),'break_index':i,'hold_index':i})
        elif edge=='LOW' and r.low<=level-tick and r.close>level:
            t.append({'style':'RECLAIM','side':'LONG','family':fam,'boundary':level,'edge':'LOW','event_extreme':float(r.low),'break_index':i,'hold_index':i})
    # accepted break: bar i-1 closes outside its pre-existing boundary and bar i holds.
    for fam,edge,level in _boundary_candidates(p):
        if edge=='HIGH' and pp.close<=level and p.close>level and r.close>level:
            t.append({'style':'ACCEPTED_BREAK','side':'LONG','family':fam,'boundary':level,'edge':'HIGH','event_extreme':float(min(p.low,r.low)),'break_index':i-1,'hold_index':i})
        elif edge=='LOW' and pp.close>=level and p.close<level and r.close<level:
            t.append({'style':'ACCEPTED_BREAK','side':'SHORT','family':fam,'boundary':level,'edge':'LOW','event_extreme':float(max(p.high,r.high)),'break_index':i-1,'hold_index':i})
    return _merge_triggers(t,tick)


def _feature_row(symbol:str,f:pd.DataFrame,i:int,event:dict,event_id:str) -> dict:
    r=f.iloc[i];p=f.iloc[i-1];side=event['side'];sgn=1.0 if side=='LONG' else -1.0
    boundary=event['boundary'];extreme=event['event_extreme'];close=float(r.close)
    excluded={
      'open','high','low','close','volume','quote_volume','count','taker_buy_volume','taker_buy_quote_volume','next_open','atr_price_60',
      'prior_high_60','prior_low_60','prior_high_240','prior_low_240',
      'p5_pivot_high','p5_pivot_low','p15_pivot_high','p15_pivot_low','p60_pivot_high','p60_pivot_low',
    }
    out={'event_id':event_id,'symbol':symbol,'decision_time_ns':int(f.index[i].value),'style':event['style'],'side':side,'boundary_family':event['family'],'boundary_families':event['boundary_families'],'boundary_count':event['boundary_count'],'boundary_span_bps':event['boundary_span_bps']}
    for c,v in r.items():
        if c in excluded or c.endswith('_pivot_high') or c.endswith('_pivot_low'):continue
        if np.isscalar(v) and not isinstance(v,(str,bytes)):out[c]=v
    out.update({
      'event_side_sign':sgn,
      'event_boundary_distance_bps':(close-boundary)/close*1e4*sgn,
      'event_penetration_bps':abs(extreme-boundary)/close*1e4,
      'event_close_from_extreme_bps':abs(close-extreme)/close*1e4,
      'event_break_body_bps':abs(float(p.close-p.open))/float(p.close)*1e4,
      'event_hold_body_bps':abs(float(r.close-r.open))/float(r.close)*1e4,
      'event_break_delta_signed':float(p.delta_share_1m)*sgn,
      'event_hold_delta_signed':float(r.delta_share_1m)*sgn,
      'event_break_return_signed_bps':float(p.ret_1m)*1e4*sgn,
      'event_hold_return_signed_bps':float(r.ret_1m)*1e4*sgn,
    })
    return out


def _targets(row: pd.Series, side: str, entry: float, risk: float) -> list[tuple[str,float]]:
    vals=[]
    for rr in (1.0,1.5,2.0):vals.append((f'RR_{rr:.1f}',entry+risk*rr if side=='LONG' else entry-risk*rr))
    if side=='LONG':
        raw=[('PIVOT_5',row.get('p5_pivot_high',np.nan)),('PIVOT_15',row.get('p15_pivot_high',np.nan)),('RANGE_60',row.get('prior_high_60',np.nan)),('RANGE_240',row.get('prior_high_240',np.nan))]
        raw=[x for x in raw if np.isfinite(x[1]) and x[1]>entry]
        raw=sorted(raw,key=lambda x:x[1])
    else:
        raw=[('PIVOT_5',row.get('p5_pivot_low',np.nan)),('PIVOT_15',row.get('p15_pivot_low',np.nan)),('RANGE_60',row.get('prior_low_60',np.nan)),('RANGE_240',row.get('prior_low_240',np.nan))]
        raw=[x for x in raw if np.isfinite(x[1]) and x[1]<entry]
        raw=sorted(raw,key=lambda x:-x[1])
    vals.extend(raw[:2])
    out=[]
    for n,v in vals:
        if not np.isfinite(v):continue
        if any(abs(v-e)<=0 for _,e in out):continue
        out.append((n,float(v)))
    return out


def _label_action(symbol:str,f:pd.DataFrame,i:int,event:dict,event_id:str,high,low,close,time_ns) -> list[dict]:
    if i+1>=len(f):return []
    tick=TICK_SIZE[symbol];side=event['side'];nxt=float(f.iloc[i].next_open)
    if not np.isfinite(nxt) or nxt<=0:return []
    entry=nxt+ENTRY_SLIPPAGE_TICKS*tick if side=='LONG' else nxt-ENTRY_SLIPPAGE_TICKS*tick
    if event['style']=='RECLAIM':
        stop=event['event_extreme']-tick if side=='LONG' else event['event_extreme']+tick
        stop_kind='SWEEP_EXTREME'
    else:
        b=event['break_index'];h=event['hold_index']
        stop=min(float(f.iloc[b].low),float(f.iloc[h].low))-tick if side=='LONG' else max(float(f.iloc[b].high),float(f.iloc[h].high))+tick
        stop_kind='BREAK_HOLD_WAVE'
    if side=='LONG' and stop>=entry-tick:return []
    if side=='SHORT' and stop<=entry+tick:return []
    risk=abs(entry-stop);risk_bps=risk/entry*1e4
    if risk_bps<2 or risk_bps>300:return []
    actions=[]
    # Temporarily use local first-passage helper but cap its horizon by slicing arrays.
    end=min(len(close),i+1+MAX_HOLD_MINUTES)
    hs=high[:end];ls=low[:end];cs=close[:end];ts=time_ns[:end]
    for target_kind,target in _targets(f.iloc[i],side,entry,risk):
        if side=='LONG' and target<=entry+tick:continue
        if side=='SHORT' and target>=entry-tick:continue
        target_dist=abs(target-entry);rr=target_dist/risk
        if rr<.75 or rr>4:continue
        outcome,j,exit_level=_first_passage(hs,ls,cs,i+1,side,stop,target,entry,tick)
        stop_fill=stop-STOP_SLIPPAGE_TICKS*tick if side=='LONG' else stop+STOP_SLIPPAGE_TICKS*tick
        stop_net=-(abs(entry-stop_fill)/entry+2*TAKER_FEE)/(risk/entry)
        target_net=(target_dist/entry-TAKER_FEE-MAKER_FEE)/(risk/entry)
        if target_net<=0 or stop_net < -2.0:continue
        if outcome=='TARGET_FIRST':net=target_net
        elif outcome in ('STOP_FIRST','AMBIGUOUS_SAME_MINUTE'):net=stop_net
        else:
            gross=(exit_level-entry)/entry if side=='LONG' else (entry-exit_level)/entry
            net=(gross-2*TAKER_FEE)/(risk/entry)
        actions.append({'event_id':event_id,'action_id':f'{event_id}|{target_kind}','symbol':symbol,'decision_time_ns':int(time_ns[i]),'entry_time_ns':int(time_ns[i+1]),'style':event['style'],'side':side,'stop_kind':stop_kind,'target_kind':target_kind,'entry':entry,'stop':stop,'target':target,'risk_bps':risk_bps,'target_bps':target_dist/entry*1e4,'gross_rr':rr,'target_net_r':target_net,'stop_net_r':stop_net,'post_cost_break_even_probability':(-stop_net)/(target_net-stop_net),'outcome':outcome,'resolution_time_ns':int(ts[j]),'holding_minutes':int(j-(i+1)+1),'net_r':net})
    return actions


def harvest(config:HarvestConfig)->dict:
    from data_re1_flow import load_range_flow
    config.output.mkdir(parents=True,exist_ok=True)
    event_rows=[];action_rows=[];counts={}
    for symbol in config.symbols:
        raw=load_range_flow(symbol,config.load_start,config.end+timedelta(days=1),config.cache)
        metrics=load_range_metrics(symbol,config.load_start,config.end+timedelta(days=1),config.cache)
        f=_prepare(symbol,raw,metrics)
        start=pd.Timestamp(config.start,tz='UTC');end=pd.Timestamp(config.end+timedelta(days=1),tz='UTC')
        positions=np.flatnonzero((f.index>=start)&(f.index<end))
        high=f.high.to_numpy(float);low=f.low.to_numpy(float);close=f.close.to_numpy(float);time_ns=f.index.as_unit('ns').asi8
        last={}
        for i in positions:
            for event in _detect_at(f,int(i),TICK_SIZE[symbol]):
                k=(event['style'],event['side'])
                # One causal episode owns nearby repeat signals. Five minutes is short
                # enough to preserve independent daytrade opportunities while preventing
                # the same sweep/hold from being counted each minute.
                if int(i)-last.get(k,-10_000)<5:continue
                last[k]=int(i)
                eid=f"{int(time_ns[i])}|{symbol}|{event['style']}|{event['side']}"
                acts=_label_action(symbol,f,int(i),event,eid,high,low,close,time_ns)
                if not acts:continue
                event_rows.append(_feature_row(symbol,f,int(i),event,eid));action_rows.extend(acts)
                counts[event['style']]=counts.get(event['style'],0)+1
    events=pd.DataFrame(event_rows);actions=pd.DataFrame(action_rows)
    if events.empty or actions.empty:raise RuntimeError('no event-specific actions')
    if events.event_id.duplicated().any() or actions.action_id.duplicated().any():raise RuntimeError('duplicate identity')
    if not set(actions.event_id).issubset(set(events.event_id)):raise RuntimeError('action without event')
    if not (actions.entry_time_ns>actions.decision_time_ns).all():raise RuntimeError('noncausal entry')
    events.to_csv(config.output/'events.csv.gz',index=False,compression='gzip');actions.to_csv(config.output/'actions.csv.gz',index=False,compression='gzip')
    summary={'start':config.start.isoformat(),'end':config.end.isoformat(),'symbols':list(config.symbols),'events':len(events),'actions':len(actions),'event_features':len(events.columns)-8,'by_style':counts,'mean_net_r':float(actions.net_r.mean()),'target_first':int(actions.outcome.eq('TARGET_FIRST').sum()),'stop_first_or_ambiguous':int(actions.outcome.isin(['STOP_FIRST','AMBIGUOUS_SAME_MINUTE']).sum()),'time_exit':int(actions.outcome.eq('TIME_EXIT').sum()),'causal_policy':'PREEXISTING_BOUNDARY_SWEEP_RECLAIM_OR_CLOSE_BREAK_NEXT_MINUTE_HOLD','positioning_policy':'BINANCE_FIVE_MINUTE_METRICS_SHIFTED_ONE_NATIVE_SAMPLE','cost_policy':'TAKER_ENTRY_MAKER_TARGET_TAKER_STOP_TWO_TICK_SLIPPAGE'}
    (config.output/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True));return summary
