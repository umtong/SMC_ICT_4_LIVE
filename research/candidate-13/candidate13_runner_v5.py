#!/usr/bin/env python3
"""Candidate 13 V5 development-protocol wrapper."""
from __future__ import annotations

from pathlib import Path

import candidate13_runner as _base
from run_multisymbol_session_v5 import run as _v5_run

_ORIGINAL_LOAD_OBJECT = _base.load_object


def _load_object(path: Path) -> dict[str, object]:
    payload = _ORIGINAL_LOAD_OBJECT(path)
    if Path(path).name == "base_config.json":
        payload["session_i7"] = _ORIGINAL_LOAD_OBJECT(
            _base.ROOT / "session_i7_config.json",
        )
    return payload


# candidate13_runner.execute resolves these module globals at call time.
_base.run = _v5_run
_base.load_object = _load_object

execute = _base.execute
main = _base.main


if __name__ == "__main__":
    raise SystemExit(main())
