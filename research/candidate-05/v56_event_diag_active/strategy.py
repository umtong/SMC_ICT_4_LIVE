"""Controlled import shim for the v56 event diagnostic."""
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
    from strategy_v56_event_idempotency_diagnostic import (  # noqa: E402
        SpotPullbackEventIdempotencyDiagnostic,
    )
finally:
    sys.modules["strategy"] = _WRAPPER

LiquidityResponseConfig = _BASE.LiquidityResponseConfig
LiquidityResponseStrategy = SpotPullbackEventIdempotencyDiagnostic

__all__ = [
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "SpotPullbackEventIdempotencyDiagnostic",
]
