#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

RISK=.03; MIN_R=1.; MAX_SOURCE_R=8.; CAP_R=1.5; GEOMETRY='ZONE_PROXIMAL_LIMIT'
T={
'efficient_approach_impact':.80760719,'efficient_approach_path':.29738837,'meaningful_zone_bps':6.0039672,
'basis_compression_bps':-1.351867,'departure_low_impact':.11387988,'source_defenses':5.,
'initial_displacement_bps':35.48466,'late_opposite_delta':-.13257523,'wide_zone_bps':7.877117,
'event_absorption_impact':.19427535,'event_absorption_risk_bps':7.5579072,'late_displacement_bps':7.7896787,
'passive_approach_delta':.08,'open_route_obstacle_bps':90.,'structure_extension_atr':8.,
'passive_defended_approach_delta':.08,'passive_defended_source_defenses':7.,'residual_control_return':.001,
'high_effort_result':5.,}
P={'DEFENDED_BASIS_ABSORPTION':8,'PASSIVE_DEFENDED_RESIDUAL_CONTROL':7,
'EVENT_ABSORPTION_DISPLACEMENT':6,'EFFICIENT_APPROACH_SOURCE':5,'PUSH_PULL_ABSORPTION':4,
'PASSIVE_APPROACH_OPEN_ROUTE_STRUCTURE_EXPANSION':3,'HIGH_EFFORT_IFVG_OB_RECLAIM':2,
'LOCAL_IFVG_RECLAIM_RETEST':1}

def n(f,c): return pd.to_numeric(f[c],errors='coerce') if c in f else pd.Series(np.nan,index=f.index,dtype=float)
def s(f,c): return f[c].fillna('').astype(str) if c in f else pd.Series('',index=f.index,dtype=str)

def masks(f):
    side=s(f,'side'); fam=s(f,'family'); setup=s(f,'setup_kind'); loc=s(f,'location_kind'); phase=s(f,'auction_phase')
    scale=n(f,'target_scale_minutes'); zone=n(f,'zone_width_bps'); acc=n(f,'auction_acceptance_strength'); defenses=n(f,'source_defense_count')
    ext=pd.Series(np.where(side.eq('LONG'),n(f,'structure_60m_high_change_atr'),-n(f,'structure_60m_low_change_atr')),index=f.index)
    accepted=fam.eq('ACCEPTED_AUCTION_CONTINUATION'); failed=fam.eq('FAILED_AUCTION_REVERSAL'); foot=loc.str.contains('FVG|OB_OVERLAP',regex=True)
    efficient=n(f,'approach_impact_per_activity_12m').ge(T['efficient_approach_impact'])&n(f,'approach_path_efficiency').ge(T['efficient_approach_path'])&zone.ge(T['meaningful_zone_bps'])
    basis=n(f,'departure_basis_change_3m_signed').le(T['basis_compression_bps'])&n(f,'departure_impact_per_activity').le(T['departure_low_impact'])&defenses.ge(T['source_defenses'])
    push=n(f,'sequence_block_0_return_bps_signed').ge(T['initial_displacement_bps'])&n(f,'sequence_block_5_delta_share_signed').le(T['late_opposite_delta'])&zone.ge(T['wide_zone_bps'])
    event=n(f,'event_impact_per_activity').le(T['event_absorption_impact'])&n(f,'risk_bps').ge(T['event_absorption_risk_bps'])&n(f,'sequence_block_3_return_bps_signed').ge(T['late_displacement_bps'])
    residual=n(f,'approach_delta_share_12m_toward').le(T['passive_defended_approach_delta'])&defenses.ge(T['passive_defended_source_defenses'])&n(f,'departure_residual_return_5m_signed').ge(T['residual_control_return'])
    openroute=n(f,'approach_delta_share_12m_toward').le(T['passive_approach_delta'])&n(f,'route_obstacle_distance_bps').ge(T['open_route_obstacle_bps'])&ext.ge(T['structure_extension_atr'])
    return {
      'DEFENDED_BASIS_ABSORPTION':basis,
      'PASSIVE_DEFENDED_RESIDUAL_CONTROL':residual&(failed|(accepted&~loc.eq('TRANSFERRED_BOUNDARY')&(~loc.eq('BOUNDARY_FVG_OVERLAP')|zone.ge(T['meaningful_zone_bps'])))),
      'EVENT_ABSORPTION_DISPLACEMENT':event&((failed&setup.eq('IFVG'))|(accepted&foot)),
      'EFFICIENT_APPROACH_SOURCE':efficient&(failed|foot|scale.le(15.)),
      'PUSH_PULL_ABSORPTION':push&(foot|(accepted&phase.eq('ACCEPTED_EXPANSION')&scale.le(60.)&acc.ge(4.*defenses))),
      'PASSIVE_APPROACH_OPEN_ROUTE_STRUCTURE_EXPANSION':openroute&(failed|scale.le(5.)),
      'LOCAL_IFVG_RECLAIM_RETEST':failed&setup.eq('IFVG')&loc.eq('IFVG')&phase.eq('FIRST_RETEST_FORMING')&scale.le(5.),
      'HIGH_EFFORT_IFVG_OB_RECLAIM':failed&setup.eq('IFVG')&loc.eq('IFVG_OB_OVERLAP')&phase.eq('FIRST_RETEST_FORMING')&scale.eq(1440.)&n(f,'auction_effort_result').ge(T['high_effort_result'])}

def choose(f):
    m=masks(f); x=f.loc[pd.concat(m,axis=1).any(axis=1)].copy(); x['scenario_family']=''
    for k,_ in sorted(P.items(),key=lambda z:z[1]): x.loc[m[k].reindex(x.index,fill_value=False),'scenario_family']=k
    x['scenario_priority']=x.scenario_family.map(P).astype(int); x=x[n(x,'planned_target_net_r').between(MIN_R,MAX_SOURCE_R)].copy()
    x['preferred_geometry']=s(x,'entry_geometry').eq(GEOMETRY).astype(int)
    x=x.sort_values(['state_id','preferred_geometry','planned_target_net_r','action_id'],ascending=[1,0,1,1],kind='mergesort').drop_duplicates('state_id')
    return x.sort_values(['order_time_ns','scenario_priority','planned_target_net_r','action_id'],ascending=[1,0,1,1],kind='mergesort').reset_index(drop=True)

def first_episode(x):
    return x.sort_values(['order_time_ns','scenario_priority','planned_target_net_r','action_id'],ascending=[1,0,1,1],kind='mergesort').drop_duplicates(['research_period','episode_id']).sort_values(['order_time_ns','scenario_priority','planned_target_net_r','action_id'],ascending=[1,0,1,1],kind='mergesort').reset_index(drop=True)

def route(x,old=False):
    busy=-1; nav=peak=1.; orders=[]; trades=[]
    for r in first_episode(x).to_dict('records'):
        ot=int(r['order_time_ns'])
        if ot<busy: continue
        out=str(r.get('outcome','UNFILLED'))
        if out=='UNFILLED':
            term=r.get('order_terminal_time_ns')
            if pd.isna(term): continue
            busy=int(term); r.update(account_busy_until_ns=busy,net_r_num=0.,nav_before=nav,nav_after=nav); orders.append(r); continue
        res=r.get('resolution_time_ns')
        if pd.isna(res): continue
        busy=int(res)
        if old:
            target=min(float(r['planned_target_net_r']),CAP_R); original=out
            if out=='TARGET_FIRST' or (out=='STOP_FIRST' and pd.notna(r.get('mfe_r')) and float(r['mfe_r'])+1e-12>=target): out,nr='TARGET_FIRST',target
            else: out,nr='STOP_FIRST',-1.
            r.update(original_outcome=original,outcome=out,original_planned_target_net_r=r['planned_target_net_r'],planned_target_net_r=target)
        else: nr=float(r['net_r']) if pd.notna(r.get('net_r')) else -1.
        before=nav; nav=max(0.,nav*(1.+RISK*nr)); peak=max(peak,nav)
        r.update(account_busy_until_ns=busy,net_r_num=nr,nav_before=before,nav_after=nav,drawdown=1.-nav/peak); orders.append(r.copy()); trades.append(r)
    return pd.DataFrame(orders),pd.DataFrame(trades)

def block(o,t):
    v=pd.to_numeric(t.net_r_num,errors='coerce').dropna() if len(t) else pd.Series(dtype=float); w=v[v>0]; l=v[v<0]; nav=peak=1.; dd=0.
    for z in v: nav=max(0.,nav*(1.+RISK*float(z))); peak=max(peak,nav); dd=max(dd,1.-nav/peak)
    outs=s(o,'outcome') if len(o) else pd.Series(dtype=str)
    return {'orders':int(len(o)),'unfilled_orders':int(outs.eq('UNFILLED').sum()),'closed_trades':int(len(v)),'wins':int((v>0).sum()),
    'win_rate':float((v>0).mean()) if len(v) else 0.,'sum_net_r':float(v.sum()) if len(v) else 0.,'mean_net_r':float(v.mean()) if len(v) else 0.,
    'average_win_r':float(w.mean()) if len(w) else 0.,'average_loss_r':float(l.mean()) if len(l) else 0.,
    'payoff_ratio':float(w.mean()/abs(l.mean())) if len(w) and len(l) else None,'ending_nav':float(nav),'max_drawdown':float(dd)}

def summary(src,o,t):
    periods=sorted(src.research_period.astype(str).unique(),key=lambda p:int(src.loc[src.research_period.astype(str).eq(p),'order_time_ns'].min()))
    return {'policy':'ML_MN_CAUSAL_LIQUIDITY_CONTROL_V1','decision_uses_symbol_identity':False,'decision_uses_outcome_fields':False,
    'account':{'one_global_pending_or_position_slot':True,'one_plan_per_causal_episode':True,'risk_fraction_of_current_nav':RISK,'scale_in_or_out':False,'minimum_planned_target_net_r':MIN_R,'maximum_realized_target_net_r':CAP_R},
    'overall_continuous_account':block(o,t),'by_period':{p:block(o[o.research_period.astype(str).eq(p)],t[t.research_period.astype(str).eq(p)]) for p in periods},
    'by_scenario_family':{k:block(o[o.scenario_family.astype(str).eq(k)],t[t.scenario_family.astype(str).eq(k)]) for k in P},
    'by_symbol':{z:block(o[o.symbol.astype(str).eq(z)],t[t.symbol.astype(str).eq(z)]) for z in sorted(src.symbol.astype(str).unique())}}

def load(root):
    fs=sorted(root.rglob('departure_actions.csv.gz'))
    if not fs: raise FileNotFoundError(root)
    a=[]
    for p in fs:
        f=pd.read_csv(p,low_memory=False); period=p.parent.name
        if period.endswith('USDT'): period=p.parent.parent.name
        f['research_period']=period; a.append(f)
    return pd.concat(a,ignore_index=True,sort=False)

def bounds(f,path):
    if path is None:return f
    cfg=json.loads(path.read_text()); ts=n(f,'order_time_ns'); keep=pd.Series(False,index=f.index)
    for p,w in cfg.items(): keep|=f.research_period.astype(str).eq(p)&ts.ge(pd.Timestamp(w['start'],tz='UTC').value)&ts.lt(pd.Timestamp(w['end'],tz='UTC').value)
    return f.loc[keep].copy()

def run(root,out,bound=None,old=False):
    src=bounds(load(root),bound); plans=choose(src); o,t=route(plans,old); sm=summary(src,o,t); out.mkdir(parents=True,exist_ok=True)
    o.to_csv(out/'selected_orders.csv',index=False); t.to_csv(out/'closed_trades.csv',index=False); (out/'summary.json').write_text(json.dumps(sm,indent=2,sort_keys=True,default=str)+'\n'); print(json.dumps(sm,indent=2,sort_keys=True)); return sm

def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,required=True); p.add_argument('--output',type=Path,required=True); p.add_argument('--period-bounds',type=Path); p.add_argument('--old-artifact-counterfactual',action='store_true'); a=p.parse_args(); run(a.root,a.output,a.period_bounds,a.old_artifact_counterfactual)
if __name__=='__main__':main()
