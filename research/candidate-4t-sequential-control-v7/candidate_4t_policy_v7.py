#!/usr/bin/env python3
"""Candidate 4t v7: v6 alpha with a correct immutable fill lifecycle."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import candidate_4t_policy_v6 as v6

base = v6.base


def _finite_time(row: pd.Series, names: tuple[str, ...], default: float) -> float:
    for name in names:
        if name not in row:
            continue
        value = base.safe_float(row.get(name), math.nan)
        if math.isfinite(value):
            return value
    return default


def immutable_route(states: pd.DataFrame):
    eligible = states[
        (states.expected_enter_log > 0.0)
        & (states.stopping_advantage > 0.0)
        & ~states.auction_phase.astype(str).eq("FAILED_REENTRY")
    ].copy()
    eligible = eligible.sort_values(
        [
            "period", "order_time_ns", "stopping_advantage",
            "expected_enter_log_per_hour", "p_ownership", "action_id",
        ],
        ascending=[True, True, False, False, False, True],
    )

    selected: list[pd.Series] = []
    replaced: list[pd.Series] = []
    blocked_candidates = 0
    for _, period_frame in eligible.groupby("period", sort=True):
        active: pd.Series | None = None
        used_episodes: set[str] = set()
        for timestamp, simultaneous in period_frame.groupby("order_time_ns", sort=True):
            timestamp = float(timestamp)

            if active is not None:
                is_filled = bool(active.get("filled", False))
                fill_time = _finite_time(active, ("fill_time_ns",), math.inf)
                fill_has_occurred = is_filled and fill_time <= timestamp
                if fill_has_occurred:
                    is_resolved = bool(active.get("resolved", False))
                    resolution_time = _finite_time(
                        active,
                        ("resolution_time_ns", "terminal_ns"),
                        math.inf,
                    )
                    if is_resolved and resolution_time <= timestamp:
                        selected.append(active)
                        used_episodes.add(str(active.episode_id))
                        active = None
                    else:
                        blocked_candidates += int(len(simultaneous))
                        continue
                else:
                    order_terminal = _finite_time(
                        active,
                        ("order_terminal_time_ns", "terminal_ns"),
                        timestamp,
                    )
                    if timestamp >= order_terminal:
                        selected.append(active)
                        used_episodes.add(str(active.episode_id))
                        active = None

            pool = simultaneous[
                ~simultaneous.episode_id.astype(str).isin(used_episodes)
            ]
            if pool.empty:
                continue
            candidate = pool.iloc[0]

            if active is None:
                active = candidate
                continue

            # At this point the active instruction is still pending and unfilled.
            if (
                str(candidate.episode_id) != str(active.episode_id)
                and float(candidate.expected_enter_log_per_hour)
                > float(active.expected_enter_log_per_hour) + 1e-12
            ):
                old = active.copy()
                old["replacement_time_ns"] = timestamp
                old["replacement_reason"] = (
                    "BETTER_INDEPENDENT_CAUSAL_OPPORTUNITY"
                )
                replaced.append(old)
                used_episodes.add(str(active.episode_id))
                active = candidate

        if active is not None:
            selected.append(active)

    orders = (
        pd.DataFrame(selected).reset_index(drop=True)
        if selected
        else eligible.iloc[:0].copy()
    )
    replacements = (
        pd.DataFrame(replaced).reset_index(drop=True)
        if replaced
        else eligible.iloc[:0].copy()
    )
    trades = (
        orders[orders.resolved & orders.net_r.notna()]
        .copy()
        .sort_values(
            [column for column in ("resolution_time_ns", "terminal_ns") if column in orders]
            or ["order_time_ns"]
        )
        .reset_index(drop=True)
    )

    nav = peak = 1.0
    maximum_drawdown = 0.0
    for result in pd.to_numeric(trades.net_r, errors="coerce").dropna():
        nav *= max(base.EPS, 1.0 + base.RISK * float(result))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)

    calendar_days = 0
    for _, group in states.groupby("period"):
        values = (
            pd.to_numeric(group.order_time_ns, errors="coerce")
            .dropna()
            .astype(np.int64)
        )
        if len(values):
            calendar_days += max(
                1,
                int(
                    math.ceil(
                        (values.max() - values.min())
                        / (24 * base.NS_PER_HOUR)
                    )
                )
                + 1,
            )
    filled_mask = base.bool_series(orders.get(
        "filled", pd.Series(False, index=orders.index)
    )) if len(orders) else pd.Series(dtype=bool)
    resolved_mask = base.bool_series(orders.get(
        "resolved", pd.Series(False, index=orders.index)
    )) if len(orders) else pd.Series(dtype=bool)
    unresolved_filled = orders[filled_mask & ~resolved_mask].copy() if len(orders) else orders.copy()
    open_pending = orders[~filled_mask & ~resolved_mask].copy() if len(orders) else orders.copy()
    negative = (
        abs(float(trades.loc[trades.net_r < 0, "net_r"].sum()))
        if len(trades)
        else 0.0
    )
    summary: dict[str, Any] = {
        "selected_orders": int(len(orders)),
        "replaced_pending_orders": int(len(replacements)),
        "closed_trades": int(len(trades)),
        "open_filled_positions_at_end": int(len(unresolved_filled)),
        "open_pending_orders_at_end": int(len(open_pending)),
        "blocked_candidates_while_filled": int(blocked_candidates),
        "calendar_days": int(calendar_days),
        "trades_per_day": float(len(trades) / max(calendar_days, 1)),
        "target_first_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_net_r": float(trades.net_r.median()) if len(trades) else None,
        "mean_planned_gross_rr": (
            float(trades.gross_rr.mean())
            if len(trades) and "gross_rr" in trades
            else None
        ),
        "median_hold_minutes": (
            float(trades.holding_minutes.median())
            if len(trades) and "holding_minutes" in trades
            else None
        ),
        "mean_hold_minutes": (
            float(trades.holding_minutes.mean())
            if len(trades) and "holding_minutes" in trades
            else None
        ),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "profit_factor_r": (
            float(trades.loc[trades.net_r > 0, "net_r"].sum())
            / max(negative, base.EPS)
            if len(trades)
            else None
        ),
        "by_period": base.group_summary(trades, "period"),
        "by_family": base.group_summary(trades, "family"),
        "by_phase": base.group_summary(trades, "auction_phase"),
        "by_symbol": base.group_summary(trades, "symbol"),
    }
    return orders, trades, replacements, summary


# v6/v5/v3 resolve the route function through this shared module at run time.
base.route = immutable_route


def run(
    development_root: Path,
    fresh_root: Path | None,
    output: Path,
) -> dict[str, Any]:
    result = v6.run(development_root, fresh_root, output)
    result["policy"] = "CANDIDATE_4T_V7_IMMUTABLE_FILLED_POSITION_LIFECYCLE"
    result["position_lifecycle"] = (
        "pending expires causally; filled remains until declared TP/SL resolution"
    )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    manifest_path = output / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["position_lifecycle"] = result["position_lifecycle"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t v7 immutable-account result\n\n"
        "The v6 causal trajectory policy is routed with a filled position held until its "
        "predeclared target or stop. An unresolved fill blocks the account through the "
        "end of observed data and is reported explicitly.\n\n```json\n"
        + json.dumps(result, ensure_ascii=False, indent=2, default=str)
        + "\n```\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
