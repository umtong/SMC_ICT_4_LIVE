#!/usr/bin/env python3
"""Run candidate-02 v94 exclusively through the existing NautilusTrader path."""
from __future__ import annotations

import v53_nt_backtest as runner
from v94_multilevel_common_breakout_core import (
    MultiLevelBreakoutConfig,
    build_rotation_signals,
    build_state,
)

runner.RotationConfig = MultiLevelBreakoutConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
