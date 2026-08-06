"""Conditionally install candidate-10 v20.2 in immutable v20 workers.

This file is intentionally inert for every other candidate-10 workflow.  The
live impact overlay is installed only after the verified v20 source has been
materialized at /tmp/candidate10-v20 by a v20 runner.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
import zipfile


_SOURCE = Path("/tmp/candidate10-v20/c10_liquidation_state.py")
_ARCHIVE = Path(__file__).resolve().with_name("v20_2_patch.zip")
_EXPECTED = "ad93590e75c6c68f0de2daaae1525bc5aa4503afa91795e829e787c58fb1dc6f"
_DESTINATION = Path("/tmp/candidate10-v20-patch-ad93590e")

if _SOURCE.exists() and _ARCHIVE.exists():
    actual = sha256(_ARCHIVE.read_bytes()).hexdigest()
    if actual != _EXPECTED:
        raise RuntimeError(
            f"candidate-10 v20.2 patch SHA256 mismatch: {actual} != {_EXPECTED}",
        )
    marker = _DESTINATION / ".verified"
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != actual:
        _DESTINATION.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(_ARCHIVE) as archive:
            bad = archive.testzip()
            if bad is not None:
                raise RuntimeError(f"corrupt candidate-10 v20.2 member: {bad}")
            archive.extractall(_DESTINATION)
        marker.write_text(actual + "\n", encoding="utf-8")
    patch_path = str(_DESTINATION)
    if patch_path not in sys.path:
        sys.path.insert(0, patch_path)
    from v20_impact_control import install

    install()
