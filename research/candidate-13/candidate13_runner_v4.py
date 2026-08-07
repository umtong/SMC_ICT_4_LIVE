#!/usr/bin/env python3
"""Candidate 13 v4 frozen-protocol wrapper."""
from __future__ import annotations

import candidate13_runner as _base
from run_leadership_scdam_v4 import run as _v4_run

# candidate13_runner.execute resolves its module-global `run` at call time.
_base.run = _v4_run

execute = _base.execute
load_object = _base.load_object
source_lock = _base.source_lock
main = _base.main


if __name__ == "__main__":
    raise SystemExit(main())
