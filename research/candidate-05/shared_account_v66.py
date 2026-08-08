#!/usr/bin/env python3
"""Shared-account launcher for the evidence-selected v66 strategy.

Spot observations are installed before the existing shared-account runner
captures ``features.load_range``. The runner's instrument, execution, account,
margin, NAV and reporting contracts remain unchanged.
"""
from __future__ import annotations

from spot_price_discovery_contract import install as install_spot_price_discovery

install_spot_price_discovery()

import shared_account_backtest_v2 as _base  # noqa: E402


if __name__ == "__main__":
    _base.main()
