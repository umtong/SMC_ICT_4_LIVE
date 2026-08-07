#!/usr/bin/env python3
"""Run the frozen causal target registry for one allowed experiment symbol.

The target registry itself is unchanged. This wrapper replaces only its
BTC-first Config loader with the V48 adapter which sends an otherwise identical
BTC clone through the original validation before restoring the allowed symbol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import causal_target_registry_enricher as base
from cross_market_information_transfer_compiler_v2 import (
    load_allowed_symbol_config,
)


def _load(cls: type[Any], path: Path):
    del cls
    return load_allowed_symbol_config(path)


base.v22.Config.load = classmethod(_load)


if __name__ == "__main__":
    base.main()
