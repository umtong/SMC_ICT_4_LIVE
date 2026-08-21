#!/usr/bin/env python3
"""Refine causal v5 first-return selection without changing event or barrier labels."""
from __future__ import annotations

import numpy as np
import pandas as pd

import causal_first_return_policy as core


def select_policy(plans: pd.DataFrame) -> pd.DataFrame:
    frame = plans.copy()
    proximal = frame[
        (frame.family == "FAILED_FIRST_RETURN")
        & (frame.price_kind == "ZONE_PROXIMAL_LIMIT")
        & np.isclose(frame.gross_rr, 1.25)
    ].copy()
    proximal["strong_location"] = (
        proximal.setup_kind.isin(["BPR", "MSS_FVG"])
        | proximal.source_kind.astype(str).str.contains("CONFIRMED_EXTERNAL", na=False)
    )
    proximal["efficient_ifvg"] = (
        proximal.setup_kind.eq("IFVG")
        & (pd.to_numeric(proximal.source_scale_minutes, errors="coerce") <= 60.0)
        & (pd.to_numeric(proximal.event_activity_ratio, errors="coerce") <= 7.0)
        & (pd.to_numeric(proximal.event_impact_per_activity, errors="coerce") >= 0.25)
        & (pd.to_numeric(proximal.source_accumulation_delta_toward, errors="coerce") > 0.0)
    )
    proximal = proximal[
        (proximal.strong_location | proximal.efficient_ifvg)
        & (pd.to_numeric(proximal.target_net_r, errors="coerce") >= 0.40)
    ].copy()
    middle = frame[
        (frame.family == "FAILED_FIRST_RETURN")
        & (frame.price_kind == "ZONE_MID_LIMIT")
        & np.isclose(frame.gross_rr, 1.25)
    ].copy().set_index("state_id")
    failed = []
    for _, row in proximal.iterrows():
        choice = row
        if bool(row.strong_location) and row.state_id in middle.index:
            candidate = middle.loc[row.state_id]
            if isinstance(candidate, pd.DataFrame):
                candidate = candidate.iloc[0]
            if (
                pd.to_numeric(candidate.target_net_r, errors="coerce") >= 0.40
                and pd.to_numeric(candidate.source_distance_atr, errors="coerce") <= 1.20
                and pd.to_numeric(candidate.dep_eff_3, errors="coerce") <= 0.50
            ):
                choice = candidate
        choice = choice.copy()
        choice["policy_family"] = "FAILED_AUCTION_FIRST_RETURN"
        failed.append(choice)
    accepted = frame[
        (frame.family == "ACCEPTED_FIRST_RETURN")
        & (frame.price_kind == "ZONE_PROXIMAL_LIMIT")
        & np.isclose(frame.gross_rr, 2.0)
    ].copy()
    accepted = accepted[
        (pd.to_numeric(accepted.target_net_r, errors="coerce") >= 0.40)
        & (pd.to_numeric(accepted.common_ret_60_signed, errors="coerce") > 0.0)
        & (pd.to_numeric(accepted.dep_ret_3_atr, errors="coerce") >= -1.0)
        & (pd.to_numeric(accepted.dep_ret_15_atr, errors="coerce") <= 10.0)
    ].copy()
    accepted["policy_family"] = "ACCEPTED_AUCTION_FIRST_RETURN"
    output = pd.concat([pd.DataFrame(failed), accepted], ignore_index=True, sort=False)
    output["filled"] = pd.to_datetime(output.entry_time, utc=True, errors="coerce").notna()
    output["quality_score"] = np.where(
        output.policy_family.eq("ACCEPTED_AUCTION_FIRST_RETURN"), 2.0, 1.5
    ) + pd.to_numeric(output.target_net_r, errors="coerce").fillna(0.0) * 0.30
    output["quality_score"] += np.where(
        output.setup_kind.isin(["BPR", "MSS_FVG"]), 0.40,
        np.where(output.source_kind.astype(str).str.contains("CONFIRMED_EXTERNAL", na=False), 0.25, 0.0),
    )
    return output


def route_account(candidates: pd.DataFrame):
    frame = candidates.copy()
    for column in ("order_time", "terminal_time", "entry_time", "exit_time"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame = frame.sort_values(
        ["order_time", "quality_score", "target_net_r", "state_id"],
        ascending=[True, False, False, True],
    )
    selected = []
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    for timestamp, group in frame.groupby("order_time", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if timestamp < busy_until:
            continue
        row = group.iloc[0]
        selected.append(row)
        # A pre-fill invalidation or target-spent cancellation releases the slot at
        # the actual cancellation time; filled positions release it only at TP/SL.
        busy_until = pd.Timestamp(row.exit_time)
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[:0].copy()
    trades = orders[
        orders.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"])
        & pd.to_numeric(orders.net_r, errors="coerce").notna()
    ].copy().reset_index(drop=True)
    nav, peak, maximum_drawdown = 1.0, 1.0, 0.0
    before, after = [], []
    for result in pd.to_numeric(trades.net_r, errors="coerce"):
        before.append(nav)
        nav *= max(core.EPS, 1.0 + 0.03 * float(result))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        after.append(nav)
    trades["nav_before"], trades["nav_after"] = before, after
    summary = {
        "selected_orders": int(len(orders)),
        "closed_trades": int(len(trades)),
        "open_or_unfilled_orders": int(len(orders) - len(trades)),
        "target_first": int(trades.outcome.eq("TARGET_FIRST").sum()),
        "target_first_rate": float(trades.outcome.eq("TARGET_FIRST").mean()) if len(trades) else None,
        "mean_net_r": float(pd.to_numeric(trades.net_r).mean()) if len(trades) else None,
        "mean_planned_gross_rr": float(pd.to_numeric(trades.gross_rr).mean()) if len(trades) else None,
        "median_hold_minutes": float(pd.to_numeric(trades.hold_minutes).median()) if len(trades) else None,
        "mean_hold_minutes": float(pd.to_numeric(trades.hold_minutes).mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
    }
    return orders, trades, summary


core.select_policy = select_policy
core.route_account = route_account

if __name__ == "__main__":
    core.main()
