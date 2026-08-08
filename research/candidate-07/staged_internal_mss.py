#!/usr/bin/env python3
"""Process-isolated staged validation for the internal-boundary MSS candidate."""
from __future__ import annotations

from pathlib import Path

import staged as _base


_original_run_child = _base._run_child


def _run_internal_mss_child(**kwargs):
    kwargs["candidate_script"] = (
        Path(__file__).resolve().parent / "candidate_internal_mss.py"
    )
    return _original_run_child(**kwargs)


_base._run_child = _run_internal_mss_child


if __name__ == "__main__":
    raise SystemExit(_base.main())
