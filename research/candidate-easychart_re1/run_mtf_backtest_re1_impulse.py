#!/usr/bin/env python3
"""Run the pullback-resumption / first-obstacle RE1 candidate."""
from __future__ import annotations

import run_mtf_backtest_re1 as _runner
from easychart_re1_impulse import EasyChartRE1ImpulseBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy


_runner.EasyChartRE1NaturalBundle = EasyChartRE1ImpulseBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
