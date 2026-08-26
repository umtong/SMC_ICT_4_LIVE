#!/usr/bin/env python3
"""Run the failed-auction policy only after durable control transfer.

The raw clinic showed that same-bar responses were usually ordinary noise and
that reversals fighting an already confirmed multiscale structure were not the
skilled-trader trade.  These are market-state semantics, not score thresholds:
the response must exist for at least one completed minute and both prior and
current multiscale votes must be non-opposing.
"""
from __future__ import annotations

import control_transfer_research as core


_base_detect = core.detect_rejection


def _quality_detect(state, frame, common):
    if float(state.get("response_delay_minutes", 0.0)) < 1.0:
        return None
    if float(state.get("prior_structure_multiscale_trend_vote", 0.0)) < 0.0:
        return None
    if float(state.get("structure_multiscale_trend_vote", 0.0)) < 0.0:
        return None
    return _base_detect(state, frame, common)


core.detect_rejection = _quality_detect

if __name__ == "__main__":
    core.main()
