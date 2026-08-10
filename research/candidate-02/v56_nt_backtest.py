#!/usr/bin/env python3
"""Run candidate-02 v56 through the audited NautilusTrader-only runner."""

from __future__ import annotations

import v53_nt_backtest as runner
from v56_nt_core import PriceDiscoveryConfig, build_rotation_signals, build_state

runner.RotationConfig = PriceDiscoveryConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals


if __name__ == "__main__":
    raise SystemExit(runner.main())
