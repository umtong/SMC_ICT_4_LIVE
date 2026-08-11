#!/usr/bin/env python3
"""Run the frozen gap-aware Picasso anatomy without monkey-patch recursion.

The v64 and v64-gapfixed strategy, data, period, gap, episode, intrabar and
arbitration contracts are unchanged.  The prior gap-aware run failed before
producing strategy results because it replaced ``V64._features`` and then the
replacement recursively called ``V64._features`` on a segment which no longer
contained ``segment_id``.  This wrapper saves the original v64 feature and
signal functions before installing the segmented adapters.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).with_name("picasso_precedence_anatomy_v64_gapfixed.py")
text = SOURCE.read_text(encoding="utf-8")
load_anchor = "V64 = _load_v64()\n"
load_replacement = (
    "V64 = _load_v64()\n"
    "ORIGINAL_FEATURES = V64._features\n"
    "ORIGINAL_SIGNALS = V64._signals\n"
)
if text.count(load_anchor) != 1:
    raise RuntimeError("v64 gap contract changed; module anchor missing")
text = text.replace(load_anchor, load_replacement, 1)
feature_call = "enriched = V64._features(source)"
if text.count(feature_call) != 1:
    raise RuntimeError("v64 gap contract changed; feature call missing")
text = text.replace(feature_call, "enriched = ORIGINAL_FEATURES(source)", 1)
signal_call = "item = V64._signals("
if text.count(signal_call) != 1:
    raise RuntimeError("v64 gap contract changed; signal call missing")
text = text.replace(signal_call, "item = ORIGINAL_SIGNALS(", 1)
exec(
    compile(text, str(SOURCE), "exec"),
    {
        "__name__": "__main__",
        "__file__": str(SOURCE),
        "__package__": None,
    },
)
