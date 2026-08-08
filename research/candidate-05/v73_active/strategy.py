"""Controlled import shim for Candidate 05 v73."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_PARENT = Path(__file__).resolve().parents[1]
_BASE_PATH = _PARENT / "strategy.py"
_SPEC = importlib.util.spec_from_file_location("_candidate05_v73_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load base strategy from {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

_WRAPPER = sys.modules[__name__]
sys.modules["strategy"] = _BASE
try:
    from strategy_v73_daily_value_failed_auction import (  # noqa: E402
        DailyValueFailedAuctionStrategy,
    )
finally:
    sys.modules["strategy"] = _WRAPPER

LiquidityResponseConfig = _BASE.LiquidityResponseConfig
LiquidityResponseStrategy = DailyValueFailedAuctionStrategy

__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]
