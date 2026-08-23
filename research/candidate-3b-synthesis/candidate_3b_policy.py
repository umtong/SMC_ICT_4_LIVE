#!/usr/bin/env python3
"""Deterministic one-account router and continuous 3%-risk NAV for candidate 3b."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
EPS = 1e-12
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}


def _period_name(path: Path) -> str:
    value = path.parent.name
    for prefix in ("candidate-3b-", "candidate_3b-"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value


def load_actions(roots: Iterable[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    seen: set[Path] = set()
    for root in roots:
        root = Path(root)
        paths = [root] if root.is_file() else sorted(root.rglob("departure_actions.csv.gz"))
        for path in paths:
            path = path.resolve()
            if path in seen or path.name != "departure_actions.csv.gz":
                continue
            seen.add(path)
            frame = pd.read_csv(path, compression="gzip", low_memory=False)
            frame["period"] = _period_name(path)
            frames.append(frame)
    if not frames:
        raise FileNotFoundError("no departure_actions.csv.gz found under supplied roots")
    return normalize(pd.concat(frames, ignore_index=True, sort=False))


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    numeric = [
        "scenario_rank",
        "target_tier_r",
        "planned_target_net_r",
        "gross_rr",
        "route_rr",
        "route_utilization",
        "proof_margin_r",
        "auction_best_progress_r",
        "auction_progress_r",
        "auction_effort_result",
        "auction_acceptance_strength",
        "order_time_ns",
        "order_terminal_time_ns",
        "fill_time_ns",
        "resolution_time_ns",
        "net_r",
        "holding_minutes",
    ]
    for column in numeric:
        if column not in output:
            output[column] = np.nan
        output[column] = pd.to_numeric(output[column], errors="coerce")
    for column in ("action_id", "state_id", "episode_id", "period", "symbol", "side", "scenario_family"):
        if column not in output:
            output[column] = ""
        output[column] = output[column].fillna("").astype(str)
    if "fill_state" not in output:
        output["fill_state"] = ""
    if "outcome" not in output:
        output["outcome"] = ""
    output["filled"] = output.fill_state.astype(str).eq("FILLED_LIMIT")
    output["resolved"] = output.outcome.astype(str).isin(RESOLVED_OUTCOMES)
    output["win"] = output["resolved"] & output.net_r.gt(0.0)
    return output


def _causal_priority_columns() -> tuple[list[str], list[bool]]:
    return (
        [
            "scenario_rank",
            "target_tier_r",
            "planned_target_net_r",
            "proof_margin_r",
            "auction_acceptance_strength",
            "auction_effort_result",
            "route_rr",
            "action_id",
        ],
        [False, False, False, False, False, False, False, True],
    )


def episode_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    """Choose one action at the first qualifying completed state of each episode."""
    if frame.empty:
        return frame.copy()
    priority, ascending = _causal_priority_columns()

    # Entry variants are simultaneous alternatives, not independent opportunities.
    states = (
        frame.sort_values(
            ["period", "state_id"] + priority,
            ascending=[True, True] + ascending,
            kind="mergesort",
        )
        .groupby(["period", "state_id"], as_index=False)
        .first()
    )

    first_time = (
        states.groupby(["period", "episode_id"], as_index=False)
        .order_time_ns.min()
        .rename(columns={"order_time_ns": "first_qualifying_time_ns"})
    )
    states = states.merge(first_time, on=["period", "episode_id"], how="inner")
    states = states[states.order_time_ns.eq(states.first_qualifying_time_ns)].copy()
    return (
        states.sort_values(
            ["period", "episode_id"] + priority,
            ascending=[True, True] + ascending,
            kind="mergesort",
        )
        .groupby(["period", "episode_id"], as_index=False)
        .first()
        .sort_values(["order_time_ns", "period", "episode_id"], kind="mergesort")
        .reset_index(drop=True)
    )


def route_one_account(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Route all four instruments through one cash/position slot."""
    if candidates.empty:
        return candidates.copy(), candidates.copy()
    priority, ascending = _causal_priority_columns()
    selected: list[pd.Series] = []
    blocked: list[pd.Series] = []
    busy_until = -math.inf

    for timestamp, simultaneous in candidates.groupby("order_time_ns", sort=True):
        timestamp = float(timestamp)
        if timestamp < busy_until:
            blocked.extend(row for _, row in simultaneous.iterrows())
            continue
        ordered = simultaneous.sort_values(priority, ascending=ascending, kind="mergesort")
        winner = ordered.iloc[0]
        selected.append(winner)
        blocked.extend(row for _, row in ordered.iloc[1:].iterrows())
        terminal = float(winner.order_terminal_time_ns)
        busy_until = max(timestamp, terminal if math.isfinite(terminal) else timestamp)

    selected_frame = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[:0].copy()
    blocked_frame = pd.DataFrame(blocked).reset_index(drop=True) if blocked else candidates.iloc[:0].copy()
    return selected_frame, blocked_frame


def account_result(
    orders: pd.DataFrame,
    *,
    evaluation_calendar_days: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    trades = orders[orders.filled & orders.resolved & orders.net_r.notna()].copy()
    trades = trades.sort_values(
        ["fill_time_ns", "order_time_ns", "action_id"], kind="mergesort"
    ).reset_index(drop=True)

    nav = peak = 1.0
    maximum_drawdown = 0.0
    nav_rows: list[dict] = []
    for index, row in trades.iterrows():
        before = nav
        nav *= max(EPS, 1.0 + RISK_FRACTION * float(row.net_r))
        peak = max(peak, nav)
        drawdown = 1.0 - nav / peak
        maximum_drawdown = max(maximum_drawdown, drawdown)
        nav_rows.append(
            {
                "trade_number": index + 1,
                "period": row.period,
                "action_id": row.action_id,
                "episode_id": row.episode_id,
                "symbol": row.symbol,
                "side": row.side,
                "scenario_family": row.scenario_family,
                "target_tier_r": float(row.target_tier_r),
                "fill_time_ns": row.fill_time_ns,
                "resolution_time_ns": row.resolution_time_ns,
                "net_r": float(row.net_r),
                "nav_before": before,
                "nav_after": nav,
                "drawdown": drawdown,
            }
        )
    nav_frame = pd.DataFrame(nav_rows)

    wins = trades.loc[trades.net_r > 0.0, "net_r"]
    losses = trades.loc[trades.net_r <= 0.0, "net_r"]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0

    def breakdown(column: str) -> list[dict]:
        if trades.empty:
            return []
        return (
            trades.groupby(column)
            .agg(
                trades=("net_r", "size"),
                win_rate=("win", "mean"),
                mean_net_r=("net_r", "mean"),
                mean_planned_gross_rr=("gross_rr", "mean"),
            )
            .reset_index()
            .to_dict("records")
        )

    summary = {
        "policy": "candidate_3b_proof_route_completion",
        "risk_fraction": RISK_FRACTION,
        "periods": int(orders.period.nunique()) if len(orders) else 0,
        "evaluation_calendar_days": float(evaluation_calendar_days),
        "selected_orders": int(len(orders)),
        "filled_orders": int(orders.filled.sum()) if len(orders) else 0,
        "closed_trades": int(len(trades)),
        "independent_completed_episodes": int(trades.episode_id.nunique()) if len(trades) else 0,
        "trades_per_calendar_day": (
            float(len(trades) / evaluation_calendar_days)
            if evaluation_calendar_days > 0.0
            else None
        ),
        "win_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_net_r": float(trades.net_r.median()) if len(trades) else None,
        "mean_win_r": float(wins.mean()) if len(wins) else None,
        "mean_loss_r": float(losses.mean()) if len(losses) else None,
        "profit_factor_r": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "mean_planned_gross_rr": float(trades.gross_rr.mean()) if len(trades) else None,
        "mean_planned_target_net_r": (
            float(trades.planned_target_net_r.mean()) if len(trades) else None
        ),
        "median_holding_minutes": (
            float(trades.holding_minutes.median()) if len(trades) else None
        ),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "by_period": breakdown("period"),
        "by_symbol": breakdown("symbol"),
        "by_side": breakdown("side"),
        "by_scenario": breakdown("scenario_family"),
    }
    return trades, nav_frame, summary


def run(
    roots: list[Path],
    output: Path,
    *,
    calendar_days_per_period: float = 7.0,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    actions = load_actions(roots)
    candidates = episode_candidates(actions)
    orders, blocked = route_one_account(candidates)
    evaluation_days = float(calendar_days_per_period) * float(actions.period.nunique())
    trades, nav, summary = account_result(
        orders,
        evaluation_calendar_days=evaluation_days,
    )
    candidates.to_csv(output / "episode_candidates.csv.gz", index=False, compression="gzip")
    orders.to_csv(output / "selected_orders.csv.gz", index=False, compression="gzip")
    blocked.to_csv(output / "blocked_by_one_account.csv.gz", index=False, compression="gzip")
    trades.to_csv(output / "trades.csv.gz", index=False, compression="gzip")
    nav.to_csv(output / "continuous_nav.csv", index=False)
    with (output / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False, allow_nan=False)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--calendar-days-per-period", type=float, default=7.0)
    args = parser.parse_args()
    summary = run(
        args.root,
        args.output,
        calendar_days_per_period=args.calendar_days_per_period,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
