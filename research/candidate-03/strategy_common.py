"""Shared causal helpers for candidate-03."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable
from model import Bar

NS_PER_MINUTE=60_000_000_000
NS_PER_DAY=1440*NS_PER_MINUTE
Emit=Callable[...,None]

def utc_date_key(timestamp_ns:int)->str:
    return datetime.fromtimestamp(timestamp_ns/1e9,tz=timezone.utc).date().isoformat()

def true_range(bar:Bar,previous_close:float|None)->float:
    if previous_close is None: return bar.high-bar.low
    return max(bar.high-bar.low,abs(bar.high-previous_close),abs(bar.low-previous_close))

def close_location(bar:Bar)->float:
    return (bar.close-bar.low)/bar.range if bar.range>0 else 0.5

def ratio(numerator:float,denominator:float)->float:
    return numerator/denominator if denominator>0 else 0.0
