#!/usr/bin/env python3
"""Run the quality control-transfer policy with TP and SL as the only exits.

The existing control-transfer detector is reused because it encodes the observed
sequence: semantic liquidity raid, reclaim, inward initiative, real pullback and
reacceleration. Only its vertical time barrier is replaced. A position whose target
and stop are both untouched at the end of available label data remains CENSORED_OPEN;
it is not liquidated and contributes no realized R.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

import control_transfer_research as core


def _barrier_only_label(
    frame: pd.DataFrame,
    entry_index: int,
    side: str,
    entry: float,
    stop: float,
    target: float,
    tick: float,
) -> core.Label:
    sign = 1.0 if side == "LONG" else -1.0
    actual_entry = float(frame.open.iloc[entry_index]) + sign * core.ENTRY_SLIPPAGE_TICKS * tick
    stop_fill = float(stop) - sign * core.STOP_SLIPPAGE_TICKS * tick
    risk_price = abs(actual_entry - stop_fill)
    if not math.isfinite(risk_price) or risk_price <= 0.0:
        raise RuntimeError("invalid barrier-only risk geometry")
    raw_stop = sign * (stop_fill - actual_entry) / risk_price - (
        core.ENTRY_FEE * abs(actual_entry) + core.STOP_FEE * abs(stop_fill)
    ) / risk_price
    normalization = max(abs(raw_stop), 1e-12)
    raw_target = sign * (float(target) - actual_entry) / risk_price - (
        core.ENTRY_FEE * abs(actual_entry) + core.TARGET_FEE * abs(target)
    ) / risk_price
    target_r = raw_target / normalization

    high = frame.high.to_numpy(dtype=float, copy=False)
    low = frame.low.to_numpy(dtype=float, copy=False)
    if side == "LONG":
        stop_hits = low[entry_index:] <= float(stop)
        target_hits = high[entry_index:] >= float(target)
    else:
        stop_hits = high[entry_index:] >= float(stop)
        target_hits = low[entry_index:] <= float(target)
    first_stop = int(np.argmax(stop_hits)) if bool(stop_hits.any()) else None
    first_target = int(np.argmax(target_hits)) if bool(target_hits.any()) else None

    if first_stop is None and first_target is None:
        end = len(frame) - 1
        return core.Label(
            "CENSORED_OPEN",
            float("nan"),
            frame.index[end] + pd.Timedelta(minutes=1),
            end - entry_index + 1,
            target_r,
        )
    # One-minute OHLC cannot order both prints inside the same bar. Do not credit a
    # target when the stop may have printed first.
    if first_stop is not None and (first_target is None or first_stop <= first_target):
        relative = first_stop
        outcome = "STOP_FIRST"
        result = -1.0
    else:
        relative = int(first_target)
        outcome = "TARGET_FIRST"
        result = target_r
    position = entry_index + int(relative)
    return core.Label(
        outcome,
        result,
        frame.index[position] + pd.Timedelta(minutes=1),
        int(relative) + 1,
        target_r,
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _barrier_only_route_account(plans: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    candidates = plans[
        pd.to_numeric(plans.direction_probability, errors="coerce").ge(0.50)
    ].copy()
    if candidates.empty:
        return candidates, {
            "selected_orders": 0,
            "closed_trades": 0,
            "open_positions_at_data_end": 0,
            "target_first_rate": None,
            "mean_net_r": None,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }
    candidates["entry_time"] = pd.to_datetime(candidates.entry_time, utc=True)
    candidates["exit_time"] = pd.to_datetime(candidates.exit_time, utc=True)
    candidates = candidates.sort_values(
        ["entry_time", "direction_probability", "path_efficiency", "move_atr", "state_id"],
        ascending=[True, False, False, False, True],
    )
    selected: list[pd.Series] = []
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    used: set[tuple[str, str]] = set()
    for entry_time, group in candidates.groupby("entry_time", sort=True):
        timestamp = pd.Timestamp(entry_time)
        if timestamp < busy_until:
            continue
        available = group[
            ~group.apply(
                lambda row: (str(row.period), str(row.episode_id)) in used,
                axis=1,
            )
        ]
        if available.empty:
            continue
        row = available.iloc[0]
        selected.append(row)
        used.add((str(row.period), str(row.episode_id)))
        busy_until = pd.Timestamp(row.exit_time)
    account = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[0:0].copy()
    account["nav_before"] = np.nan
    account["nav_after"] = np.nan
    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for index, row in account.iterrows():
        if str(row.outcome) not in {"TARGET_FIRST", "STOP_FIRST"} or pd.isna(row.net_r):
            continue
        account.at[index, "nav_before"] = nav
        nav *= max(1e-9, 1.0 + core.RISK_FRACTION * _safe_float(row.net_r))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        account.at[index, "nav_after"] = nav
    closed = account[
        account.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"])
        & pd.to_numeric(account.net_r, errors="coerce").notna()
    ]
    days = 8 * int(account.period.nunique()) if len(account) else 0
    summary = {
        "selected_orders": int(len(account)),
        "closed_trades": int(len(closed)),
        "open_positions_at_data_end": int(len(account) - len(closed)),
        "periods": int(account.period.nunique()) if len(account) else 0,
        "approximate_calendar_days": int(days),
        "closed_trades_per_day": float(len(closed) / days) if days else 0.0,
        "target_first_rate": float(closed.outcome.eq("TARGET_FIRST").mean()) if len(closed) else None,
        "mean_net_r": float(pd.to_numeric(closed.net_r, errors="coerce").mean()) if len(closed) else None,
        "median_hold_minutes": float(pd.to_numeric(closed.hold_minutes, errors="coerce").median()) if len(closed) else None,
        "mean_hold_minutes": float(pd.to_numeric(closed.hold_minutes, errors="coerce").mean()) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "by_period": {
            str(period): {
                "selected": int(len(group)),
                "closed": int(group.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"]).sum()),
                "open": int((~group.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"])).sum()),
                "target_first_rate": float(group.loc[group.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"]), "outcome"].eq("TARGET_FIRST").mean()) if group.outcome.astype(str).isin(["TARGET_FIRST", "STOP_FIRST"]).any() else None,
            }
            for period, group in account.groupby("period")
        },
    }
    return account, summary


core._label = _barrier_only_label
core.route_account = _barrier_only_route_account

# This import patches the event detector with the durable-response and non-opposing
# multiscale-structure semantics found in the trade-by-trade chart clinic.
import quality_control_transfer  # noqa: E402,F401

if __name__ == "__main__":
    core.main()
