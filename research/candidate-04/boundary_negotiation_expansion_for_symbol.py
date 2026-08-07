#!/usr/bin/env python3
"""Run the frozen V31 parent compiler for one allowed experiment symbol.

Only BTC-first loader guards are adapted.  All market-state, percentile,
entry, invalidation and scenario relations remain the frozen V31 logic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import boundary_negotiation_expansion_compiler as base
from cross_market_information_transfer_compiler_v2 import (
    load_allowed_symbol_config,
    load_allowed_symbol_rich,
)


def _load(cls: type[Any], path: Path):
    del cls
    return load_allowed_symbol_config(path)


base.v22.Config.load = classmethod(_load)
base.v22.base.load_rich = load_allowed_symbol_rich


if __name__ == "__main__":
    base.main()
