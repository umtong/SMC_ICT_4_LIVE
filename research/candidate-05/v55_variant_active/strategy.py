"""Controlled v55 shim for one-variable diagnostic ablations.

Environment variables are read before importing the strategy module. Every run
records its exact values and changes at most one causal threshold relative to
the strict v55 contract.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


_PARENT = Path(__file__).resolve().parents[1]
if str(_PARENT) not in sys.path:
    sys.path.append(str(_PARENT))

import spot_led_repricing_logic as _logic  # noqa: E402

if "V55_SPOT_LEAD_BPS_MIN" in os.environ:
    _logic.SPOT_LEAD_BPS_MIN = float(os.environ["V55_SPOT_LEAD_BPS_MIN"])
if "V55_ACCEPTANCE_FLOW_MIN" in os.environ:
    _logic.SPOT_CONTEXT_ACCEPTANCE_FLOW_3M_MIN = float(
        os.environ["V55_ACCEPTANCE_FLOW_MIN"],
    )
if "V55_CONTEXT_MIN_AGE_BARS" in os.environ:
    _logic.SPOT_CONTEXT_MIN_AGE_BARS = int(
        os.environ["V55_CONTEXT_MIN_AGE_BARS"],
    )

_BASE_PATH = _PARENT / "strategy.py"
_SPEC = importlib.util.spec_from_file_location("_candidate05_v55_variant_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load base strategy from {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

_WRAPPER = sys.modules[__name__]
sys.modules["strategy"] = _BASE
try:
    from strategy_v55_spot_price_discovery import (  # noqa: E402
        SpotLedPriceDiscoveryStrategy,
    )
finally:
    sys.modules["strategy"] = _WRAPPER

LiquidityResponseConfig = _BASE.LiquidityResponseConfig
LiquidityResponseStrategy = SpotLedPriceDiscoveryStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
