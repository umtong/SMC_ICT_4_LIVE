#!/usr/bin/env python3
"""Compatibility entry point for the frozen Candidate ML-k V3 policy.

The original fresh-window workflow was registered against this path before V3
was frozen.  Keeping the path stable lets that existing run evaluate the exact
V3 source rather than an obsolete V1 router.  All implementation and thresholds
remain in ``candidate-ml-k-control-transfer-v3/control_transfer_router_v3.py``.
"""
from __future__ import annotations

from pathlib import Path
import sys

V3_DIR = Path(__file__).resolve().parents[1] / "candidate-ml-k-control-transfer-v3"
if str(V3_DIR) not in sys.path:
    sys.path.insert(0, str(V3_DIR))

from control_transfer_router_v3 import (  # noqa: E402,F401
    SCENARIO_PRIORITY,
    THRESHOLDS,
    choose_public_plans,
    label_scenarios,
    main,
    run,
    route_account,
    scenario_masks,
)


if __name__ == "__main__":
    main()
