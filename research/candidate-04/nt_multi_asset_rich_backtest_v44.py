#!/usr/bin/env python3
"""Four-instrument execution with the repaired 3% fill-risk contract.

The V3 multi-asset runner owns the one-account global coordinator, exact trusted
NautilusTrader venue configuration and risk-evidence reconciliation. This
wrapper changes only the shared bracket sizing callback to the conditional mean
of completed adverse entry-delay transitions established by the V45c repair.
"""
from __future__ import annotations

import nt_multi_asset_rich_backtest_v3 as base
import nt_liquidity_strategy as core
from nt_conditional_adverse_fill_risk_sizing import (
    conditional_adverse_fill_submit_bracket,
)


core.LiquidityTransitionStrategy._submit_bracket = (
    conditional_adverse_fill_submit_bracket
)


if __name__ == "__main__":
    base.main()
