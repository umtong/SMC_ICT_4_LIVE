#!/usr/bin/env python3
"""Run the locked v89 single-variable ablation through NautilusTrader only."""
from __future__ import annotations

import v53_nt_backtest as runner
from v89_impact_retention_ablation_core import (
    ImpactRetentionAblationConfig,
    build_rotation_signals,
    build_state,
)

runner.RotationConfig = ImpactRetentionAblationConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
