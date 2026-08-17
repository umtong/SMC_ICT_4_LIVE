#!/usr/bin/env python3
"""Reconstruct and execute the direct causal state-action research policy."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SOURCE = "".join(
    path.read_text(encoding="utf-8")
    for path in sorted((_ROOT / "_source").glob("part_*.pyfrag"))
)
exec(compile(_SOURCE, str(_ROOT / "state_policy.py"), "exec"), globals(), globals())
