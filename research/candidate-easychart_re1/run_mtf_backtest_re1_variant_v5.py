#!/usr/bin/env python3
from __future__ import annotations

import os

import run_mtf_backtest_re1 as _runner
from easychart_re1_ablation import EasyChartRE1LocalAlignmentBundle, EasyChartRE1LocationBundle
from easychart_re1_adjacent import EasyChartRE1AdjacentCompleteBundle
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_geometry import EasyChartRE1GeometryBundle
from easychart_re1_impulse import EasyChartRE1ImpulseBundle
from easychart_re1_liquidity import EasyChartRE1LiquidityLocalBundle, EasyChartRE1LiquidityLocationBundle
from easychart_re1_validated_structure import EasyChartRE1ValidatedStructureBundle
from execution_re1_breadth import EasyChartRE1BreadthStructuralStrategy
from execution_re1_btc_context import EasyChartRE1BitcoinContextStrategy
from execution_re1_family_filter import EasyChartRE1FamilyFilterStrategy
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy


VARIANTS: dict[str, tuple[object, object, str | None]] = {
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
    "geometry": (EasyChartRE1GeometryBundle, EasyChartRE1StructuralFixedStrategy, None),
    "geometry-btc": (EasyChartRE1GeometryBundle, EasyChartRE1BitcoinContextStrategy, None),
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
