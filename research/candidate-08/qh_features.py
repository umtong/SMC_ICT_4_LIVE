"""Causal feature construction for quarter-hour continuation logic."""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np
import pandas as pd

from qh_logic import FlowBar


NUMERIC_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "taker_buy_volume",
)


def _numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=list(NUMERIC_COLUMNS)).copy()


def build_flow_feature_map(frame: pd.DataFrame) -> dict[int, FlowBar]:
    """Return close-time keyed features using only information observed by each close."""

    data = _numeric_frame(frame)
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr20"] = true_range.rolling(20, min_periods=20).mean()
    data["volume_median60"] = data["volume"].shift(1).rolling(60, min_periods=30).median()
    data["trades_median60"] = data["trade_count"].shift(1).rolling(60, min_periods=30).median()
    data["volume_ratio"] = data["volume"] / data["volume_median60"].replace(0, np.nan)
    data["trade_ratio"] = data["trade_count"] / data["trades_median60"].replace(0, np.nan)
    data["imbalance"] = (
        2.0 * data["taker_buy_volume"] - data["volume"]
    ) / data["volume"].replace(0, np.nan)

    session_key = data.index.floor("4h")
    sessions = data.groupby(session_key, sort=True).agg(
        session_open=("open", "first"),
        session_high=("high", "max"),
        session_low=("low", "min"),
        session_close=("close", "last"),
    )
    sessions["previous_session_high"] = sessions["session_high"].shift(1)
    sessions["previous_session_low"] = sessions["session_low"].shift(1)
    sessions["previous_session_direction"] = np.sign(
        sessions["session_close"].shift(1) - sessions["session_open"].shift(1)
    )
    for column in (
        "previous_session_high",
        "previous_session_low",
        "previous_session_direction",
    ):
        data[column] = session_key.map(sessions[column])

    movement = data["close"].diff()
    path = movement.abs().shift(1).rolling(60, min_periods=45).sum()
    data["efficiency_60m"] = (
        data["close"].shift(1) - data["close"].shift(61)
    ).abs() / path.replace(0, np.nan)
    data["direction_60m"] = np.sign(data["close"].shift(1) - data["close"].shift(61))

    lag_values: list[float] = []
    recent_boundaries: deque[float] = deque(maxlen=4)
    for timestamp, imbalance in zip(data.index, data["imbalance"], strict=True):
        lag_values.append(float(np.mean(recent_boundaries)) if recent_boundaries else float("nan"))
        if int(timestamp.minute) in (0, 15, 30, 45) and np.isfinite(imbalance):
            recent_boundaries.append(float(imbalance))
    data["lag_mean4"] = lag_values

    required = (
        "atr20",
        "volume_ratio",
        "trade_ratio",
        "imbalance",
        "lag_mean4",
        "previous_session_high",
        "previous_session_low",
        "previous_session_direction",
        "efficiency_60m",
        "direction_60m",
    )
    timestamps_ns = data.index.as_unit("ns").asi8
    result: dict[int, FlowBar] = {}
    for index, ((timestamp, row), timestamp_ns) in enumerate(
        zip(data.iterrows(), timestamps_ns, strict=True)
    ):
        if not all(np.isfinite(float(row[column])) for column in required):
            continue
        result[int(timestamp_ns)] = FlowBar(
            index=index,
            ts_event_ns=int(timestamp_ns),
            minute=int(timestamp.minute),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            trade_count=float(row["trade_count"]),
            imbalance=float(row["imbalance"]),
            atr=float(row["atr20"]),
            volume_ratio=float(row["volume_ratio"]),
            trade_ratio=float(row["trade_ratio"]),
            lag_mean4=float(row["lag_mean4"]),
            previous_session_high=float(row["previous_session_high"]),
            previous_session_low=float(row["previous_session_low"]),
            previous_session_direction=float(row["previous_session_direction"]),
            efficiency_60m=float(row["efficiency_60m"]),
            direction_60m=float(row["direction_60m"]),
        )
    return result
