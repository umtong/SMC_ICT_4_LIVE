"""Shared conditional context model with no symbol or desired win-rate feature.
Earlier counterfactual labels train the model. Only native Nautilus accounts
measure the selected policy. Every examined evaluation window is development.
"""
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from market_io import prepare,bars,months_between,SYMBOLS
from control_transfer import candidates as base_candidates
from structural_context import features,FEATURES
from limit_observations import outcomes
from first_passage import attach_episodes
OUT=Path('research_results/candidate_ml_easychart_astra2')

class ContextModel:
    def fit(self,observations):
        d=observations[(observations.filled==1)&(observations.censored==0)].copy()
        if len(d)<100: raise ValueError('Not enough completed conditional observations')
        x=d[FEATURES].replace([np.inf,-np.inf],np.nan)
        counts=d.groupby('episode').ts.transform('count')
        weight=1/counts; weight*=len(d)/weight.sum()
        self.model=HistGradientBoostingClassifier(max_leaf_nodes=5,max_iter=100,min_samples_leaf=30,l2_regularization=10.,learning_rate=.06,early_stopping=False,random_state=7)
        self.model.fit(x,(d.outcome==0).astype(int),sample_weight=weight)
        self.last_label=int(d.label_end.max()); self.training_rows=len(d)
        return self
    def apply(self,plans,frames):
        d=features(plans,frames)
        if d.empty: return d
        p=self.model.predict_proba(d[FEATURES].replace([np.inf,-np.inf],np.nan))[:,1]
        risk=(d.entry-d.stop).abs(); budget=risk+d.entry*.0002+d.stop*.0007
        win=((d.target-d.entry).abs()-(d.entry+d.target)*.0002)/budget
        d['predicted_win']=p
        d['predicted_value']=p*np.log1p(.03*win)+(1-p)*np.log(.97)
        return d[d.predicted_value>0].copy()

def train():
    start=pd.Timestamp('2024-01-01',tz='UTC'); end=pd.Timestamp('2024-08-01',tz='UTC'); warm=start-pd.Timedelta(days=7)
    prepare(months_between(warm,end),kinds=('klines',))
    frames={s:bars(s,warm,end) for s in SYMBOLS}
    plans=pd.concat([base_candidates(s,d) for s,d in frames.items()],ignore_index=True)
    plans=plans[(plans.ts>=start.value)&(plans.ts<end.value)].copy()
    plans=attach_episodes(features(plans,frames),frames)
    observed=outcomes(plans,frames)
    observed=observed[(observed.censored==0)&(observed.label_end<end.value)]
    model=ContextModel().fit(observed)
    OUT.mkdir(parents=True,exist_ok=True)
    joblib.dump(model,OUT/'context_model.joblib')
    (OUT/'context_model.json').write_text(json.dumps({'training_start':str(start),'training_end':str(end),'last_completed_label':model.last_label,'training_rows':model.training_rows,'features':FEATURES,'parameters':model.model.get_params()},indent=2)+'\n')
    observed.to_csv(OUT/'context_training_observations.csv',index=False)
    table=observed[observed.filled==1].assign(won=lambda x:x.outcome==0).groupby(['owner_60','owner_15']).agg(observations=('won','size'),target_fraction=('won','mean'),mean_gross_rr=('rr','mean'))
    print('CONTEXT OBSERVATIONS (not an account)\n'+table.to_string(),flush=True)
    return model

def main():
    model=train()
    import nautilus_account as account
    account.OUT=OUT/'context_native'
    def selected(symbol,d):
        raw=base_candidates(symbol,d)
        return model.apply(raw,{symbol:d}) if not raw.empty else raw
    account.candidates=selected
    results=[]
    for a,b in [('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24')]:
        result=account.run(a,b)
        result['policy']='earlier_trained_context'
        results.append(result)
    (OUT/'context_native_results.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__': main()
