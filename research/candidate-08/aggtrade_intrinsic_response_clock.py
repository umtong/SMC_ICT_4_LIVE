"""Causal intrinsic-time response events for a possible candidate-08 successor.

This module is deliberately not wired to trading. It addresses one falsifiable successor question:
if a fixed thirty-second window fails because market activity is irregular, can the response be
measured over a variable physical duration containing one frozen, causally estimated unit of
aggressive-flow activity?

An event starts strictly after a caller-selected market event. At its first completed ten-second
bucket it freezes an activity budget estimated only from earlier completed reference windows. It
then accumulates normalized absolute aggressive activity until that budget is reached or a fixed
maximum number of buckets expires. Price progress, excursion retention and expected impact are
measured only over the resulting completed intrinsic event.

No external level, target, stop, order, fill, account value, PnL or future outcome enters this
module. It is a detector foundation and may become a new candidate only after clean evidence
identifies physical-time truncation or dilution as the V2 failure mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_flow_response import (
    FlowResponseConfig,
    FlowResponseState,
    causal_flow_response_frame,
)


class IntrinsicEventStatus(str, Enum):
    COMPLETE = "COMPLETE"
    TIMEOUT = "TIMEOUT"
    UNOBSERVABLE = "UNOBSERVABLE"


@dataclass(frozen=True, slots=True)
class IntrinsicResponseClockConfig:
    response: FlowResponseConfig = FlowResponseConfig()
    maximum_event_bars: int = 9

    def validate(self) -> None:
        self.response.validate()
        if self.maximum_event_bars < self.response.response_window_bars:
            raise ValueError(
                "maximum intrinsic event length must be at least the reference response window"
            )


@dataclass(frozen=True, slots=True)
class IntrinsicResponseEvent:
    status: IntrinsicEventStatus
    response_state: FlowResponseState
    start_position: int
    end_position: int
    start_time_ns: int
    end_time_ns: int
    physical_bars: int
    frozen_activity_budget: float
    cumulative_signed_activity: float
    cumulative_absolute_activity: float
    activity_budget_fraction: float
    flow_direction: int
    flow_consistency: float
    start_close: float
    end_close: float
    directional_progress: float
    directional_excursion: float
    progress_noise: float
    excursion_noise: float
    retention: float
    expected_response: float
    response_surprise: float
    frozen_noise_reserve: float


_REQUIRED_RAW_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "signed_volume",
    "trade_count",
)
_REQUIRED_FEATURE_COLUMNS = (
    "signed_activity",
    "causal_noise_reserve",
    "causal_impact_beta",
)


def _validate_raw_frame(data: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in _REQUIRED_RAW_COLUMNS if column not in data.columns]
    if missing:
        raise KeyError(f"intrinsic response input is missing columns: {missing}")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("intrinsic response data must use a timezone-aware DatetimeIndex")
    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("intrinsic response timestamps must be unique and increasing")
    result = data.loc[:, _REQUIRED_RAW_COLUMNS].copy()
    for column in _REQUIRED_RAW_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    values = result.to_numpy(dtype="float64")
    if not np.isfinite(values).all():
        raise ValueError("intrinsic response input contains non-finite values")
    if (result["volume"] <= 0.0).any() or (result["trade_count"] <= 0.0).any():
        raise ValueError("intrinsic response input requires positive volume and trade count")
    invalid = (
        (result["high"] < result[["open", "close"]].max(axis=1))
        | (result["low"] > result[["open", "close"]].min(axis=1))
        | (result["high"] < result["low"])
    )
    if bool(invalid.any()):
        raise ValueError("intrinsic response input contains invalid OHLC geometry")
    return result


def _validate_features(data: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    if not features.index.equals(data.index):
        raise ValueError("intrinsic response features must have the exact input index")
    missing = [column for column in _REQUIRED_FEATURE_COLUMNS if column not in features.columns]
    if missing:
        raise KeyError(f"intrinsic response features are missing columns: {missing}")
    result = features.copy()
    for column in _REQUIRED_FEATURE_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def causal_activity_budget_series(
    features: pd.DataFrame,
    *,
    config: IntrinsicResponseClockConfig = IntrinsicResponseClockConfig(),
) -> pd.Series:
    """Return the shifted tail activity budget for one reference response event.

    The value at row ``t`` uses completed reference windows ending no later than ``t-1``. The
    current row and every later row are excluded.
    """

    config.validate()
    if "signed_activity" not in features.columns:
        raise KeyError("flow-response features are missing signed_activity")
    activity = pd.to_numeric(features["signed_activity"], errors="coerce").abs()
    reference_window = activity.rolling(
        config.response.response_window_bars,
        min_periods=config.response.response_window_bars,
    ).sum()
    return reference_window.shift(1).rolling(
        config.response.impact_lookback_bars,
        min_periods=config.response.minimum_history_bars,
    ).quantile(config.response.pressure_quantile)


def _unobservable_event(
    *,
    data: pd.DataFrame,
    start_position: int,
    end_position: int,
    budget: float = float("nan"),
) -> IntrinsicResponseEvent:
    start_time = int(data.index[start_position].as_unit("ns").value)
    end_time = int(data.index[end_position].as_unit("ns").value)
    start_close = float(data.iloc[start_position - 1]["close"])
    end_close = float(data.iloc[end_position]["close"])
    return IntrinsicResponseEvent(
        status=IntrinsicEventStatus.UNOBSERVABLE,
        response_state=FlowResponseState.UNOBSERVABLE,
        start_position=start_position,
        end_position=end_position,
        start_time_ns=start_time,
        end_time_ns=end_time,
        physical_bars=end_position - start_position + 1,
        frozen_activity_budget=budget,
        cumulative_signed_activity=float("nan"),
        cumulative_absolute_activity=float("nan"),
        activity_budget_fraction=float("nan"),
        flow_direction=0,
        flow_consistency=float("nan"),
        start_close=start_close,
        end_close=end_close,
        directional_progress=float("nan"),
        directional_excursion=float("nan"),
        progress_noise=float("nan"),
        excursion_noise=float("nan"),
        retention=float("nan"),
        expected_response=float("nan"),
        response_surprise=float("nan"),
        frozen_noise_reserve=float("nan"),
    )


def _classify_completed_event(
    *,
    flow_consistency: float,
    progress_noise: float,
    excursion_noise: float,
    retention: float,
    response_surprise: float,
    config: FlowResponseConfig,
) -> FlowResponseState:
    if flow_consistency < config.minimum_flow_consistency:
        return FlowResponseState.BALANCED_OR_UNRESOLVED
    if (
        progress_noise >= config.initiative_progress_noise
        and retention >= config.initiative_retention
        and response_surprise >= 0.0
    ):
        return FlowResponseState.INITIATIVE_RESPONSE
    if (
        excursion_noise >= config.absorption_minimum_excursion_noise
        and progress_noise < config.absorption_maximum_progress_noise
        and retention < config.absorption_maximum_retention
        and response_surprise < 0.0
    ):
        return FlowResponseState.ABSORBED_RESPONSE
    return FlowResponseState.BALANCED_OR_UNRESOLVED


def build_intrinsic_response_event(
    data: pd.DataFrame,
    *,
    start_position: int,
    tick: float,
    config: IntrinsicResponseClockConfig = IntrinsicResponseClockConfig(),
    flow_response_features: pd.DataFrame | None = None,
    activity_budgets: pd.Series | None = None,
) -> IntrinsicResponseEvent:
    """Build the first complete intrinsic response event from ``start_position``.

    ``start_position`` is the first bucket after the caller's completed interaction or absorption
    event. The price baseline is therefore the close at ``start_position - 1``.
    """

    config.validate()
    if not isfinite(float(tick)) or tick <= 0.0:
        raise ValueError("tick must be finite and positive")
    values = _validate_raw_frame(data)
    if not 1 <= start_position < len(values.index):
        raise ValueError("start_position must reference a row after an observable baseline close")

    features = _validate_features(
        values,
        causal_flow_response_frame(values, tick=tick, config=config.response)
        if flow_response_features is None
        else flow_response_features,
    )
    budgets = (
        causal_activity_budget_series(features, config=config)
        if activity_budgets is None
        else pd.to_numeric(activity_budgets, errors="coerce")
    )
    if not budgets.index.equals(values.index):
        raise ValueError("activity budget series must have the exact input index")

    budget = float(budgets.iloc[start_position])
    frozen_noise = float(features.iloc[start_position]["causal_noise_reserve"])
    if not isfinite(budget) or budget <= 0.0 or not isfinite(frozen_noise) or frozen_noise <= 0.0:
        return _unobservable_event(
            data=values,
            start_position=start_position,
            end_position=start_position,
            budget=budget,
        )

    end_limit = min(
        len(values.index) - 1,
        start_position + config.maximum_event_bars - 1,
    )
    cumulative_signed = 0.0
    cumulative_absolute = 0.0
    expected_signed_response = 0.0
    end_position = end_limit
    status = IntrinsicEventStatus.TIMEOUT

    for position in range(start_position, end_limit + 1):
        row = features.iloc[position]
        signed_activity = float(row["signed_activity"])
        impact_beta = float(row["causal_impact_beta"])
        if not isfinite(signed_activity) or not isfinite(impact_beta) or impact_beta <= 0.0:
            return _unobservable_event(
                data=values,
                start_position=start_position,
                end_position=position,
                budget=budget,
            )
        cumulative_signed += signed_activity
        cumulative_absolute += abs(signed_activity)
        expected_signed_response += impact_beta * signed_activity
        if cumulative_absolute >= budget:
            end_position = position
            status = IntrinsicEventStatus.COMPLETE
            break

    direction = int(np.sign(cumulative_signed))
    consistency = (
        abs(cumulative_signed) / cumulative_absolute
        if cumulative_absolute > 0.0
        else 0.0
    )
    start_close = float(values.iloc[start_position - 1]["close"])
    end_close = float(values.iloc[end_position]["close"])
    observed = values.iloc[start_position : end_position + 1]
    if direction > 0:
        progress = end_close - start_close
        excursion = float(observed["high"].max()) - start_close
    elif direction < 0:
        progress = start_close - end_close
        excursion = start_close - float(observed["low"].min())
    else:
        progress = 0.0
        excursion = max(
            float(observed["high"].max()) - start_close,
            start_close - float(observed["low"].min()),
        )

    progress_noise = progress / frozen_noise
    excursion_noise = excursion / frozen_noise
    retention = (
        min(1.0, max(0.0, progress) / excursion)
        if excursion > float(tick)
        else 0.0
    )
    expected_response = direction * expected_signed_response if direction else 0.0
    response_surprise = progress_noise - expected_response
    response_state = (
        _classify_completed_event(
            flow_consistency=consistency,
            progress_noise=progress_noise,
            excursion_noise=excursion_noise,
            retention=retention,
            response_surprise=response_surprise,
            config=config.response,
        )
        if status is IntrinsicEventStatus.COMPLETE
        else FlowResponseState.BALANCED_OR_UNRESOLVED
    )

    return IntrinsicResponseEvent(
        status=status,
        response_state=response_state,
        start_position=start_position,
        end_position=end_position,
        start_time_ns=int(values.index[start_position].as_unit("ns").value),
        end_time_ns=int(values.index[end_position].as_unit("ns").value),
        physical_bars=end_position - start_position + 1,
        frozen_activity_budget=budget,
        cumulative_signed_activity=cumulative_signed,
        cumulative_absolute_activity=cumulative_absolute,
        activity_budget_fraction=cumulative_absolute / budget,
        flow_direction=direction,
        flow_consistency=consistency,
        start_close=start_close,
        end_close=end_close,
        directional_progress=progress,
        directional_excursion=excursion,
        progress_noise=progress_noise,
        excursion_noise=excursion_noise,
        retention=retention,
        expected_response=expected_response,
        response_surprise=response_surprise,
        frozen_noise_reserve=frozen_noise,
    )


__all__ = [
    "IntrinsicEventStatus",
    "IntrinsicResponseClockConfig",
    "IntrinsicResponseEvent",
    "build_intrinsic_response_event",
    "causal_activity_budget_series",
]
