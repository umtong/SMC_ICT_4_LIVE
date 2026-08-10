#!/usr/bin/env python3
"""Aggregate the frozen v58 shards with pandas mixed-ISO compatibility.

All twelve measurement shards completed successfully.  Aggregation failed only
because pandas 3 inferred one fractional-second ISO8601 format for a column
which also contains exact-second timestamps.  This wrapper changes no event,
return, cost, selection or summary formula; it supplies ``format='mixed'`` for
object/string datetime vectors and delegates to the frozen aggregator.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
TARGET_PATH = HERE / "quarter_hour_orderflow.py"


def _load_target() -> Any:
    spec = importlib.util.spec_from_file_location(
        "candidate51_quarter_hour_v58_target", TARGET_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TARGET_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install_mixed_iso_parser():
    original = pd.to_datetime

    def compatible(arg, *args, **kwargs):
        if "format" not in kwargs:
            dtype = getattr(arg, "dtype", None)
            if dtype is not None and (
                pd.api.types.is_object_dtype(dtype)
                or pd.api.types.is_string_dtype(dtype)
            ):
                kwargs["format"] = "mixed"
        return original(arg, *args, **kwargs)

    pd.to_datetime = compatible
    return original


def main() -> None:
    target = _load_target()
    original = _install_mixed_iso_parser()
    try:
        target.main()
    finally:
        pd.to_datetime = original


if __name__ == "__main__":
    main()
