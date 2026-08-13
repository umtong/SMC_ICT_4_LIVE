#!/usr/bin/env python3
from __future__ import annotations

import gate_re1_candidates as _gate

_gate.VARIANT_PATTERNS = (
    ("displacement-invalidation", "displacement-invalidation"),
    ("displacement-5m", "displacement-5m"),
    ("decision-area-invalidation", "decision-area-invalidation"),
    ("geometry-invalidation", "geometry-invalidation"),
    ("zone-invalidation", "zone-invalidation"),
    ("decision-area", "decision-area"),
    ("geometry-static", "geometry-static"),
    ("geometry-5m", "geometry-5m"),
    ("validated-static", "validated-static"),
    ("validated-5m", "validated-5m"),
    ("zone-static", "zone-static"),
    ("zone-5m", "zone-5m"),
    ("zone-1m", "zone-1m"),
    ("geometry-btc", "geometry-btc"),
    ("validated-btc", "validated-btc"),
    ("complete-breadth", "complete-breadth"),
    ("validated-structure", "validated-structure"),
    ("combo-diagonal-horizontal", "validated-diagonal-horizontal"),
    ("combo-diagonal-liquidity", "validated-diagonal-liquidity"),
    ("combo-horizontal-liquidity", "validated-horizontal-liquidity"),
    ("family-major-liquidity", "validated-major-liquidity"),
    ("family-horizontal", "validated-horizontal"),
    ("family-diagonal", "validated-diagonal"),
    ("geometry", "geometry"),
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
