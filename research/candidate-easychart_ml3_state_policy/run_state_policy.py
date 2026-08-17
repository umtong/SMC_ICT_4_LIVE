#!/usr/bin/env python3
"""Reconstruct and execute the direct causal state-action research policy."""
from pathlib import Path
import lzma

_ROOT = Path(__file__).resolve().parent
_COMPRESSED = b"".join(path.read_bytes() for path in sorted((_ROOT / "_exact_xz").glob("chunk_*")))
_SOURCE = lzma.decompress(_COMPRESSED)
exec(compile(_SOURCE, str(_ROOT / "state_policy.py"), "exec"), globals(), globals())
