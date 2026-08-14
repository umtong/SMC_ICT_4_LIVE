#!/usr/bin/env python3
"""Run the complete EasyChart RE1 policy with causal market state."""
from __future__ import annotations

from easychart_re1_state import EasyChartRE1StateBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1StateBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
