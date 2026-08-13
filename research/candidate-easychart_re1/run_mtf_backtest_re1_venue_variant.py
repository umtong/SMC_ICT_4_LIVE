#!/usr/bin/env python3
"""Backtest the exact paper transport for one frozen candidate variant."""
from __future__ import annotations

import os

import run_mtf_backtest_re1 as _runner
from easychart_re1_ablation import EasyChartRE1LocalAlignmentBundle, EasyChartRE1LocationBundle
from easychart_re1_adjacent import EasyChartRE1AdjacentCompleteBundle
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_decision_area import EasyChartRE1DecisionAreaBundle
from easychart_re1_displacement import EasyChartRE1DisplacementBundle
from easychart_re1_geometry import EasyChartRE1GeometryBundle
from easychart_re1_impulse import EasyChartRE1ImpulseBundle
from easychart_re1_liquidity import EasyChartRE1LiquidityLocalBundle, EasyChartRE1LiquidityLocationBundle
from easychart_re1_validated_structure import EasyChartRE1ValidatedStructureBundle
from easychart_re1_zone_targets import EasyChartRE1ZoneTargetBundle
from execution_re1_venue_context import (
    EasyChartRE1VenueSafeBitcoinContextStrategy,
    EasyChartRE1VenueSafeBreadthStrategy,
    EasyChartRE1VenueSafeFamilyFilterStrategy,
)
from execution_re1_venue_safe import (
    EasyChartRE1VenueSafeDecisionStrategy,
    EasyChartRE1VenueSafeInvalidationStrategy,
    EasyChartRE1VenueSafeStaticStrategy,
    EasyChartRE1VenueSafeStructuralStrategy,
)


Variant = tuple[object, object, str | None]
VARIANTS: dict[str, Variant] = {
    "impulse": (EasyChartRE1ImpulseBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "location": (EasyChartRE1LocationBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "local-alignment": (EasyChartRE1LocalAlignmentBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "liquidity-location": (EasyChartRE1LiquidityLocationBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "liquidity-local": (EasyChartRE1LiquidityLocalBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "complete": (EasyChartRE1CompleteBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "complete-breadth": (EasyChartRE1CompleteBundle, EasyChartRE1VenueSafeBreadthStrategy, None),
    "adjacent": (EasyChartRE1AdjacentCompleteBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "validated-structure": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeStructuralStrategy,
        None,
    ),
    "validated-btc": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeBitcoinContextStrategy,
        None,
    ),
    "validated-static": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeStaticStrategy,
        None,
    ),
    "validated-5m": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeDecisionStrategy,
        None,
    ),
    "geometry": (EasyChartRE1GeometryBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "geometry-btc": (
        EasyChartRE1GeometryBundle,
        EasyChartRE1VenueSafeBitcoinContextStrategy,
        None,
    ),
    "geometry-static": (EasyChartRE1GeometryBundle, EasyChartRE1VenueSafeStaticStrategy, None),
    "geometry-5m": (EasyChartRE1GeometryBundle, EasyChartRE1VenueSafeDecisionStrategy, None),
    "geometry-invalidation": (
        EasyChartRE1GeometryBundle,
        EasyChartRE1VenueSafeInvalidationStrategy,
        None,
    ),
    "zone-1m": (EasyChartRE1ZoneTargetBundle, EasyChartRE1VenueSafeStructuralStrategy, None),
    "zone-static": (EasyChartRE1ZoneTargetBundle, EasyChartRE1VenueSafeStaticStrategy, None),
    "zone-5m": (EasyChartRE1ZoneTargetBundle, EasyChartRE1VenueSafeDecisionStrategy, None),
    "zone-invalidation": (
        EasyChartRE1ZoneTargetBundle,
        EasyChartRE1VenueSafeInvalidationStrategy,
        None,
    ),
    "decision-area": (
        EasyChartRE1DecisionAreaBundle,
        EasyChartRE1VenueSafeDecisionStrategy,
        None,
    ),
    "decision-area-invalidation": (
        EasyChartRE1DecisionAreaBundle,
        EasyChartRE1VenueSafeInvalidationStrategy,
        None,
    ),
    "displacement-5m": (
        EasyChartRE1DisplacementBundle,
        EasyChartRE1VenueSafeDecisionStrategy,
        None,
    ),
    "displacement-invalidation": (
        EasyChartRE1DisplacementBundle,
        EasyChartRE1VenueSafeInvalidationStrategy,
        None,
    ),
    "validated-diagonal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterStrategy,
        "DIAGONAL",
    ),
    "validated-horizontal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterStrategy,
        "HORIZONTAL",
    ),
    "validated-major-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterStrategy,
        "MAJOR_LIQUIDITY",
    ),
    "validated-diagonal-horizontal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterStrategy,
        "DIAGONAL,HORIZONTAL",
    ),
    "validated-diagonal-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterStrategy,
        "DIAGONAL,MAJOR_LIQUIDITY",
    ),
    "validated-horizontal-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterStrategy,
        "HORIZONTAL,MAJOR_LIQUIDITY",
    ),
}


def main() -> None:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    try:
        bundle, strategy, families = VARIANTS[name]
    except KeyError as exc:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        ) from exc
    if families is None:
        os.environ.pop("EASYCHART_RE1_FAMILIES", None)
    else:
        os.environ["EASYCHART_RE1_FAMILIES"] = families
    _runner.EasyChartRE1NaturalBundle = bundle
    _runner.EasyChartRE1StructuralStrategy = strategy
    _runner.main()


if __name__ == "__main__":
    main()
