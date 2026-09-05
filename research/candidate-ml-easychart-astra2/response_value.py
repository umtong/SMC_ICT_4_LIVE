"""Shared conditional first-passage model, not a symbol-specific strategy.
The neutral barrier probability is 1/(1+RR). A regularized logistic adjustment
learns how observed auction responses change it. Positive expected log NAV is
an economic action criterion, not a target win-rate or an evaluation score.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from auction_geometry import FEATURES
from first_passage import FEE_TAKER,FEE_MAKER,SLIP,RISK

POSITIVE={'rr','cost_r','scale','impulse_volume','test_effort','width_atr','reaction_volume'}

class ResponseValue:
    def __init__(self):
        self.features=[x for x in FEATURES if x!='rr']
    def raw(self,rows):
        x=rows[self.features].to_numpy(float)
        for i,name in enumerate(self.features):
            if name in POSITIVE: x[:,i]=np.log1p(np.maximum(x[:,i],0))
        return np.nan_to_num(x,nan=0.,posinf=10.,neginf=-10.)
    def fit(self,rows):
        d=rows[rows.censored==0].copy()
        if len(d)<200: raise ValueError('Insufficient observed auction histories')
        x=self.raw(d); self.center=np.median(x,axis=0)
        self.scale=np.maximum(np.percentile(x,75,axis=0)-np.percentile(x,25,axis=0),.01)
        x=np.c_[np.ones(len(x)),np.clip((x-self.center)/self.scale,-5,5)]
        offset=-np.log(d.rr.to_numpy()); y=(d.reason=='target').to_numpy(float)
        # Correlated proposals from one common episode share one unit of likelihood.
        counts=d.groupby(['window','episode']).ts.transform('count').to_numpy()
        weight=1/np.maximum(counts,1); weight*=len(d)/weight.sum()
        penalty=10.
        def loss(w):
            z=offset+x@w
            val=np.sum(weight*(np.logaddexp(0,z)-y*z))+.5*penalty*np.sum(w[1:]**2)
            grad=x.T@(weight*(expit(z)-y)); grad[1:]+=penalty*w[1:]
            return val,grad
        fit=minimize(loss,np.zeros(x.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':300})
        if not fit.success: raise RuntimeError(fit.message)
        self.coef=fit.x; self.training_rows=len(d); self.last_observed_exit=int(d.exit_ts.max())
        return self
    def predict(self,rows):
        x=np.c_[np.ones(len(rows)),np.clip((self.raw(rows)-self.center)/self.scale,-5,5)]
        return expit(-np.log(rows.rr.to_numpy())+x@self.coef)
    def apply(self,rows):
        d=rows.copy(); p=self.predict(d); entry=d.entry.to_numpy(); stop=d.stop.to_numpy(); target=d.target.to_numpy()
        gross=np.abs(entry-stop)
        budget=gross+entry*(FEE_TAKER+SLIP)+stop*(FEE_TAKER+SLIP)
        win=(np.abs(target-entry)-entry*(FEE_TAKER+SLIP)-target*FEE_MAKER)/budget
        # Pre-trade funding reserve is a fixed economic estimate, never the realized future rate.
        win-=entry*.0001/budget
        d['predicted_win']=p
        d['predicted_value']=p*np.log1p(RISK*win)+(1-p)*np.log1p(-RISK)
        return d
    def save(self,path):
        Path(path).write_text(json.dumps({'features':self.features,'center':self.center.tolist(),'scale':self.scale.tolist(),'coef':self.coef.tolist(),'training_rows':self.training_rows,'last_observed_exit':self.last_observed_exit},indent=2)+'\n')
