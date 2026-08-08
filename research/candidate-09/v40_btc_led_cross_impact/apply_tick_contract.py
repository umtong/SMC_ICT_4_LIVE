#!/usr/bin/env python3
"""Bind multi-symbol footprint ticks to the frozen Nautilus instruments."""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = "V40_FROZEN_FOOTPRINT_TICK_CONTRACT"
if marker in text:
    raise SystemExit(0)
old = '    "SOLUSDT": 0.001,\n'
new = '    "SOLUSDT": 0.01,  # V40_FROZEN_FOOTPRINT_TICK_CONTRACT\n'
if old not in text:
    raise RuntimeError("V37 SOL footprint tick assumption not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
