#!/usr/bin/env python3
"""Install the existing timestamp compatibility contract, then run the frozen state study."""
from __future__ import annotations

import runpy
from pathlib import Path

from timestamp_contract import install

install()
runpy.run_path(str(Path(__file__).with_name("pressure_capacity_study.py")), run_name="__main__")
