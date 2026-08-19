#!/usr/bin/env python3
"""Render selected trades and genuinely missed no-trade opportunities for inspection.

Future outcomes are used only to locate cases for offline skilled-trader reverse
engineering. They are never features or runtime gates.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _period_from_path(path: Path) -> str | None:
    for part in path.parts:
        for prefix in ("train-", "cal-", "holdout-", "eval-"):
            position = part.find(prefix)
            if position >= 0:
                return part[position:]
    return None


def _raw_by_period(root: Path) -> dict[str, dict[str, pd.DataFrame]]:
    output: dict[str, dict[str, pd.DataFrame]] = {}
    for path in root.glob("**/raw_universe_1m.csv.gz"):
        period = _period_from_path(path)
        if period is None:
            continue
        frame = pd.read_csv(path, parse_dates=["open_time_dt"], low_memory=False)
        frame["open_time_dt"] = pd.to_datetime(frame["open_time_dt"], utc=True)
        output[period] = {
            str(symbol): group.sort_values("open_time_dt").set_index("open_time_dt")
            for symbol, group in frame.groupby("symbol")
        }
    return output


def _occupied(timestamp: pd.Timestamp, orders: pd.DataFrame) -> bool:
    if orders.empty:
        return False
    return bool(((orders["entry_time"] <= timestamp) & (timestamp < orders["exit_time"])).any())


def _clinic_cases(scored: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    selected_ids = set(orders["action_id"].astype(str))
    selected_events = set(orders["event_id"].astype(str))
    rows: list[dict[str, Any]] = []
    for _, row in orders.iterrows():
        if str(row["outcome"]) == "STOP_FIRST":
            case = "SELECTED_LOSS"
        elif str(row["outcome"]) == "TARGET_FIRST":
            case = "SELECTED_WIN"
        else:
            case = "SELECTED_OPEN"
        rows.append({"case_kind": case, **row.to_dict()})

    resolved = scored[scored["resolved_label"].eq(1) & scored["net_r"].notna()].copy()
    for (period, event_id), group in resolved.groupby(["period", "event_id"], sort=False):
        best = group.sort_values(["net_r", "actual_target_net_r"], ascending=[False, False]).iloc[0]
        if float(best["net_r"]) <= 0.0 or str(event_id) in selected_events:
            continue
        timestamp = pd.Timestamp(best["entry_time"])
        period_orders = orders[orders["period"].astype(str).eq(str(period))]
        if _occupied(timestamp, period_orders):
            continue
        rows.append({"case_kind": "MISSED_WHILE_ACCOUNT_FREE", **best.to_dict()})

    # Include high-scored rejected events whose best plan lost. They expose false positives
    # that the router correctly avoided and help distinguish good no-trade judgment from luck.
    rejected = resolved[~resolved["action_id"].astype(str).isin(selected_ids)].copy()
    if not rejected.empty:
        losing_best = (
            rejected.sort_values(["period", "event_id", "net_r"], ascending=[True, True, False])
            .groupby(["period", "event_id"], as_index=False)
            .first()
        )
        losing_best = losing_best[losing_best["net_r"].le(0.0)].sort_values("policy_score", ascending=False).head(24)
        for _, row in losing_best.iterrows():
            rows.append({"case_kind": "CORRECT_NO_TRADE", **row.to_dict()})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    priority = {
        "SELECTED_LOSS": 0,
        "SELECTED_OPEN": 1,
        "MISSED_WHILE_ACCOUNT_FREE": 2,
        "SELECTED_WIN": 3,
        "CORRECT_NO_TRADE": 4,
    }
    frame["_priority"] = frame["case_kind"].map(priority).fillna(9)
    frame = frame.sort_values(["_priority", "period", "entry_time", "policy_score"], ascending=[True, True, True, False])
    wins = frame[frame["case_kind"].eq("SELECTED_WIN")].groupby("period", as_index=False).head(4)
    frame = pd.concat([frame[~frame["case_kind"].eq("SELECTED_WIN")], wins], ignore_index=True, sort=False)
    return frame.drop(columns=["_priority"], errors="ignore")


def _plot_case(row: pd.Series, raw: dict[str, dict[str, pd.DataFrame]], path: Path) -> None:
    period = str(row["period"])
    symbol = str(row["symbol"])
    if period not in raw or symbol not in raw[period]:
        return
    frame = raw[period][symbol]
    entry = pd.Timestamp(row["entry_time"])
    exit_time = pd.Timestamp(row["exit_time"])
    start = entry - pd.Timedelta(hours=6)
    end = max(exit_time, entry + pd.Timedelta(hours=4)) + pd.Timedelta(hours=2)
    view = frame[(frame.index >= start) & (frame.index <= end)].copy()
    if view.empty:
        return

    figure, axes = plt.subplots(3, 1, figsize=(15, 10), sharex=True, gridspec_kw={"height_ratios": [3.5, 1.2, 1.2]})
    price, volume, flow = axes
    price.plot(view.index, view["close"], linewidth=1.0)
    price.fill_between(view.index, view["low"], view["high"], alpha=0.16)
    price.axhline(float(row["entry"]), linestyle="--", linewidth=1.0, label="planned entry")
    price.axhline(float(row["stop"]), linestyle=":", linewidth=1.2, label="stop")
    price.axhline(float(row["target"]), linestyle=":", linewidth=1.2, label="target")
    price.axvline(entry, linestyle="--", linewidth=1.0)
    price.axvline(exit_time, linestyle="--", linewidth=1.0)
    price.set_title(
        f"{row['case_kind']} | {period} {symbol} {row['side']} | {row['family']} | "
        f"outcome={row['outcome']} netR={row['net_r']} score={float(row['policy_score']):.5f}"
    )
    price.legend(loc="best")
    volume.bar(view.index, view["quote_volume"], width=0.00065)
    volume.set_ylabel("quote volume")
    signed = view["signed_quote_flow"] if "signed_quote_flow" in view else (2.0 * view["taker_buy_quote_volume"] - view["quote_volume"])
    flow.bar(view.index, signed, width=0.00065)
    flow.axhline(0.0, linewidth=0.8)
    flow.set_ylabel("signed taker flow")
    figure.tight_layout()
    figure.savefig(path, dpi=130)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    scored = pd.read_csv(args.result / "holdout_scored_actions.csv.gz", low_memory=False)
    orders = pd.read_csv(args.result / "holdout_account_orders.csv", low_memory=False)
    for frame in (scored, orders):
        frame["entry_time"] = pd.to_datetime(frame["entry_time"], utc=True, errors="coerce")
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
    cases = _clinic_cases(scored, orders)
    cases.to_csv(args.output / "clinic_index.csv", index=False)
    raw = _raw_by_period(args.root)
    for number, (_, row) in enumerate(cases.iterrows(), start=1):
        name = f"{number:04d}_{row['case_kind']}_{row['period']}_{row['symbol']}.png"
        _plot_case(row, raw, args.output / name)
    print({"cases": int(len(cases)), "charts": int(len(list(args.output.glob('*.png'))))})


if __name__ == "__main__":
    main()
