#!/usr/bin/env python3
"""Run response-confirmed RE1 plus the single-major-swing family."""
from __future__ import annotations

from easychart_re1_major_swing import EasyChartRE1MajorSwingBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1MajorSwingBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
