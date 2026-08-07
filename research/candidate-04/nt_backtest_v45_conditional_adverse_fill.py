#!/usr/bin/env python3
"""Replay frozen V44 with conditional adverse fill expectation only."""
from __future__ import annotations

import nt_backtest_v22_rich_router as routed
import nt_liquidity_strategy as core
from nt_conditional_adverse_fill_risk_sizing import (
    conditional_adverse_fill_submit_bracket,
)


core.LiquidityTransitionStrategy._submit_bracket = (
    conditional_adverse_fill_submit_bracket
)


if __name__ == "__main__":
    routed.base.main()
