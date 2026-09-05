"""Pooled directional transition model, trained strictly before evaluation.

The no-drift barrier probability 1/(1+RR) is an offset, not a profitability
claim. Boosting learns the observed state-dependent correction to that offset.
Repeated observations of the same defense/objective path share one training
vote. This is not a claim that all training labels are independent trades.

LightGBM init_score is deliberately re-added at prediction: it is NOT stored
in the trained trees. Calibration has two parameters on a later development
interval; neither the tree fit nor calibration optimizes a win-rate threshold.
"""
import json,pickle
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.special import expit
from scipy.optimize import minimize
from sklearn.metrics import roc_auc_score

class TransitionDecision:
    def __init__(self,model,features,a,b,trained_through):
        self.model=model;self.features=tuple(features);self.a=float(a);self.b=float(b)
        self.trained_through=int(trained_through)
    def probabilities(self,plans):
        if not plans:return {}
        x=pd.DataFrame([p.features for p in plans]).reindex(columns=self.features).replace([np.inf,-np.inf],np.nan)
        offset=-np.log(np.array([p.gross_rr for p in plans],dtype=float))
        correction=self.model.predict(x,raw_score=True)
        result=expit(offset+self.a*correction+self.b)
        return {p.plan_id:float(q) for p,q in zip(plans,result)}

def path_weights(frame):
    count=frame.causal_event_id.value_counts()
    weights=1./frame.causal_event_id.map(count).to_numpy(dtype=float)
    return weights/weights.mean()

def fit(base,labels,train_end,cal_end):
    train=labels[labels.label_closed<base.ns(train_end)].copy()
    cal=labels[(labels.observed_time_ns>=base.ns(train_end))&(labels.label_closed<base.ns(cal_end))].copy()
    if len(train)<1000 or len(cal)<200:raise ValueError(f'insufficient direction-state observations: {len(train)} {len(cal)}')
    features=tuple(base.FEATURES)
    x=train.reindex(columns=features).replace([np.inf,-np.inf],np.nan)
    offset=-np.log(train.gross_rr.to_numpy(dtype=float))
    train_set=lgb.Dataset(x,label=train.label_target,weight=path_weights(train),init_score=offset)
    parameters=dict(objective='binary',metric='binary_logloss',learning_rate=.035,num_leaves=15,
        min_data_in_leaf=250,max_bin=63,lambda_l2=20.,feature_fraction=1.,verbosity=-1,
        num_threads=2,seed=20260905,deterministic=True,force_col_wise=True,boost_from_average=False)
    model=lgb.train(parameters,train_set,num_boost_round=220)
    cx=cal.reindex(columns=features).replace([np.inf,-np.inf],np.nan)
    co=-np.log(cal.gross_rr.to_numpy(dtype=float));delta=model.predict(cx,raw_score=True)
    y=cal.label_target.to_numpy(dtype=float);weight=path_weights(cal);weight/=weight.sum()
    def objective(ab):
        a,b=ab;z=co+a*delta+b;p=expit(z)
        loss=np.sum(weight*(np.logaddexp(0,z)-y*z))+.002*(a-1)**2+.002*b*b
        grad=np.array([np.sum(weight*(p-y)*delta)+.004*(a-1),np.sum(weight*(p-y))+.004*b])
        return float(loss),grad
    solution=minimize(objective,np.array([1.,0.]),jac=True,bounds=((0.,None),(None,None)),method='L-BFGS-B')
    if not solution.success:raise RuntimeError('calibration optimizer did not converge: '+solution.message)
    a,b=solution.x;decision=TransitionDecision(model,features,a,b,base.ns(cal_end))
    pred=expit(co+a*delta+b)
    info=dict(train_end=train_end,calibration_end=cal_end,train_observations=len(train),
        train_paths=int(train.causal_event_id.nunique()),calibration_observations=len(cal),
        calibration_paths=int(cal.causal_event_id.nunique()),train_target_rate=float(train.label_target.mean()),
        calibration_target_rate=float(y.mean()),calibration_slope=float(a),calibration_intercept=float(b),
        calibration_auc=float(roc_auc_score(y,pred)),calibration_brier=float(np.mean((pred-y)**2)),
        geometry_baseline_auc=float(roc_auc_score(y,expit(co))),features=features,
        most_used_features=sorted(dict(zip(features,map(float,model.feature_importance(importance_type='gain')))).items(),key=lambda x:-x[1])[:15],
        decision='one shared long/short/no-trade policy; positive expected log NAV increment after current-NAV execution costs')
    base.write(base.OUT/'model.json',info)
    model.save_model(str(base.OUT/'transition_model.txt'))
    (base.OUT/'decision.pkl').write_bytes(pickle.dumps(decision))
    print('DIRECTION_MODEL',json.dumps(info),flush=True)
    return decision
