#!/usr/bin/env python3
"""Replay frozen V31 core with only the fill expectation changed."""
from __future__ import annotations

import nt_backtest_v22_rich_router as routed
import nt_liquidity_strategy as core
from nt_expected_fill_only_risk_sizing import expected_fill_only_submit_bracket


core.LiquidityTransitionStrategy._submit_bracket = expected_fill_only_submit_bracket


if __name__ == "__main__":
    routed.base.main()
