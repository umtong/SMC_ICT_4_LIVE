"""Common causal entry-candidate extraction for Candidate 05 v50."""
from __future__ import annotations

import inspect
import math
from typing import Any,Callable


FEATURE_NAMES=(
    'directional_flow_15s','directional_flow_60s','directional_flow_3m',
    'tail_flow_improvement','directional_depth','efficiency_60s',
    'log_notional_burst','log_absorption_60s','oi_change_15m',
    'penetration_atr','log_pool_age','planned_reward_r',
    'branch_sponsored','branch_retrace','branch_second_touch',
    'branch_balance','branch_breakaway',
)


def number(value:Any)->float:
    try:result=float(value)
    except (TypeError,ValueError):return math.nan
    return result if math.isfinite(result) else math.nan


def exact_side(value:Any)->int|None:
    if value is None:return None
    if hasattr(value,'side'):
        result=exact_side(getattr(value,'side'))
        if result is not None:return result
    text=str(value).upper()
    if text in {'BUY','LONG','1','1.0','ORDERSIDE.BUY'}:return 1
    if text in {'SELL','SHORT','-1','-1.0','ORDERSIDE.SELL'}:return -1
    if isinstance(value,(int,float)) and float(value) in (-1.0,1.0):return int(float(value))
    return None


def bound_values(original:Callable[...,Any],self:Any,args:tuple[Any,...],kwargs:dict[str,Any])->dict[str,Any]:
    try:return dict(inspect.signature(original).bind_partial(self,*args,**kwargs).arguments)
    except TypeError:return {'args':args,**kwargs}


def side_from_bound(bound:dict[str,Any])->int|None:
    for name in ('side','order_side','entry_side'):
        result=exact_side(bound.get(name))
        if result is not None:return result
    for name in ('setup','pending','path','watch','candidate'):
        result=exact_side(bound.get(name))
        if result is not None:return result
    for value in bound.values():
        if hasattr(value,'side'):
            result=exact_side(value)
            if result is not None:return result
    return None


def flatten_objects(bound:dict[str,Any])->dict[str,Any]:
    merged:dict[str,Any]={}
    def add(prefix:str,value:Any)->None:
        if isinstance(value,dict):
            for key,child in value.items():add(f'{prefix}.{key}' if prefix else str(key),child)
            return
        if isinstance(value,(list,tuple)):
            return
        merged[prefix]=value
        details=getattr(value,'details',None)
        if isinstance(details,dict):add(f'{prefix}.details',details)
        if hasattr(value,'__dataclass_fields__'):
            for key in value.__dataclass_fields__:add(f'{prefix}.{key}',getattr(value,key,None))
    for key,value in bound.items():add(str(key),value)
    return merged


def find_value(flat:dict[str,Any],names:tuple[str,...],*,exclude:tuple[str,...]=())->float:
    ranked:list[tuple[int,float]]=[]
    for key,value in flat.items():
        low=key.lower().replace('-','_')
        if any(token in low for token in exclude):continue
        for rank,name in enumerate(names):
            if low==name or low.endswith('.'+name) or low.endswith('_'+name):
                candidate=number(value)
                if math.isfinite(candidate):ranked.append((rank,candidate))
    return min(ranked,key=lambda item:item[0])[1] if ranked else math.nan


def geometry(bound:dict[str,Any],side:int)->dict[str,float]:
    flat=flatten_objects(bound)
    entry=find_value(flat,('entry_price','expected_entry_price','worst_entry_price','limit_price','retrace_level','entry'),exclude=('stop','target','take_profit'))
    stop=find_value(flat,('stop_price','stop_loss_price','structural_stop','retrace_stop','stop'),exclude=('target',))
    target=find_value(flat,('target_price','take_profit_price','take_profit','target'),exclude=('stop',))
    if not math.isfinite(entry):
        entry=find_value(flat,('price',),exclude=('stop','target','pool','structure'))
    reward=(side*(target-entry))/(side*(entry-stop)) if all(math.isfinite(value) for value in (entry,stop,target)) and side*(entry-stop)>0.0 and side*(target-entry)>0.0 else math.nan
    return {'entry_price':entry,'stop_price':stop,'target_price':target,'planned_reward_r':reward}


def details(bound:dict[str,Any])->dict[str,Any]:
    flat=flatten_objects(bound);result:dict[str,Any]={}
    for key,value in flat.items():
        low=key.lower()
        for name in ('branch','penetration_atr','pool_age_minutes','pool_age_bars','target_net_r','expected_net_r','minimum_net_r'):
            if low==name or low.endswith('.'+name):result[name]=value
    return result


def feature_vector(strategy:Any,*,side:int,helper_name:str,bound:dict[str,Any],geometry_values:dict[str,float])->tuple[float,...]:
    def feature(name:str)->float:
        try:return number(strategy._feature(name))
        except Exception:
            current=getattr(strategy,'current_feature',None)
            return number(current.get(name)) if isinstance(current,dict) else math.nan
    flow15=feature('flow_15s');flow60=feature('flow_60s');flow3m=feature('flow_3m');depth=feature('depth_imbalance_1');efficiency=feature('efficiency_60s');burst=feature('notional_burst');absorption=feature('absorption_60s');oi=feature('oi_change_15m')
    extra=details(bound);penetration=number(extra.get('penetration_atr'));pool_age=number(extra.get('pool_age_minutes',extra.get('pool_age_bars')));branch=f"{helper_name} {extra.get('branch','')}".lower()
    log=lambda value:math.log1p(max(value,0.0)) if math.isfinite(value) else math.nan
    return (
        side*flow15 if math.isfinite(flow15) else math.nan,
        side*flow60 if math.isfinite(flow60) else math.nan,
        side*flow3m if math.isfinite(flow3m) else math.nan,
        side*(flow15-flow60) if math.isfinite(flow15) and math.isfinite(flow60) else math.nan,
        side*depth if math.isfinite(depth) else math.nan,
        efficiency,log(burst),log(absorption),oi,penetration,log(pool_age),geometry_values['planned_reward_r'],
        float('sponsor' in branch or 'choch' in branch),float('retrace' in branch),float('second' in branch or 'touch' in branch),float('balance' in branch or 'position_build' in branch),float('breakaway' in branch),
    )


def clear_rejected_state(strategy:Any)->None:
    for name in ('pending','armed_entry_path','balance_acceptance_watch','confirmed_second_touch_watch'):
        if not hasattr(strategy,name):continue
        value=getattr(strategy,name)
        if isinstance(value,list):value.clear()
        else:setattr(strategy,name,None)
    if hasattr(strategy,'entry_pending'):strategy.entry_pending=False
