#!/usr/bin/env python3
"""Run RE1 with ordered four-point channel-phase semantics."""
from __future__ import annotations

from easychart_re1_phase import EasyChartRE1PhaseBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1PhaseBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
