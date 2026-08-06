"""Trend-embedded high-impact price discovery for candidate-02 v57."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from v56_nt_core import PriceDiscoveryConfig, build_rotation_signals
import v56_nt_core


@dataclass(frozen=True, slots=True)
class TrendPriceDiscoveryConfig(PriceDiscoveryConfig):
    trend_abs_return_quantile: float = 0.60

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "TrendPriceDiscoveryConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v57 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        super().__post_init__()
        if not 0 < self.trend_abs_return_quantile < 1:
            raise ValueError("trend_abs_return_quantile must be in (0, 1)")


def build_state(features: pd.DataFrame, config: TrendPriceDiscoveryConfig) -> pd.DataFrame:
    x = v56_nt_core.build_state(features, config)
    x["directional_return_60m"] = x["event_direction"] * x["log_ret_60m"]
    x["absolute_return_60m_threshold"] = x["log_ret_60m"].abs().shift(1).rolling(
        config.prior_quantile_days * 288,
        min_periods=config.prior_quantile_min_rows,
    ).quantile(config.trend_abs_return_quantile)
    x["eligible_price_discovery"] = (
        x["eligible_price_discovery"]
        & (x["directional_return_60m"] >= x["absolute_return_60m_threshold"])
    )
    return x


__all__ = [
    "TrendPriceDiscoveryConfig",
    "build_state",
    "build_rotation_signals",
]
