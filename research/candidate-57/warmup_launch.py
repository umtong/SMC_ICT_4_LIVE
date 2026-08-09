#!/usr/bin/env python3
"""Integrity-checked Candidate 57 warmup-aware NautilusTrader launcher."""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

EXPECTED_SOURCE_SHA256 = "d4b29f1a337705058a0bde5a8e5da6934d6848a3587db726292557326ad1d112"
PAYLOAD = Path(__file__).with_name("warmup_launch_v1.py.gz.b64")
source_bytes = gzip.decompress(
    base64.b64decode(PAYLOAD.read_text(encoding="ascii").strip(), validate=True)
)
actual = hashlib.sha256(source_bytes).hexdigest()
if actual != EXPECTED_SOURCE_SHA256:
    raise RuntimeError(f"Candidate 57 warmup payload hash mismatch: {actual}")
exec(compile(source_bytes.decode("utf-8"), str(PAYLOAD), "exec"), globals(), globals())
