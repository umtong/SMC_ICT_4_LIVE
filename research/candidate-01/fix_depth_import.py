#!/usr/bin/env python3
"""Add the diagnostic CLI import without changing research logic."""

from pathlib import Path

path = Path(__file__).with_name("depth_diagnostics.py")
text = path.read_text(encoding="utf-8")
old = "from __future__ import annotations\n\nfrom concurrent.futures"
new = "from __future__ import annotations\n\nimport argparse\nfrom concurrent.futures"
if text.count(old) != 1:
    raise SystemExit(f"expected one import insertion point, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
