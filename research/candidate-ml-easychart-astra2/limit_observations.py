"""Counterfactual labels for learning only. Nautilus remains the account evaluator.
Limits require a one-tick trade-through. Same-minute entry/target ambiguity is
not credited as a winning fill; stop/target ambiguity after filling is stop-first.
"""
import numpy as np
import pandas as pd
from defended_origin import TICKS

def outcomes(plans,frames):
    records=[]
    for symbol,d in frames.items():
        ts=d.index.asi8; o,h,l,c=[d[k].to_numpy(float) for k in ['open','high','low','close']]
        for r in plans[plans.symbol==symbol].to_dict('records'):
            s=r['side']; entry=r['entry']; stop=r['stop']; target=r['target']; tick=TICKS[symbol]
            a=np.searchsorted(ts,r['ts'],side='right')
            entry_hits=np.flatnonzero(l[a:]<entry-tick if s==1 else h[a:]>entry+tick)
            target_hits=np.flatnonzero(h[a:]>=target if s==1 else l[a:]<=target)
            first_target=a+int(target_hits[0]) if len(target_hits) else len(d)
            first_fill=a+int(entry_hits[0]) if len(entry_hits) else len(d)
            if first_target<=first_fill or first_fill==len(d):
                end=min(first_target,len(d)-1)
                records.append(dict(r,outcome=2,net_r=0.,label_end=int(ts[end]),filled=0,censored=int(first_target==len(d)),entry_index=-1,exit_index=end))
                continue
            stop_hits=np.flatnonzero(l[first_fill:]<=stop if s==1 else h[first_fill:]>=stop)
            target_hits=np.flatnonzero(h[first_fill:]>=target if s==1 else l[first_fill:]<=target)
            st=first_fill+int(stop_hits[0]) if len(stop_hits) else len(d)
            tp=first_fill+int(target_hits[0]) if len(target_hits) else len(d)
            end=min(st,tp,len(d)-1); outcome=1 if st<=tp else 0
            risk=abs(entry-stop); budget=risk+entry*.0002+stop*.0007
            exitp=target if outcome==0 else (min(o[end],stop) if s==1 else max(o[end],stop))*(1-s*.0002)
            pnl=s*(exitp-entry)-entry*.0002-exitp*(.0002 if outcome==0 else .0005)
            records.append(dict(r,outcome=outcome,net_r=pnl/budget,label_end=int(ts[end]),filled=1,censored=int(min(st,tp)==len(d)),entry_index=first_fill,exit_index=end))
    return pd.DataFrame(records)
