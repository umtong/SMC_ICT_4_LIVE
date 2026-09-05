"""One shared conditional response policy; earlier-only training and calibration.
Gross barrier labels are training observations, not account returns. Actual
performance is always produced by the existing native Nautilus account.
"""
from __future__ import annotations
import argparse,importlib,json,subprocess,sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from market_io import prepare,bars,months_between,SYMBOLS
from response_geometry import candidates as geometries
from impact_context import enrich,FEATURES
from first_passage import attach_episodes,label
from nautilus_policy import AuctionExecution
from decision_experiment import WINDOWS,view_cases

OUT=Path('research_results/candidate_ml_easychart_astra2/impact_response')
MODEL=OUT/'model.joblib'


def utc(s): return pd.Timestamp(s,tz='UTC')

def observations(start,end):
    left=utc(start)-pd.Timedelta(days=7); right=utc(end)
    prepare(months_between(left,right),kinds=('klines',))
    frames={s:bars(s,left,right) for s in SYMBOLS}
    plans=pd.concat([geometries(s,d) for s,d in frames.items()],ignore_index=True)
    plans=plans[(plans.ts>=utc(start).value)&(plans.ts<right.value)].copy()
    plans=attach_episodes(plans,frames)
    plans=enrich(plans,frames)
    return plans,frames


def weights(data):
    w=1/data.groupby('episode').episode.transform('size').to_numpy(float)
    return w/w.mean()


def train():
    plans,frames=observations('2024-01-01','2024-08-01')
    # Only first-touch target/stop labels are used here. Zero cashflows are
    # intentional because this classifier does not estimate economic returns.
    empty={s:pd.Series(dtype=float,index=pd.DatetimeIndex([],tz='UTC')) for s in SYMBOLS}
    data=label(plans,frames,empty,frames)
    data=data[data.censored==0].copy()
    fit=data[(data.ts<utc('2024-07-01').value)&(data.exit_ts<utc('2024-06-30').value)].copy()
    cal=data[(data.root_ts>=utc('2024-07-01').value)&(data.exit_ts<utc('2024-08-01').value)].copy()
    if len(fit)<200 or len(cal)<100: raise ValueError(f'Too few earlier observations: {len(fit)}, {len(cal)}')
    model=HistGradientBoostingClassifier(max_leaf_nodes=7,max_iter=120,min_samples_leaf=80,l2_regularization=20,learning_rate=.05,random_state=7,early_stopping=False)
    model.fit(fit[FEATURES],(fit.reason=='target').astype(int),sample_weight=weights(fit))
    raw=np.clip(model.predict_proba(cal[FEATURES])[:,1],1e-5,1-1e-5)
    calibrator=LogisticRegression(C=1,random_state=7)
    calibrator.fit(np.log(raw/(1-raw)).reshape(-1,1),(cal.reason=='target').astype(int),sample_weight=weights(cal))
    OUT.mkdir(parents=True,exist_ok=True)
    joblib.dump({'model':model,'calibrator':calibrator,'features':FEATURES,'trained_through':'2024-07-31'},MODEL)
    meta={'fit_rows':len(fit),'calibration_rows':len(cal),'fit_episodes':int(fit.episode.nunique()),'calibration_episodes':int(cal.episode.nunique()),'fit_target_fraction':float((fit.reason=='target').mean()),'calibration_target_fraction':float((cal.reason=='target').mean()),'calibration_slope':float(calibrator.coef_[0,0]),'calibration_intercept':float(calibrator.intercept_[0]),'features':FEATURES}
    (OUT/'training.json').write_text(json.dumps(meta,indent=2)+'\n')
    # No counterfactual candidate returns are presented as an account.
    data.drop(columns=['net_r','fee_r','funding_r','unit_risk']).to_csv(OUT/'earlier_observations.csv',index=False)
    print(json.dumps(meta),flush=True)


class ImpactExecution(AuctionExecution):
    def __init__(self,plans,*args,**kwargs):
        super().__init__(plans,*args,**kwargs)
        self.plans['predicted_win']=plans.predicted_win.to_numpy()
    def value(self,row):
        inst=self.instruments[row['symbol']]; s=row['side']; entry=float(inst.make_price(row['entry'])); stop=float(inst.make_price(row['stop'])); target=float(inst.make_price(row['target']))
        risk=s*(entry-stop); reward=s*(target-entry)
        if risk<=0 or reward<risk: return -1.
        nav=self.balance(); now=self.clock.timestamp_ns(); q=.03*nav/risk
        for _ in range(15):
            entry_cost=.0005+self.costs.surcharge(row['symbol'],q*entry,now)
            stop_cost=.0005+self.costs.surcharge(row['symbol'],q*stop,now)
            unit=risk+entry*entry_cost+stop*stop_cost+float(inst.price_increment)
            q=.03*nav/unit
        win=(reward-entry*entry_cost-target*.0002)/unit
        if win<=0: return -1.
        p=row['predicted_win']
        return float(p*np.log1p(.03*win)+(1-p)*np.log(.97))
    def on_decision(self,event):
        t=int(event.name.split('-')[1])
        if self.active is not None or self.cache.positions_open(): return
        for row in self.groups[t]: row['predicted_value']=self.value(row)
        super().on_decision(event)
    def submit(self,row):
        if row.get('predicted_value',-1)<=0: return False
        return super().submit(row)


def native(start,end):
    saved=joblib.load(MODEL)
    if utc(start)<=utc(saved['trained_through']): raise ValueError('Evaluation must follow all training and calibration observations')
    plans,frames=observations(start,end)
    p=np.clip(saved['model'].predict_proba(plans[FEATURES])[:,1],1e-5,1-1e-5)
    plans['predicted_win']=saved['calibrator'].predict_proba(np.log(p/(1-p)).reshape(-1,1))[:,1]
    plans['predicted_value']=0.
    import nautilus_account as account
    account.OUT=OUT; account.AuctionExecution=ImpactExecution
    account.candidates=lambda symbol,d: plans[plans.symbol==symbol].copy()
    result=account.run(start,end)
    view_cases(OUT,start,end)
    probabilities=plans.predicted_win.describe().to_dict()
    (OUT/f'probabilities_{start}.json').write_text(json.dumps(probabilities,indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--train',action='store_true'); ap.add_argument('--start'); ap.add_argument('--end'); args=ap.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.train: train(); return
    if args.start: native(args.start,args.end); return
    train(); results=[]
    for start,end in WINDOWS:
        subprocess.run([sys.executable,__file__,'--start',start,'--end',end],check=True)
        results.append(json.loads((OUT/f'nautilus_transfer_{start}_1-MINUTE_summary.json').read_text()))
    (OUT/'short_results.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__': main()
