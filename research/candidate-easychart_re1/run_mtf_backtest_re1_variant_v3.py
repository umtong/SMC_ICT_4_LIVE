#!/usr/bin/env python3
from __future__ import annotations

import os

import run_mtf_backtest_re1 as _runner
from easychart_re1_ablation import EasyChartRE1LocalAlignmentBundle, EasyChartRE1LocationBundle
from easychart_re1_adjacent import EasyChartRE1AdjacentCompleteBundle
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_impulse import EasyChartRE1ImpulseBundle
from easychart_re1_liquidity import EasyChartRE1LiquidityLocalBundle, EasyChartRE1LiquidityLocationBundle
from execution_re1_breadth import EasyChartRE1BreadthStructuralStrategy
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy


VARIANTS = {
    "impulse": (EasyChartRE1ImpulseBundle, EasyChartRE1StructuralFixedStrategy),
    "location": (EasyChartRE1LocationBundle, EasyChartRE1StructuralFixedStrategy),
    "local-alignment": (EasyChartRE1LocalAlignmentBundle, EasyChartRE1StructuralFixedStrategy),
    "liquidity-location": (EasyChartRE1LiquidityLocationBundle, EasyChartRE1StructuralFixedStrategy),
    "liquidity-local": (EasyChartRE1LiquidityLocalBundle, EasyChartRE1StructuralFixedStrategy),
    "complete": (EasyChartRE1CompleteBundle, EasyChartRE1StructuralFixedStrategy),
    "complete-breadth": (EasyChartRE1CompleteBundle, EasyChartRE1BreadthStructuralStrategy),
    "adjacent": (EasyChartRE1AdjacentCompleteBundle, EasyChartRE1StructuralFixedStrategy),
}


def main() -> None:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    try:
        bundle, strategy = VARIANTS[name]
    except KeyError as exc:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        ) from exc
    _runner.EasyChartRE1NaturalBundle = bundle
    _runner.EasyChartRE1StructuralStrategy = strategy
    _runner.main()


if __name__ == "__main__":
    main()
