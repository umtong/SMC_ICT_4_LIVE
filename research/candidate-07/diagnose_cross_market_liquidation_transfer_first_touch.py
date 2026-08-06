#!/usr/bin/env python3
"""First-touch-safe entry point for the cross-market transfer diagnostic.

The base classifier validates minimum penetration after contact.  This wrapper
ensures the contact detector returns every raw crossing (zero minimum distance),
so a pool is consumed on its literal first causal touch even when that first
contact later fails the penetration or cross-market route tests.
"""
from __future__ import annotations

import diagnose_cross_market_liquidation_transfer as base

_original_contacted = base._contacted


def _first_touch_contacted(pools, row, previous_close, *, minimum_penetration):
    del minimum_penetration
    return _original_contacted(
        pools,
        row,
        previous_close,
        minimum_penetration=0.0,
    )


base._contacted = _first_touch_contacted


if __name__ == "__main__":
    raise SystemExit(base.main())
