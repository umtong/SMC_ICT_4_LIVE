#!/usr/bin/env python3
from __future__ import annotations

import gate_re1_candidates as _gate

_gate.VARIANT_PATTERNS = (
    ("mechanism-combo-continuation-invalidation", "mechanism-continuation-invalidation"),
    ("mechanism-combo-continuation-5m", "mechanism-continuation-5m"),
    ("mechanism-combo-rejection-invalidation", "mechanism-rejection-invalidation"),
    ("mechanism-combo-rejection-5m", "mechanism-rejection-5m"),
    ("mechanism-combo-easychart-core-invalidation", "mechanism-easychart-core-invalidation"),
    ("mechanism-combo-easychart-core-5m", "mechanism-easychart-core-5m"),
    ("mechanism-combo-liquidity-reversal-invalidation", "mechanism-liquidity-reversal-invalidation"),
    ("mechanism-combo-liquidity-reversal-5m", "mechanism-liquidity-reversal-5m"),
    ("mechanism-diagonal-acceptance", "mechanism-diagonal-acceptance"),
    ("mechanism-diagonal-rejection", "mechanism-diagonal-rejection"),
    ("mechanism-horizontal-acceptance", "mechanism-horizontal-acceptance"),
    ("mechanism-horizontal-rejection", "mechanism-horizontal-rejection"),
    ("mechanism-horizontal-rotation", "mechanism-horizontal-rotation"),
    ("mechanism-major-liquidity-rejection", "mechanism-major-liquidity-rejection"),
    ("mechanism-decision-ob-rejection", "mechanism-decision-ob-rejection"),
    ("mechanism-decision-ob-rotation", "mechanism-decision-ob-rotation"),
    ("decision-area-v2-invalidation", "decision-area-v2-invalidation"),
    ("decision-area-v2-5m", "decision-area-v2-5m"),
    ("displacement-v2-invalidation", "displacement-v2-invalidation"),
    ("displacement-v2-5m", "displacement-v2-5m"),
    ("geometry-v2-invalidation", "geometry-v2-invalidation"),
    ("geometry-v2-static", "geometry-v2-static"),
    ("geometry-v2-5m", "geometry-v2-5m"),
    ("zone-v2-invalidation", "zone-v2-invalidation"),
    ("zone-v2-static", "zone-v2-static"),
    ("zone-v2-5m", "zone-v2-5m"),
    ("complete-breadth", "complete-breadth"),
    ("validated-structure", "validated-structure"),
    ("combo-diagonal-horizontal", "validated-diagonal-horizontal"),
    ("combo-diagonal-liquidity", "validated-diagonal-liquidity"),
    ("combo-horizontal-liquidity", "validated-horizontal-liquidity"),
    ("family-major-liquidity", "validated-major-liquidity"),
    ("family-horizontal", "validated-horizontal"),
    ("family-diagonal", "validated-diagonal"),
    ("validated-static", "validated-static"),
    ("validated-5m", "validated-5m"),
    ("validated-btc", "validated-btc"),
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
