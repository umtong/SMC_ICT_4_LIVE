#!/usr/bin/env python3
"""Route only causally armed first-return plans through one global account.

The arm is an observed market-state transition, not a learned hindsight filter.
Models compare trade/no-trade and immutable plan geometry only after that transition.
The action space keeps both failed and accepted auctions, both first-return depths,
and all economically valid 1R-2R targets; weak families or geometries must earn
positive post-cost log growth out of period rather than being rescued by fallback.
"""
from __future__ import annotations

import strict_causal_global_router as core


_BASE_LOAD = core.load_actions


def load_actions(root):
    frame = _BASE_LOAD(root)
    if "armed" not in frame:
        raise RuntimeError("commitment census is missing armed state")
    return frame[frame.armed.fillna(False).astype(bool)].copy()


def economic_lattice(frame):
    rr = frame.gross_rr.astype(float)
    target = frame.target_net_r.astype(float)
    entry = frame.entry_geometry.astype(str)
    family = frame.family.astype(str)
    failed = family.str.contains("FAILED")
    accepted = family.str.contains("ACCEPTED")
    # The family transition determines whether an order may exist.  Geometry is
    # still an economic choice: both known-zone depths and every gross RR from
    # 1R through 2R remain available when the causal route pays for them.
    keep = (
        (failed | accepted)
        & entry.isin(["ZONE_PROXIMAL_LIMIT", "ZONE_MID_LIMIT"])
        & rr.between(1.0, 2.0)
        & target.ge(0.30)
    )
    return frame[keep].copy()


core.load_actions = load_actions
core.economic_lattice = economic_lattice

if __name__ == "__main__":
    core.main()
