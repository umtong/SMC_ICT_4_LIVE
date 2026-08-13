#!/usr/bin/env python3
from __future__ import annotations

import gate_re1_candidates as _gate

_gate.VARIANT_PATTERNS = (
    ("complete-breadth", "complete-breadth"),
    ("adjacent", "adjacent"),
    *tuple(
        item
        for item in _gate.VARIANT_PATTERNS
        if item[1] not in {"complete-breadth", "adjacent"}
    ),
)

if __name__ == "__main__":
    _gate.main()
