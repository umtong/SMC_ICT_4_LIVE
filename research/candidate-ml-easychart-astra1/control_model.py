"""Learn excess directional evidence relative to the pre-entry barrier geometry.

Adapted from candidate-ml-easychart-astra3/models.py, not its strategy.
With no directional evidence the continuous martingale first-passage null has
P(target first)=1/(1+RR). This is a null, not an assumption that crypto prices
are Brownian. Calibrate the LEARNED EXCESS, never flatten the RR-dependent null.
The old sigmoid slope was 0.0207: nearly constant probabilities rewarded distant
objectives mechanically. This model removes that incorrect decision mechanism.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.special import expit
from scipy.optimize import minimize

@dataclass
class BarrierDecision:
    model: object
    columns: tuple
    intercept: float
    slope: float
    trained_through: int
    def probabilities(self,plans):
        if not plans:return {}
        x=pd.DataFrame([p.features for p in plans]).reindex(columns=self.columns)
        rr=np.array([p.gross_rr for p in plans])
        if np.any(rr<1) or np.any(~np.isfinite(rr)):raise ValueError('invalid geometry')
        excess=self.model.predict(x,raw_score=True)
        probabilities=expit(-np.log(rr)+self.intercept+self.slope*excess)
        return {p.plan_id:float(q) for p,q in zip(plans,probabilities,strict=True)}
    def predict_frame(self,frame):
        excess=self.model.predict(frame[list(self.columns)],raw_score=True)
        return expit(-np.log(frame.gross_rr)+self.intercept+self.slope*excess)

def fit_barrier(train,cal,columns,trained_through):
    columns=tuple(k for k in columns if k!='planned_rr')
    group=train.observed_time_ns//(5*60_000_000_000)
    weights=1./group.map(group.value_counts()).to_numpy()
    weights*=len(weights)/weights.sum()
    data=lgb.Dataset(train[list(columns)],label=train.label_target,
                     init_score=-np.log(train.gross_rr),weight=weights,feature_name=list(columns))
    model=lgb.train(dict(objective='binary',num_leaves=7,max_depth=3,min_data_in_leaf=50,
                         learning_rate=.05,lambda_l2=10.,verbosity=-1,num_threads=2,
                         seed=20260905,deterministic=True,force_col_wise=True),data,num_boost_round=160)
    raw=model.predict(cal[list(columns)],raw_score=True)
    offset=-np.log(cal.gross_rr.to_numpy());y=cal.label_target.to_numpy()
    # Ordinary likelihood calibration with mild unit-scale parameter priors.
    def objective(z):
        eta=offset+z[0]+z[1]*raw
        return float(np.sum(np.logaddexp(0.,eta)-y*eta)+z[0]**2+(z[1]-1)**2)
    result=minimize(objective,np.array([0.,1.]),method='BFGS')
    if not result.success and np.linalg.norm(result.jac)>1e-4:raise RuntimeError(result.message)
    b,a=map(float,result.x)
    decision=BarrierDecision(model,columns,b,a,int(trained_through))
    fitted=decision.predict_frame(cal)
    null=1/(1+cal.gross_rr.to_numpy())
    details={'train_candidates':len(train),'calibration_candidates':len(cal),
             'calibration_intercept':b,'calibration_excess_slope':a,
             'calibration_brier':float(np.mean((fitted-y)**2)),
             'null_brier':float(np.mean((null-y)**2)),
             'null_model':'logit P(target first) = -log(gross_RR)',
             'features':columns,
             'importance':dict(zip(columns,map(float,model.feature_importance(importance_type='gain')),strict=True))}
    return decision,details

def describe_observations(labels):
    """Small hypothesis-oriented diagnosis, not a filter search or a scorecard."""
    out={}
    conditions={
        'all':np.ones(len(labels),dtype=bool),
        'higher_direction_agrees':(labels.context_15>0)&(labels.context_60>0),
        'higher_direction_opposes':(labels.context_15<0)&(labels.context_60<0),
        'higher_liquidity_reclaimed':(labels.sweep_15>0)|(labels.sweep_60>0),
        'pullback_opposition_without_progress':(labels.pullback_flow<0)&(labels.innovation_slow>0),
        'market_and_local_demand_agree':(labels.market_15>0)&(labels.innovation_slow>0),
        'meaningful_displacement':labels.impulse_range>2,
        'strong_displacement_with_weaker_pullback':(labels.impulse_range>2)&(labels.pullback_activity<labels.impulse_activity),
    }
    for name,mask in conditions.items():
        d=labels.loc[mask]
        out[name]={'observations':len(d),'target_rate':float(d.label_target.mean()) if len(d) else None,
                   'mean_net_r':float(d.label_net_r.mean()) if len(d) else None,
                   'mean_rr':float(d.gross_rr.mean()) if len(d) else None,
                   'mean_cost_r':float(d.cost_r.mean()) if len(d) else None,
                   'mean_excess_vs_null':float((d.label_target-1/(1+d.gross_rr)).mean()) if len(d) else None}
    return out
