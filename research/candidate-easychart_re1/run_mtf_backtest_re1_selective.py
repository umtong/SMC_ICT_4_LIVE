#!/usr/bin/env python3
"""Run the mechanism-selective RE1 account policy."""
from __future__ import annotations

from easychart_re1_selective import EasyChartRE1SelectiveBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1SelectiveBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
