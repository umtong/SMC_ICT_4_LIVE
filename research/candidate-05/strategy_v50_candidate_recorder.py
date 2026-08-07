"""Candidate 05 v50 research recorder.

Every inherited v26 entry proposal is recorded before any order is sent, then
rejected so later independent opportunities remain observable.  NautilusTrader
still drives the market clock and feature causality; this module produces no
fills, positions, fees or performance claims.
"""
from __future__ import annotations

from typing import Any,Callable

import strategy_v26 as _v26
from v50_candidate_common import (
    FEATURE_NAMES,bound_values,clear_rejected_state,feature_vector,geometry,
    side_from_bound,
)


def _base_class()->type:
    found=[value for value in vars(_v26).values() if isinstance(value,type) and value.__module__==_v26.__name__ and value.__name__.endswith('Strategy')]
    if len(found)!=1:raise RuntimeError(f'expected one v26 strategy, found {[value.__name__ for value in found]}')
    return found[0]


_BASE=_base_class()


class AllCandidateRecorderStrategy(_BASE):
    """Observe all completed inherited entry candidates without trading."""

    def __init__(self,config:Any)->None:
        super().__init__(config)
        self.v50_candidates:list[dict[str,Any]]=[]
        self.diagnostics.update({'v50_candidate_count':0,'v50_geometry_failures':0,'v50_side_failures':0,'v50_candidates':self.v50_candidates,'v50_feature_names':list(FEATURE_NAMES)})


def _wrap(name:str,original:Callable[...,Any])->Callable[...,Any]:
    def wrapped(self:AllCandidateRecorderStrategy,*args:Any,**kwargs:Any)->bool:
        bound=bound_values(original,self,args,kwargs);side=side_from_bound(bound)
        if side is None:
            self.diagnostics['v50_side_failures']+=1
            clear_rejected_state(self)
            return False
        geo=geometry(bound,side)
        if not all(geo[key]==geo[key] for key in ('entry_price','stop_price','target_price','planned_reward_r')):
            self.diagnostics['v50_geometry_failures']+=1
            clear_rejected_state(self)
            return False
        row=self.bars[-1] if getattr(self,'bars',None) else {'ts':0,'close':geo['entry_price']}
        record={
            'candidate_id':f"v50-{len(self.v50_candidates)+1:08d}",
            'ts_event':int(row['ts']),
            'helper':name,
            'side':side,
            **geo,
            'features':list(feature_vector(self,side=side,helper_name=name,bound=bound,geometry_values=geo)),
        }
        self.v50_candidates.append(record)
        self.diagnostics['v50_candidate_count']+=1
        clear_rejected_state(self)
        return False
    wrapped.__name__=name;wrapped.__qualname__=f'AllCandidateRecorderStrategy.{name}';wrapped.__doc__=getattr(original,'__doc__',None)
    return wrapped


_WRAPPED:list[str]=[]
for _name in dir(_BASE):
    if not (_name.startswith('_submit') and 'entry' in _name):continue
    _method=getattr(_BASE,_name)
    if callable(_method):setattr(AllCandidateRecorderStrategy,_name,_wrap(_name,_method));_WRAPPED.append(_name)
if not _WRAPPED:raise RuntimeError('v50 recorder found no inherited entry helpers')

CandidateStrategy=AllCandidateRecorderStrategy
StrategyClass=AllCandidateRecorderStrategy
