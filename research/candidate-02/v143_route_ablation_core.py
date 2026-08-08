"""Single-variable route attribution for the causal v142 auction resolver."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import CostConfig, RotationSignal
from v142_dual_auction_core import (
    DualAuctionConfig,
    build_rotation_signals as _build_dual_signals,
    build_state as _build_dual_state,
)


@dataclass(frozen=True, slots=True)
class RouteAblationConfig(DualAuctionConfig):
    route_mode: str = "DUAL"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RouteAblationConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v143 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        DualAuctionConfig.__post_init__(self)
        if self.route_mode not in {"DUAL", "ROTATION_ONLY", "CONTINUATION_ONLY"}:
            raise ValueError(f"unknown v143 route mode: {self.route_mode}")


def build_state(features: pd.DataFrame, config: RouteAblationConfig) -> pd.DataFrame:
    return _build_dual_state(features, config)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: RouteAblationConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    signals = _build_dual_signals(
        state=state,
        raw=raw,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        config=config,
        costs=costs,
    )
    if config.route_mode == "DUAL":
        return signals
    required = "ROTATION" if config.route_mode == "ROTATION_ONLY" else "CONTINUATION"
    result = [signal for signal in signals if signal.details.get("competition_result") == required]
    for signal in result:
        signal.details["v143_route_mode"] = config.route_mode
    return result
