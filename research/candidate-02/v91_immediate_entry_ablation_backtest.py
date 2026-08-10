#!/usr/bin/env python3
"""Run the v91 confirmation-minute ablation through NautilusTrader only."""
from __future__ import annotations

import v53_nt_backtest as runner
from v91_immediate_entry_ablation_core import (
    CrossMarketFairValueConfig,
    build_rotation_signals,
    build_state,
)

runner.RotationConfig = CrossMarketFairValueConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
