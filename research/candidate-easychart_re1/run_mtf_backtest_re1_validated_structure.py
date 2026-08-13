#!/usr/bin/env python3
from __future__ import annotations

import run_mtf_backtest_re1 as _runner
from easychart_re1_validated_structure import EasyChartRE1ValidatedStructureBundle
from execution_re1_structural_fixed import EasyChartRE1StructuralFixedStrategy

_runner.EasyChartRE1NaturalBundle = EasyChartRE1ValidatedStructureBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1StructuralFixedStrategy

if __name__ == "__main__":
    _runner.main()
