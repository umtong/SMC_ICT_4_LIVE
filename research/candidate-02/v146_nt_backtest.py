#!/usr/bin/env python3
"""NautilusTrader-only runner for candidate-02 v146."""
from __future__ import annotations

import v53_nt_backtest as _base
from v146_prior_event_model_core import PriorEventModelConfig, build_rotation_signals, build_state

_base.RotationConfig = PriorEventModelConfig
_base.build_state = build_state
_base.build_rotation_signals = build_rotation_signals
run_first_week = _base.run_first_week

if __name__ == "__main__":
    raise SystemExit(_base.main())
