#!/usr/bin/env python3
"""Replay frozen rich-state signals with causal expected fill sizing."""
from __future__ import annotations

import nt_backtest_v22_rich_router as routed
import nt_liquidity_strategy as core
from nt_expected_fill_risk_sizing import expected_fill_risk_sized_submit_bracket


core.LiquidityTransitionStrategy._submit_bracket = (
    expected_fill_risk_sized_submit_bracket
)


if __name__ == "__main__":
    routed.base.main()
