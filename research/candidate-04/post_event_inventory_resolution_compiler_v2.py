#!/usr/bin/env python3
"""Compatibility-only wrapper for V34 without changing scenario logic.

The rich archive has historically exposed both exact-horizon notional columns
and one-minute notional depending on the feature version.  V34's economic test
is directional aggressor effort; this wrapper preserves that relation while
falling back to the completed one-minute notional scaled by elapsed minutes when
an exact horizon column is absent.  It also resolves the existing stop-buffer
field names without changing the configured numeric buffer.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pandas as pd

import post_event_inventory_resolution_compiler as base


def directional_effort_compatible(
    row: pd.Series,
    side: int,
    seconds: int = 60,
) -> float:
    flow_key = f"flow_{seconds}s"
    notional_key = f"notional_{seconds}s"
    flow = base.finite(row.get(flow_key))
    if not math.isfinite(flow):
        return float("nan")
    notional = base.finite(row.get(notional_key))
    if not math.isfinite(notional):
        one_minute = base.finite(row.get("notional_60s"))
        if not math.isfinite(one_minute):
            return float("nan")
        notional = one_minute * max(seconds / 60.0, 1.0)
    if side not in (-1, 1):
        return float("nan")
    return side * flow * max(notional, 0.0)


def response_stop_compatible(
    data: pd.DataFrame,
    start: int,
    end: int,
    trade_side: int,
    impact_parameters: Any,
) -> float:
    buffer = getattr(impact_parameters, "stop_buffer_atr", None)
    if buffer is None:
        buffer = getattr(impact_parameters, "sweep_stop_buffer_atr", None)
    if buffer is None:
        raise AttributeError("impact configuration has no structural stop buffer")
    proxy = SimpleNamespace(stop_buffer_atr=float(buffer))
    return base._response_stop(data, start, end, trade_side, proxy)


base._directional_effort = directional_effort_compatible
base._response_stop = response_stop_compatible
base.v22.collect_signals = base.collect_signals


if __name__ == "__main__":
    base.v22.main()
