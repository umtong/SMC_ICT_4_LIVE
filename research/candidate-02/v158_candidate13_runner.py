#!/usr/bin/env python3
"""Candidate-02 V158 frozen-protocol wrapper."""
from __future__ import annotations

import candidate13_runner as _base
from v158_run_leadership_scdam import run as _v158_run

_base.run = _v158_run

execute = _base.execute
load_object = _base.load_object
source_lock = _base.source_lock
main = _base.main


if __name__ == "__main__":
    raise SystemExit(main())
