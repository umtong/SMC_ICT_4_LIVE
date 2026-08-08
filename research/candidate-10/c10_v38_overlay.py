"""Pure helpers for v38 one-minute confirmed micro-pivot protection."""
from __future__ import annotations

import os

from c10_v37_overlay import (  # re-export frozen v37/v36/v29/v28/v27 layers
    CostAwareRiskSizer,
    InternalPivotProtection,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    first_favorable_internal_pivot,
    internal_pivot_protection_enabled,
    normalize_kline_open_time,
    rejection_displacement,
    repair_kline_flow_frame,
    source_equilibrium,
)


def micro_pivot_protection_enabled() -> bool:
    return os.environ.get("C10_V38_MICRO_PIVOT_PROTECTION", "0") == "1"


def micro_pivot_reference_contract() -> str:
    value = os.environ.get(
        "C10_V38_MICRO_PIVOT_REFERENCE",
        "CE_RETEST_EXTREME",
    )
    if value not in {"CE_RETEST_EXTREME", "EXPECTED_ENTRY"}:
        raise ValueError(f"unsupported v38 pivot reference: {value}")
    return value


__all__ = [
    "CostAwareRiskSizer",
    "InternalPivotProtection",
    "LiveImpactLedger",
    "apply_cost_overlay",
    "build_leadership_gate",
    "certify_plan",
    "consequent_encroachment",
    "first_favorable_internal_pivot",
    "internal_pivot_protection_enabled",
    "micro_pivot_protection_enabled",
    "micro_pivot_reference_contract",
    "normalize_kline_open_time",
    "rejection_displacement",
    "repair_kline_flow_frame",
    "source_equilibrium",
]
