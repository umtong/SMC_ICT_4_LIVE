#!/usr/bin/env python3
"""Load the frozen candidate-04 v5 source fragments as one module."""
from pathlib import Path

_ROOT = Path(__file__).with_name("swing_displacement_source")
_SOURCE = "".join(path.read_text(encoding="utf-8") for path in sorted(_ROOT.glob("*.pyfrag")))
if not _SOURCE:
    raise RuntimeError(f"candidate source fragments not found under {_ROOT}")
exec(compile(_SOURCE, str(Path(__file__)), "exec"), globals(), globals())
