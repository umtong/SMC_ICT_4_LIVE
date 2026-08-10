#!/usr/bin/env python3
"""Run candidate-02 v103 exclusively through the existing NautilusTrader path."""
from __future__ import annotations

import v53_nt_backtest as runner
from v103_endogenous_flow_clock_core import (
    EndogenousFlowClockConfig,
    build_rotation_signals,
    build_state,
)

runner.RotationConfig = EndogenousFlowClockConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
