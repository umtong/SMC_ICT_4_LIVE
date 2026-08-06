"""Pandas 3 compatibility contract for NautilusTrader 1.230 data wranglers.

NautilusTrader's Cython ``BarDataWrangler`` consumes ``DataFrame.values`` as a
writable typed memoryview.  pandas 3 Copy-on-Write intentionally returns a
read-only view.  This adapter preserves the official wrangler while ensuring
its observational input exposes an independent, C-contiguous writable array.
It changes no bar values and contains no execution or accounting logic.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

import numpy as np
import pandas as pd

import features as _features


class WritableWranglerFrame(pd.DataFrame):
    @property
    def _constructor(self):  # type: ignore[override]
        return WritableWranglerFrame

    @property
    def values(self) -> np.ndarray:  # type: ignore[override]
        array = self.to_numpy(dtype=np.float64, copy=True)
        if not array.flags.c_contiguous:
            array = np.ascontiguousarray(array)
        array.setflags(write=True)
        return array


def install() -> None:
    current = _features.load_range
    if getattr(current, "_candidate05_writable_contract", False):
        return

    @wraps(current)
    def load_range(*args: Any, **kwargs: Any):
        klines, feature_path, manifest_files, evidence = current(*args, **kwargs)
        writable = WritableWranglerFrame(klines.copy(deep=True))
        return writable, feature_path, manifest_files, evidence

    setattr(load_range, "_candidate05_writable_contract", True)
    _features.load_range = load_range
