#!/usr/bin/env python3
"""Treat non-positive official bookDepth rows as missing observations."""
from pathlib import Path

path = Path(__file__).resolve().parent / "data_loader.py"
text = path.read_text(encoding="utf-8")
old = (
    "        depth = float(row[di])\n"
    "        notional = float(row[ni])\n"
    "        by_timestamp.setdefault(observed_ns, {})[percentage] = (depth, notional)\n"
)
new = (
    "        depth = float(row[di])\n"
    "        notional = float(row[ni])\n"
    "        if depth <= 0.0 or notional <= 0.0:\n"
    "            continue\n"
    "        by_timestamp.setdefault(observed_ns, {})[percentage] = (depth, notional)\n"
)
if new in text:
    raise SystemExit(0)
if old not in text:
    raise RuntimeError("depth validation insertion contract not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
