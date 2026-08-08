#!/usr/bin/env python3
"""Remove unused positioning/basis wrappers from the frozen stage launcher.

V39 consumes kline, aggTrade footprint and public depth observations only.
The patch changes no market state, signal, order, fill, cost, risk or period; it
prevents an unrelated historical metrics boundary conflict from blocking the
same pre-registered experiment.
"""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = "V39_MINIMAL_OBSERVATION_CONTRACTS"
if marker in text:
    raise SystemExit(0)
replacements = {
    "from positioning_contract import install as install_positioning_contract\n": "",
    "from basis_contract import install as install_basis_contract\n": "",
    "install_positioning_contract()\n": "",
    "install_basis_contract()\n": "",
}
for old, new in replacements.items():
    if old not in text:
        raise RuntimeError(f"candidate launcher contract line not found: {old.strip()}")
    text = text.replace(old, new, 1)
text = text.replace(
    "install_book_depth_gap_contract()\n",
    "install_book_depth_gap_contract()\n# V39_MINIMAL_OBSERVATION_CONTRACTS\n",
    1,
)
path.write_text(text, encoding="utf-8")
