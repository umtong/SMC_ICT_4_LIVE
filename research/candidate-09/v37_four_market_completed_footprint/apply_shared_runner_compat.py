#!/usr/bin/env python3
"""Implementation-only bridge for the frozen Candidate 05 instrument factory."""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if "V37_FROZEN_INSTRUMENT_FACTORY_BRIDGE" in text:
    raise SystemExit(0)
old_import = "from backtest import make_instrument\n"
new_import = (
    "from backtest import make_instrument\n"
    "from instrument_contracts import instrument_contract\n"
    "# V37_FROZEN_INSTRUMENT_FACTORY_BRIDGE\n"
)
if old_import not in text:
    raise RuntimeError("shared runner make_instrument import not found")
text = text.replace(old_import, new_import, 1)
old_call = "        instrument = make_instrument(configs[symbol])\n"
new_call = (
    "        contract = instrument_contract(symbol)\n"
    "        frozen_instrument_id = InstrumentId.from_str(contract.instrument_id)\n"
    "        instrument = make_instrument(\n"
    "            configs[symbol],\n"
    "            contract,\n"
    "            frozen_instrument_id,\n"
    "        )\n"
)
if old_call not in text:
    raise RuntimeError("shared runner instrument factory call not found")
path.write_text(text.replace(old_call, new_call, 1), encoding="utf-8")
