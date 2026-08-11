#!/usr/bin/env python3
"""Run the source-shaped W/M session Trap diagnostic."""
from __future__ import annotations

import screen_v7_fixed  # noqa: F401
import screen_v7 as _base
from market_v12 import EasyChartWMTrapEngine


_base.EasyChartSessionTrapEngine = EasyChartWMTrapEngine


if __name__ == "__main__":
    _base.main()
