#!/usr/bin/env python3
"""Candidate 4t immutable-action harvester.

This is a synthesis adapter, not another signal generator. It reuses candidate 1k's
semantic-liquidity/exact-route actions, the complete episode event-time state stream,
and candidate 2c's causal pending-order lifecycle. Candidate 4t changes the decision
policy in ``candidate_4t_policy.py`` rather than inventing another OB/FVG trigger.
"""
from __future__ import annotations

import candidate_2c_harvest as synthesis

core = synthesis.core
core.POLICY = (
    "CANDIDATE_4T_IMMUTABLE_ACTIONS_SEMANTIC_LIQUIDITY_COMPLETE_EPISODE_"
    "CAUSAL_PENDING_LIFECYCLE_EXACT_OPPOSING_ROUTE"
)
core.generate_symbol = synthesis.synthesis.generate_symbol

if __name__ == "__main__":
    core.main()
