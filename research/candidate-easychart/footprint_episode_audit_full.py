#!/usr/bin/env python3
"""Full source-footprint audit for W/M episodes.

This combines the enum-normalization fix with the source-relevant higher
execution/context timeframes.  Footprint history is extended to thirty days,
while the session-range state machine still receives its original build start
and evaluation dates.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Mapping, Sequence

import footprint_episode_audit as _base
from trade_semantic_audit import Bar, LifecycleAudit
from trade_semantic_audit import audit_setup_lifecycle as _audit_setup_lifecycle


_base.TIMEFRAMES = (1, 5, 15, 60, 240, 720, 1440)
_ORIGINAL_LOAD_RANGE = _base.load_range


def load_range(symbol, start, end, cache):
    return _ORIGINAL_LOAD_RANGE(symbol, start - timedelta(days=25), end, cache)


def audit_setup_lifecycle(
    setup: Mapping[str, object],
    bars: Sequence[Bar],
) -> LifecycleAudit:
    normalized = dict(setup)
    target_mode = normalized.get("target_mode")
    value = getattr(target_mode, "value", target_mode)
    text = str(value)
    if text.endswith(".FIXED_STRUCTURE"):
        text = "FIXED_STRUCTURE"
    elif text.endswith(".IMPULSE_EXTREME"):
        text = "IMPULSE_EXTREME"
    normalized["target_mode"] = text
    return _audit_setup_lifecycle(normalized, bars)


_base.load_range = load_range
_base.audit_setup_lifecycle = audit_setup_lifecycle


if __name__ == "__main__":
    _base.main()
