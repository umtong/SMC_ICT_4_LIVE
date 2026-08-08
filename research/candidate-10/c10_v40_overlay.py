"""Pure configuration surface for v40 detector/scenario separation.

The source-equilibrium primary trade must not depend on an unrelated external
liquidity draw merely to exist.  When enabled, the regional source boundary
sweep is the detector event; reclaim, MSS and displacement confirm failed
auction; source dealing-range equilibrium is selected independently as the
primary objective.  External draw mapping remains available to other scenario
families but is not an input to this primary trade.
"""
from __future__ import annotations

import os

from c10_v38_overlay import (  # re-export frozen execution and management layers
    CostAwareRiskSizer,
    InternalPivotProtection,
    LiveImpactLedger,
    apply_cost_overlay,
    build_leadership_gate,
    certify_plan,
    consequent_encroachment,
    first_favorable_internal_pivot,
    internal_pivot_protection_enabled,
    micro_pivot_protection_enabled,
    micro_pivot_reference_contract,
    normalize_kline_open_time,
    rejection_displacement,
    repair_kline_flow_frame,
    source_equilibrium,
)


def source_equilibrium_detector_enabled() -> bool:
    return os.environ.get("C10_V40_SOURCE_EQUILIBRIUM_DETECTOR", "0") == "1"


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
    "source_equilibrium_detector_enabled",
]
