"""Causal aggressive-flow versus price-response primitives for candidate-08 successors.

Binance ``aggTrades`` expose executed aggressive flow, not passive limit submissions or
cancellations. This module therefore does not claim to reconstruct order-flow imbalance. It asks a
narrower observable question after each completed ten-second bucket:

* was recent signed aggressive activity unusually large relative to its prior hour;
* how much price progress did that pressure produce relative to already-observed noise;
* how much of the maximum directional excursion remained at the completed close; and
* was the realized response stronger or weaker than the causal local impact baseline.

No external level, future path, target, stop, order, fill, PnL, or outcome enters these features.
They are a detector foundation for a possible successor only if the auction router is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from aggtrade_acceptance_signals import causal_stop_slippage_reserve_series


_REQUIRED_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "signed_volume",
    "trade_count",
)
_STATE_FEATURE_COLUMNS = (
    "causal_impact_beta",
    "flow_direction",
    "flow_consistency",
    "window_pressure_ratio",
    "progress_noise",
    "excursion_noise",
    "retention",
    "response_surprise",
)


class FlowResponseState(str, Enum):
    INITIATIVE_RESPONSE = "INITIATIVE_RESPONSE"
    ABSORBED_RESPONSE = "ABSORBED_RESPONSE"
    BALANCED_OR_UNRESOLVED = "BALANCED_OR_UNRESOLVED"
    UNOBSERVABLE = "UNOBSERVABLE"


@dataclass(frozen=True, slots=True)
class FlowResponseConfig:
    impact_lookback_bars: int = 360
    minimum_history_bars: int = 90
    response_window_bars: int = 3
    pressure_quantile: float = 0.90
    noise_quantile: float = 0.99
    minimum_flow_consistency: float = 2.0 / 3.0
    initiative_progress_noise: float = 1.0
    initiative_retention: float = 0.50
    absorption_minimum_excursion_noise: float = 0.50
    absorption_maximum_progress_noise: float = 0.50
    absorption_maximum_retention: float = 0.50

    def validate(self) -> None:
        if self.impact_lookback_bars <= 0:
            raise ValueError("impact lookback must be positive")
        if not 2 <= self.minimum_history_bars <= self.impact_lookback_bars:
            raise ValueError("minimum history must be within the impact lookback")
        if self.response_window_bars < 2:
            raise ValueError("response window must contain at least two completed bars")
        if not 0.5 < self.pressure_quantile < 1.0:
            raise ValueError("pressure quantile must be in (0.5, 1.0)")
        if not 0.5 < self.noise_quantile < 1.0:
            raise ValueError("noise quantile must be in (0.5, 1.0)")
        if not 0.5 <= self.minimum_flow_consistency <= 1.0:
            raise ValueError("flow consistency must be in [0.5, 1.0]")
        if self.initiative_progress_noise <= 0:
            raise ValueError("initiative progress threshold must be positive")
        if not 0.0 < self.initiative_retention <= 1.0:
            raise ValueError("initiative retention must be in (0, 1]")
        if self.absorption_minimum_excursion_noise <= 0:
            raise ValueError("absorption excursion threshold must be positive")
        if self.absorption_maximum_progress_noise >= self.initiative_progress_noise:
            raise ValueError("absorption and initiative progress regions must not overlap")
        if not 0.0 <= self.absorption_maximum_retention <= 1.0:
            raise ValueError("absorption retention must be in [0, 1]")


def _numeric_frame(data: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in _REQUIRED_COLUMNS if column not in data.columns]
    if missing:
        raise KeyError(f"flow-response input is missing columns: {missing}")
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise TypeError("flow-response data must use a timezone-aware DatetimeIndex")
    if not data.index.is_monotonic_increasing or data.index.has_duplicates:
        raise ValueError("flow-response timestamps must be unique and increasing")
    result = data.loc[:, _REQUIRED_COLUMNS].copy()
    for column in _REQUIRED_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _classify_frame_states(
    features: pd.DataFrame,
    *,
    config: FlowResponseConfig,
) -> pd.Series:
    """Vectorized equivalent of :func:`classify_flow_response`."""

    matrix = features.loc[:, _STATE_FEATURE_COLUMNS].to_numpy(dtype="float64")
    positions = {name: index for index, name in enumerate(_STATE_FEATURE_COLUMNS)}
    finite = np.isfinite(matrix).all(axis=1)
    beta = matrix[:, positions["causal_impact_beta"]]
    direction = matrix[:, positions["flow_direction"]]
    consistency = matrix[:, positions["flow_consistency"]]
    pressure = matrix[:, positions["window_pressure_ratio"]]
    progress = matrix[:, positions["progress_noise"]]
    excursion = matrix[:, positions["excursion_noise"]]
    retention = matrix[:, positions["retention"]]
    surprise = matrix[:, positions["response_surprise"]]

    observable = finite & (beta > 0.0) & (direction != 0.0)
    persistent_tail_pressure = (
        observable
        & (pressure >= 1.0)
        & (consistency >= config.minimum_flow_consistency)
    )
    initiative = (
        persistent_tail_pressure
        & (progress >= config.initiative_progress_noise)
        & (retention >= config.initiative_retention)
        & (surprise >= 0.0)
    )
    absorption = (
        persistent_tail_pressure
        & (excursion >= config.absorption_minimum_excursion_noise)
        & (progress < config.absorption_maximum_progress_noise)
        & (retention < config.absorption_maximum_retention)
        & (surprise < 0.0)
    )

    states = np.full(
        len(features.index),
        FlowResponseState.BALANCED_OR_UNRESOLVED.value,
        dtype=object,
    )
    states[~observable] = FlowResponseState.UNOBSERVABLE.value
    states[initiative] = FlowResponseState.INITIATIVE_RESPONSE.value
    states[absorption] = FlowResponseState.ABSORBED_RESPONSE.value
    return pd.Series(states, index=features.index, dtype="string")


def _causal_window_impact_response(
    *,
    signed_activity: pd.Series,
    normalized_price_change: pd.Series,
    impact_beta: pd.Series,
    flow_direction: pd.Series,
    window: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return realized response, expected response and surprise in additive noise units.

    Each completed bucket contributes the price change normalized by the noise state already known
    for that bucket. Its expected contribution uses the positive impact beta estimated before that
    bucket and that bucket's signed aggressive activity. Only after the full window is complete is
    the common window direction applied. This keeps the realized and expected response in the same
    additive units and avoids multiplying the entire window by a single endpoint beta.
    """

    if window < 2:
        raise ValueError("impact response window must contain at least two completed bars")
    expected_signed = (impact_beta * signed_activity).rolling(
        window,
        min_periods=window,
    ).sum()
    realized_signed = normalized_price_change.rolling(
        window,
        min_periods=window,
    ).sum()
    realized_directional = flow_direction * realized_signed
    expected_directional = flow_direction * expected_signed
    return (
        realized_directional,
        expected_directional,
        realized_directional - expected_directional,
    )


def causal_flow_response_frame(
    data: pd.DataFrame,
    *,
    tick: float,
    config: FlowResponseConfig = FlowResponseConfig(),
) -> pd.DataFrame:
    """Return completed-bar response features whose baselines exclude the current row."""

    config.validate()
    if not isfinite(float(tick)) or tick <= 0:
        raise ValueError("tick must be finite and positive")
    values = _numeric_frame(data)
    close = values["close"]
    previous_close = close.shift(1)
    volume = values["volume"]
    signed_volume = values["signed_volume"]

    noise = causal_stop_slippage_reserve_series(
        values,
        tick=float(tick),
        lookback_bars=config.impact_lookback_bars,
        quantile=config.noise_quantile,
    )
    volume_baseline = volume.shift(1).rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    ).median()
    signed_activity = signed_volume / volume_baseline.replace(0.0, np.nan)
    normalized_price_change = (close - previous_close) / noise.replace(0.0, np.nan)

    historical_x = signed_activity.shift(1)
    historical_y = normalized_price_change.shift(1)
    rolling_x = historical_x.rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    )
    rolling_y = historical_y.rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    )
    mean_x = rolling_x.mean()
    mean_y = rolling_y.mean()
    mean_xy = (historical_x * historical_y).rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    ).mean()
    mean_x2 = historical_x.pow(2).rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    ).mean()
    covariance = mean_xy - mean_x * mean_y
    variance = mean_x2 - mean_x.pow(2)
    impact_beta = (covariance / variance.where(variance > 1e-12)).where(
        lambda series: series > 0.0
    )

    pressure_scale = signed_activity.abs().shift(1).rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    ).quantile(config.pressure_quantile)
    pressure_ratio = signed_activity.abs() / pressure_scale.replace(0.0, np.nan)

    window = config.response_window_bars
    cumulative_signed_activity = signed_activity.rolling(window, min_periods=window).sum()
    cumulative_absolute_activity = signed_activity.abs().rolling(
        window,
        min_periods=window,
    ).sum()
    flow_direction = np.sign(cumulative_signed_activity)
    flow_consistency = cumulative_signed_activity.abs() / cumulative_absolute_activity.replace(
        0.0,
        np.nan,
    )
    window_pressure_scale = cumulative_signed_activity.abs().shift(1).rolling(
        config.impact_lookback_bars,
        min_periods=config.minimum_history_bars,
    ).quantile(config.pressure_quantile)
    window_pressure_ratio = cumulative_signed_activity.abs() / window_pressure_scale.replace(
        0.0,
        np.nan,
    )

    window_start_close = close.shift(window)
    window_high = values["high"].rolling(window, min_periods=window).max()
    window_low = values["low"].rolling(window, min_periods=window).min()
    directional_progress = flow_direction * (close - window_start_close)
    directional_excursion = pd.Series(
        np.where(
            flow_direction > 0.0,
            window_high - window_start_close,
            np.where(
                flow_direction < 0.0,
                window_start_close - window_low,
                np.nan,
            ),
        ),
        index=values.index,
        dtype="float64",
    )
    progress_noise = directional_progress / noise.replace(0.0, np.nan)
    excursion_noise = directional_excursion / noise.replace(0.0, np.nan)
    retention = directional_progress.clip(lower=0.0) / directional_excursion.where(
        directional_excursion > float(tick)
    )
    retention = retention.clip(lower=0.0, upper=1.0)
    (
        directional_normalized_progress,
        expected_response,
        response_surprise,
    ) = _causal_window_impact_response(
        signed_activity=signed_activity,
        normalized_price_change=normalized_price_change,
        impact_beta=impact_beta,
        flow_direction=flow_direction,
        window=window,
    )

    result = values.copy()
    result["causal_noise_reserve"] = noise
    result["causal_volume_baseline"] = volume_baseline
    result["signed_activity"] = signed_activity
    result["normalized_price_change"] = normalized_price_change
    result["causal_impact_beta"] = impact_beta
    result["pressure_ratio"] = pressure_ratio
    result["window_signed_activity"] = cumulative_signed_activity
    result["window_absolute_activity"] = cumulative_absolute_activity
    result["flow_direction"] = flow_direction
    result["flow_consistency"] = flow_consistency
    result["window_pressure_ratio"] = window_pressure_ratio
    result["directional_progress"] = directional_progress
    result["directional_excursion"] = directional_excursion
    result["progress_noise"] = progress_noise
    result["excursion_noise"] = excursion_noise
    result["retention"] = retention
    result["directional_normalized_progress"] = directional_normalized_progress
    result["expected_response"] = expected_response
    result["response_surprise"] = response_surprise
    result["flow_response_state"] = _classify_frame_states(result, config=config)
    return result


def classify_flow_response(
    row: pd.Series | dict[str, Any],
    *,
    config: FlowResponseConfig = FlowResponseConfig(),
) -> FlowResponseState:
    """Classify one completed feature row without external levels or future outcomes."""

    config.validate()
    try:
        values = {name: float(row[name]) for name in _STATE_FEATURE_COLUMNS}
    except (KeyError, TypeError, ValueError):
        return FlowResponseState.UNOBSERVABLE
    if not all(isfinite(value) for value in values.values()):
        return FlowResponseState.UNOBSERVABLE
    if values["causal_impact_beta"] <= 0.0 or values["flow_direction"] == 0.0:
        return FlowResponseState.UNOBSERVABLE

    persistent_tail_pressure = (
        values["window_pressure_ratio"] >= 1.0
        and values["flow_consistency"] >= config.minimum_flow_consistency
    )
    if not persistent_tail_pressure:
        return FlowResponseState.BALANCED_OR_UNRESOLVED

    if (
        values["progress_noise"] >= config.initiative_progress_noise
        and values["retention"] >= config.initiative_retention
        and values["response_surprise"] >= 0.0
    ):
        return FlowResponseState.INITIATIVE_RESPONSE

    if (
        values["excursion_noise"] >= config.absorption_minimum_excursion_noise
        and values["progress_noise"] < config.absorption_maximum_progress_noise
        and values["retention"] < config.absorption_maximum_retention
        and values["response_surprise"] < 0.0
    ):
        return FlowResponseState.ABSORBED_RESPONSE

    return FlowResponseState.BALANCED_OR_UNRESOLVED


__all__ = [
    "FlowResponseConfig",
    "FlowResponseState",
    "causal_flow_response_frame",
    "classify_flow_response",
]
