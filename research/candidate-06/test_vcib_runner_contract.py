#!/usr/bin/env python3
from __future__ import annotations

import inspect

from volume_clock_impact_engine import VolumeClockImpactBifurcationEngine
from volume_clock_nautilus_runner import run_volume_clock_nautilus_backtest

runner = inspect.getsource(run_volume_clock_nautilus_backtest)
observe = inspect.getsource(VolumeClockImpactBifurcationEngine.observe)
assert "BacktestEngine" in runner
assert "engine.add_instrument" in runner
assert "engine.add_data" in runner
assert "engine.add_strategy" in runner
assert "VolumeClockImpactBifurcationEngine" in runner
assert observe.index("_advance_episode") < observe.index("_ingest")
print("VCIB Nautilus-only runner and response-order contracts passed")
