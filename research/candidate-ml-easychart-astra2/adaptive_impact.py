"""Two economical tests of the response hypothesis.
Monthly belief updates use only preceding observations. The offset alternative
learns excess directional information above 1/(1+RR), rather than spending its
capacity rediscovering unequal-barrier geometry. No holdout-selected cutoff.
"""
import argparse,json,subprocess,sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.special import expit
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from impact_learning import observations,weights,utc,ImpactExecution
from impact_context import FEATURES
from first_passage import label
from market_io import SYMBOLS
from decision_experiment import WINDOWS,view_cases
BASE=Path('research_results/candidate_ml_easychart_astra2')
CACHE=BASE/'adaptive_response_models'
LINEAR=[x for x in FEATURES if x!='rr']


def cut_for(start): return utc(start).replace(day=1)

def design(data,med,scale):
    z=data[LINEAR].fillna(pd.Series(med,index=LINEAR)).to_numpy(float)
    return np.column_stack([np.ones(len(z)),np.tanh((z-med)/scale)])


def fit_month(cut):
    CACHE.mkdir(parents=True,exist_ok=True); key=cut.strftime('%Y-%m-%d')
    left=cut-pd.DateOffset(months=7); split=cut-pd.DateOffset(months=1)
    old=BASE/'impact_response/earlier_observations.csv'
    if key=='2024-08-01' and old.exists(): data=pd.read_csv(old)
    else:
        plans,frames=observations(left.strftime('%Y-%m-%d'),key)
        empty={s:pd.Series(dtype=float,index=pd.DatetimeIndex([],tz='UTC')) for s in SYMBOLS}
        data=label(plans,frames,empty,frames); data=data[data.censored==0].copy()
        data.drop(columns=['net_r','fee_r','funding_r','unit_risk']).to_csv(CACHE/f'observations_{key}.csv',index=False)
    fit=data[(data.ts<split.value)&(data.exit_ts<(split-pd.Timedelta(days=1)).value)]
    cal=data[(data.root_ts>=split.value)&(data.exit_ts<cut.value)]
    if len(fit)<200 or len(cal)<100: raise ValueError('Insufficient completed earlier responses')
    y=(fit.reason=='target').astype(int); yc=(cal.reason=='target').astype(int)
    hgb=HistGradientBoostingClassifier(max_leaf_nodes=7,max_iter=120,min_samples_leaf=80,l2_regularization=20,learning_rate=.05,random_state=7,early_stopping=False)
    hgb.fit(fit[FEATURES],y,sample_weight=weights(fit))
    p=np.clip(hgb.predict_proba(cal[FEATURES])[:,1],1e-5,1-1e-5)
    calibration=LogisticRegression(C=1,random_state=7).fit(np.log(p/(1-p)).reshape(-1,1),yc,sample_weight=weights(cal))
    joblib.dump({'model':hgb,'calibrator':calibration},CACHE/f'hgb_{key}.joblib')
    med=fit[LINEAR].median().fillna(0).to_numpy(float)
    spread=(fit[LINEAR].quantile(.75)-fit[LINEAR].quantile(.25)).fillna(1).to_numpy(float)
    spread=np.where(spread>1e-8,spread,1.)
    x=design(fit,med,spread); xc=design(cal,med,spread)
    offset=-np.log(fit.rr.to_numpy()); offsetc=-np.log(cal.rr.to_numpy())
    model=sm.GLM(y,x,family=sm.families.Binomial(),offset=offset,freq_weights=weights(fit)).fit_regularized(alpha=.01,L1_wt=0,maxiter=1000)
    raw=xc@model.params; xcal=np.column_stack([np.ones(len(cal)),raw])
    adjusted=sm.GLM(yc,xcal,family=sm.families.Binomial(),offset=offsetc,freq_weights=weights(cal)).fit_regularized(alpha=.01,L1_wt=0,maxiter=1000)
    linear={'coef':np.asarray(model.params),'median':med,'scale':spread,'calibration':np.asarray(adjusted.params)}
    joblib.dump(linear,CACHE/f'offset_{key}.joblib')
    meta={'information_cutoff':key,'fit_rows':len(fit),'calibration_rows':len(cal),'fit_target_fraction':float(y.mean()),'calibration_target_fraction':float(yc.mean()),'hgb_calibration_slope':float(calibration.coef_[0,0]),'offset_calibration':linear['calibration'].tolist(),'offset_coefficients':dict(zip(['intercept']+LINEAR,linear['coef'].tolist()))}
    (CACHE/f'model_{key}.json').write_text(json.dumps(meta,indent=2)+'\n'); print(json.dumps(meta),flush=True)


def native(start,end,kind):
    cut=cut_for(start); key=cut.strftime('%Y-%m-%d'); model=joblib.load(CACHE/f'{kind}_{key}.joblib')
    plans,frames=observations(start,end)
    if kind=='hgb':
        p=np.clip(model['model'].predict_proba(plans[FEATURES])[:,1],1e-5,1-1e-5)
        p=model['calibrator'].predict_proba(np.log(p/(1-p)).reshape(-1,1))[:,1]
    else:
        raw=design(plans,model['median'],model['scale'])@model['coef']; a,b=model['calibration']
        p=expit(-np.log(plans.rr.to_numpy())+a+b*raw)
    plans['predicted_win']=p; plans['predicted_value']=0.
    import nautilus_account as account
    account.OUT=BASE/f'adaptive_impact_{kind}'; account.OUT.mkdir(parents=True,exist_ok=True)
    account.AuctionExecution=ImpactExecution
    account.candidates=lambda symbol,d:plans[plans.symbol==symbol].copy()
    summary=account.run(start,end)
    if summary['trades']>0: view_cases(account.OUT,start,end)
    return summary


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--kind',choices=['hgb','offset']); ap.add_argument('--cut'); args=ap.parse_args()
    if args.cut: fit_month(utc(args.cut)); return
    if args.start: native(args.start,args.end,args.kind); return
    for start,end in WINDOWS:
        cut=cut_for(start).strftime('%Y-%m-%d')
        subprocess.run([sys.executable,__file__,'--cut',cut],check=True)
        for kind in ['hgb','offset']:
            subprocess.run([sys.executable,__file__,'--start',start,'--end',end,'--kind',kind],check=True)
    for kind in ['hgb','offset']:
        out=BASE/f'adaptive_impact_{kind}'
        results=[json.loads((out/f'nautilus_transfer_{start}_1-MINUTE_summary.json').read_text()) for start,end in WINDOWS]
        (out/'short_results.json').write_text(json.dumps(results,indent=2)+'\n')
if __name__=='__main__': main()
