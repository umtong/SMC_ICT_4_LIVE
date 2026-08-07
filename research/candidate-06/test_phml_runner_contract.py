#!/usr/bin/env python3
from __future__ import annotations

import inspect

from pressure_gated_hml_engine import PressureGatedHierarchicalEngine
from pressure_gated_hml_nautilus_runner import run_pressure_gated_hml_nautilus_backtest
from pressure_state_tracker import PressureStateTracker

runner = inspect.getsource(run_pressure_gated_hml_nautilus_backtest)
observe = inspect.getsource(PressureGatedHierarchicalEngine.observe)
tracker = inspect.getsource(PressureStateTracker.update)
assert "BacktestEngine" in runner
assert "engine.add_instrument" in runner
assert "engine.add_data" in runner
assert "engine.add_strategy" in runner
assert "PressureGatedHierarchicalEngine" in runner
assert observe.index("self._pressure.update") < observe.index("self._hml.observe")
assert tracker.index("history = self.history") < tracker.index("self.history.append")
print("PHML prior-only and Nautilus-only runner contracts passed")
