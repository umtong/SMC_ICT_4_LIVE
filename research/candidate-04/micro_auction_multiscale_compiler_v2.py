#!/usr/bin/env python3
"""Run the unchanged completed micro-auction state machine at 15m, 30m and 60m."""
from __future__ import annotations

import micro_auction_multiscale_compiler as base


base.SCALES = (60, 30, 15)


if __name__ == "__main__":
    base.main()
