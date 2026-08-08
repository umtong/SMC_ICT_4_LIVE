#!/usr/bin/env python3
"""Process-isolated staged runner for protected-swing MSS/retest."""
from __future__ import annotations

from pathlib import Path

import staged as _base


_original_run_child = _base._run_child


def _run_structural_child(**kwargs):
    kwargs["candidate_script"] = (
        Path(__file__).resolve().parent
        / "candidate_structural_mss.py"
    )
    return _original_run_child(**kwargs)


_base._run_child = _run_structural_child


if __name__ == "__main__":
    raise SystemExit(_base.main())
