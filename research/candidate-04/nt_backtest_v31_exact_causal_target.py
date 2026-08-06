#!/usr/bin/env python3
"""Replay frozen rich-state signals with their exact causal target in Nautilus."""
from __future__ import annotations

import nt_backtest_v22_rich_router as routed
import nt_liquidity_strategy as core
from nt_exact_causal_target_risk_sizing import (
    exact_causal_target_risk_sized_submit_bracket,
)


# nt_backtest_v22_rich_router first installs the frozen causal fill-risk adapter.
# Replace only its target-selection relation; every other execution component is
# inherited unchanged.
core.LiquidityTransitionStrategy._submit_bracket = (
    exact_causal_target_risk_sized_submit_bracket
)


if __name__ == "__main__":
    routed.base.main()
