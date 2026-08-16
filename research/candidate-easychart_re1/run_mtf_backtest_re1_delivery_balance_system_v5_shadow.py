#!/usr/bin/env python3
"""Run v5 once while labeling every emitted plan on later one-minute bars."""
from __future__ import annotations

import sys

from execution_re1_ml_a_shadow import EasyChartRE1PlanLabelStrategy
import run_mtf_backtest_re1_delivery_balance_system_v5 as _v5


if __name__ == "__main__":
    destination = _v5.flow_runner._output_path(sys.argv)
    _v5.flow_runner._runner.EasyChartRE1Strategy = EasyChartRE1PlanLabelStrategy
    _v5.flow_runner._runner.main()
    if destination is not None:
        _v5.flow_runner._rewrite_metadata(destination)
        _v5.rewrite_metadata(destination)
