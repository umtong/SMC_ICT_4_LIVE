"""Cheap first-passage diagnostics, NOT the final Nautilus account evaluator.
Candidate labels are counterfactual training observations. Only route() has a
single account. OHLC ambiguity is stop-first. No candidate PnLs are summed.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
FEE_TAKER=.0005
FEE_MAKER=.0002
SLIP=.0002
RISK=.03


def label(rows,frames,fundings,marks):
    result=[]
    arrays={s:tuple(d[k].to_numpy() for k in ['open','high','low','close']) for s,d in frames.items()}
    for row in rows.to_dict('records'):
        d=frames[row['symbol']]; ts=d.index.asi8
        i=int(np.searchsorted(ts,row['ts']))+1
        if i>=len(d): continue
        s=row['side']; intended=row['entry']; stop=row['stop']; target=row['target']
        op,hi,lo,cl=arrays[row['symbol']]
        entry=op[i]*(1+s*SLIP)
        unit_risk=abs(intended-stop)+intended*(FEE_TAKER+SLIP)+stop*(FEE_TAKER+SLIP)
        # Submission-time budget: future fill prices cannot alter it.
        j=i; reason='open_at_end'; exitp=cl[-1]
        while j<len(d):
            if (lo[j]<=stop if s==1 else hi[j]>=stop):
                exitp=(min(op[j],stop) if s==1 else max(op[j],stop))*(1-s*SLIP); reason='stop'; break
            if (hi[j]>=target if s==1 else lo[j]<=target):
                exitp=target; reason='target'; break
            j+=1
        j=min(j,len(d)-1)
        fees=entry*FEE_TAKER+exitp*(FEE_MAKER if reason=='target' else FEE_TAKER)
        f=fundings[row['symbol']]
        # Bar label i is its close: diagnostic fill proxy is next bar's open.
        entry_ts=int(ts[i]-60000000000+1); end_ts=int(ts[j])
        paid=f.loc[(f.index.asi8>=entry_ts)&(f.index.asi8<=end_ts)]
        mark=marks[row['symbol']].close
        funding_cost=sum(s*float(rate)*float(mark.asof(stamp)) for stamp,rate in paid.items())
        pnl=s*(exitp-entry)-fees-funding_cost
        result.append(dict(row,entry_fill=entry,exit_fill=exitp,entry_ts=entry_ts,exit_ts=end_ts,reason=reason,net_r=pnl/unit_risk,unit_risk=unit_risk,hold_minutes=(end_ts-entry_ts)/60000000000,fee_r=fees/unit_risk,funding_r=funding_cost/unit_risk,censored=int(reason=='open_at_end')))
    return pd.DataFrame(result)


def factor_episodes(frames):
    close=pd.concat({s:d.close for s,d in frames.items()},axis=1).dropna()
    logp=np.log(close).mean(axis=1).resample('15min',closed='right',label='right',origin='epoch').last().dropna()
    x=logp.to_numpy(); scale=logp.diff().abs().ewm(span=32,adjust=False).mean().shift().fillna(.001).to_numpy()
    epoch=0; direction=1; extreme=x[0]; ids=[]
    for i,p in enumerate(x):
        threshold=max(2*scale[i],1e-6)
        if direction==1:
            extreme=max(extreme,p)
            if p<extreme-threshold: epoch+=1; direction=-1; extreme=p
        else:
            extreme=min(extreme,p)
            if p>extreme+threshold: epoch+=1; direction=1; extreme=p
        ids.append(epoch)
    return pd.Series(ids,index=logp.index)


def attach_episodes(rows,frames):
    ep=factor_episodes(frames)
    out=rows.copy(); ix=np.searchsorted(ep.index.asi8,out.root_ts.to_numpy(),side='right')-1
    out['episode']=ep.to_numpy()[np.maximum(ix,0)]
    return out


def route(rows,start,end,threshold=None):
    if len(rows)==0: return pd.DataFrame(),{'trades':0,'nav':10000.}
    x=rows[(rows.ts>=pd.Timestamp(start).value)&(rows.ts<pd.Timestamp(end).value)].copy()
    if threshold is not None: x=x[x.predicted_value>threshold]
    if 'predicted_value' not in x: x['predicted_value']=0.
    x=x.sort_values(['ts','predicted_value','scale','symbol'],ascending=[True,False,False,True])
    free=pd.Timestamp(start).value; used=set(); nav=10000.; peak=nav; dd=0.; chosen=[]
    for row in x.to_dict('records'):
        if row['ts']<free or row['episode'] in used: continue
        row['nav_before']=nav
        nav*=1+RISK*row['net_r']; row['nav_after']=nav
        chosen.append(row); free=row['exit_ts']; used.add(row['episode'])
        peak=max(peak,nav); dd=min(dd,nav/peak-1)
        if nav<=0: break
    t=pd.DataFrame(chosen); days=(pd.Timestamp(end)-pd.Timestamp(start)).total_seconds()/86400
    closed=t[t.censored==0] if len(t) else t
    summary={'days':days,'trades':len(closed),'open_at_end':len(t)-len(closed),'trades_per_day':len(closed)/days,'nav':nav,'return_pct':(nav/10000-1)*100,'closed_trade_drawdown_pct':dd*100}
    if len(closed):
        summary.update(win_rate=float((closed.net_r>0).mean()),mean_net_r=float(closed.net_r.mean()),mean_planned_rr=float(closed.rr.mean()),mean_hold_minutes=float(closed.hold_minutes.mean()),median_hold_minutes=float(closed.hold_minutes.median()),profit_factor=float(closed.loc[closed.net_r>0,'net_r'].sum()/max(-closed.loc[closed.net_r<0,'net_r'].sum(),1e-12)))
    return t,summary
