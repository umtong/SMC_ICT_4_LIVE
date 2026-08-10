"""Focused rerun of the fixed V15 lifecycle.

Only the unmodified source and the depth-confirmed three-minute acceptance
lifecycle are compared.  v1 already proved that flow-only/depth/strict variants
were implementation-null, so rerunning all three would waste compute.  The
existing sequential runner still consumes fresh-B only after implementation and
fresh-A have been completed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_PATH = Path(__file__).resolve().with_name("run_zaratustra_v15_lifecycle.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_lifecycle_runner_v1", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load lifecycle runner v1: {_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

_BASE.VARIANTS = {
    "source": {
        "v15_lifecycle_mode": "source",
        "v15_acceptance_deadline_minutes": 3,
    },
    "accept_depth_3": {
        "v15_lifecycle_mode": "accept_depth_3",
        "v15_acceptance_deadline_minutes": 3,
    },
}

if __name__ == "__main__":
    raise SystemExit(_BASE.main())
