#!/usr/bin/env python3
"""Integrity-checked loader for the Candidate 57 adaptive campaign.

The payload is stored as compressed text only to keep the GitHub contents write
small. It expands to ordinary Python, is compiled before execution, and its
source hash is fixed here for reproducibility.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from pathlib import Path

EXPECTED_SOURCE_SHA256 = "1d797d5ff3e656f614ef13e520b422df81bdca97b2410f4da0ccaff7b1973874"
PAYLOAD = Path(__file__).with_name("campaign_v2.py.gz.b64")
compressed = base64.b64decode(PAYLOAD.read_text(encoding="ascii").strip(), validate=True)
source_bytes = gzip.decompress(compressed)
actual = hashlib.sha256(source_bytes).hexdigest()
if actual != EXPECTED_SOURCE_SHA256:
    raise RuntimeError(f"Candidate 57 campaign payload hash mismatch: {actual}")
source = source_bytes.decode("utf-8")
exec(compile(source, str(PAYLOAD), "exec"), globals(), globals())
