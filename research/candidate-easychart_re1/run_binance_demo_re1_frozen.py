#!/usr/bin/env python3
"""Run one explicitly frozen RE1 candidate on Binance USD-M Demo.

Set ``EASYCHART_RE1_VARIANT`` to the exact variant selected by the validation
artifact.  This wrapper deliberately has no fallback candidate: an absent or
unknown manifest is a configuration failure, not permission to run older code.
"""
from __future__ import annotations

import os

import run_binance_demo_re1 as _base
from easychart_re1_ablation import EasyChartRE1LocalAlignmentBundle, EasyChartRE1LocationBundle
from easychart_re1_adjacent import EasyChartRE1AdjacentCompleteBundle
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_decision_area import EasyChartRE1DecisionAreaBundle
from easychart_re1_geometry import EasyChartRE1GeometryBundle
from easychart_re1_impulse import EasyChartRE1ImpulseBundle
from easychart_re1_liquidity import EasyChartRE1LiquidityLocalBundle, EasyChartRE1LiquidityLocationBundle
from easychart_re1_validated_structure import EasyChartRE1ValidatedStructureBundle
from easychart_re1_zone_targets import EasyChartRE1ZoneTargetBundle
from paper_re1_generic import (
    EasyChartRE1VenueSafeDecisionPaperStrategy,
    EasyChartRE1VenueSafeInvalidationPaperStrategy,
    EasyChartRE1VenueSafeStaticPaperStrategy,
    EasyChartRE1VenueSafeStructuralPaperStrategy,
)
from paper_re1_variant import (
    EasyChartRE1VenueSafeBitcoinContextPaperStrategy,
    EasyChartRE1VenueSafeBreadthPaperStrategy,
    EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
)


Variant = tuple[object, object, str | None]
VARIANTS: dict[str, Variant] = {
    "impulse": (EasyChartRE1ImpulseBundle, EasyChartRE1VenueSafeStructuralPaperStrategy, None),
    "location": (EasyChartRE1LocationBundle, EasyChartRE1VenueSafeStructuralPaperStrategy, None),
    "local-alignment": (
        EasyChartRE1LocalAlignmentBundle,
        EasyChartRE1VenueSafeStructuralPaperStrategy,
        None,
    ),
    "liquidity-location": (
        EasyChartRE1LiquidityLocationBundle,
        EasyChartRE1VenueSafeStructuralPaperStrategy,
        None,
    ),
    "liquidity-local": (
        EasyChartRE1LiquidityLocalBundle,
        EasyChartRE1VenueSafeStructuralPaperStrategy,
        None,
    ),
    "complete": (EasyChartRE1CompleteBundle, EasyChartRE1VenueSafeStructuralPaperStrategy, None),
    "complete-breadth": (
        EasyChartRE1CompleteBundle,
        EasyChartRE1VenueSafeBreadthPaperStrategy,
        None,
    ),
    "adjacent": (
        EasyChartRE1AdjacentCompleteBundle,
        EasyChartRE1VenueSafeStructuralPaperStrategy,
        None,
    ),
    "validated-structure": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeStructuralPaperStrategy,
        None,
    ),
    "validated-btc": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeBitcoinContextPaperStrategy,
        None,
    ),
    "validated-static": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeStaticPaperStrategy,
        None,
    ),
    "validated-5m": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeDecisionPaperStrategy,
        None,
    ),
    "geometry": (EasyChartRE1GeometryBundle, EasyChartRE1VenueSafeStructuralPaperStrategy, None),
    "geometry-btc": (
        EasyChartRE1GeometryBundle,
        EasyChartRE1VenueSafeBitcoinContextPaperStrategy,
        None,
    ),
    "geometry-static": (
        EasyChartRE1GeometryBundle,
        EasyChartRE1VenueSafeStaticPaperStrategy,
        None,
    ),
    "geometry-5m": (
        EasyChartRE1GeometryBundle,
        EasyChartRE1VenueSafeDecisionPaperStrategy,
        None,
    ),
    "geometry-invalidation": (
        EasyChartRE1GeometryBundle,
        EasyChartRE1VenueSafeInvalidationPaperStrategy,
        None,
    ),
    "zone-1m": (EasyChartRE1ZoneTargetBundle, EasyChartRE1VenueSafeStructuralPaperStrategy, None),
    "zone-static": (EasyChartRE1ZoneTargetBundle, EasyChartRE1VenueSafeStaticPaperStrategy, None),
    "zone-5m": (EasyChartRE1ZoneTargetBundle, EasyChartRE1VenueSafeDecisionPaperStrategy, None),
    "zone-invalidation": (
        EasyChartRE1ZoneTargetBundle,
        EasyChartRE1VenueSafeInvalidationPaperStrategy,
        None,
    ),
    "decision-area": (
        EasyChartRE1DecisionAreaBundle,
        EasyChartRE1VenueSafeDecisionPaperStrategy,
        None,
    ),
    "decision-area-invalidation": (
        EasyChartRE1DecisionAreaBundle,
        EasyChartRE1VenueSafeInvalidationPaperStrategy,
        None,
    ),
    "validated-diagonal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
        "DIAGONAL",
    ),
    "validated-horizontal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
        "HORIZONTAL",
    ),
    "validated-major-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
        "MAJOR_LIQUIDITY",
    ),
    "validated-diagonal-horizontal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
        "DIAGONAL,HORIZONTAL",
    ),
    "validated-diagonal-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
        "DIAGONAL,MAJOR_LIQUIDITY",
    ),
    "validated-horizontal-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1VenueSafeFamilyFilterPaperStrategy,
        "HORIZONTAL,MAJOR_LIQUIDITY",
    ),
}


def main() -> None:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    try:
        bundle, paper_strategy, families = VARIANTS[name]
    except KeyError as exc:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        ) from exc
    if families is None:
        os.environ.pop("EASYCHART_RE1_FAMILIES", None)
    else:
        os.environ["EASYCHART_RE1_FAMILIES"] = families
    _base.EasyChartRE1FreshBundle = bundle
    _base.EasyChartRE1CoherentPaperStrategy = paper_strategy
    _base.main()


if __name__ == "__main__":
    main()
