#!/usr/bin/env python3
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
from execution_re1_breadth import EasyChartRE1BreadthStructuralStrategy
from execution_re1_btc_context import EasyChartRE1BitcoinContextStrategy
from execution_re1_family_filter import EasyChartRE1FamilyFilterStrategy
from execution_re1_invalidation import EasyChartRE1InvalidationDecisionStrategy
from execution_re1_management import EasyChartRE1DecisionSwingStrategy, EasyChartRE1StaticStrategy
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy


Variant = tuple[object, object, str | None]
VARIANTS: dict[str, Variant] = {
    "impulse": (EasyChartRE1ImpulseBundle, EasyChartRE1StructuralFixedStrategy, None),
    "location": (EasyChartRE1LocationBundle, EasyChartRE1StructuralFixedStrategy, None),
    "local-alignment": (EasyChartRE1LocalAlignmentBundle, EasyChartRE1StructuralFixedStrategy, None),
    "liquidity-location": (EasyChartRE1LiquidityLocationBundle, EasyChartRE1StructuralFixedStrategy, None),
    "liquidity-local": (EasyChartRE1LiquidityLocalBundle, EasyChartRE1StructuralFixedStrategy, None),
    "complete": (EasyChartRE1CompleteBundle, EasyChartRE1StructuralFixedStrategy, None),
    "complete-breadth": (EasyChartRE1CompleteBundle, EasyChartRE1BreadthStructuralStrategy, None),
    "adjacent": (EasyChartRE1AdjacentCompleteBundle, EasyChartRE1StructuralFixedStrategy, None),
    "validated-structure": (EasyChartRE1ValidatedStructureBundle, EasyChartRE1StructuralFixedStrategy, None),
    "validated-btc": (EasyChartRE1ValidatedStructureBundle, EasyChartRE1BitcoinContextStrategy, None),
    "validated-static": (EasyChartRE1ValidatedStructureBundle, EasyChartRE1StaticStrategy, None),
    "validated-5m": (EasyChartRE1ValidatedStructureBundle, EasyChartRE1DecisionSwingStrategy, None),
    "geometry": (EasyChartRE1GeometryBundle, EasyChartRE1StructuralFixedStrategy, None),
    "geometry-btc": (EasyChartRE1GeometryBundle, EasyChartRE1BitcoinContextStrategy, None),
    "geometry-static": (EasyChartRE1GeometryBundle, EasyChartRE1StaticStrategy, None),
    "geometry-5m": (EasyChartRE1GeometryBundle, EasyChartRE1DecisionSwingStrategy, None),
    "geometry-invalidation": (EasyChartRE1GeometryBundle, EasyChartRE1InvalidationDecisionStrategy, None),
    "zone-1m": (EasyChartRE1ZoneTargetBundle, EasyChartRE1StructuralFixedStrategy, None),
    "zone-static": (EasyChartRE1ZoneTargetBundle, EasyChartRE1StaticStrategy, None),
    "zone-5m": (EasyChartRE1ZoneTargetBundle, EasyChartRE1DecisionSwingStrategy, None),
    "zone-invalidation": (EasyChartRE1ZoneTargetBundle, EasyChartRE1InvalidationDecisionStrategy, None),
    "decision-area": (EasyChartRE1DecisionAreaBundle, EasyChartRE1DecisionSwingStrategy, None),
    "decision-area-invalidation": (
        EasyChartRE1DecisionAreaBundle,
        EasyChartRE1InvalidationDecisionStrategy,
        None,
    ),
    "displacement-5m": (EasyChartRE1DisplacementBundle, EasyChartRE1DecisionSwingStrategy, None),
    "displacement-invalidation": (
        EasyChartRE1DisplacementBundle,
        EasyChartRE1InvalidationDecisionStrategy,
        None,
    ),
    "validated-diagonal": (EasyChartRE1ValidatedStructureBundle, EasyChartRE1FamilyFilterStrategy, "DIAGONAL"),
    "validated-horizontal": (EasyChartRE1ValidatedStructureBundle, EasyChartRE1FamilyFilterStrategy, "HORIZONTAL"),
    "validated-major-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1FamilyFilterStrategy,
        "MAJOR_LIQUIDITY",
    ),
    "validated-diagonal-horizontal": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1FamilyFilterStrategy,
        "DIAGONAL,HORIZONTAL",
    ),
    "validated-diagonal-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1FamilyFilterStrategy,
        "DIAGONAL,MAJOR_LIQUIDITY",
    ),
    "validated-horizontal-liquidity": (
        EasyChartRE1ValidatedStructureBundle,
        EasyChartRE1FamilyFilterStrategy,
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
