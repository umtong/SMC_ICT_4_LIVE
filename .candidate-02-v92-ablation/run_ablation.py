#!/usr/bin/env python3
"""Run the generated v92 single-variable ablation through NautilusTrader."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

CORE = Path("/tmp/v92_no_sweep_flow_core.py")
if not CORE.is_file():
    raise RuntimeError(f"missing generated ablation source: {CORE}")
spec = spec_from_file_location("v92_no_sweep_flow_core", CORE)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load generated v92 ablation core")
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

import v53_nt_backtest as runner

runner.RotationConfig = module.SessionLiquiditySweepConfig
runner.build_state = module.build_state
runner.build_rotation_signals = module.build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
