"""Candidate-09 v9 contracts built on the validated v8 session-auction model."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

from state_engine_v8_model import (
    DAY_NS,
    MINUTE_NS,
    DiagnosticEvent,
    EngineConfig as V8EngineConfig,
    EngineResult,
    FlowBar,
    LiquidityLevel,
    PendingAcceptanceFailure,
    RiskSizing,
    SessionBuilder,
    SessionRange,
    SessionSpec,
    Signal,
    risk_based_quantity,
)


@dataclass(frozen=True, slots=True)
class EngineConfig(V8EngineConfig):
    require_failure_trap: bool = True
    require_reacceptance_retest: bool = False
    use_half_range_extension: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "plain-acceptance", "reacceptance-retest", "half-range-target"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        base = V8EngineConfig.from_mapping(payload, ablation="baseline")
        inherited = {field.name: getattr(base, field.name) for field in fields(V8EngineConfig)}
        inherited["require_failure_retest"] = False
        inherited["use_midpoint_target"] = False
        return cls(
            **inherited,
            require_failure_trap=ablation != "plain-acceptance",
            require_reacceptance_retest=ablation == "reacceptance-retest",
            use_half_range_extension=ablation == "half-range-target",
        )


__all__ = [
    "DAY_NS",
    "MINUTE_NS",
    "DiagnosticEvent",
    "EngineConfig",
    "EngineResult",
    "FlowBar",
    "LiquidityLevel",
    "PendingAcceptanceFailure",
    "RiskSizing",
    "SessionBuilder",
    "SessionRange",
    "SessionSpec",
    "Signal",
    "risk_based_quantity",
]
