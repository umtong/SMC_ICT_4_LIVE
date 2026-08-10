#!/usr/bin/env python3
"""Run candidate-02 v68 through NautilusTrader."""
from __future__ import annotations

import v53_nt_backtest as runner
from v68_failed_absorption_core import (
    FailedAbsorptionConfig,
    build_rotation_signals,
    build_state,
)

runner.RotationConfig = FailedAbsorptionConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
