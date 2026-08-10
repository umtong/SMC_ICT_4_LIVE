#!/usr/bin/env python3
"""Causal quarter-hour opening order-flow diagnostic.

This experiment adapts the fixed mechanism in Kim and Hansen (2026): the first
10 seconds of each UTC quarter-hour contain recurring algorithmic order flow,
and its normalized aggressor imbalance may predict returns over the next
4--12 hours.  The paper sample ends in October 2024.  This implementation tests
frozen 2025--2026 periods, including a period after the paper's July 2026
publication, on BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.

Candidate 05's checksum verifier and raw aggTrade reader are reused.  Entry is
the first observed trade at or after boundary+10 seconds, so the complete signal
window is known.  Exit is the first one-minute open at or after the requested
horizon.  No parameter search, NAV simulation, leverage, or position reuse is
performed here; this is a mechanism diagnostic before NautilusTrader promotion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HORIZONS_MIN = (30, 60, 120, 240, 480, 720)
ROUND_TRIP_COST_BPS = 19.0
ABS_IMBALANCE_BINS = (-1e-12, 0.25, 0.50, 0.75, 1.0000001)
ABS_IMBALANCE_LABELS = ("0_025", "025_050", "050_075", "075_100")
BURST_BINS = (-math.inf, 0.5, 1.0, 2.0, math.inf)
BURST_LABELS = ("lt_05", "05_10", "10_20", "ge_20")
PAPER = {
    "title": "The Quarter-Hour Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency Futures",
    "authors": ["Chan Kim", "Peter Reinhard Hansen"],
    "arxiv": "2607.09426",
    "version_date": "2026-07-16",
    "source_sample_end": "2024-10-31",
    "frozen_prediction": {
        "30m": "opening imbalance may mean-revert over the first 30 minutes",
        "4h_12h": "opening imbalance predicts continuation over 4 to 12 hours",
        "robustness": "effect should not require top-of-hour or funding boundaries",
    },
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value))


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    return None if losses <= 0.0 else gains / losses


def _summary(values: Iterable[Any]) -> dict[str, Any]:
    clean = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(float)
    if clean.size == 0:
        return {"count": 0}
    positive = np.sort(clean[clean > 0.0])[::-1]
    positive_sum = float(positive.sum())
    without_best = np.delete(clean, int(np.argmax(clean))) if clean.size > 1 else np.array([])
    return {
        "count": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(np.median(clean)),
        "win_rate": float(np.mean(clean > 0.0)),
        "profit_factor": _profit_factor(clean),
        "gross_profit": positive_sum,
        "gross_loss": float(-clean[clean < 0.0].sum()),
        "q10": float(np.quantile(clean, 0.10)),
        "q90": float(np.quantile(clean, 0.90)),
        "best": float(clean.max()),
        "worst": float(clean.min()),
        "mean_without_best": None if without_best.size == 0 else float(without_best.mean()),
        "top1_positive_share": None if positive_sum <= 0.0 else float(positive[:1].sum() / positive_sum),
        "top2_positive_share": None if positive_sum <= 0.0 else float(positive[:2].sum() / positive_sum),
    }


def _timestamp(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    unit = "us" if float(numeric.iloc[0]) > 10**14 else "ms"
    return pd.to_datetime(numeric, unit=unit, utc=True)


def _opening_events(path: Path, base: Any) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for chunk in base._agg_reader(path):
        timestamp = _timestamp(chunk["transact_time"])
        boundary = timestamp.dt.floor("15min")
        in_boundary_minute = timestamp.dt.floor("min").eq(boundary)
        if not bool(in_boundary_minute.any()):
            continue
        work = chunk.loc[in_boundary_minute, ["agg_trade_id", "price", "quantity", "is_buyer_maker"]].copy()
        work["timestamp"] = timestamp.loc[in_boundary_minute].to_numpy()
        work["boundary"] = boundary.loc[in_boundary_minute].to_numpy()
        work["price"] = pd.to_numeric(work["price"], errors="raise").astype(float)
        work["quantity"] = pd.to_numeric(work["quantity"], errors="raise").astype(float)
        maker = base._maker_mask(work["is_buyer_maker"])
        sign = np.where(maker.to_numpy(), -1.0, 1.0)
        work["signed_quantity"] = sign * work["quantity"].to_numpy()
        work["notional"] = work["price"] * work["quantity"]
        work["signed_notional"] = sign * work["notional"].to_numpy()
        pieces.append(
            work[[
                "boundary", "timestamp", "agg_trade_id", "price", "quantity",
                "signed_quantity", "notional", "signed_notional",
            ]]
        )
    if not pieces:
        raise RuntimeError(f"no quarter-hour boundary trades in {path}")
    trades = pd.concat(pieces, ignore_index=True)
    trades = trades.sort_values(["timestamp", "agg_trade_id"], kind="stable")
    elapsed = (trades["timestamp"] - trades["boundary"]).dt.total_seconds()
    opening = trades[elapsed.lt(10.0)].copy()
    after = trades[elapsed.ge(10.0)].copy()
    if opening.empty or after.empty:
        raise RuntimeError(f"incomplete quarter-hour opening windows in {path}")

    grouped = opening.groupby("boundary", sort=True).agg(
        opening_quantity=("quantity", "sum"),
        opening_signed_quantity=("signed_quantity", "sum"),
        opening_notional=("notional", "sum"),
        opening_signed_notional=("signed_notional", "sum"),
        opening_trade_count=("price", "size"),
        opening_first_time=("timestamp", "first"),
        opening_last_time=("timestamp", "last"),
        opening_first_price=("price", "first"),
        opening_last_price=("price", "last"),
    )
    entries = (
        after.groupby("boundary", sort=True, as_index=False).first()
        .set_index("boundary")[["timestamp", "price", "agg_trade_id"]]
        .rename(columns={
            "timestamp": "entry_time",
            "price": "entry_price",
            "agg_trade_id": "entry_agg_trade_id",
        })
    )
    result = grouped.join(entries, how="inner")
    result["imbalance_quantity"] = (
        result["opening_signed_quantity"]
        / result["opening_quantity"].replace(0.0, np.nan)
    )
    result["imbalance_notional"] = (
        result["opening_signed_notional"]
        / result["opening_notional"].replace(0.0, np.nan)
    )
    result["opening_return_bps"] = (
        np.log(result["opening_last_price"] / result["opening_first_price"]) * 10_000.0
    )
    result["entry_latency_ms"] = (
        result["entry_time"] - (result.index + pd.Timedelta(seconds=10))
    ).dt.total_seconds() * 1000.0
    return result.reset_index()


def _load_symbol(
    *, symbol: str, start: date, end: date, cache: Path,
    candidate05_path: Path, candidate51_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    base = _load_module(candidate05_path / "features.py", f"qho_base_{symbol}")
    kline_module = _load_module(candidate51_path / "kline_only_inputs.py", f"qho_kline_{symbol}")
    event_frames: list[pd.DataFrame] = []
    kline_frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    day = start
    while day <= end:
        agg_path, _, agg_evidence = base.download_checked("aggTrades", symbol, day, cache)
        kline_path, _, kline_evidence = base.download_checked("klines", symbol, day, cache)
        event_frames.append(_opening_events(agg_path, base))
        kline_frames.append(kline_module._read_kline(kline_path))
        evidence.extend([asdict(agg_evidence), asdict(kline_evidence)])
        day += timedelta(days=1)

    events = pd.concat(event_frames, ignore_index=True).sort_values("boundary")
    events = events.drop_duplicates("boundary", keep="last").reset_index(drop=True)
    expected_boundaries = pd.date_range(
        pd.Timestamp(start, tz="UTC"),
        pd.Timestamp(end + timedelta(days=1), tz="UTC") - pd.Timedelta(minutes=15),
        freq="15min",
    )
    missing_boundaries = expected_boundaries.difference(pd.DatetimeIndex(events["boundary"]))
    if len(missing_boundaries):
        raise RuntimeError(
            f"missing quarter-hour opening events for {symbol}: "
            f"{[value.isoformat() for value in missing_boundaries[:10]]}"
        )

    klines = pd.concat(kline_frames, ignore_index=True).sort_values("open_time_dt")
    if klines["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate kline opens for {symbol}")
    return events, klines.reset_index(drop=True), evidence


def _bin_labels(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["abs_imbalance"] = out["imbalance_quantity"].abs()
    out["abs_imbalance_bin"] = pd.cut(
        out["abs_imbalance"], bins=ABS_IMBALANCE_BINS,
        labels=ABS_IMBALANCE_LABELS, include_lowest=True, right=False,
    ).astype(str)
    baseline = out["opening_notional"].shift(1).rolling(96, min_periods=96).median()
    out["opening_notional_burst"] = out["opening_notional"] / baseline.replace(0.0, np.nan)
    out["notional_burst_bin"] = pd.cut(
        out["opening_notional_burst"], bins=BURST_BINS,
        labels=BURST_LABELS, include_lowest=True, right=False,
    ).astype(str)
    out["phase_minute"] = out["boundary"].dt.minute
    out["top_of_hour"] = out["phase_minute"].eq(0)
    out["funding_boundary"] = out["top_of_hour"] & out["boundary"].dt.hour.isin((0, 8, 16))
    out["side"] = np.sign(out["imbalance_quantity"]).astype(int)
    out["same_phase_prior_imbalance"] = out.groupby("phase_minute", sort=False)["imbalance_quantity"].shift(1)
    out["same_phase_aligned"] = (
        np.sign(out["same_phase_prior_imbalance"]) == out["side"]
    ) & out["same_phase_prior_imbalance"].notna() & out["side"].ne(0)
    return out


def _attach_paths(events: pd.DataFrame, klines: pd.DataFrame) -> pd.DataFrame:
    result = events.copy()
    times = pd.DatetimeIndex(pd.to_datetime(klines["open_time_dt"], utc=True))
    for horizon in HORIZONS_MIN:
        continuation: list[float | None] = []
        reversal: list[float | None] = []
        exit_times: list[pd.Timestamp | None] = []
        exit_latency: list[float | None] = []
        for row in result.itertuples(index=False):
            if int(row.side) == 0 or not math.isfinite(float(row.entry_price)):
                continuation.append(None); reversal.append(None)
                exit_times.append(None); exit_latency.append(None); continue
            target_time = pd.Timestamp(row.entry_time) + pd.Timedelta(minutes=horizon)
            position = int(times.searchsorted(target_time, side="left"))
            if position >= len(times):
                continuation.append(None); reversal.append(None)
                exit_times.append(None); exit_latency.append(None); continue
            exit_price = float(klines.iloc[position]["open"])
            gross = int(row.side) * (exit_price / float(row.entry_price) - 1.0)
            continuation.append(gross - ROUND_TRIP_COST_BPS / 10_000.0)
            reversal.append(-gross - ROUND_TRIP_COST_BPS / 10_000.0)
            exit_times.append(times[position])
            exit_latency.append((times[position] - target_time).total_seconds())
        result[f"cont_{horizon}m"] = continuation
        result[f"rev_{horizon}m"] = reversal
        result[f"exit_time_{horizon}m"] = exit_times
        result[f"exit_latency_seconds_{horizon}m"] = exit_latency
    return result


def run_one(args: argparse.Namespace) -> None:
    evaluation_start = date.fromisoformat(args.start)
    evaluation_end = date.fromisoformat(args.end)
    load_start = evaluation_start - timedelta(days=args.warmup_days)
    load_end = evaluation_end + timedelta(days=args.forward_days)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    events, klines, evidence = _load_symbol(
        symbol=args.symbol, start=load_start, end=load_end,
        cache=Path(args.cache), candidate05_path=Path(args.candidate05_path),
        candidate51_path=Path(args.candidate51_path),
    )
    events = _bin_labels(events)
    events = events[
        events["boundary"].dt.date.between(evaluation_start, evaluation_end)
    ].copy()
    events = _attach_paths(events, klines)
    events["symbol"] = args.symbol
    events["period_label"] = args.period_label
    events["split"] = args.split
    events["event_id"] = [
        f"{args.symbol}:{pd.Timestamp(value).isoformat()}"
        for value in events["boundary"]
    ]
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper": PAPER,
        "symbol": args.symbol,
        "period_label": args.period_label,
        "split": args.split,
        "evaluation_start": args.start,
        "evaluation_end": args.end,
        "load_start": load_start.isoformat(),
        "load_end": load_end.isoformat(),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "raw_evidence": evidence,
        "events": events.to_dict(orient="records"),
    }
    (output / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"symbol": args.symbol, "period": args.period_label, "events": len(events)}, indent=2))


def _global_boundary_choice(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values(
        ["boundary", "abs_imbalance", "opening_notional_burst", "symbol"],
        ascending=[True, False, False, True], kind="stable",
    )
    return ordered.drop_duplicates("boundary", keep="first").copy()


def _one_slot(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    choices = _global_boundary_choice(frame)
    selected: list[int] = []
    occupied_until: pd.Timestamp | None = None
    for idx, row in choices.sort_values("entry_time", kind="stable").iterrows():
        entry = pd.Timestamp(row["entry_time"])
        if occupied_until is not None and entry < occupied_until:
            continue
        selected.append(idx)
        occupied_until = entry + pd.Timedelta(minutes=horizon)
    return choices.loc[selected].copy()


def _group_rows(events: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups: list[tuple[str, pd.DataFrame]] = [("all", events)]
    for prefix, columns in (
        ("split", ["split"]), ("period", ["period_label"]),
        ("asset", ["symbol"]), ("phase", ["phase_minute"]),
        ("abs_imbalance", ["abs_imbalance_bin"]),
        ("burst", ["notional_burst_bin"]),
        ("same_phase_aligned", ["same_phase_aligned"]),
        ("top_of_hour", ["top_of_hour"]),
        ("funding_boundary", ["funding_boundary"]),
        ("split_asset", ["split", "symbol"]),
    ):
        for key, group in events.groupby(columns, sort=True, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            groups.append((f"{prefix}:" + ":".join(str(v) for v in values), group))
    for name, group in groups:
        for horizon in HORIZONS_MIN:
            for policy, column in (
                ("continuation", f"cont_{horizon}m"),
                ("reversal", f"rev_{horizon}m"),
            ):
                rows.append({
                    "group": name, "policy": policy, "horizon_min": horizon,
                    **_summary(group[column]),
                })
    return rows


def _period_robustness(events: pd.DataFrame, column: str) -> dict[str, Any]:
    period = events.groupby(["split", "period_label"], sort=True)[column].mean().dropna()
    if period.empty:
        return {"periods": 0}
    leave_one_out: list[float] = []
    for split, label in period.index:
        retained = events[~((events["split"] == split) & (events["period_label"] == label))]
        values = pd.to_numeric(retained[column], errors="coerce").dropna()
        if len(values):
            leave_one_out.append(float(values.mean()))
    return {
        "periods": int(len(period)),
        "positive_period_share": float((period > 0.0).mean()),
        "minimum_period_mean": float(period.min()),
        "median_period_mean": float(period.median()),
        "maximum_period_mean": float(period.max()),
        "leave_one_period_out_min": None if not leave_one_out else float(min(leave_one_out)),
    }


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    events = pd.concat([pd.DataFrame(payload["events"]) for payload in payloads], ignore_index=True)
    if events.empty:
        raise RuntimeError("no quarter-hour events")
    for column in ("boundary", "entry_time", "opening_first_time", "opening_last_time"):
        events[column] = pd.to_datetime(events[column], utc=True, errors="raise")
    events["same_phase_aligned"] = events["same_phase_aligned"].astype(bool)
    events["top_of_hour"] = events["top_of_hour"].astype(bool)
    events["funding_boundary"] = events["funding_boundary"].astype(bool)

    # Cross-asset breadth is descriptive and fixed: how many simultaneous
    # assets share the event direction at the same UTC boundary.
    sign_table = events.pivot_table(index="boundary", columns="symbol", values="side", aggfunc="first")
    for symbol in SYMBOLS:
        if symbol not in sign_table:
            sign_table[symbol] = 0
    sign_table = sign_table[list(SYMBOLS)].fillna(0)
    breadth: dict[tuple[pd.Timestamp, str], int] = {}
    for boundary, row in sign_table.iterrows():
        for symbol in SYMBOLS:
            side = int(row[symbol])
            breadth[(boundary, symbol)] = int((row == side).sum()) if side else 0
    events["cross_asset_breadth"] = [
        breadth.get((pd.Timestamp(row.boundary), str(row.symbol)), 0)
        for row in events.itertuples(index=False)
    ]

    rows = _group_rows(events)
    for breadth_value, group in events.groupby("cross_asset_breadth", sort=True):
        for horizon in HORIZONS_MIN:
            for policy, column in (("continuation", f"cont_{horizon}m"), ("reversal", f"rev_{horizon}m")):
                rows.append({
                    "group": f"breadth:{breadth_value}", "policy": policy,
                    "horizon_min": horizon, **_summary(group[column]),
                })

    expected_policy = {30: "reversal", 60: "continuation", 120: "continuation", 240: "continuation", 480: "continuation", 720: "continuation"}
    global_slots: dict[str, Any] = {}
    evaluation_days = int(sum((date.fromisoformat(p["evaluation_end"]) - date.fromisoformat(p["evaluation_start"])).days + 1 for p in payloads if p["symbol"] == SYMBOLS[0]))
    for horizon in HORIZONS_MIN:
        policy = expected_policy[horizon]
        column = f"{'rev' if policy == 'reversal' else 'cont'}_{horizon}m"
        selected = _one_slot(events, horizon)
        global_slots[f"{policy}:{horizon}m"] = {
            "evaluation_days": evaluation_days,
            "trades": int(len(selected)),
            "trades_per_day": float(len(selected) / max(evaluation_days, 1)),
            "performance": _summary(selected[column]),
            "by_split": {
                str(split): _summary(group[column])
                for split, group in selected.groupby("split", sort=True)
            },
            "by_asset": selected["symbol"].value_counts().sort_index().to_dict(),
            "period_robustness": _period_robustness(selected, column),
        }

    primary: dict[str, Any] = {}
    for horizon in (240, 480, 720):
        item = global_slots[f"continuation:{horizon}m"]
        performance = item["performance"]
        post = item["by_split"].get("post_publication", {})
        reasons: list[str] = []
        if performance.get("mean", 0.0) <= 0.0 or (performance.get("profit_factor") or 0.0) <= 1.0:
            reasons.append("global one-slot continuation is non-positive after 19 bp")
        if post.get("count", 0) and post.get("mean", 0.0) <= 0.0:
            reasons.append("post-publication continuation mean is non-positive")
        if performance.get("mean_without_best") is not None and performance["mean_without_best"] <= 0.0:
            reasons.append("best event removal eliminates the mean")
        if item["period_robustness"].get("positive_period_share", 0.0) < 1.0:
            reasons.append("chronological period sign is not stable")
        primary[str(horizon)] = {
            "status": "mechanism_survives_initial_falsification" if not reasons else "mechanism_not_stable",
            "reasons": reasons,
            "global_slot": item,
        }

    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper": PAPER,
        "source_runs": len(payloads),
        "symbols": sorted(events["symbol"].unique()),
        "periods": sorted(events["period_label"].unique()),
        "splits": sorted(events["split"].unique()),
        "evaluation_days": evaluation_days,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "events": int(len(events)),
        "global_slots": global_slots,
        "primary_decision": primary,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(rows).to_csv(output / "SUMMARY.csv", index=False)
    events.to_csv(output / "EVENTS.csv", index=False)

    md = [
        "# Quarter-hour opening order-flow post-sample audit", "",
        f"- source runs: {len(payloads)}", f"- assets: {', '.join(result['symbols'])}",
        f"- evaluation periods: {len(result['periods'])}", f"- evaluation days: {evaluation_days}",
        f"- events: {len(events)}", f"- cost screen: {ROUND_TRIP_COST_BPS:.0f} bp round trip",
        "- signal window: complete first 10 seconds of each UTC quarter-hour",
        "- causal entry: first observed aggregate trade at or after boundary+10 seconds",
        "- no fitted threshold; normalized imbalance sign is the direction",
        "- mechanism diagnostic, not NautilusTrader NAV", "",
        "## Global one-slot expected-policy results", "",
        "| policy | horizon | trades | trades/day | mean bp | median bp | win % | PF | post-publication bp | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for horizon in HORIZONS_MIN:
        policy = expected_policy[horizon]
        item = global_slots[f"{policy}:{horizon}m"]
        perf = item["performance"]
        post = item["by_split"].get("post_publication", {})
        pf = perf.get("profit_factor")
        status = primary.get(str(horizon), {}).get("status", "diagnostic")
        md.append(
            f"| {policy} | {horizon}m | {item['trades']} | {item['trades_per_day']:.3f} | "
            f"{10000*perf.get('mean', 0):.2f} | {10000*perf.get('median', 0):.2f} | "
            f"{100*perf.get('win_rate', 0):.1f} | {'na' if pf is None else f'{pf:.2f}'} | "
            f"{10000*post.get('mean', 0):.2f} | {status} |"
        )
    md += ["", "## Interpretation contract", "",
           "The paper's 2021--2024 result is not accepted by citation. The family advances only if the fixed 4h--12h continuation prediction remains positive after 19 bp in global one-slot routing, in every frozen chronological period including the July 2026 post-publication period, and after removing the best event. Fixed magnitude, burst, phase, funding and cross-asset breadth groups are diagnostics of failure or portability, not an optimized filter search.", ""]
    (output / "ANATOMY.md").write_text("\n".join(md), encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("symbol", "start", "end", "period_label", "split", "output"):
        run.add_argument(f"--{name.replace('_', '-')}", dest=name, required=True)
    run.add_argument("--cache", default=".cache/candidate-51-quarter-hour-v58")
    run.add_argument("--candidate05-path", default="research/candidate-05")
    run.add_argument("--candidate51-path", default="research/candidate-51")
    run.add_argument("--warmup-days", type=int, default=2)
    run.add_argument("--forward-days", type=int, default=1)
    run.set_defaults(func=run_one)
    agg = sub.add_parser("aggregate")
    agg.add_argument("--results-root", required=True)
    agg.add_argument("--output", required=True)
    agg.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
