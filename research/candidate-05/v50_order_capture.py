"""Actual Nautilus order-object geometry extraction for Candidate 05 v50b."""
from __future__ import annotations

import math
from typing import Any,Iterable

from v50_candidate_common import number


def _bool_attr(value:Any,*names:str)->bool:
    for name in names:
        attribute=getattr(value,name,None)
        if callable(attribute):
            try:return bool(attribute())
            except Exception:continue
        if attribute is not None:return bool(attribute)
    return False


def _orders(value:Any)->list[Any]:
    if value is None:return []
    if isinstance(value,(list,tuple)):return list(value)
    items=getattr(value,'orders',None)
    if callable(items):
        try:items=items()
        except Exception:items=None
    if items is not None:
        try:return list(items)
        except TypeError:pass
    return [value]


def _price(order:Any,*names:str)->float:
    for name in names:
        value=getattr(order,name,None)
        if callable(value):
            try:value=value()
            except Exception:continue
        candidate=number(value)
        if math.isfinite(candidate):return candidate
    return math.nan


def _side(order:Any)->int|None:
    value=getattr(order,'side',None);text=str(value).upper()
    if 'BUY' in text:return 1
    if 'SELL' in text:return -1
    return None


def _reduce_only(order:Any)->bool:
    return _bool_attr(order,'is_reduce_only','reduce_only')


def bracket_geometry(value:Any,*,fallback_entry:float)->dict[str,Any]|None:
    orders=_orders(value)
    if not orders:return None
    entries=[order for order in orders if not _reduce_only(order)]
    exits=[order for order in orders if _reduce_only(order)]
    if len(entries)!=1 or len(exits)<1:return None
    entry_order=entries[0];side=_side(entry_order)
    if side is None:return None
    entry=_price(entry_order,'price','limit_price','trigger_price','stop_price')
    if not math.isfinite(entry):entry=float(fallback_entry)
    stop=math.nan;target=math.nan
    for order in exits:
        text=f"{getattr(order,'order_type','')} {type(order).__name__}".upper()
        candidate=_price(order,'trigger_price','stop_price','price','limit_price')
        if not math.isfinite(candidate):continue
        if 'STOP' in text and 'TAKE_PROFIT' not in text:
            stop=candidate
        elif 'TAKE_PROFIT' in text or 'LIMIT' in text:
            target=candidate
    if not math.isfinite(stop):
        candidates=[_price(order,'trigger_price','stop_price','price','limit_price') for order in exits]
        candidates=[candidate for candidate in candidates if math.isfinite(candidate) and side*(entry-candidate)>0.0]
        if candidates:stop=max(candidates) if side>0 else min(candidates)
    if not math.isfinite(target):
        candidates=[_price(order,'trigger_price','stop_price','price','limit_price') for order in exits]
        candidates=[candidate for candidate in candidates if math.isfinite(candidate) and side*(candidate-entry)>0.0]
        if candidates:target=min(candidates) if side>0 else max(candidates)
    if not all(math.isfinite(value) for value in (entry,stop,target)) or side*(entry-stop)<=0.0 or side*(target-entry)<=0.0:return None
    return {'side':side,'entry_price':entry,'stop_price':stop,'target_price':target,'planned_reward_r':side*(target-entry)/(side*(entry-stop)),'order_count':len(orders)}
