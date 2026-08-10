#!/usr/bin/env python3
"""Remove unrelated config keys before parsing the source-fidelity strategy."""
from pathlib import Path

path = Path("research/candidate-57/winner_source_fidelity_campaign.py")
text = path.read_text(encoding="utf-8")
old = '''    config = copy.deepcopy(base)\n    config["strategy"].update(\n'''
new = '''    config = copy.deepcopy(base)\n    for key in (\n        "sma_offset_low",\n        "sma_offset_high",\n        "sma_stop_min_fraction",\n        "sma_stop_max_fraction",\n        "sma_stop_atr_buffer",\n    ):\n        config["strategy"].pop(key, None)\n    config["strategy"].update(\n'''
if new in text:
    print("source-fidelity config cleanup already present")
elif old not in text:
    raise RuntimeError("source-fidelity config marker not found")
else:
    text = text.replace(old, new, 1)
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")
    print("removed unrelated SMA-offset keys from source-fidelity config")
