#!/usr/bin/env python3
"""Run one candidate-02 v63 impact-state scenario via NautilusTrader."""

from __future__ import annotations

import v53_nt_backtest as runner
from v63_impact_state_core import ImpactStateConfig, build_rotation_signals, build_state

runner.RotationConfig = ImpactStateConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
