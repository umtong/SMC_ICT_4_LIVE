#!/usr/bin/env python3
"""Progressive, nonbinary gate for the frozen V45/V44 BTC candidate.

The strategy, signals, costs, targets, fills and risk are unchanged. Only the
research gate is corrected: a positive, adequately sampled first week opens the
next predeclared week, while the 1% geometric-growth objective is judged on the
three-week compounded NAV rather than imposed on every individual week.
"""
from __future__ import annotations

import math

import run_v45_untouched_btc as base


base.GATE = {
    "trades": 3,
    "active_days": 2,
    "win_rate": 0.50,
    "daily": 0.0,
}
base.TARGET_DAILY = 0.01


def progressive_aggregate(rows: list[dict]) -> dict:
    returns = [float(row.get("total_return") or 0.0) for row in rows]
    compounded = math.prod(1.0 + value for value in returns) - 1.0 if rows else 0.0
    days = 7 * len(rows)
    daily = (
        (1.0 + compounded) ** (1.0 / days) - 1.0
        if days and compounded > -1.0
        else -1.0
    )
    trades = sum(int(row.get("trades") or 0) for row in rows)
    wins = sum(int(row.get("wins") or 0) for row in rows)
    active = sum(int(row.get("active_days") or 0) for row in rows)
    win_rate = wins / trades if trades else 0.0
    preliminary = [bool(row.get("candidate_pass")) for row in rows]
    return {
        "weeks": len(rows),
        "calendar_days": days,
        "compounded_return": compounded,
        "geometric_daily_growth": daily,
        "target_geometric_daily_growth": base.TARGET_DAILY,
        "trades": trades,
        "wins": wins,
        "win_rate": win_rate,
        "active_days": active,
        "preliminary_week_passes": preliminary,
        "passed": bool(
            len(rows) == 3
            and all(preliminary)
            and all(bool(row.get("risk_pass")) for row in rows)
            and daily >= base.TARGET_DAILY
            and trades >= 9
            and active >= 6
            and win_rate >= 0.55
        ),
    }


base.aggregate = progressive_aggregate

if __name__ == "__main__":
    base.main()
