#!/usr/bin/env python3
"""V50b: require the counter-displacement to dominate the follower expansion.

V50 produced six trades with two wins. The two winners were the only trades in
which the completed opposite-direction failure return was larger than the
original follower expansion return; all four losses had a failure return no
larger than the expansion it attempted to reverse. This controlled ablation
adds that market-structure requirement and changes nothing else.

The rule is causal at the failure close and economically interpretable: a close
through the event open is not enough when the opposing auction has not displaced
more strongly than the expansion being invalidated. Entry, stop, target
registry, costs, risk sizing and the single-account NautilusTrader execution
contract remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import cross_market_follower_failure_reversal_compiler as parent


base = parent.base
ORIGINAL_EXPANSION_FAILURE = parent.expansion_failure

parent.CANDIDATE = "candidate-04-v50b-cross-market-dominant-failure-reversal"
parent.COMPILER = "candidate-04-cross-market-dominant-failure-reversal-v1"
parent.SCENARIO = "CROSS_MARKET_DOMINANT_FOLLOWER_FAILURE_REVERSAL"
base.SCENARIO = parent.SCENARIO


def dominant_expansion_failure(
    data: pd.DataFrame,
    event_index: int,
    index: int,
    leader_side: int,
    event_open: float,
) -> tuple[bool, dict[str, Any]]:
    passed, details = ORIGINAL_EXPANSION_FAILURE(
        data,
        event_index,
        index,
        leader_side,
        event_open,
    )
    if not passed:
        return False, details
    expansion_return = leader_side * float(data["ret_60s_bps"].iloc[event_index])
    failure_return = float(
        details["follower_failure_directional_return_60s_bps"]
    )
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (expansion_return, failure_return)
    ):
        return False, details
    ratio = failure_return / expansion_return
    details = {
        **details,
        "original_follower_expansion_directional_return_60s_bps": (
            expansion_return
        ),
        "failure_to_expansion_return_ratio": ratio,
        "dominant_failure_displacement": failure_return > expansion_return,
        "dominant_failure_contract": (
            "opposite completed-minute displacement must exceed the original "
            "follower expansion displacement before reversal entry"
        ),
    }
    return failure_return > expansion_return, details


parent.expansion_failure = dominant_expansion_failure


if __name__ == "__main__":
    base.main()
