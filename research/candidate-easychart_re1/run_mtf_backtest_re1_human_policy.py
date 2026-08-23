#!/usr/bin/env python3
"""Run the human-entry, fixed-plan EasyChart RE1 account."""
from __future__ import annotations

from easychart_re1_human_policy import EasyChartRE1HumanPolicyBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy
import run_mtf_backtest_re1 as _runner


_runner.EasyChartRE1NaturalBundle = EasyChartRE1HumanPolicyBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy


if __name__ == "__main__":
    _runner.main()
