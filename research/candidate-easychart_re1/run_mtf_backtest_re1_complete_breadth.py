#!/usr/bin/env python3
from __future__ import annotations

import run_mtf_backtest_re1 as _runner
from easychart_re1_complete import EasyChartRE1CompleteBundle
from execution_re1_breadth import EasyChartRE1BreadthStructuralStrategy

_runner.EasyChartRE1NaturalBundle = EasyChartRE1CompleteBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1BreadthStructuralStrategy

if __name__ == "__main__":
    _runner.main()
