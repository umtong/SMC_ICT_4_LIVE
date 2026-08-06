#!/usr/bin/env python3
"""Single-variable ablation of mark penetration at contact.

All data, five-minute pools, OI-release and aggressor-flow requirements, index
non-transfer, mark/index premium expansion, prompt trade/mark reclaim,
structural stops, liquidity targets, one-slot blocking and exit-safe accounting
remain unchanged.  The only removed condition is that mark-price penetration at
the contact must be at least a fixed fraction of traded-price penetration.
"""
from __future__ import annotations

import diagnose_mark_index_overshoot_5m as base
import diagnose_mark_index_overshoot_5m_int64  # installs int64-safe confirmations


def _without_contact_mark_penetration(row, pool, *, logic):
    trade_atr = float(row["atr"])
    mark_atr = float(row["mark_atr"])
    index_atr = float(row["index_atr"])
    if pool.side == "UPPER":
        trade_pen = (float(row["high"]) - pool.trade_level) / trade_atr
        mark_pen = max(0.0, (float(row["mark_high"]) - pool.mark_level) / mark_atr)
        index_pen = max(0.0, (float(row["index_high"]) - pool.index_level) / index_atr)
        directional_premium_change = float(row["premium_change"])
        index_inside = float(row["index_close"]) <= pool.index_level
        attack_direction = "LONG"
        trade_direction = "SHORT"
    else:
        trade_pen = (pool.trade_level - float(row["low"])) / trade_atr
        mark_pen = max(0.0, (pool.mark_level - float(row["mark_low"])) / mark_atr)
        index_pen = max(0.0, (pool.index_level - float(row["index_low"])) / index_atr)
        directional_premium_change = -float(row["premium_change"])
        index_inside = float(row["index_close"]) >= pool.index_level
        attack_direction = "SHORT"
        trade_direction = "LONG"
    if not logic.contact_min_atr <= trade_pen <= logic.contact_max_atr:
        return None
    mark_ratio = mark_pen / max(trade_pen, 1e-12)
    index_ratio = index_pen / max(trade_pen, 1e-12)
    if not (
        index_ratio <= logic.maximum_index_transfer_ratio
        and index_inside
        and directional_premium_change > 0.0
        and float(row["premium_change_rank"]) >= logic.minimum_premium_change_rank
    ):
        return None
    return {
        "attack_direction": attack_direction,
        "trade_direction": trade_direction,
        "trade_penetration_atr": trade_pen,
        "mark_penetration_atr": mark_pen,
        "index_penetration_atr": index_pen,
        "mark_transfer_ratio": mark_ratio,
        "index_transfer_ratio": index_ratio,
        "premium_change_rank": float(row["premium_change_rank"]),
        "directional_premium_change_bps": directional_premium_change * 10_000.0,
        "ablation_removed_contact_mark_penetration": True,
    }


base._qualify_contact = _without_contact_mark_penetration


if __name__ == "__main__":
    raise SystemExit(base.main())
