#!/usr/bin/env python3
"""Run a candidate-02 v61 scenario through the audited NautilusTrader path."""

from __future__ import annotations

import v53_nt_backtest as runner
from v61_family_core import ScenarioFamilyConfig, build_rotation_signals, build_state

runner.RotationConfig = ScenarioFamilyConfig
runner.build_state = build_state
runner.build_rotation_signals = build_rotation_signals


if __name__ == "__main__":
    raise SystemExit(runner.main())
