#!/usr/bin/env python3
"""Run candidate-02 v91 through the existing NautilusTrader-only path."""
from __future__ import annotations

import v53_nt_backtest as runner
from v91_cross_market_fair_value_core import (
    CrossMarketFairValueConfig,
    build_rotation_signals,
    build_state,
)

runner.RotationConfig = CrossMarketFairValueConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
