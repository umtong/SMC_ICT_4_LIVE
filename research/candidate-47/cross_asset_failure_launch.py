#!/usr/bin/env python3
"""Install the inherited Binance timestamp contract, then run the diagnostic."""
from __future__ import annotations

from pathlib import Path
import runpy
import sys

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE05))

from timestamp_contract import install  # noqa: E402

install()
runpy.run_path(str(HERE / "cross_asset_failure_diagnostic.py"), run_name="__main__")
