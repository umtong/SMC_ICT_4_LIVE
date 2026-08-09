"""Robust actual Nautilus bracket extraction for Candidate 05 v50c."""
from __future__ import annotations

from collections import Counter
import math
from typing import Any

from v50_order_capture import _orders,_price,_reduce_only,_side


def bracket_geometry(value:Any,*,fallback_entry:float)->dict[str,Any]|None:
    orders=_orders(value)
    if not orders:return None
    entries=[order for order in orders if not _reduce_only(order)]
    exits=[order for order in orders if _reduce_only(order)]
    if len(entries)!=1 or len(exits)<1:
        sided=[(order,_side(order)) for order in orders]
        counts=Counter(side for _,side in sided if side in (-1,1))
        if len(orders)>=3 and sorted(counts.values())[-2:]==[1,len(orders)-1]:
            entry_side=next(side for side,count in counts.items() if count==1)
            entries=[order for order,side in sided if side==entry_side]
            exits=[order for order,side in sided if side==-entry_side]
    if len(entries)!=1 or len(exits)<1:return None
    entry_order=entries[0];side=_side(entry_order)
    if side is None:return None
    entry=_price(entry_order,'price','limit_price','trigger_price','stop_price')
    if not math.isfinite(entry):entry=float(fallback_entry)
    stop=math.nan;target=math.nan
    exit_prices=[]
    for order in exits:
        text=f"{getattr(order,'order_type','')} {type(order).__name__}".upper()
        candidate=_price(order,'trigger_price','stop_price','price','limit_price')
        if not math.isfinite(candidate):continue
        exit_prices.append((order,candidate,text))
        if 'STOP' in text and 'TAKE_PROFIT' not in text:stop=candidate
        elif 'TAKE_PROFIT' in text or 'LIMIT' in text:target=candidate
    if not math.isfinite(stop):
        candidates=[candidate for _,candidate,_ in exit_prices if side*(entry-candidate)>0.0]
        if candidates:stop=max(candidates) if side>0 else min(candidates)
    if not math.isfinite(target):
        candidates=[candidate for _,candidate,_ in exit_prices if side*(candidate-entry)>0.0]
        if candidates:target=min(candidates) if side>0 else max(candidates)
    if not all(math.isfinite(item) for item in (entry,stop,target)) or side*(entry-stop)<=0.0 or side*(target-entry)<=0.0:return None
    return {'side':side,'entry_price':entry,'stop_price':stop,'target_price':target,'planned_reward_r':side*(target-entry)/(side*(entry-stop)),'order_count':len(orders)}
