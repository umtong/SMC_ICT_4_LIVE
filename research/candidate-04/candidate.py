#!/usr/bin/env python3
"""Deterministically load the reviewable candidate source fragments."""
from pathlib import Path

_SOURCE_DIR = Path(__file__).with_name("candidate_source")
_SOURCE = "".join(path.read_text(encoding="utf-8") for path in sorted(_SOURCE_DIR.glob("*.pyfrag")))
exec(compile(_SOURCE, str(Path(__file__).with_name("candidate.py")), "exec"), globals(), globals())
