"""Candidate 09 v14: failed-boundary invalidation for every accepted-breakout reversal.

v13 demonstrated that the same causal invalidation which salvaged otherwise rejected
reversals was stronger when applied consistently to every accepted-breakout failure.
The baseline therefore promotes that controlled ablation without changing the detector,
entry timing, equilibrium target, costs, risk budget, or declared BTC weeks.

The three ablations isolate the promoted confirmation layer:

* ``accepted-extreme-stop`` restores the exact v10 accepted-excursion invalidation;
* ``salvage-only`` restores the v13 mixed policy (boundary invalidation only when the
  accepted-extreme signal is rejected);
* ``no-flow`` keeps the v14 geometry but removes order-flow confirmation.

NautilusTrader remains the sole execution and accounting engine.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

from state_engine_v13_direct import (
    MINUTE_NS,
    AuctionLevel,
    DiagnosticEvent,
    EngineConfig as V13EngineConfig,
    EngineResult,
    FlowBar,
    LiquidityStateEngine as V13LiquidityStateEngine,
    PendingResolution,
    RiskSizing,
    Signal,
    risk_based_quantity,
)


@dataclass(frozen=True, slots=True)
class EngineConfig(V13EngineConfig):
    """Frozen v14 configuration with explicit causal ablation mapping."""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {
            "baseline",
            "accepted-extreme-stop",
            "salvage-only",
            "no-flow",
        }
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")

        v13_ablation = {
            "baseline": "boundary-stop-all",
            "accepted-extreme-stop": "no-boundary-stop-salvage",
            "salvage-only": "baseline",
            "no-flow": "boundary-stop-all",
        }[ablation]
        base = V13EngineConfig.from_mapping(payload, ablation=v13_ablation)
        inherited = {field.name: getattr(base, field.name) for field in fields(V13EngineConfig)}
        if ablation == "no-flow":
            inherited["use_flow_confirmation"] = False
        return cls(**inherited)


class LiquidityStateEngine(V13LiquidityStateEngine):
    """v13 engine with the promoted configuration contract; logic is unchanged."""

    config: EngineConfig


__all__ = [
    "MINUTE_NS",
    "AuctionLevel",
    "DiagnosticEvent",
    "EngineConfig",
    "EngineResult",
    "FlowBar",
    "LiquidityStateEngine",
    "PendingResolution",
    "RiskSizing",
    "Signal",
    "risk_based_quantity",
]
