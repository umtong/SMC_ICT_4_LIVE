#!/usr/bin/env python3
"""Run the coherent EasyChart RE1 account with natural trade geometry."""
from __future__ import annotations

from easychart_re1_natural_geometry import EasyChartRE1NaturalGeometryBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1NaturalGeometryBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
