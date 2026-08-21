#!/usr/bin/env python3
"""Route one-plan liquidity episodes through one continuous four-market account."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
EPS = 1e-12
PERIOD_PATTERN = re.compile(r"(?:^|[-_])(dev|fresh|cal|holdout|eval)-\d{4}-[a-z0-9]+", re.IGNORECASE)


def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def _period_from_path(path: Path, summary: dict[str, Any]) -> str:
    if summary.get("period"):
        return str(summary["period"])
    for part in reversed(path.parts):
        match = PERIOD_PATTERN.search(part)
        if match:
            return match.group(0).lstrip("-_")
    return f"{summary.get('start', 'unknown')}__{summary.get('end', 'unknown')}"


def _role(period: str) -> str:
    return period.split("-", 1)[0] if "-" in period else "unknown"


def load_universe(root: Path) -> tuple[pd.DataFrame, dict[str, int], dict[str, dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    period_days: dict[str, int] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for action_path in sorted(root.glob("**/departure_actions.csv.gz")):
        summary_path = action_path.parent / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        period = _period_from_path(action_path, summary)
        frame = pd.read_csv(action_path, low_memory=False)
        if frame.empty:
            summaries[period] = summary
            continue
        frame["period"] = period
        frame["role"] = _role(period)
        frames.append(frame)
        start = pd.Timestamp(summary.get("start")) if summary.get("start") else None
        end = pd.Timestamp(summary.get("end")) if summary.get("end") else None
        if start is not None and end is not None:
            period_days[period] = max(1, int((end - start).days))
        summaries[period] = summary
    if not frames:
        return pd.DataFrame(), period_days, summaries
    return pd.concat(frames, ignore_index=True, sort=False), period_days, summaries


def _timestamp_ns(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    return pd.to_datetime(values, unit="ns", utc=True, errors="coerce")


def route_account(episodes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if episodes.empty:
        return episodes.copy(), episodes.copy(), {
            "selected_orders": 0,
            "closed_trades": 0,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }
    orders = episodes[_bool_series(episodes["order_exists"])].copy()
    if orders.empty:
        return orders, orders, {
            "selected_orders": 0,
            "closed_trades": 0,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }
    orders["order_time"] = _timestamp_ns(orders, "order_time_ns")
    orders["terminal_time"] = _timestamp_ns(orders, "order_terminal_time_ns")
    orders["fill_time"] = _timestamp_ns(orders, "fill_time_ns")
    orders["resolution_time"] = _timestamp_ns(orders, "resolution_time_ns")
    for column in ("decision_quality", "gross_rr", "route_strength", "planned_target_net_r"):
        orders[column] = pd.to_numeric(orders[column], errors="coerce")
    orders = orders.sort_values(
        ["order_time", "decision_quality", "gross_rr", "route_strength", "episode_id"],
        ascending=[True, False, False, False, True],
    )

    selected: list[pd.Series] = []
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    used_episodes: set[str] = set()
    for timestamp, group in orders.groupby("order_time", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if pd.isna(timestamp) or timestamp < busy_until:
            continue
        available = group[~group.episode_id.astype(str).isin(used_episodes)]
        if available.empty:
            continue
        row = available.iloc[0].copy()
        selected.append(row)
        used_episodes.add(str(row.episode_id))
        terminal = pd.Timestamp(row.terminal_time)
        if pd.isna(terminal):
            terminal = timestamp
        busy_until = max(timestamp, terminal)

    selected_orders = pd.DataFrame(selected).reset_index(drop=True) if selected else orders.iloc[:0].copy()
    closed = selected_orders[
        pd.to_numeric(selected_orders.net_r, errors="coerce").notna()
        & selected_orders.outcome.astype(str).isin(
            ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE"]
        )
    ].copy().reset_index(drop=True)
    closed["net_r"] = pd.to_numeric(closed.net_r, errors="coerce")

    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    before: list[float] = []
    after: list[float] = []
    for result in closed.net_r.astype(float):
        before.append(nav)
        nav *= max(EPS, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        after.append(nav)
    closed["nav_before"] = before
    closed["nav_after"] = after

    wins = closed.outcome.astype(str).eq("TARGET_FIRST")
    summary = {
        "selected_orders": int(len(selected_orders)),
        "closed_trades": int(len(closed)),
        "unfilled_or_censored_selected": int(len(selected_orders) - len(closed)),
        "target_first": int(wins.sum()),
        "target_first_rate": float(wins.mean()) if len(closed) else None,
        "mean_net_r": float(closed.net_r.mean()) if len(closed) else None,
        "median_net_r": float(closed.net_r.median()) if len(closed) else None,
        "mean_planned_gross_rr": float(pd.to_numeric(closed.gross_rr, errors="coerce").mean()) if len(closed) else None,
        "median_holding_minutes": float(pd.to_numeric(closed.holding_minutes, errors="coerce").median()) if len(closed) else None,
        "mean_holding_minutes": float(pd.to_numeric(closed.holding_minutes, errors="coerce").mean()) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "risk_fraction": RISK_FRACTION,
    }
    return selected_orders, closed, summary


def _group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    if frame.empty or key not in frame:
        return {}
    output: dict[str, Any] = {}
    for value, group in frame.groupby(key, dropna=False):
        wins = group.outcome.astype(str).eq("TARGET_FIRST")
        output[str(value)] = {
            "trades": int(len(group)),
            "target_first_rate": float(wins.mean()) if len(group) else None,
            "mean_net_r": float(pd.to_numeric(group.net_r, errors="coerce").mean()) if len(group) else None,
            "mean_gross_rr": float(pd.to_numeric(group.gross_rr, errors="coerce").mean()) if len(group) else None,
            "median_hold_minutes": float(pd.to_numeric(group.holding_minutes, errors="coerce").median()) if len(group) else None,
        }
    return output


def _no_trade_analysis(episodes: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if episodes.empty:
        return episodes.copy(), {}
    no_trade = episodes[~_bool_series(episodes["order_exists"])].copy()
    if no_trade.empty:
        return no_trade, {}
    up = pd.to_numeric(no_trade.get("future_up_atr_diagnostic"), errors="coerce")
    down = pd.to_numeric(no_trade.get("future_down_atr_diagnostic"), errors="coerce")
    no_trade["future_favorable_atr_diagnostic"] = np.where(
        no_trade.side.astype(str).eq("LONG"), up, down
    )
    no_trade = no_trade.sort_values("future_favorable_atr_diagnostic", ascending=False)
    reasons = {
        str(reason): {
            "episodes": int(len(group)),
            "mean_future_favorable_atr": float(pd.to_numeric(group.future_favorable_atr_diagnostic, errors="coerce").mean()),
            "large_missed_move_share": float(pd.to_numeric(group.future_favorable_atr_diagnostic, errors="coerce").ge(1.5).mean()),
        }
        for reason, group in no_trade.groupby("no_trade_reason", dropna=False)
    }
    return no_trade, reasons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    episodes, period_days, source_summaries = load_universe(args.root)
    selected_orders, trades, account = route_account(episodes)
    no_trade, no_trade_reasons = _no_trade_analysis(episodes)
    calendar_days = int(sum(period_days.values()))
    account["calendar_days"] = calendar_days
    account["closed_trades_per_day"] = float(len(trades) / calendar_days) if calendar_days else 0.0
    account["by_period"] = _group_metrics(trades, "period")
    account["by_role"] = _group_metrics(trades, "role")
    account["by_family"] = _group_metrics(trades, "family")
    account["by_symbol"] = _group_metrics(trades, "symbol")

    summary = {
        "policy": "liquidity world model -> one causal episode -> one destination-first plan -> one global pending/position slot -> TP/SL only",
        "episode_rows": int(len(episodes)),
        "causal_plan_episodes": int(_bool_series(episodes["order_exists"]).sum()) if not episodes.empty else 0,
        "no_trade_episodes": int((~_bool_series(episodes["order_exists"])).sum()) if not episodes.empty else 0,
        "account": account,
        "no_trade_reasons": no_trade_reasons,
        "period_days": period_days,
        "source_summaries": source_summaries,
        "one_plan_per_episode": True,
        "fixed_rr_target_lattice": False,
        "target_selected_before_rr": True,
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
        "single_global_account_slot": True,
    }

    episodes.to_csv(args.output / "all_episodes.csv.gz", index=False, compression="gzip")
    selected_orders.to_csv(args.output / "selected_orders.csv", index=False)
    trades.to_csv(args.output / "closed_trades.csv", index=False)
    no_trade.head(200).to_csv(args.output / "largest_missed_no_trade_episodes.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
