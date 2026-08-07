#!/usr/bin/env python3
"""V55 multi-asset runner with q95 adverse-fill sizing and zero-trade evidence.

The trusted V44/V52 NautilusTrader execution, venue, fee, latency, fill,
portfolio and target contracts remain unchanged. This wrapper replaces only the
causal delayed-entry deterioration estimator used in the 3% NAV quantity
calculation after importing the established runner stack.
"""
from __future__ import annotations

import nt_multi_asset_rich_backtest_v52 as runner
import nt_expected_fill_only_risk_sizing as sizing
from nt_tail_adverse_fill_risk_sizing import (
    FILL_EXPECTATION_CONTRACT,
    causal_tail_adverse_entry_deterioration,
)

sizing.causal_expected_entry_deterioration = (
    causal_tail_adverse_entry_deterioration
)
sizing.FILL_EXPECTATION_CONTRACT = FILL_EXPECTATION_CONTRACT


if __name__ == "__main__":
    runner.v44.base.main()
