#!/usr/bin/env python3
"""Int64-safe entry point for the mark/index overshoot diagnostic.

No market rule changes.  It replaces only five-minute pivot timestamp recovery:
`iterrows`/mixed Series may represent nanosecond integers as float64, so the
nearest exact value from the original int64 timestamp column is restored and the
confirmation timestamp is rebuilt from the exact bar position.
"""
from __future__ import annotations

from collections import defaultdict

import diagnose_mark_index_overshoot_5m as base


def _int64_safe_confirmations(
    trade_five,
    mark_five,
    index_five,
    *,
    radius: int,
):
    if not trade_five["timestamp_ns"].equals(mark_five["timestamp_ns"]):
        raise RuntimeError("five-minute trade and mark timestamps differ")
    if not trade_five["timestamp_ns"].equals(index_five["timestamp_ns"]):
        raise RuntimeError("five-minute trade and index timestamps differ")
    exact = [int(value) for value in trade_five["timestamp_ns"].astype("int64")]
    output = defaultdict(list)
    raw = base.pivot_confirmations(
        trade_five,
        radius=radius,
        prefix="C5",
    )
    for pools in raw.values():
        for pool in pools:
            approximate = int(pool.pivot_ts_ns)
            position = min(
                range(len(exact)),
                key=lambda index: abs(exact[index] - approximate),
            )
            if abs(exact[position] - approximate) > 4096:
                raise RuntimeError(
                    "cannot recover exact five-minute pivot timestamp: "
                    f"approximate={approximate}, nearest={exact[position]}"
                )
            confirmation_position = position + radius
            if confirmation_position >= len(exact):
                raise RuntimeError("pivot confirmation position is outside data")
            pivot_ns = exact[position]
            confirmation_ns = exact[confirmation_position]
            mark_row = mark_five.iloc[position]
            index_row = index_five.iloc[position]
            output[confirmation_ns].append(
                base.MarkIndexPool(
                    pool_id=f"MIX5:{pool.pool_id}",
                    side=pool.side,
                    trade_level=float(pool.level),
                    mark_level=(
                        float(mark_row["mark_high"])
                        if pool.side == "UPPER"
                        else float(mark_row["mark_low"])
                    ),
                    index_level=(
                        float(index_row["index_high"])
                        if pool.side == "UPPER"
                        else float(index_row["index_low"])
                    ),
                    pivot_ts_ns=pivot_ns,
                    confirmed_ts_ns=confirmation_ns,
                )
            )
    return dict(output)


base.mark_index_pool_confirmations = _int64_safe_confirmations


if __name__ == "__main__":
    raise SystemExit(base.main())
