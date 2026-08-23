"""Train/live parity corrections for the robust ML-system router.

The original router draft used a derived ``trade_count_ratio`` name although the
strategy trace and counterfactual trace both expose the observed trade count.
It also resolved aliases only for offline rows, not for live trace dictionaries.
This module patches the shared router namespace before re-exporting it so the
same semantic feature is used by training, backtest and eventual live routing.
"""
from __future__ import annotations

from typing import Any, Mapping

import robust_router as _base

_base.TRACE_NUMERIC_FEATURES = tuple(
    "trace_flow_trade_count" if name == "trace_flow_trade_count_ratio" else name
    for name in _base.TRACE_NUMERIC_FEATURES
)
_base.NUMERIC_FEATURES = (
    _base.PLAN_NUMERIC_FEATURES
    + _base.TRACE_NUMERIC_FEATURES
    + _base.STATE_FEATURES
    + _base.BREADTH_FEATURES
)
_aliases = dict(_base.ROW_ALIASES)
_aliases.pop("trace_flow_trade_count_ratio", None)
_aliases["trace_flow_trade_count"] = (
    "trace_flow_trade_count",
    "flow_trade_count",
)
_base.ROW_ALIASES = _aliases


def _trace_value(trace: Mapping[str, Any] | None, name: str) -> Any:
    if trace is None:
        return None
    keys = [name, name.removeprefix("trace_")]
    for alias in _base.ROW_ALIASES.get(name, ()):
        keys.extend((alias, alias.removeprefix("trace_")))
    for key in dict.fromkeys(keys):
        if key in trace:
            return trace[key]
    return None


_base._trace_value = _trace_value

from robust_router import *  # noqa: E402,F401,F403

TRACE_NUMERIC_FEATURES = _base.TRACE_NUMERIC_FEATURES
NUMERIC_FEATURES = _base.NUMERIC_FEATURES
ROW_ALIASES = _base.ROW_ALIASES
