#!/usr/bin/env python3
"""Run the generated v94 nearest-pivot ablation through NautilusTrader."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

CORE = Path("/tmp/v94_nearest_pivot_core.py")
if not CORE.is_file():
    raise RuntimeError(f"missing generated v94 ablation source: {CORE}")
spec = spec_from_file_location("v94_nearest_pivot_core", CORE)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load generated v94 ablation core")
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

import v53_nt_backtest as runner

runner.RotationConfig = module.MultiLevelBreakoutConfig
runner.build_state = module.build_state
runner.build_rotation_signals = module.build_rotation_signals

if __name__ == "__main__":
    raise SystemExit(runner.main())
