#!/usr/bin/env python3
"""Reject depth snapshots whose implied price is inconsistent with the kline."""
from pathlib import Path

path = Path(__file__).resolve().parent / "data_loader.py"
text = path.read_text(encoding="utf-8")
if "MAX_DEPTH_IMPLIED_PRICE_DEVIATION = 0.10" in text:
    raise SystemExit(0)
text = text.replace(
    "MINIMUM_DEPTH_COVERAGE = 0.95\n",
    "MINIMUM_DEPTH_COVERAGE = 0.95\nMAX_DEPTH_IMPLIED_PRICE_DEVIATION = 0.10\n",
    1,
)
old = '''            if active is None or ts_ns - active.observed_ns > MAX_DEPTH_AGE_NS:
                values = (None, None, None, None, None)
            else:
                values = (
                    active.bid_depth,
                    active.ask_depth,
                    active.bid_notional,
                    active.ask_notional,
                    active.observed_ns,
                )
'''
new = '''            if active is None or ts_ns - active.observed_ns > MAX_DEPTH_AGE_NS:
                values = (None, None, None, None, None)
            else:
                implied_bid = active.bid_notional / active.bid_depth
                implied_ask = active.ask_notional / active.ask_depth
                price_misaligned = max(
                    abs(implied_bid / c - 1.0),
                    abs(implied_ask / c - 1.0),
                ) > MAX_DEPTH_IMPLIED_PRICE_DEVIATION
                if price_misaligned:
                    values = (None, None, None, None, None)
                else:
                    values = (
                        active.bid_depth,
                        active.ask_depth,
                        active.bid_notional,
                        active.ask_notional,
                        active.observed_ns,
                    )
'''
if old not in text:
    raise RuntimeError("depth implied-price validation contract not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
