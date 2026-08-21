#!/usr/bin/env python3
"""Route causal control-transfer responses through one continuous 3%-risk account.

The harvester has already made the selective market decision: semantic liquidity event,
fresh control transfer, first retest, completed price/flow response, structural
invalidation, and an opposing route which can pay at least 1R.  This router therefore
does not add an opaque score or a pass/fail apparatus.  It enforces causal-episode
independence and the actual single-account constraint.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-12


def _period_from_path(path: Path) -> str:
    name = path.parent.name
    for marker in ("fresh-", "dev-"):
        if marker in name:
            return name.split(marker, 1)[1]
    return name


def load_actions(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        # Ignore symbol-specific duplicates; only the aggregate file has this exact
        # basename at each artifact root.
        frame = pd.read_csv(path)
        if frame.empty:
            continue
        frame["period"] = _period_from_path(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    output = pd.concat(frames, ignore_index=True, sort=False)
    for column in (
        "order_time_ns",
        "order_terminal_time_ns",
        "resolution_time_ns",
        "planned_target_net_r",
        "actual_fill_gross_rr",
        "gross_rr",
        "net_r",
    ):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    output["terminal_ns"] = pd.to_numeric(
        output.get("resolution_time_ns"), errors="coerce"
    ).fillna(
        pd.to_numeric(output.get("order_terminal_time_ns"), errors="coerce")
    )
    return output


def independent_episode_decisions(actions: pd.DataFrame) -> pd.DataFrame:
    if actions.empty:
        return actions.copy()
    # Multiple fresh micro zones can develop inside one semantic event.  The first
    # complete response is the episode decision.  If exact-time alternatives exist,
    # the route with the greater predeclared post-cost reward is preferred.
    ordered = actions.sort_values(
        [
            "period",
            "episode_id",
            "order_time_ns",
            "planned_target_net_r",
            "actual_fill_gross_rr",
        ],
        ascending=[True, True, True, False, False],
    )
    return (
        ordered.groupby(["period", "episode_id"], as_index=False)
        .first()
        .sort_values(
            ["order_time_ns", "planned_target_net_r", "actual_fill_gross_rr"],
            ascending=[True, False, False],
        )
        .reset_index(drop=True)
    )


def route_one_account(actions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    decisions = independent_episode_decisions(actions)
    if decisions.empty:
        return decisions, decisions.copy()
    selected: list[pd.Series] = []
    busy_until = -np.inf
    for timestamp, simultaneous in decisions.groupby("order_time_ns", sort=True):
        timestamp = float(timestamp)
        if not math.isfinite(timestamp) or timestamp < busy_until:
            continue
        row = simultaneous.sort_values(
            [
                "planned_target_net_r",
                "actual_fill_gross_rr",
                "route_rr",
                "risk_bps",
                "symbol",
            ],
            ascending=[False, False, False, True, True],
        ).iloc[0]
        selected.append(row)
        terminal = float(row.terminal_ns)
        busy_until = max(timestamp, terminal) if math.isfinite(terminal) else timestamp
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else decisions.iloc[:0].copy()
    trades = orders[
        orders.net_r.notna()
        & orders.outcome.astype(str).isin(
            ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE"]
        )
    ].copy()
    return orders, trades.reset_index(drop=True)


def _group_summary(trades: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    return (
        trades.groupby(columns, dropna=False)
        .agg(
            trades=("net_r", "size"),
            target_first_rate=("outcome", lambda s: float((s == "TARGET_FIRST").mean())),
            mean_net_r=("net_r", "mean"),
            mean_gross_rr=("actual_fill_gross_rr", "mean"),
            median_hold_minutes=("holding_minutes", "median"),
        )
        .reset_index()
        .to_dict("records")
    )


def summarize(actions: pd.DataFrame, orders: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    nav = peak = 1.0
    maximum_drawdown = 0.0
    gross_profit = gross_loss = 0.0
    for value in pd.to_numeric(trades.net_r, errors="coerce").dropna():
        result = float(value)
        nav *= max(EPS, 1.0 + RISK * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        if result > 0.0:
            gross_profit += result
        elif result < 0.0:
            gross_loss += -result
    periods = int(actions.period.nunique()) if not actions.empty else 0
    calendar_days = 7 * periods
    return {
        "policy": (
            "FIRST_COMPLETE_CONTROL_TRANSFER_RESPONSE_PER_SEMANTIC_EPISODE_"
            "ONE_ACCOUNT_3_PERCENT_NAV_RISK_STOP_OR_TARGET_ONLY"
        ),
        "candidate_actions": int(len(actions)),
        "independent_episodes": int(
            actions[["period", "episode_id"]].drop_duplicates().shape[0]
        ) if not actions.empty else 0,
        "selected_orders": int(len(orders)),
        "closed_trades": int(len(trades)),
        "periods": periods,
        "calendar_days": calendar_days,
        "trades_per_day": float(len(trades) / max(calendar_days, 1)),
        "target_first_rate": (
            float((trades.outcome == "TARGET_FIRST").mean()) if len(trades) else None
        ),
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_net_r": float(trades.net_r.median()) if len(trades) else None,
        "mean_actual_gross_rr": (
            float(trades.actual_fill_gross_rr.mean()) if len(trades) else None
        ),
        "profit_factor_r": (
            float(gross_profit / gross_loss) if gross_loss > 0.0 else None
        ),
        "median_hold_minutes": (
            float(trades.holding_minutes.median()) if len(trades) else None
        ),
        "mean_hold_minutes": (
            float(trades.holding_minutes.mean()) if len(trades) else None
        ),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "by_period": _group_summary(trades, ["period"]),
        "by_symbol": _group_summary(trades, ["symbol"]),
        "by_family": _group_summary(trades, ["family"]),
        "by_entry_geometry": _group_summary(trades, ["entry_geometry"]),
        "by_response_kind": _group_summary(
            trades, ["control_response_kind"]
        ) if "control_response_kind" in trades else [],
    }


def run(root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    actions = load_actions(root)
    orders, trades = route_one_account(actions)
    summary = summarize(actions, orders, trades)
    orders.to_csv(output / "orders.csv", index=False)
    trades.to_csv(output / "trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.output), indent=2, default=str))


if __name__ == "__main__":
    main()
