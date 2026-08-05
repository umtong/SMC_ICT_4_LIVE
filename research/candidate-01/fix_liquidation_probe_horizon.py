#!/usr/bin/env python3
"""Use the minimum valid inactive-machine horizon in liquidation probes."""

from pathlib import Path

path = Path(__file__).with_name("liquidation_exhaustion_probe.py")
text = path.read_text(encoding="utf-8")
old = 'variant=Variant(rule, ("BTCUSDT",), (SHOCK_BARS,)),'
new = 'variant=Variant(rule, ("BTCUSDT",), (60,)),'
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one liquidation variant horizon match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
