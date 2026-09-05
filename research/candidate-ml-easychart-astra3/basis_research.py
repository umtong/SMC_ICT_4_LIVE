"""Cash-anchored dislocation: test an economic constraint, not a visual pattern.

A perpetual's deviation from its cash index is observed, not an identified
liquidation. A three-robust-scale departure starts one episode; the first later
price/basis recovery can supply entry. The index-adjusted pre-shock basis is the
pre-entry destination, and the observed excursion is invalidation. Neither
index stability nor profitable convergence is assumed to be established.

This is a research extension of the source's liquidity-sweep interpretation.
The normal basis uses a trailing four-hour median strictly before the event.
All actual account outcomes still come from the existing Nautilus engine.
"""
from __future__ import annotations
from dataclasses import dataclass
import json,math,traceback
from collections import Counter
import numpy as np
import pandas as pd
import research as r
from astra_policy import Plan
from domain import Side
from extended_inputs import EXTRA_FEATURES


def proposals(tape):
    plans=[];diagnosis={};basis_index=EXTRA_FEATURES.index('x_index_basis')
    for symbol,d in tape.raw.items():
        a=d[['ts','open','high','low','close']].to_numpy()
        basis=pd.Series(tape.extra.tables[symbol][1][:,basis_index])
        normal=basis.rolling(240,min_periods=240).median().shift(1)
        # A robust scale proxy uses the distribution of prior basis increments;
        # the additive tick floor is a market observation, not a profit filter.
        scale=(basis-normal).abs().rolling(240,min_periods=240).median().shift(1)*1.4826
        residual=basis-normal;tick=tape.ticks[symbol]
        diagnosis[symbol]={'absolute_residual_bps_quantiles':residual.abs().quantile([.5,.9,.99,.999]).to_dict(),
            'minutes_above_bps':{str(x):int((residual.abs()>x).sum()) for x in (10,20,30,50)}}
        event=None;events=0
        for i in range(481,len(a)):
            ts,op,high,low,close=a[i];ts=int(ts);b=float(basis.iloc[i])
            m=float(normal.iloc[i]);sd=float(scale.iloc[i])
            if not all(math.isfinite(x) for x in (b,m,sd)):continue
            residual_now=b-m
            if event is not None:
                side=event['side'];event['high']=max(event['high'],high);event['low']=min(event['low'],low)
                remaining=side*(event['basis']-b)
                if remaining<=0:
                    event=None
                elif not event['used'] and ts>event['ts']:
                    recovery=side*(b-float(basis.iloc[i-1]))>0 and side*(close-op)>0
                    if recovery:
                        event['used']=True
                        index=close/(1+b/10000.)
                        target=index*(1+event['basis']/10000.)
                        stop=event['low']-tick if side>0 else event['high']+tick
                        risk=side*(close-stop);reward=side*(target-close)
                        if risk>0 and reward>=risk:
                            key=f'{symbol}:CASH_DISLOCATION:{event["ts"]}:{side}'
                            f={'cost_r':.0006*(close+stop)/risk,'risk_bps':1e4*risk/close,'planned_rr':reward/risk,
                               'basis_deviation':remaining,'basis_scale':event['scale'],'baseline_basis':event['basis'],
                               'event_age':(ts-event['ts'])/r.MINUTE}
                            f.update(tape.extra.at(symbol,ts,side,1.))
                            plans.append(Plan(key,key,symbol,Side.LONG if side>0 else Side.SHORT,ts,event['ts'],
                                close,stop,target,reward/risk,index,240,key,'CASH_INDEX_PLUS_PRESH0CK_BASIS',
                                min(close,index),max(close,index),event['high'],event['low'],f,'CASH_ANCHORED_DISLOCATION'))
                if event is not None:continue
            threshold=3*max(sd,1e4*tick/close)
            if abs(residual_now)>threshold:
                side=-1 if residual_now>0 else 1
                event={'side':side,'ts':ts,'basis':m,'scale':sd,'high':high,'low':low,'used':False};events+=1
        diagnosis[symbol]['independent_basis_excursions']=events
    plans.sort(key=lambda p:(p.observed_time_ns,p.symbol))
    return plans,diagnosis


def strategy_class(tape,normalization_exit):
    base=r.AccountStrategy
    class CashAnchoredStrategy(base):
        def on_bar(self,bar):
            super().on_bar(bar)
            if not normalization_exit or self.bucket or self.active_plan is None or self.emergency_exit_requested:return
            plan=self.active_plan;now=int(bar.ts_event)
            if now<=plan.observed_time_ns:return
            s=plan.symbol;stamps,values=tape.extra.tables[s]
            i=int(np.searchsorted(stamps,now,side='right'))-1
            if i<0 or stamps[i]!=now:raise ValueError('missing current cash-index observation')
            b=float(values[i,EXTRA_FEATURES.index('x_index_basis')])
            if not math.isfinite(b):return
            if plan.side.value*(plan.features['baseline_basis']-b)>0:return
            self.emergency_exit_requested=True
            self.expected_cancel_ids.update(self._protective_ids());iid=self.active_instrument_id
            self.cancel_all_orders(iid)
            if not self.portfolio.is_flat(iid):self.close_all_positions(iid)
    return CashAnchoredStrategy


def run():
    request=json.loads((r.HERE/'request.json').read_text());results=[];diagnoses={};original=r.AccountStrategy
    for job in request['experiments']:
        tape=r.Tape(job['month']);plans,diag=proposals(tape);diagnoses[job['month']]=diag
        print('CASH_DISLOCATION',job['month'],len(plans),json.dumps(diag),flush=True)
        scores={}
        for p in plans:
            side=int(p.side.value);e=p.entry*(1+side*.0001)
            risk=abs(p.entry-p.stop)+.0006*(p.entry+p.stop)+p.entry*.0001
            win=(side*(p.target-e)-.0005*e-.0002*p.target)/risk
            scores[p.plan_id]=(float(win),float('nan'))
        for mode in ('endpoint','normalization'):
            r.AccountStrategy=strategy_class(tape,mode=='normalization')
            try:
                result=r.backtest(tape,plans,scores,f'{request["prefix"]}_{mode}_{job["name"]}',job['start'],job['end'])
                results.append(result)
            finally:r.AccountStrategy=original
    (r.OUT/f'{request["prefix"]}_opportunities.json').write_text(json.dumps(diagnoses,indent=2))
    (r.OUT/f'{request["prefix"]}_results.json').write_text(json.dumps(results,indent=2))
    (r.OUT/'latest.json').write_text(json.dumps(results,indent=2));(r.OUT/'error.txt').unlink(missing_ok=True)

if __name__=='__main__':
    try:run()
    except Exception:
        (r.OUT/'error.txt').write_text(traceback.format_exc());raise
