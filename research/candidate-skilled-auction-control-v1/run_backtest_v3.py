#!/usr/bin/env python3
"""Execute structural auction control v3 through the inherited v1 harness."""
from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

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

from structural_auction_control_v3 import StructuralAuctionControlV3Bundle
import skilled_auction_control_v1 as v1_policy

v1_policy.SkilledAuctionControlV1Bundle = StructuralAuctionControlV3Bundle
v1_policy.MultiScaleScenarioBundle = StructuralAuctionControlV3Bundle

import run_backtest as inherited


def main() -> None:
    direct = getattr(inherited, "main", None)
    if callable(direct):
        direct()
        return
    preferred = getattr(inherited, "runner", None)
    if isinstance(preferred, ModuleType) and callable(getattr(preferred, "main", None)):
        preferred.main()
        return
    for value in vars(inherited).values():
        if isinstance(value, ModuleType) and callable(getattr(value, "main", None)):
            value.main()
            return
    raise RuntimeError("the inherited v1 backtest entrypoint exposes no callable main")


if __name__ == "__main__":
    main()
