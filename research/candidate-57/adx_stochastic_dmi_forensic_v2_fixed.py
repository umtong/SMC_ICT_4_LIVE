#!/usr/bin/env python3
"""JSON-safe launcher for the completed ADXStochastic DMI audit.

The original behaviour-identical account runs completed successfully; only the
final report serialization failed because Nautilus metrics contain IEEE NaN.
This wrapper changes no market or account logic. It replaces the report writer
with a recursive non-finite-to-null conversion and reuses the frozen audit.
"""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "adx_stochastic_dmi_forensic_v2.py"
SPEC = importlib.util.spec_from_file_location("candidate57_adx_dmi_v2_base", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SOURCE}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


def json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def safe_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            json_safe(value),
            indent=2,
            sort_keys=True,
            allow_nan=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


BASE.dump = safe_dump


if __name__ == "__main__":
    raise SystemExit(BASE.main())
