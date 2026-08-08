#!/usr/bin/env python3
"""Implementation-only bridge from installed Candidate 05 wrappers to features_base."""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
marker = '    _base.download_checked = globals()["download_checked"]\n'
replacement = (
    '    _base.read_kline = globals()["read_kline"]\n'
    '    _base.download_checked = globals()["download_checked"]\n'
)
if replacement in text:
    raise SystemExit(0)
if marker not in text:
    raise RuntimeError("v33 dynamic wrapper bridge insertion point not found")
path.write_text(text.replace(marker, replacement, 1), encoding="utf-8")
