#!/usr/bin/env python3
"""Run candidate-02 v89 exclusively through NautilusTrader."""
from __future__ import annotations

import v53_nt_backtest as runner
from v89_cross_market_impact_core import CrossMarketImpactConfig, build_rotation_signals, build_state

runner.RotationConfig = CrossMarketImpactConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
