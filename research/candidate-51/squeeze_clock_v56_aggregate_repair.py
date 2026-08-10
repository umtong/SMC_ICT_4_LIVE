#!/usr/bin/env python3
"""Run the frozen v56 squeeze-clock aggregation with tuple-safe pandas rename.

The 52 source shards were produced successfully. Aggregation failed only
because pandas 3 does not treat a full MultiIndex column tuple as a scalar key
in ``DataFrame.rename(columns={tuple_key: 'value'})``. This wrapper changes no
market data, signal, fill, cost, clock, or summary formula. It only makes that
one full-tuple rename explicit, then delegates to the frozen aggregator.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
TARGET_PATH = HERE / "squeeze_clock_anatomy.py"


def _load_target() -> Any:
    spec = importlib.util.spec_from_file_location(
        "candidate51_squeeze_clock_v56_target", TARGET_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TARGET_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install_tuple_safe_rename():
    original = pd.DataFrame.rename

    def tuple_safe_rename(self, *args, **kwargs):
        columns_mapper = kwargs.get("columns")
        if isinstance(self.columns, pd.MultiIndex) and isinstance(columns_mapper, dict):
            tuple_keys = [
                key
                for key in columns_mapper
                if isinstance(key, tuple) and key in self.columns
            ]
            if tuple_keys:
                result = self.copy()
                result.columns = pd.Index(
                    [columns_mapper.get(column, column) for column in self.columns],
                    dtype=object,
                )
                return result
        return original(self, *args, **kwargs)

    pd.DataFrame.rename = tuple_safe_rename
    return original


def main() -> None:
    target = _load_target()
    original = _install_tuple_safe_rename()
    try:
        target.main()
    finally:
        pd.DataFrame.rename = original


if __name__ == "__main__":
    main()
