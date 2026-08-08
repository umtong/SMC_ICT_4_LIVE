#!/usr/bin/env python3
"""Process-isolated staged validation for clock-alert initiative execution."""
from __future__ import annotations

from pathlib import Path

import staged as _base


_original_run_child = _base._run_child


def _run_clock_child(**kwargs):
    kwargs["candidate_script"] = (
        Path(__file__).resolve().parent
        / "candidate_initiative_auction_clock.py"
    )
    return _original_run_child(**kwargs)


_base._run_child = _run_clock_child


if __name__ == "__main__":
    raise SystemExit(_base.main())
