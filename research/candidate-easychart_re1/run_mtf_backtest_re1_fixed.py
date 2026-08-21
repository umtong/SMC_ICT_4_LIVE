#!/usr/bin/env python3
"""Run the canonical RE1 backtest with the Nautilus 1.230 modify binding fix."""
from __future__ import annotations

from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
