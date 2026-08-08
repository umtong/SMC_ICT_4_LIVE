#!/usr/bin/env python3
"""Bind V41 research-event observation to the current completed strategy bar.

The OI source timestamp remains in event details and still determines the five-
minute delay.  This patch changes only event-log ordering: the state becomes
observable to the strategy at the current completed bar, not at the exchange's
interval label inside that bar.
"""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = "V41_EVENT_OBSERVED_ON_CURRENT_COMPLETED_BAR"
if marker in text:
    raise SystemExit(0)
old = (
    "            event_end_ns,\n"
    "            metrics_observed_ns,\n"
    "            \"FIRST_RETEST_ARMED\",\n"
)
new = (
    "            event_end_ns,\n"
    "            ts_event,  # V41_EVENT_OBSERVED_ON_CURRENT_COMPLETED_BAR\n"
    "            \"FIRST_RETEST_ARMED\",\n"
)
if old not in text:
    raise RuntimeError("V41 transition observation-time block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
