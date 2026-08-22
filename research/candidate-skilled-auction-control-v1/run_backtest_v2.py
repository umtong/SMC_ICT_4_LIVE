#!/usr/bin/env python3
"""Run structural auction control v2 through the inherited Nautilus harness."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for path in (
    HERE,
    HERE.parent / "candidate-easychart_re1",
    HERE.parent / "candidate-easychart-v5",
    HERE.parent / "candidate-easychart-v3",
):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

# Preserve every execution/accounting patch already used by v1, but replace its
# bundle before importing the entrypoint so the actual harness instantiates v2.
from structural_auction_control_v2 import StructuralAuctionControlV2Bundle
import skilled_auction_control_v1 as v1_policy

v1_policy.SkilledAuctionControlV1Bundle = StructuralAuctionControlV2Bundle
v1_policy.MultiScaleScenarioBundle = StructuralAuctionControlV2Bundle

import run_backtest as inherited


if __name__ == "__main__":
    inherited.main()
