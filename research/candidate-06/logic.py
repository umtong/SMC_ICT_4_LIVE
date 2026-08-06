"""Public candidate-06 causal-logic API.

The implementation is split so primitives and scenario decisions remain
independently testable; this module preserves a compact import surface.
"""

from lrb_types import (
    BarObservation,
    PrimitiveSnapshot,
    ScenarioSignal,
    ScenarioStep,
    ScenarioTransition,
    SweepPrimitive,
)
from primitives import CausalPrimitiveDetector, LiquiditySweepDetector
from scenario_engine import LiquidityResponseScenarioEngine

__all__ = [
    "BarObservation",
    "PrimitiveSnapshot",
    "SweepPrimitive",
    "ScenarioTransition",
    "ScenarioSignal",
    "ScenarioStep",
    "CausalPrimitiveDetector",
    "LiquiditySweepDetector",
    "LiquidityResponseScenarioEngine",
]
