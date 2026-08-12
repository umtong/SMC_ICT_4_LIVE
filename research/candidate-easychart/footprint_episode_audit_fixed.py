#!/usr/bin/env python3
"""Normalize enum-backed setup records before episode lifecycle replay.

``dataclasses.asdict`` preserves ``TargetMode`` enum instances.  The generic
trade semantic audit intentionally consumes persisted string records, so a
rebuilt in-memory setup was previously labeled ``UNSUPPORTED_DYNAMIC_TARGET``
even though it was a fixed structural target.  This front end normalizes only
the representation and then delegates to the unchanged episode audit.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import footprint_episode_audit as _base
from trade_semantic_audit import Bar, LifecycleAudit
from trade_semantic_audit import audit_setup_lifecycle as _audit_setup_lifecycle


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


_base.audit_setup_lifecycle = audit_setup_lifecycle


if __name__ == "__main__":
    _base.main()
