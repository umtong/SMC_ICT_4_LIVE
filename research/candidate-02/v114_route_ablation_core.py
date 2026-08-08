"""Single-variable route ablation for candidate-02 v114.

The persistent-pool detector, event chronology, targets, stops and all numeric
settings remain exactly v113.  This module changes only which already-resolved
causal route is permitted to submit a trade intent.  It is therefore a causal
attribution experiment, not a new return-fitting strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from v53_nt_core import CostConfig, RotationSignal
from v113_persistent_pool_router_core import (
    PersistentPoolRouterConfig,
    build_rotation_signals as _build_v113_signals,
    build_state,
    get_last_scenario_diagnostics as _get_v113_diagnostics,
)


@dataclass(frozen=True, slots=True)
class RouteAblationConfig(PersistentPoolRouterConfig):
    route_mode: str = "BOTH"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RouteAblationConfig":
        data = dict(values)
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v114 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        PersistentPoolRouterConfig.__post_init__(self)
        if self.route_mode not in {"BOTH", "REVERSAL", "CONTINUATION"}:
            raise ValueError(f"unknown v114 route mode: {self.route_mode}")


_LAST_DIAGNOSTICS: dict[str, Any] = {"summary": {}, "examples": {}}


def get_last_scenario_diagnostics() -> dict[str, Any]:
    return {
        "summary": dict(_LAST_DIAGNOSTICS.get("summary", {})),
        "examples": {
            str(key): list(values)
            for key, values in dict(_LAST_DIAGNOSTICS.get("examples", {})).items()
        },
    }


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: RouteAblationConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    global _LAST_DIAGNOSTICS

    all_signals = _build_v113_signals(
        state=state,
        raw=raw,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        config=config,
        costs=costs,
    )
    if config.route_mode == "BOTH":
        retained = list(all_signals)
    else:
        retained = [
            signal
            for signal in all_signals
            if str(signal.details.get("route")) == config.route_mode
        ]

    original = _get_v113_diagnostics()
    route_counts = {"REVERSAL": 0, "CONTINUATION": 0}
    side_counts = {"BUY": 0, "SELL": 0}
    session_counts: dict[str, int] = {}
    for signal in all_signals:
        route = str(signal.details.get("route"))
        if route in route_counts:
            route_counts[route] += 1
    for signal in retained:
        if signal.side in side_counts:
            side_counts[signal.side] += 1
        session = str(signal.details.get("session", "UNKNOWN"))
        session_counts[session] = session_counts.get(session, 0) + 1

    summary = dict(original.get("summary", {}))
    summary.update(
        {
            "candidate": "candidate-02-v114-route-ablation",
            "route_mode": config.route_mode,
            "v113_all_resolved_signal_routes": route_counts,
            "signals_before_route_ablation": len(all_signals),
            "signals_after_route_ablation": len(retained),
            "retained_side_counts": side_counts,
            "retained_session_counts": dict(sorted(session_counts.items())),
            "single_variable_changed": "allowed resolved route",
        }
    )
    _LAST_DIAGNOSTICS = {
        "summary": summary,
        "examples": dict(original.get("examples", {})),
    }
    return retained
