"""Temporary import shim for the controlled v45 Nautilus experiment.

The production candidate remains in the parent ``strategy.py``. This shim loads
that exact module under a private name, then exposes only the v45 subclass while
preserving the same config class and every inherited execution contract.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_PARENT = Path(__file__).resolve().parents[1]
_BASE_PATH = _PARENT / "strategy.py"
_SPEC = importlib.util.spec_from_file_location("_candidate05_v39_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load base strategy from {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

_WRAPPER = sys.modules[__name__]
sys.modules["strategy"] = _BASE
try:
    from strategy_v45_external_active_inventory import (  # noqa: E402
        ActiveExternalInventoryStrategy,
    )
finally:
    sys.modules["strategy"] = _WRAPPER

LiquidityResponseConfig = _BASE.LiquidityResponseConfig
LiquidityResponseStrategy = ActiveExternalInventoryStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
