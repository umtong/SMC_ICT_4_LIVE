"""One conditional target-before-invalidation belief, refreshed as an auction unfolds.

This is not a claimed optimal-stopping solution. It is a one-step policy
improvement over holding the original barriers: compare their conditional
expected log terminal NAV with the executable full-liquidation value now.
Research inspiration: Longstaff & Schwartz (2001), conditional continuation
value, DOI 10.1093/rfs/14.1.113. The continuation approximation here is a
physical-measure two-barrier belief, not risk-neutral option valuation.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import lightgbm as lgb
from scipy.special import expit
from scipy.optimize import minimize_scalar
from path_state import FEATURES


@dataclass
class AuctionBelief:
    model: object
    intercept: float
    columns: tuple=FEATURES
    def probability(self,plans):
        if not plans:return np.empty(0)
        rr=np.array([p.gross_rr for p in plans],dtype=float)
        if np.any(~np.isfinite(rr)) or np.any(rr<=0):raise ValueError('invalid remaining barriers')
        x=np.array([[p.features[k] for k in self.columns] for p in plans])
        return expit(self.model.predict(x,raw_score=True,num_threads=2)-np.log(rr)+self.intercept)


def event_weights(frame):
    counts=frame.causal_event_id.value_counts()
    # Sum of weights is number of episodes, NOT number of repeated states.
    return np.array([1./counts[k] for k in frame.causal_event_id],dtype=float)


def fit_belief(train,calibration):
    if not len(train) or train.label_target.nunique()<2:raise ValueError('insufficient two-outcome episodes')
    rr=train.gross_rr.to_numpy(dtype=float)
    if np.any(~np.isfinite(rr)) or np.any(rr<=0):raise ValueError('invalid remaining RR in training')
    w=event_weights(train)
    data=lgb.Dataset(train[list(FEATURES)].to_numpy(),label=train.label_target.to_numpy(),
                     init_score=-np.log(rr),weight=w,feature_name=list(FEATURES))
    model=lgb.train(dict(objective='binary',num_leaves=7,max_depth=3,min_data_in_leaf=40,
                        min_sum_hessian_in_leaf=5.,learning_rate=.05,lambda_l2=10.,
                        verbosity=-1,num_threads=2,seed=31,deterministic=True,force_col_wise=True),
                    data,num_boost_round=160)
    intercept=0.
    if len(calibration):
        raw=model.predict(calibration[list(FEATURES)].to_numpy(),raw_score=True,num_threads=2)-np.log(calibration.gross_rr.to_numpy())
        y=calibration.label_target.to_numpy();cw=event_weights(calibration)
        fit=minimize_scalar(lambda b:float(np.sum(cw*(np.logaddexp(0.,raw+b)-y*(raw+b)))+b*b),bracket=(-1.,1.))
        if not fit.success:raise RuntimeError('continuation intercept fit failed')
        intercept=float(fit.x)
    return AuctionBelief(model,intercept),dict(training_states=len(train),training_episodes=int(train.causal_event_id.nunique()),
        calibration_states=len(calibration),calibration_episodes=int(calibration.causal_event_id.nunique()),
        calibration_intercept=intercept,features=list(FEATURES),
        importance=dict(zip(FEATURES,map(float,model.feature_importance(importance_type='gain')),strict=True)))


def expected_log_gain(probability:float,win_nav:float,loss_nav:float,current_nav:float)->float:
    if not 0<=probability<=1:raise ValueError('invalid target-first probability')
    if min(win_nav,loss_nav,current_nav)<=0:raise ValueError('non-positive NAV in continuation comparison')
    return float(probability*np.log(win_nav/current_nav)+(1-probability)*np.log(loss_nav/current_nav))
