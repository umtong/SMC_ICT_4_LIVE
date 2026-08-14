#!/usr/bin/env python3
"""Run the human-entry EasyChart RE1 account plus terminal wedges."""
from __future__ import annotations

from easychart_re1_wedge import EasyChartRE1WedgeBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1WedgeBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
