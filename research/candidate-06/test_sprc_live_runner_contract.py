#!/usr/bin/env python3
from __future__ import annotations

import inspect

from sequential_pressure_live_engine import SequentialPressureLiveEngine
from sequential_pressure_live_nautilus_runner import (
    run_sequential_pressure_live_nautilus_backtest,
)

runner = inspect.getsource(run_sequential_pressure_live_nautilus_backtest)
advance = inspect.getsource(SequentialPressureLiveEngine._advance)
assert "BacktestEngine" in runner
assert "engine.add_instrument" in runner
assert "engine.add_data" in runner
assert "engine.add_strategy" in runner
assert "SequentialPressureLiveEngine" in runner
assert 'state.state == "POSITION_CONTEXT"' in advance
assert "PRESSURE_REGIME_TERMINATED_BY_OPPOSITE_CUSUM" in advance
print("SPRC live Nautilus-only runner contract passed")
