"""Campaign-local dispatch shim for the depth-liquidity engine.

The production candidate-06 strategy composition remains untouched.  This shim
loads it under an internal module name, replaces only the scenario-engine
factory, and exports its native Nautilus strategy class factory.  The child
validation process prepends this directory to ``PYTHONPATH``.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Mapping

_BASE_PATH = Path(__file__).resolve().parents[1] / "nautilus_strategy.py"
_SPEC = spec_from_file_location("candidate06_base_nautilus_strategy", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load base Nautilus strategy from {_BASE_PATH}")
_BASE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BASE)
_BASE_FACTORY = _BASE._make_scenario_engine


def _make_scenario_engine(logic_params: Mapping[str, Any]) -> Any:
    name = str(logic_params.get("engine", ""))
    if name == "DEPTH_LIQUIDITY_VACUUM_REPLENISHMENT":
        from depth_liquidity_response_engine import DepthLiquidityVacuumReplenishmentEngine

        return DepthLiquidityVacuumReplenishmentEngine(logic_params)
    return _BASE_FACTORY(logic_params)


_BASE._make_scenario_engine = _make_scenario_engine
make_strategy_class = _BASE.make_strategy_class
