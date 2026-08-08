#!/usr/bin/env python3
"""Execute V58 parent-state micro-auction intents through NautilusTrader only."""
from __future__ import annotations

import nt_backtest_v22_rich_router as routed
import nt_declared_causal_target as target_contract
import nt_liquidity_strategy as core
from nt_tail_adverse_fill_risk_sizing import q95_adverse_fill_submit_bracket


if "completed_frozen_balance_" not in target_contract.ALLOWED_TARGET_PREFIXES:
    target_contract.ALLOWED_TARGET_PREFIXES = (
        *target_contract.ALLOWED_TARGET_PREFIXES,
        "completed_frozen_balance_",
    )

core.LiquidityTransitionStrategy._submit_bracket = q95_adverse_fill_submit_bracket


if __name__ == "__main__":
    routed.base.main()
