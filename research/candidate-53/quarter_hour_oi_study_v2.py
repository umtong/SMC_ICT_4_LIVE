#!/usr/bin/env python3
"""Install the existing pandas/Binance timestamp contract, then run the frozen QH study."""
from __future__ import annotations

import runpy
from pathlib import Path

from timestamp_contract import install

install()
runpy.run_path(str(Path(__file__).with_name("quarter_hour_oi_study.py")), run_name="__main__")
