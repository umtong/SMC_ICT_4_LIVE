#!/usr/bin/env python3
"""Dispatch one named structural candidate through the canonical account runner."""
from __future__ import annotations

import os

import run_mtf_backtest_re1 as _runner
from easychart_re1_ablation import EasyChartRE1LocalAlignmentBundle, EasyChartRE1LocationBundle
from easychart_re1_complete import EasyChartRE1CompleteBundle
from easychart_re1_impulse import EasyChartRE1ImpulseBundle
from easychart_re1_liquidity import (
    EasyChartRE1LiquidityLocalBundle,
    EasyChartRE1LiquidityLocationBundle,
)
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy


VARIANTS = {
    "impulse": EasyChartRE1ImpulseBundle,
    "location": EasyChartRE1LocationBundle,
    "local-alignment": EasyChartRE1LocalAlignmentBundle,
    "liquidity-location": EasyChartRE1LiquidityLocationBundle,
    "liquidity-local": EasyChartRE1LiquidityLocalBundle,
    "complete": EasyChartRE1CompleteBundle,
}


def main() -> None:
    name = os.environ.get("EASYCHART_RE1_VARIANT", "").strip()
    if name not in VARIANTS:
        raise SystemExit(
            "EASYCHART_RE1_VARIANT must be one of: " + ", ".join(sorted(VARIANTS)),
        )
    _runner.EasyChartRE1NaturalBundle = VARIANTS[name]
    _runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy
    _runner.main()


if __name__ == "__main__":
    main()
