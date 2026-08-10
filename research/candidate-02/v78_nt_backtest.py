#!/usr/bin/env python3
"""Run candidate-02 v78 through the existing NautilusTrader weekly harness."""
from __future__ import annotations

import v53_nt_backtest as runner
from v78_impact_resilience_core import ImpactResilienceConfig, build_rotation_signals, build_state

runner.RotationConfig = ImpactResilienceConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
