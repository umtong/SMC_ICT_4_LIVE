#!/usr/bin/env python3
from __future__ import annotations

import run_mtf_backtest_re1 as _runner
from easychart_re1_validated_structure import EasyChartRE1ValidatedStructureBundle
from execution_re1_btc_context import EasyChartRE1BitcoinContextStrategy

_runner.EasyChartRE1NaturalBundle = EasyChartRE1ValidatedStructureBundle
_runner.EasyChartRE1StructuralStrategy = EasyChartRE1BitcoinContextStrategy

if __name__ == "__main__":
    _runner.main()
