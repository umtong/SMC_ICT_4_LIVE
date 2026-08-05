#!/usr/bin/env python3
"""Load the auditable event-time candidate from ordered source fragments."""
from pathlib import Path

_SOURCE_ROOT = Path(__file__).with_name("event_time_source")
_SOURCE_FILES = sorted(_SOURCE_ROOT.glob("*.pyfrag"))
if not _SOURCE_FILES:
    raise RuntimeError(f"no event-time source fragments in {_SOURCE_ROOT}")
_SOURCE = "".join(path.read_text(encoding="utf-8") for path in _SOURCE_FILES)
exec(compile(_SOURCE, str(_SOURCE_ROOT), "exec"), globals(), globals())
