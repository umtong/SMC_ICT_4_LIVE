"""Candidate 05 v50: fixed pre-evaluation high-precision analog selector.

The selector is applied at the exact inherited v26 order-submission boundary.
It changes neither scenario geometry nor risk: every admitted candidate keeps
its original entry, structural stop, real liquidity target, fees, adverse
slippage, 3% current-NAV quantity and NautilusTrader lifecycle.
"""
from __future__ import annotations

import inspect
import json
import math
import os
from pathlib import Path
from typing import Any,Callable

import numpy as np

import strategy_v26 as _v26
from train_v50_analog_model import K,MAX_NEIGHBOR_DISTANCE,MIN_NEIGHBOR_EXPECTANCY_R,MIN_NEIGHBOR_WIN_RATE
from v50_candidate_common import FEATURE_NAMES,bound_values,clear_rejected_state,feature_vector,geometry,side_from_bound


def _base_class()->type:
    found=[value for value in vars(_v26).values() if isinstance(value,type) and value.__module__==_v26.__name__ and value.__name__.endswith('Strategy')]
    if len(found)!=1:raise RuntimeError(f'expected one v26 strategy, found {[value.__name__ for value in found]}')
    return found[0]


_BASE=_base_class()


class HighPrecisionAnalogStrategy(_BASE):
    """Trade only when fixed pre-evaluation structural analogs agree."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        path=Path(os.environ.get('CANDIDATE05_V50_MODEL',str(Path(__file__).with_name('v50_model.json'))));payload=json.loads(path.read_text(encoding='utf-8'))
        if payload.get('schema')!='candidate-05-v50-analog-model-v1' or payload.get('validation_pass') is not True:raise RuntimeError('v50 model was not admitted by pre-evaluation validation')
        if tuple(payload.get('feature_names',()))!=FEATURE_NAMES:raise RuntimeError('v50 feature contract mismatch')
        model=payload['model'];self.v50_center=np.asarray(model['center'],dtype=float);self.v50_scale=np.asarray(model['scale'],dtype=float);self.v50_reference=np.asarray(model['features'],dtype=float);self.v50_labels=np.asarray(model['labels'],dtype=float);self.v50_rewards=np.asarray(model['reward_r'],dtype=float)
        if self.v50_reference.ndim!=2 or self.v50_reference.shape[1]!=len(FEATURE_NAMES):raise RuntimeError('invalid v50 reference shape')
        self.diagnostics.update({'v50_entry_attempts':0,'v50_selected_entries':0,'v50_rejected_entries':0,'v50_geometry_failures':0,'v50_side_failures':0,'v50_max_neighbor_win_rate':0.0,'v50_max_neighbor_expectancy_r':-1.0})

    def _analog_state(self,values:tuple[float,...])->dict[str,float]:
        query=np.asarray(values,dtype=float);query=np.where(np.isfinite(query),query,self.v50_center);reference=np.where(np.isfinite(self.v50_reference),self.v50_reference,self.v50_center);distance=np.sqrt(np.mean(((reference-query)/self.v50_scale)**2,axis=1));order=np.argsort(distance)[:min(K,len(distance))]
        labels=self.v50_labels[order];rewards=self.v50_rewards[order];win_rate=float(labels.mean()) if len(order) else 0.0;expectancy=float(np.mean(np.where(labels>0.5,rewards,-1.0))) if len(order) else -1.0;maximum=float(distance[order].max()) if len(order) else math.inf
        return {'win_rate':win_rate,'expectancy_r':expectancy,'max_distance':maximum,'selected':float(len(order)>=min(K,20) and win_rate>=MIN_NEIGHBOR_WIN_RATE and expectancy>=MIN_NEIGHBOR_EXPECTANCY_R and maximum<=MAX_NEIGHBOR_DISTANCE)}


def _wrap(name:str,original:Callable[...,Any])->Callable[...,Any]:
    def wrapped(self:HighPrecisionAnalogStrategy,*args:Any,**kwargs:Any)->Any:
        bound=bound_values(original,self,args,kwargs);side=side_from_bound(bound);self.diagnostics['v50_entry_attempts']+=1
        if side is None:
            self.diagnostics['v50_side_failures']+=1;clear_rejected_state(self);return False
        geo=geometry(bound,side)
        if not all(math.isfinite(geo[key]) for key in ('entry_price','stop_price','target_price','planned_reward_r')):
            self.diagnostics['v50_geometry_failures']+=1;clear_rejected_state(self);return False
        state=self._analog_state(feature_vector(self,side=side,helper_name=name,bound=bound,geometry_values=geo));self.diagnostics['v50_max_neighbor_win_rate']=max(float(self.diagnostics['v50_max_neighbor_win_rate']),state['win_rate']);self.diagnostics['v50_max_neighbor_expectancy_r']=max(float(self.diagnostics['v50_max_neighbor_expectancy_r']),state['expectancy_r'])
        if state['selected']<0.5:
            self.diagnostics['v50_rejected_entries']+=1;clear_rejected_state(self);return False
        self.diagnostics['v50_selected_entries']+=1
        return original(self,*args,**kwargs)
    wrapped.__name__=name;wrapped.__qualname__=f'HighPrecisionAnalogStrategy.{name}';wrapped.__doc__=getattr(original,'__doc__',None);return wrapped


_WRAPPED:list[str]=[]
for _name in dir(_BASE):
    if not (_name.startswith('_submit') and 'entry' in _name):continue
    _method=getattr(_BASE,_name)
    if callable(_method):setattr(HighPrecisionAnalogStrategy,_name,_wrap(_name,_method));_WRAPPED.append(_name)
if not _WRAPPED:raise RuntimeError('v50 selector found no inherited entry helpers')

CandidateStrategy=HighPrecisionAnalogStrategy
StrategyClass=HighPrecisionAnalogStrategy
