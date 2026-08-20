#!/usr/bin/env python3
"""Economic first-return router with causal pending-order replacement.

A filled position remains untouched until TP or SL.  Before fill, however, a newly
observed independent plan may replace the pending order when its conservative expected
log-growth rate is strictly higher.  This is a trade-selection decision, not a new exit:
the old unfilled instruction is canceled and the account still has one global slot.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import sequential_commitment_router_economic as economic

EPS = 1e-12
_BASE_ROUTE = economic.route


def route(frame: pd.DataFrame):
    best = frame.sort_values(
        ["period", "state_id", "expected_arm_rate", "expected_arm_log", "planned_target_net_r"],
        ascending=[True, True, False, False, False],
    ).groupby(["period", "state_id"], as_index=False).first()
    best = best[
        (best.expected_arm_log > 0.0)
        & (best.expected_arm_rate > 0.0)
        & (best.stopping_advantage > 0.0)
    ].sort_values(
        ["period", "order_time_ns", "expected_arm_rate", "stopping_advantage", "state_id"],
        ascending=[True, True, False, False, True],
    )
    selected = []
    replaced = []
    for period, group in best.groupby("period", sort=True):
        active = None
        used = set()
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            timestamp = float(timestamp)
            candidate_pool = simultaneous[~simultaneous.episode_id.astype(str).isin(used)]
            if candidate_pool.empty:
                continue
            candidate = candidate_pool.iloc[0]
            if active is not None:
                terminal = float(active.terminal_ns)
                fill_time = float(active.fill_time_ns) if pd.notna(active.fill_time_ns) else np.inf
                if timestamp >= terminal:
                    selected.append(active)
                    used.add(str(active.episode_id))
                    active = None
                elif fill_time <= timestamp:
                    # The position already exists; only its declared TP/SL may end it.
                    continue
                else:
                    # Still pending.  The same episode is not churned; an independent
                    # plan replaces it only when its conservative account-time value is higher.
                    if (
                        str(candidate.episode_id) != str(active.episode_id)
                        and float(candidate.expected_arm_rate) > float(active.expected_arm_rate) + EPS
                    ):
                        old = active.copy()
                        old["replacement_time_ns"] = timestamp
                        old["replacement_reason"] = "HIGHER_CONSERVATIVE_LOG_GROWTH_RATE"
                        replaced.append(old)
                        used.add(str(active.episode_id))
                        active = candidate
                    continue
            if active is None:
                active = candidate
        if active is not None:
            selected.append(active)
            used.add(str(active.episode_id))
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else best.iloc[:0].copy()
    trades = orders[orders.resolved & orders.net_r.notna()].copy().reset_index(drop=True)
    nav = peak = 1.0
    maximum_drawdown = 0.0
    for result in trades.net_r.astype(float):
        nav *= max(EPS, 1.0 + economic.RISK * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    days = 7 * int(frame.period.nunique())
    summary = {
        "selected_orders": int(len(orders)), "replaced_pending_orders": int(len(replaced)),
        "closed_trades": int(len(trades)), "periods": int(frame.period.nunique()),
        "calendar_days": int(days), "trades_per_day": float(len(trades) / max(days, 1)),
        "target_first_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "mean_planned_gross_rr": float(trades.gross_rr.mean()) if len(trades) else None,
        "median_hold_minutes": float(trades.holding_minutes.median()) if len(trades) else None,
        "mean_hold_minutes": float(trades.holding_minutes.mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav), "maximum_drawdown": float(maximum_drawdown),
        "by_period": trades.groupby("period").agg(
            trades=("net_r", "size"), target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
        "by_family": trades.groupby("family").agg(
            trades=("net_r", "size"), target_first_rate=("win", "mean"),
            mean_net_r=("net_r", "mean"),
        ).reset_index().to_dict("records") if len(trades) else [],
    }
    economic._replacement_orders = pd.DataFrame(replaced)
    return orders, trades, summary


economic.route = route

if __name__ == "__main__":
    economic.main()
    output = getattr(economic, "_replacement_orders", None)
