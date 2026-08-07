#!/usr/bin/env python3
from __future__ import annotations

import inspect

from open_interest_contraction_engine import OpenInterestContractionBifurcationEngine
from open_interest_nautilus_runner import run_open_interest_nautilus_backtest

runner = inspect.getsource(run_open_interest_nautilus_backtest)
observe = inspect.getsource(OpenInterestContractionBifurcationEngine.observe)
assert "BacktestEngine" in runner
assert "engine.add_instrument" in runner
assert "engine.add_data" in runner
assert "engine.add_strategy" in runner
assert "OpenInterestContractionBifurcationEngine" in runner
assert "self._accumulate(snapshot)" in observe
assert observe.index("self._maybe_start_shock") < observe.index("self._advance_shock")
print("OICB Nautilus-only runner contract passed")
