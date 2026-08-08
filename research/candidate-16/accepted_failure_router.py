"""Load Candidate 16 v2 pure router from its compressed source payload.

Acceptance, failure, and
    entry therefore cannot be asserted by the same completed bar.
"""
from __future__ import annotations

from pathlib import Path
import zlib

_payload = Path(__file__).with_name("accepted_failure_router.py.zlib")
_source = zlib.decompress(_payload.read_bytes()).decode("utf-8")
exec(compile(_source, str(_payload) + "::source", "exec"), globals(), globals())
