#!/usr/bin/env python3
"""Run v8 with the corrected repeated-wick reference lifecycle."""
from __future__ import annotations

import screen_v8 as _base
from market_v8_fixed import CorrectedEasyChartLiquidityPoolEngine


_base.EasyChartLiquidityPoolEngine = CorrectedEasyChartLiquidityPoolEngine


if __name__ == "__main__":
    _base.main()
