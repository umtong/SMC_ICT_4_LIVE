#!/usr/bin/env python3
"""Render strict-causal selected trades and missed departures from exact 1m paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd


def period_name(path: Path) -> str:
    for token in ("fresh-", "dev-", "eval-", "train-", "cal-", "holdout-"):
        at = path.name.find(token)
        if at >= 0:
            return path.name[at:]
    return path.name


def load_raw(root: Path):
    output = {}
    for path in root.rglob("raw_universe_1m.csv.gz"):
        period = period_name(path.parent.parent if path.parent.name == "chart" else path.parent)
        frame = pd.read_csv(path, parse_dates=["open_time_dt"], low_memory=False)
        frame["open_time_dt"] = pd.to_datetime(frame.open_time_dt, utc=True)
        output[period] = {
            symbol: group.sort_values("open_time_dt").set_index("open_time_dt")
            for symbol, group in frame.groupby("symbol")
        }
    return output


def render(row: pd.Series, raw, path: Path, prefix: str) -> bool:
    period, symbol = str(row.period), str(row.symbol)
    if period not in raw or symbol not in raw[period]:
        return False
    frame = raw[period][symbol]
    order = pd.to_datetime(float(row.order_time_ns), unit="ns", utc=True)
    fill = pd.to_datetime(float(row.fill_time_ns), unit="ns", utc=True) if pd.notna(row.get("fill_time_ns")) else None
    resolution = pd.to_datetime(float(row.resolution_time_ns), unit="ns", utc=True) if pd.notna(row.get("resolution_time_ns")) else None
    end_anchor = resolution if resolution is not None else order + pd.Timedelta(minutes=180)
    chart = frame.loc[order - pd.Timedelta(minutes=180):end_anchor + pd.Timedelta(minutes=60)].copy()
    if chart.empty:
        return False
    aggregation = {"open":"first", "high":"max", "low":"min", "close":"last"}
    for column in ("quote_volume", "signed_quote_flow", "taker_buy_quote_volume", "count"):
        if column in chart:
            aggregation[column] = "sum"
    bars = chart.resample("5min", label="right", closed="left").agg(aggregation).dropna(subset=["open","high","low","close"])
    figure = plt.figure(figsize=(14, 7))
    price = figure.add_axes([0.07, 0.33, 0.88, 0.61])
    volume = figure.add_axes([0.07, 0.08, 0.88, 0.18], sharex=price)
    numbers = mdates.date2num(bars.index.to_pydatetime())
    width = 4.0 / 1440.0
    for number, (_, bar) in zip(numbers, bars.iterrows()):
        price.vlines(number, float(bar.low), float(bar.high), linewidth=0.7)
        lower = min(float(bar.open), float(bar.close))
        upper = max(float(bar.open), float(bar.close))
        price.add_patch(Rectangle((number-width/2, lower), width, max(upper-lower, 1e-12), fill=float(bar.close)>=float(bar.open), alpha=0.45, linewidth=0.6))
    for column, label, style in (("entry","entry","--"),("stop","stop",":"),("target","target",":"),("route_price","route","-.")):
        if column in row and pd.notna(row[column]):
            price.axhline(float(row[column]), linestyle=style, linewidth=1.0, label=label)
    price.axvline(order, linestyle="--", linewidth=1.0, label="order")
    if fill is not None:
        price.axvline(fill, linestyle="-.", linewidth=1.0, label="fill")
    if resolution is not None:
        price.axvline(resolution, linestyle=":", linewidth=1.0, label="resolution")
    price.legend(loc="upper left", ncol=6, fontsize=8)
    price.grid(alpha=0.2)
    price.set_title(
        f"{prefix} | {period} {symbol} {row.get('family','')} {row.get('side','')} | "
        f"{row.get('outcome','')} netR={pd.to_numeric(row.get('net_r'),errors='coerce'):.3f} | "
        f"{row.get('entry_geometry','')} RR={row.get('gross_rr','')}"
    )
    if "quote_volume" in bars:
        volume.bar(bars.index, bars.quote_volume, width=0.003, alpha=0.45)
    if "signed_quote_flow" in bars:
        flow = volume.twinx()
        flow.plot(bars.index, bars.signed_quote_flow, linewidth=0.8)
        flow.set_ylabel("signed flow")
    volume.set_ylabel("quote volume")
    volume.grid(alpha=0.2)
    volume.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M"))
    figure.savefig(path, dpi=130)
    plt.close(figure)
    return True


def diagnose(row: pd.Series) -> str:
    issues = []
    def number(name, default=np.nan):
        return pd.to_numeric(row.get(name, default), errors="coerce")
    if number("common_ret_60_signed", 0.0) <= 0.0:
        issues.append("common market did not support the side")
    if number("source_distance_atr", 0.0) > 1.5:
        issues.append("departure was already extended from the source")
    if number("dep_eff_3", 1.0) <= 0.0 or number("dep_eff_5", 1.0) <= 0.0:
        issues.append("departure lacked short-horizon path control")
    if number("dep_flow_5", 0.0) < 0.0:
        issues.append("aggressive flow opposed the plan")
    if "ACCUMULATED" in str(row.get("source_pool_kind", "")):
        issues.append("source was an internal accumulated pool")
    if number("route_rr", 99.0) < number("gross_rr", 0.0) + 0.1:
        issues.append("target route had little clearance")
    return "; ".join(issues) if issues else "no single scalar issue; inspect auction sequence and geometry"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    shutil.rmtree(args.output, ignore_errors=True)
    args.output.mkdir(parents=True)
    trades = pd.read_csv(next(args.result_root.rglob("fresh_trades.csv")), low_memory=False)
    orders = pd.read_csv(next(args.result_root.rglob("fresh_orders.csv")), low_memory=False)
    scored = pd.read_csv(next(args.result_root.rglob("fresh_scored_plans.csv.gz")), low_memory=False)
    raw = load_raw(args.fresh_root)
    selected_states = set(orders.state_id.astype(str))
    missed = scored[
        scored.outcome.astype(str).eq("TARGET_FIRST")
        & ~scored.state_id.astype(str).isin(selected_states)
    ].copy()
    missed = missed.sort_values(["net_r", "target_net_r"], ascending=[False,False]).groupby(["period","state_id"], as_index=False).first().head(30)
    losses = trades[~trades.outcome.astype(str).eq("TARGET_FIRST")].copy()
    wins = trades[trades.outcome.astype(str).eq("TARGET_FIRST")].copy().sort_values(["period","family","symbol","order_time_ns"]).groupby(["period","family"], as_index=False).head(2).head(24)
    index = []
    for kind, frame in (("loss", losses), ("win", wins), ("missed_win", missed)):
        directory = args.output / kind
        directory.mkdir()
        for ordinal, (_, row) in enumerate(frame.iterrows()):
            path = directory / f"{kind}_{ordinal:03d}_{row.period}_{row.symbol}.png"
            if render(row, raw, path, kind.upper()):
                index.append({"kind":kind, "path":str(path.relative_to(args.output)), "period":row.period, "symbol":row.symbol, "family":row.get("family"), "outcome":row.get("outcome"), "net_r":row.get("net_r"), "diagnosis":diagnose(row)})
    table = pd.DataFrame(index)
    table.to_csv(args.output / "INDEX.csv", index=False)
    lines = ["# Strict causal fresh trade/no-trade clinic", "", "## Selected losses", ""]
    loss_table = table[table.kind.eq("loss")]
    lines.append(loss_table.to_markdown(index=False) if len(loss_table) else "No selected losses.")
    lines += ["", "## Representative wins", ""]
    win_table = table[table.kind.eq("win")]
    lines.append(win_table.to_markdown(index=False) if len(win_table) else "No selected wins.")
    lines += ["", "## Missed profitable causal departures", ""]
    missed_table = table[table.kind.eq("missed_win")]
    lines.append(missed_table.to_markdown(index=False) if len(missed_table) else "No missed target-first departure in the available scored lattice.")
    (args.output / "CLINIC.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
