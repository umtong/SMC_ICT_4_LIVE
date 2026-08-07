"""Candidate 05 v50b selector over actual constructed Nautilus brackets."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any,Callable

import numpy as np

import strategy_v26 as _v26
from train_v50_analog_model import K,MAX_NEIGHBOR_DISTANCE,MIN_NEIGHBOR_EXPECTANCY_R,MIN_NEIGHBOR_WIN_RATE
from v50_candidate_common import FEATURE_NAMES,bound_values,clear_rejected_state,feature_vector
from v50_order_capture import _orders,bracket_geometry


def _base_class()->type:
    found=[value for value in vars(_v26).values() if isinstance(value,type) and value.__module__==_v26.__name__ and value.__name__.endswith('Strategy')]
    if len(found)!=1:raise RuntimeError(f'expected one v26 strategy, found {[value.__name__ for value in found]}')
    return found[0]


_BASE=_base_class()


class ActualOrderAnalogStrategy(_BASE):
    """Admit an actual v26 bracket only when frozen analogs strongly agree."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        path=Path(os.environ.get('CANDIDATE05_V50_MODEL',str(Path(__file__).with_name('v50_model.json'))));payload=json.loads(path.read_text(encoding='utf-8'))
        if payload.get('schema')!='candidate-05-v50-analog-model-v1' or payload.get('validation_pass') is not True:raise RuntimeError('v50 model was not admitted')
        if tuple(payload.get('feature_names',()))!=FEATURE_NAMES:raise RuntimeError('v50 feature contract mismatch')
        model=payload['model'];self.v50_center=np.asarray(model['center'],dtype=float);self.v50_scale=np.asarray(model['scale'],dtype=float);self.v50_reference=np.asarray(model['features'],dtype=float);self.v50_labels=np.asarray(model['labels'],dtype=float);self.v50_rewards=np.asarray(model['reward_r'],dtype=float)
        self.v50b_context:dict[str,Any]|None=None;self.v50b_orders:list[Any]=[];self.v50b_decision:bool|None=None;self.v50b_submitted=False
        self.diagnostics.update({'v50_entry_attempts':0,'v50_selected_entries':0,'v50_rejected_entries':0,'v50_geometry_failures':0,'v50_max_neighbor_win_rate':0.0,'v50_max_neighbor_expectancy_r':-1.0,'v50_actual_order_selection':True})

    def _analog_state(self,values:tuple[float,...])->dict[str,float]:
        query=np.asarray(values,dtype=float);query=np.where(np.isfinite(query),query,self.v50_center);reference=np.where(np.isfinite(self.v50_reference),self.v50_reference,self.v50_center);distance=np.sqrt(np.mean(((reference-query)/self.v50_scale)**2,axis=1));order=np.argsort(distance)[:min(K,len(distance))];labels=self.v50_labels[order];rewards=self.v50_rewards[order];win_rate=float(labels.mean()) if len(order) else 0.0;expectancy=float(np.mean(np.where(labels>0.5,rewards,-1.0))) if len(order) else -1.0;maximum=float(distance[order].max()) if len(order) else math.inf;selected=len(order)>=min(K,20) and win_rate>=MIN_NEIGHBOR_WIN_RATE and expectancy>=MIN_NEIGHBOR_EXPECTANCY_R and maximum<=MAX_NEIGHBOR_DISTANCE
        return {'selected':float(selected),'win_rate':win_rate,'expectancy_r':expectancy,'max_distance':maximum}

    def _decide(self)->bool|None:
        if self.v50b_context is None:return None
        row=self.bars[-1] if getattr(self,'bars',None) else {'close':0.0}
        geo=bracket_geometry(self.v50b_orders,fallback_entry=float(row['close']))
        if geo is None:return None
        state=self._analog_state(feature_vector(self,side=int(geo['side']),helper_name=self.v50b_context['helper'],bound=self.v50b_context['bound'],geometry_values=geo));self.diagnostics['v50_max_neighbor_win_rate']=max(float(self.diagnostics['v50_max_neighbor_win_rate']),state['win_rate']);self.diagnostics['v50_max_neighbor_expectancy_r']=max(float(self.diagnostics['v50_max_neighbor_expectancy_r']),state['expectancy_r']);self.v50b_decision=state['selected']>0.5
        if self.v50b_decision:self.diagnostics['v50_selected_entries']+=1
        else:self.diagnostics['v50_rejected_entries']+=1
        return self.v50b_decision

    def submit_order_list(self,order_list:Any,*args:Any,**kwargs:Any)->None:
        if self.v50b_context is None:return super().submit_order_list(order_list,*args,**kwargs)
        self.v50b_orders.extend(_orders(order_list));decision=self._decide()
        if decision:
            self.v50b_submitted=True
            return super().submit_order_list(order_list,*args,**kwargs)
        return None

    def submit_order(self,order:Any,*args:Any,**kwargs:Any)->None:
        if self.v50b_context is None:return super().submit_order(order,*args,**kwargs)
        self.v50b_orders.extend(_orders(order));decision=self._decide()
        if decision and not self.v50b_submitted:
            self.v50b_submitted=True
            for captured in self.v50b_orders:super().submit_order(captured,*args,**kwargs)
        return None

    def _finish(self)->None:
        if self.v50b_decision is None:
            self.diagnostics['v50_geometry_failures']+=1;self.v50b_decision=False
        if not self.v50b_decision:clear_rejected_state(self)
        self.v50b_context=None;self.v50b_orders.clear();self.v50b_decision=None;self.v50b_submitted=False


def _wrap(name:str,original:Callable[...,Any])->Callable[...,Any]:
    def wrapped(self:ActualOrderAnalogStrategy,*args:Any,**kwargs:Any)->Any:
        if self.v50b_context is not None:raise RuntimeError('nested v50 selector context')
        self.diagnostics['v50_entry_attempts']+=1;self.v50b_context={'helper':name,'bound':bound_values(original,self,args,kwargs)};self.v50b_orders.clear();self.v50b_decision=None;self.v50b_submitted=False
        try:return original(self,*args,**kwargs)
        finally:self._finish()
    wrapped.__name__=name;wrapped.__qualname__=f'ActualOrderAnalogStrategy.{name}';wrapped.__doc__=getattr(original,'__doc__',None);return wrapped


_WRAPPED:list[str]=[]
for _name in dir(_BASE):
    if not (_name.startswith('_submit') and 'entry' in _name):continue
    _method=getattr(_BASE,_name)
    if callable(_method):setattr(ActualOrderAnalogStrategy,_name,_wrap(_name,_method));_WRAPPED.append(_name)
if not _WRAPPED:raise RuntimeError('v50b selector found no inherited entry helpers')

CandidateStrategy=ActualOrderAnalogStrategy
StrategyClass=ActualOrderAnalogStrategy
