#!/usr/bin/env python3
"""Run V57 micro-auction signals through NautilusTrader with q95 fill-risk sizing."""
from __future__ import annotations

import nt_backtest_v22_rich_router as routed
import nt_liquidity_strategy as core
from nt_tail_adverse_fill_risk_sizing import q95_adverse_fill_submit_bracket


core.LiquidityTransitionStrategy._submit_bracket = q95_adverse_fill_submit_bracket


if __name__ == "__main__":
    routed.base.main()
