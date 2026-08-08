"""Memory-efficient, decision-identical Candidate 05 v54 strategy."""
from __future__ import annotations

from array import array
import math
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from strategy import LiquidityResponseConfig
from strategy import LiquidityResponseStrategy


class CompactEquity:
    """Columnar equity sequence compatible with the inherited v54 writer."""

    def __init__(self) -> None:
        self._times = array("q")
        self._values = array("d")

    def __bool__(self) -> bool:
        return bool(self._times)

    def __len__(self) -> int:
        return len(self._times)

    def __getitem__(self, index: int) -> dict[str, float | int]:
        return {
            "ts_event": int(self._times[index]),
            "equity": float(self._values[index]),
        }

    def __iter__(self) -> Iterator[dict[str, float | int]]:
        for ts_event, equity in zip(self._times, self._values):
            yield {"ts_event": int(ts_event), "equity": float(equity)}

    @property
    def last_time(self) -> int | None:
        return int(self._times[-1]) if self._times else None

    def append_raw(self, ts_event: int, equity: float) -> None:
        self._times.append(int(ts_event))
        self._values.append(float(equity))

    def replace_last(self, equity: float) -> None:
        if not self._values:
            raise RuntimeError("cannot replace an empty equity series")
        self._values[-1] = float(equity)


class Candidate32Config(LiquidityResponseConfig, frozen=True):
    """The production v54 configuration with no new alpha parameters."""


class Candidate32Strategy(LiquidityResponseStrategy):
    """Change feature/equity representation only; inherit every v54 decision."""

    def __init__(self, config: Candidate32Config) -> None:
        super().__init__(config=config)
        self.equity = CompactEquity()
        self._feature_times = np.empty(0, dtype=np.int64)
        self._feature_ready_values = np.empty(0, dtype=np.bool_)
        self._feature_values: dict[str, np.ndarray] = {}

    def _load_features(self, path: Path) -> None:
        columns = list(pd.read_csv(path, compression="infer", nrows=0).columns)
        required = {"observed_time_ns", "feature_ready"}
        if not required.issubset(columns):
            raise RuntimeError(f"invalid feature schema: {columns}")
        numeric_columns = [column for column in columns if column not in required]
        time_parts: list[np.ndarray] = []
        ready_parts: list[np.ndarray] = []
        value_parts: dict[str, list[np.ndarray]] = {
            column: [] for column in numeric_columns
        }
        for frame in pd.read_csv(path, compression="infer", chunksize=100_000):
            time_parts.append(
                pd.to_numeric(frame["observed_time_ns"], errors="raise")
                .astype("int64")
                .to_numpy(copy=True)
            )
            ready = frame["feature_ready"]
            ready_parts.append(
                ready.to_numpy(dtype=np.bool_, copy=True)
                if ready.dtype == bool
                else ready.astype(str)
                .str.lower()
                .isin({"true", "1", "yes"})
                .to_numpy(dtype=np.bool_, copy=True)
            )
            for column in numeric_columns:
                value_parts[column].append(
                    pd.to_numeric(frame[column], errors="coerce")
                    .to_numpy(dtype=np.float64, copy=True)
                )
        self._feature_times = (
            np.concatenate(time_parts) if time_parts else np.empty(0, dtype=np.int64)
        )
        self._feature_ready_values = (
            np.concatenate(ready_parts) if ready_parts else np.empty(0, dtype=np.bool_)
        )
        self._feature_values = {
            column: np.concatenate(parts) if parts else np.empty(0, dtype=np.float64)
            for column, parts in value_parts.items()
        }
        expected = self._feature_times.size
        if expected == 0:
            raise RuntimeError("feature file is empty")
        if np.any(np.diff(self._feature_times) <= 0):
            raise RuntimeError("feature observation times must be unique and monotonic")
        if self._feature_ready_values.size != expected or any(
            values.size != expected for values in self._feature_values.values()
        ):
            raise RuntimeError("columnar feature arrays have inconsistent lengths")
        self.features = []
        self.feature_cursor = -1
        self.current_feature = None
        self.diagnostics["candidate32_feature_rows"] = int(expected)
        self.diagnostics["candidate32_columnar_feature_storage"] = True
        self.diagnostics["candidate32_continuous_account"] = True
        self.diagnostics["candidate32_alpha_parent"] = "candidate05_v54"

    def _advance_features(self, ts_event: int) -> None:
        cursor = int(np.searchsorted(self._feature_times, ts_event, side="right") - 1)
        if cursor >= self.feature_cursor:
            self.feature_cursor = cursor

    def _features_ready(self, ts_event: int) -> bool:
        cursor = self.feature_cursor
        if cursor < 0 or not bool(self._feature_ready_values[cursor]):
            return False
        age_seconds = (ts_event - int(self._feature_times[cursor])) / 1_000_000_000
        if age_seconds < -1e-9:
            raise RuntimeError("future feature observation reached strategy")
        if age_seconds > self.config.feature_max_age_seconds:
            self.diagnostics["feature_stale_bars"] = int(
                self.diagnostics["feature_stale_bars"],
            ) + 1
            return False
        return True

    def _feature(self, name: str) -> float:
        cursor = self.feature_cursor
        values = self._feature_values.get(name)
        if cursor < 0 or values is None:
            return float("nan")
        value = float(values[cursor])
        return value if math.isfinite(value) else float("nan")

    def _record_equity(self, ts_event: int) -> None:
        if ts_event <= 0:
            return
        value = self._equity_value()
        if not math.isfinite(value) or value <= 0.0:
            return
        if self.equity.last_time == ts_event:
            self.equity.replace_last(value)
        else:
            self.equity.append_raw(ts_event, value)


__all__ = ["Candidate32Config", "Candidate32Strategy", "CompactEquity"]
