#!/usr/bin/env python3
"""Promotion gate including validated-structure family ablations."""
from __future__ import annotations

import gate_re1_candidates as _gate

# Longest/most specific strings must precede their components.
_gate.VARIANT_PATTERNS = (
    ("complete-breadth", "complete-breadth"),
    ("validated-structure", "validated-structure"),
    ("combo-diagonal-horizontal", "validated-diagonal-horizontal"),
    ("combo-diagonal-liquidity", "validated-diagonal-liquidity"),
    ("combo-horizontal-liquidity", "validated-horizontal-liquidity"),
    ("family-major-liquidity", "validated-major-liquidity"),
    ("family-horizontal", "validated-horizontal"),
    ("family-diagonal", "validated-diagonal"),
    ("adjacent", "adjacent"),
    ("liquidity-location", "liquidity-location"),
    ("liquidity-local", "liquidity-local"),
    ("local-alignment", "local-alignment"),
    ("complete", "complete"),
    ("impulse", "impulse"),
    ("location", "location"),
)

if __name__ == "__main__":
    _gate.main()
