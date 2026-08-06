#!/usr/bin/env python3
"""Run candidate-02 v67 through NautilusTrader."""
from __future__ import annotations
import v53_nt_backtest as runner
from v67_two_close_acceptance_core import TwoCloseConfig, build_rotation_signals, build_state
runner.RotationConfig=TwoCloseConfig
runner.build_state=build_state
runner.build_rotation_signals=build_rotation_signals
if __name__=="__main__": raise SystemExit(runner.main())
