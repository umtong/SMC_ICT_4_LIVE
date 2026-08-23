#!/usr/bin/env python3
from __future__ import annotations

import run_mtf_backtest_re1 as _runner
from easychart_re1_geometry import EasyChartRE1GeometryBundle
from execution_re1_management import EasyChartRE1StaticStrategy

_runner.EasyChartRE1NaturalBundle = EasyChartRE1GeometryBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StaticStrategy

if __name__ == "__main__":
    _runner.main()
