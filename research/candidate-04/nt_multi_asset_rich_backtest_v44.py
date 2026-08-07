#!/usr/bin/env python3
"""Four-instrument V44 execution with the trusted single-asset fill contract.

The V3 multi-asset runner owns the one-account global coordinator, exact trusted
NautilusTrader venue configuration and risk-evidence reconciliation. This
wrapper changes only the shared bracket sizing callback to the same expected-fill
contract used by the frozen single-BTC V44 evidence.
"""
from __future__ import annotations

import nt_multi_asset_rich_backtest_v3 as base
import nt_liquidity_strategy as core
from nt_expected_fill_only_risk_sizing import expected_fill_only_submit_bracket


core.LiquidityTransitionStrategy._submit_bracket = expected_fill_only_submit_bracket


if __name__ == "__main__":
    base.main()
