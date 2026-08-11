#!/usr/bin/env python3
"""Runtime entry for the frozen jump comparison.

The reusable campaign module intentionally has no generic numeric coercion helper;
this entry injects the same finite-number contract used by the comparison without
changing any trading decision.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

SOURCE = Path(__file__).resolve().with_name("jump_conditional_fresh_v1.py")
SPEC = importlib.util.spec_from_file_location(
    "candidate57_jump_conditional_fresh_runtime_impl", SOURCE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import fresh jump comparison: {SOURCE}")
CAMPAIGN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CAMPAIGN
SPEC.loader.exec_module(CAMPAIGN)


def finite_number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


CAMPAIGN.MODULE.number = finite_number

if __name__ == "__main__":
    raise SystemExit(CAMPAIGN.main())
