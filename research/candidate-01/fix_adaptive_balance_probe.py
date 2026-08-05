#!/usr/bin/env python3
"""Repair probe-extreme invalidation ordering in the diagnostic source."""

from pathlib import Path

path = Path(__file__).with_name("adaptive_balance_diagnostics.py")
text = path.read_text(encoding="utf-8")
old = '''        if probe.side is Side.SHORT:
            probe.sweep_extreme = max(probe.sweep_extreme, item.high)
            invalid = item.high > probe.sweep_extreme + profile.stop_buffer_atr * item.atr
            body_atr = abs(item.close - item.open) / item.atr
            flow = -item.flow_z
            overshoot = (probe.internal_break - item.close) / item.atr
'''
new = '''        if probe.side is Side.SHORT:
            prior_extreme = probe.sweep_extreme
            invalid = item.high > prior_extreme + profile.stop_buffer_atr * item.atr
            probe.sweep_extreme = max(prior_extreme, item.high)
            body_atr = abs(item.close - item.open) / item.atr
            flow = -item.flow_z
            overshoot = (probe.internal_break - item.close) / item.atr
'''
if text.count(old) != 1:
    raise SystemExit(f"short probe block found {text.count(old)} times")
text = text.replace(old, new, 1)
old = '''        else:
            probe.sweep_extreme = min(probe.sweep_extreme, item.low)
            invalid = item.low < probe.sweep_extreme - profile.stop_buffer_atr * item.atr
            body_atr = abs(item.close - item.open) / item.atr
            flow = item.flow_z
            overshoot = (item.close - probe.internal_break) / item.atr
'''
new = '''        else:
            prior_extreme = probe.sweep_extreme
            invalid = item.low < prior_extreme - profile.stop_buffer_atr * item.atr
            probe.sweep_extreme = min(prior_extreme, item.low)
            body_atr = abs(item.close - item.open) / item.atr
            flow = item.flow_z
            overshoot = (item.close - probe.internal_break) / item.atr
'''
if text.count(old) != 1:
    raise SystemExit(f"long probe block found {text.count(old)} times")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
