"""Offset binary model: learn directional excess, not a raw RR-dependent hit rate.

For a driftless continuous-price process between fixed barriers, the target-hit
probability is 1/(1+RR). This is an explicit null model, not a claim that markets
are Brownian. LightGBM learns departures from its log-odds. The offset is added
again at inference because Dataset.init_score is not stored in Booster trees.
"""
from dataclasses import dataclass
import math
import numpy as np
import lightgbm as lgb
from scipy.special import expit
from scipy.optimize import minimize_scalar

@dataclass
class OffsetDecision:
    model: object
    columns: tuple
    intercept: float=0.
    def probability(self,plans):
        if not plans:return np.empty(0)
        x=np.array([[p.features[k] for k in self.columns] for p in plans],dtype=float)
        rr=np.array([p.gross_rr for p in plans],dtype=float)
        if np.any(~np.isfinite(rr)) or np.any(rr<=0):raise ValueError('invalid pre-entry geometry')
        return expit(self.model.predict(x,raw_score=True)-np.log(rr)+self.intercept)
    def scores(self,plans):
        scores={}
        for p,prob in zip(plans,self.probability(plans),strict=True):
            side=int(p.side.value);entry=p.entry*(1+side*.0001)
            risk=abs(p.entry-p.stop)+.0006*(p.entry+p.stop)+p.entry*.0001
            win_r=(side*(p.target-entry)-.0005*entry-.0002*p.target)/risk
            score=prob*math.log1p(.03*win_r)+(1-prob)*math.log(.97)
            scores[p.plan_id]=(float(score),float(prob))
        return scores

def fit_offset(train,calibration,columns):
    columns=tuple(k for k in columns if k!='planned_rr')
    rr=train.gross_rr.to_numpy(dtype=float)
    if not len(train) or np.any(~np.isfinite(rr)) or np.any(rr<1):raise ValueError('invalid training plans')
    counts=train.causal_event_id.value_counts()
    weights=np.array([1./counts[k] for k in train.causal_event_id])
    weights*=len(weights)/weights.sum()
    data=lgb.Dataset(train[list(columns)].to_numpy(),label=train.label_target.to_numpy(),
                     init_score=-np.log(rr),weight=weights,feature_name=list(columns))
    params=dict(objective='binary',num_leaves=7,max_depth=3,min_data_in_leaf=40,
                learning_rate=.05,lambda_l2=10.,verbosity=-1,num_threads=2,
                seed=31,deterministic=True,force_col_wise=True)
    model=lgb.train(params,data,num_boost_round=160)
    intercept=0.
    if len(calibration):
        raw=model.predict(calibration[list(columns)].to_numpy(),raw_score=True)-np.log(calibration.gross_rr.to_numpy())
        y=calibration.label_target.to_numpy()
        result=minimize_scalar(lambda b:float(np.sum(np.logaddexp(0.,raw+b)-y*(raw+b))+b*b),bracket=(-1.,1.))
        if not result.success:raise RuntimeError('offset-intercept optimization failed')
        intercept=float(result.x)
    decision=OffsetDecision(model,columns,intercept)
    return decision,dict(training_labels=len(train),calibration_labels=len(calibration),features=list(columns),
                         null_model='logit(p_target)=-log(gross_RR)',calibration_intercept=intercept,
                         importance={k:float(v) for k,v in zip(columns,model.feature_importance(importance_type='gain'),strict=True)})
