#!/usr/bin/env python3
"""Causal anatomy test for the quarter-hour order-flow effect.

This is a statistical diagnostic, not a backtest engine.  It reuses the
checksum-verified Binance Vision ingestion from candidate-05 and measures the
geometry of quarter-hour causal episodes before any NautilusTrader strategy is
promoted.  Every signal uses only trades from the first ten seconds of a
quarter-hour minute and is considered observable at that minute's close.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np
import pandas as pd

PAPER = {
    "title": "The Quarter-Hour Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency Futures",
    "arxiv": "2607.09426",
    "source_hypotheses": [
        "first-ten-second quarter-hour order imbalance is phase-specific",
        "short-horizon response can reverse during the first half hour",
        "the imbalance direction predicts cumulative returns over four to twelve hours",
    ],
}

DELAYS_MIN = (1, 2, 15, 30, 60)
HORIZONS_MIN = (60, 120, 240, 480, 720)
PHASE_OFFSETS = {
    "quarter_hour": 0,
    "placebo_03": 3,
    "placebo_07": 7,
    "placebo_11": 11,
}
KEY_RETURN_COLUMNS = (
    "dir_bps_d1_h60",
    "dir_bps_d1_h240",
    "dir_bps_d1_h480",
    "dir_bps_d1_h720",
    "dir_bps_d30_h240",
    "dir_bps_d30_h480",
    "dir_bps_d30_h720",
    "dir_bps_d60_h240",
    "dir_bps_d60_h480",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _combine_agg(frames: list[pd.DataFrame]) -> pd.DataFrame:
    agg = pd.concat(frames).sort_index()
    if not agg.index.duplicated().any():
        return agg
    return agg.groupby(level=0, sort=True).agg(
        trade_open=("trade_open", "first"),
        trade_high=("trade_high", "max"),
        trade_low=("trade_low", "min"),
        trade_close=("trade_close", "last"),
        quantity_60s=("quantity_60s", "sum"),
        notional_60s=("notional_60s", "sum"),
        signed_notional_60s=("signed_notional_60s", "sum"),
        buy_notional_60s=("buy_notional_60s", "sum"),
        sell_notional_60s=("sell_notional_60s", "sum"),
        trade_count_60s=("trade_count_60s", "sum"),
        path_60s_bps=("path_60s_bps", "sum"),
        notional_15s=("notional_15s", "sum"),
        signed_notional_15s=("signed_notional_15s", "sum"),
        trade_count_15s=("trade_count_15s", "sum"),
        path_15s_bps=("path_15s_bps", "sum"),
        notional_open_10s=("notional_open_10s", "sum"),
        signed_notional_open_10s=("signed_notional_open_10s", "sum"),
        trade_count_open_10s=("trade_count_open_10s", "sum"),
    )


def load_minute_frame(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    source_path: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    sys.path.insert(0, str(source_path.resolve()))
    import features as source  # type: ignore
    try:
        from kline_only_inputs import _read_kline as read_kline_strict  # type: ignore
    except ImportError:
        read_kline_strict = source.read_kline

    warm_start = start - timedelta(days=1)
    forward_end = end + timedelta(days=1)
    klines: list[pd.DataFrame] = []
    aggs: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []

    day = warm_start
    while day <= forward_end:
        kline_path, _, kline_evidence = source.download_checked("klines", symbol, day, cache)
        agg_path, _, agg_evidence = source.download_checked("aggTrades", symbol, day, cache)
        klines.append(read_kline_strict(kline_path))
        aggs.append(source.aggregate_agg_trades(agg_path))
        evidence.extend([asdict(kline_evidence), asdict(agg_evidence)])
        day += timedelta(days=1)

    kline = pd.concat(klines, ignore_index=True).sort_values("open_time_dt")
    kline = kline.drop_duplicates("open_time_dt", keep="last").set_index("open_time_dt")
    agg = _combine_agg(aggs)
    frame = kline.join(agg, how="left").sort_index()

    denominator = frame["notional_60s"].replace(0.0, np.nan)
    open_denominator = frame["notional_open_10s"].replace(0.0, np.nan)
    frame["flow_60s"] = frame["signed_notional_60s"] / denominator
    frame["flow_open_10s"] = frame["signed_notional_open_10s"] / open_denominator
    frame["flow_3m"] = (
        frame["signed_notional_60s"].rolling(3, min_periods=3).sum()
        / frame["notional_60s"].rolling(3, min_periods=3).sum().replace(0.0, np.nan)
    )
    frame["ret_60s_bps"] = np.log(frame["trade_close"] / frame["trade_open"]) * 10_000.0
    frame["efficiency_60s"] = (
        frame["ret_60s_bps"].abs() / frame["path_60s_bps"].replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    frame["absorption_60s"] = frame["flow_60s"].abs() * (1.0 - frame["efficiency_60s"])
    past_notional = frame["notional_60s"].shift(1).rolling(120, min_periods=60).median()
    past_open = frame["notional_open_10s"].shift(1).rolling(120, min_periods=60).median()
    past_count = frame["trade_count_60s"].shift(1).rolling(120, min_periods=60).median()
    frame["notional_burst"] = frame["notional_60s"] / past_notional.replace(0.0, np.nan)
    frame["notional_open_10s_burst"] = frame["notional_open_10s"] / past_open.replace(0.0, np.nan)
    frame["trade_count_burst"] = frame["trade_count_60s"] / past_count.replace(0.0, np.nan)
    return frame, evidence


def _lookup(series: pd.Series, index: pd.DatetimeIndex, minutes: int) -> np.ndarray:
    target = index + pd.to_timedelta(minutes, unit="min")
    return series.reindex(target).to_numpy(dtype=float)


def build_events(
    frame: pd.DataFrame,
    start: date,
    end: date,
    *,
    phase: str,
    phase_offset: int,
) -> pd.DataFrame:
    qh = frame[((frame.index.minute - phase_offset) % 15 == 0)].copy()
    qh = qh[
        qh["flow_open_10s"].notna()
        & qh["trade_open"].notna()
        & qh["notional_open_10s"].gt(0.0)
    ].copy()
    if qh.empty:
        raise RuntimeError("no valid quarter-hour observations")

    qh["event_time"] = qh.index
    qh["phase"] = phase
    qh["phase_offset_minute"] = int(phase_offset)
    qh["direction"] = np.sign(qh["flow_open_10s"]).astype(int)
    qh = qh[qh["direction"].ne(0)].copy()
    qh["abs_flow_open_10s"] = qh["flow_open_10s"].abs()
    qh["aligned_ret_1m_bps"] = qh["direction"] * qh["ret_60s_bps"]
    qh["aligned_flow_60s"] = qh["direction"] * qh["flow_60s"]
    qh["phase_minute"] = qh.index.minute
    qh["hour_utc"] = qh.index.hour
    qh["funding_open"] = (qh.index.minute == 0) & qh.index.hour.isin([0, 8, 16])

    for lag in range(1, 13):
        qh[f"lag_flow_{lag}"] = qh["flow_open_10s"].shift(lag)
        qh[f"lag_ret_{lag}"] = qh["ret_60s_bps"].shift(lag)
    qh["lag_flow_consensus_3"] = (
        np.sign(qh[["lag_flow_1", "lag_flow_2", "lag_flow_3"]]).sum(axis=1)
    )
    qh["aligned_lag_flow_1"] = qh["direction"] * qh["lag_flow_1"]
    qh["aligned_lag_ret_1"] = qh["direction"] * qh["lag_ret_1"]
    qh["aligned_lag_consensus_3"] = qh["direction"] * qh["lag_flow_consensus_3"]

    for delay in DELAYS_MIN:
        qh[f"entry_price_d{delay}"] = _lookup(frame["open"], qh.index, delay)
    for horizon in HORIZONS_MIN:
        qh[f"exit_price_h{horizon}"] = _lookup(frame["open"], qh.index, horizon)
    for delay in DELAYS_MIN:
        for horizon in HORIZONS_MIN:
            if horizon <= delay:
                continue
            qh[f"dir_bps_d{delay}_h{horizon}"] = (
                qh["direction"]
                * np.log(qh[f"exit_price_h{horizon}"] / qh[f"entry_price_d{delay}"])
                * 10_000.0
            )

    qh["dir_bps_d1_h30"] = (
        qh["direction"]
        * np.log(_lookup(frame["open"], qh.index, 30) / qh["entry_price_d1"])
        * 10_000.0
    )
    qh["dir_bps_d1_h15"] = (
        qh["direction"]
        * np.log(_lookup(frame["open"], qh.index, 15) / qh["entry_price_d1"])
        * 10_000.0
    )

    state_index = qh.index + pd.to_timedelta(29, unit="min")
    for column in (
        "flow_3m",
        "flow_60s",
        "ret_60s_bps",
        "notional_burst",
        "notional_open_10s_burst",
        "trade_count_burst",
        "absorption_60s",
        "efficiency_60s",
    ):
        qh[f"state30_{column}"] = frame[column].reindex(state_index).to_numpy(dtype=float)
    qh["state30_aligned_flow_3m"] = qh["direction"] * qh["state30_flow_3m"]
    qh["state30_aligned_flow_60s"] = qh["direction"] * qh["state30_flow_60s"]
    qh["state30_aligned_ret_60s_bps"] = qh["direction"] * qh["state30_ret_60s_bps"]

    # Pre-specified causal scenario labels.  Labels involving state30 are only
    # eligible for entries delayed to minute 30 or later.
    qh["sc_all"] = True
    qh["sc_abs50"] = qh["abs_flow_open_10s"].ge(0.50)
    qh["sc_abs75"] = qh["abs_flow_open_10s"].ge(0.75)
    qh["sc_burst125"] = qh["notional_open_10s_burst"].ge(1.25)
    qh["sc_extreme_burst"] = qh["sc_abs50"] & qh["sc_burst125"]
    qh["sc_nonfunding_abs50"] = qh["sc_abs50"] & ~qh["funding_open"]
    qh["sc_nonfunding_extreme"] = qh["sc_extreme_burst"] & ~qh["funding_open"]
    qh["sc_aligned_1m"] = qh["sc_abs50"] & qh["aligned_ret_1m_bps"].gt(0.0)
    qh["sc_absorbed_1m"] = qh["sc_abs50"] & qh["aligned_ret_1m_bps"].le(0.0)
    qh["sc_persistent_flow"] = qh["sc_abs50"] & qh["aligned_lag_flow_1"].gt(0.0)
    qh["sc_consensus_flow"] = qh["sc_abs50"] & qh["aligned_lag_consensus_3"].ge(1.0)
    qh["sc_reversal30"] = qh["sc_abs50"] & qh["dir_bps_d1_h30"].lt(0.0)
    qh["sc_reversal30_5bps"] = qh["sc_abs50"] & qh["dir_bps_d1_h30"].lt(-5.0)
    qh["sc_reversal30_realign"] = qh["sc_reversal30"] & qh["state30_aligned_flow_3m"].gt(0.0)
    qh["sc_reversal30_realign_burst"] = (
        qh["sc_reversal30_realign"] & qh["state30_notional_burst"].ge(1.0)
    )
    qh["sc_reversal30_realign_price"] = (
        qh["sc_reversal30_realign"] & qh["state30_aligned_ret_60s_bps"].gt(0.0)
    )
    qh["sc_reversal30_full"] = (
        qh["sc_reversal30_5bps"]
        & qh["state30_aligned_flow_3m"].gt(0.0)
        & qh["state30_aligned_ret_60s_bps"].gt(0.0)
        & qh["state30_notional_burst"].ge(1.0)
    )
    in_range = (qh["event_time"].dt.date >= start) & (qh["event_time"].dt.date <= end)
    return qh.loc[in_range].reset_index(drop=True)


def _profit_factor(values: np.ndarray) -> float | None:
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 0:
        return None
    return float(gains / losses)


def _summarize_values(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    n = int(clean.size)
    if n == 0:
        return {"count": 0}
    mean = float(np.mean(clean))
    std = float(np.std(clean, ddof=1)) if n > 1 else 0.0
    t_stat = mean / (std / math.sqrt(n)) if n > 1 and std > 0 else None
    return {
        "count": n,
        "mean_bps": mean,
        "median_bps": float(np.median(clean)),
        "std_bps": std,
        "t_stat_naive": t_stat,
        "hit_rate": float(np.mean(clean > 0.0)),
        "win_count": int(np.sum(clean > 0.0)),
        "loss_count": int(np.sum(clean < 0.0)),
        "flat_count": int(np.sum(clean == 0.0)),
        "taker_10bp_net_mean": mean - 10.0,
        "maker_4bp_net_mean": mean - 4.0,
        "taker_10bp_win_rate": float(np.mean(clean > 10.0)),
        "profit_factor_gross": _profit_factor(clean),
        "q10_bps": float(np.quantile(clean, 0.10)),
        "q25_bps": float(np.quantile(clean, 0.25)),
        "q75_bps": float(np.quantile(clean, 0.75)),
        "q90_bps": float(np.quantile(clean, 0.90)),
        "sum_bps": float(np.sum(clean)),
    }


def _parse_return_column(column: str) -> tuple[int, int]:
    # dir_bps_d30_h480
    pieces = column.split("_")
    delay = int(next(part[1:] for part in pieces if part.startswith("d") and part[1:].isdigit()))
    horizon = int(next(part[1:] for part in pieces if part.startswith("h") and part[1:].isdigit()))
    return delay, horizon


def _greedy_nonoverlap(frame: pd.DataFrame, return_column: str) -> pd.DataFrame:
    delay, horizon = _parse_return_column(return_column)
    chosen: list[int] = []
    occupied_until: pd.Timestamp | None = None
    order_columns = ["event_time"]
    ascending = [True]
    for candidate in ("abs_flow_open_10s", "notional_open_10s_burst"):
        if candidate in frame.columns:
            order_columns.append(candidate)
            ascending.append(False)
    if "symbol" in frame.columns:
        order_columns.append("symbol")
        ascending.append(True)
    ordered = frame.sort_values(order_columns, ascending=ascending, kind="stable")
    for idx, row in ordered.iterrows():
        event_time = pd.Timestamp(row["event_time"])
        entry_time = event_time + pd.Timedelta(minutes=delay)
        if occupied_until is not None and entry_time < occupied_until:
            continue
        if pd.isna(row.get(return_column)):
            continue
        chosen.append(idx)
        occupied_until = event_time + pd.Timedelta(minutes=horizon)
    return frame.loc[chosen].copy()


def scenario_summaries(events: pd.DataFrame) -> dict[str, Any]:
    scenarios = [column for column in events.columns if column.startswith("sc_")]
    results: dict[str, Any] = {}
    evaluation_days = int(events["event_time"].dt.date.nunique()) if not events.empty else 0
    for scenario in scenarios:
        selected = events[events[scenario].fillna(False).astype(bool)].copy()
        scenario_result: dict[str, Any] = {
            "events": int(len(selected)),
            "calendar_days": evaluation_days,
            "returns": {},
        }
        for column in KEY_RETURN_COLUMNS:
            if column not in selected:
                continue
            delay, _ = _parse_return_column(column)
            if scenario.startswith("sc_reversal30") and delay < 30:
                continue
            summary = _summarize_values(selected[column])
            independent = _greedy_nonoverlap(selected, column)
            summary["independent"] = _summarize_values(independent[column])
            if summary["independent"].get("count", 0) and scenario_result["calendar_days"]:
                summary["independent"]["events_per_day"] = (
                    summary["independent"]["count"] / scenario_result["calendar_days"]
                )
            scenario_result["returns"][column] = summary
        results[scenario] = scenario_result
    return results


def run_one(args: argparse.Namespace) -> None:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    frame, evidence = load_minute_frame(
        symbol=args.symbol,
        start=start,
        end=end,
        cache=Path(args.cache),
        source_path=Path(args.source_path),
    )
    phase_frames = [
        build_events(
            frame,
            start,
            end,
            phase=phase,
            phase_offset=offset,
        )
        for phase, offset in PHASE_OFFSETS.items()
    ]
    events = pd.concat(phase_frames, ignore_index=True)
    events["symbol"] = args.symbol
    events["period_label"] = args.period_label
    events["split"] = args.split

    opening_flow: dict[str, Any] = {}
    for phase, phase_events in events.groupby("phase", sort=True):
        values = phase_events["flow_open_10s"].to_numpy(dtype=float)
        opening_flow[str(phase)] = {
            "count": int(values.size),
            "mean": _safe_float(np.mean(values)),
            "std": _safe_float(np.std(values, ddof=1)),
            "median_abs": _safe_float(np.median(np.abs(values))),
            "ar1": _safe_float(pd.Series(values).autocorr(1)),
            "abs_ge_050_share": _safe_float(np.mean(np.abs(values) >= 0.50)),
            "abs_ge_075_share": _safe_float(np.mean(np.abs(values) >= 0.75)),
        }
    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper": PAPER,
        "symbol": args.symbol,
        "period_label": args.period_label,
        "split": args.split,
        "start": args.start,
        "end": args.end,
        "event_count": int(len(events)),
        "event_days": int(events["event_time"].dt.date.nunique()),
        "opening_flow": opening_flow,
        "phase_summaries": {
            str(phase): scenario_summaries(phase_events.copy())
            for phase, phase_events in events.groupby("phase", sort=True)
        },
        "raw_evidence": evidence,
        "events": events.to_dict(orient="records"),
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: result[k] for k in ("symbol", "period_label", "event_count", "opening_flow")}, indent=2))


def _normalize_event_times(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["event_time"] = pd.to_datetime(result["event_time"], utc=True, errors="raise")
    for column in result.columns:
        if column.startswith("sc_"):
            result[column] = result[column].fillna(False).astype(bool)
    return result


def _summary_rows(
    summary: dict[str, Any],
    split: str,
    asset: str,
    phase: str,
    return_column: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario, payload in summary.items():
        metric = payload.get("returns", {}).get(return_column, {})
        independent = metric.get("independent", {})
        rows.append(
            {
                "split": split,
                "asset": asset,
                "phase": phase,
                "scenario": scenario,
                "return_column": return_column,
                "events": metric.get("count", 0),
                "independent_events": independent.get("count", 0),
                "independent_events_per_day": independent.get("events_per_day"),
                "mean_bps": metric.get("mean_bps"),
                "net10_mean_bps": metric.get("taker_10bp_net_mean"),
                "hit_rate": metric.get("hit_rate"),
                "profit_factor": metric.get("profit_factor_gross"),
                "independent_mean_bps": independent.get("mean_bps"),
                "independent_net10_mean_bps": independent.get("taker_10bp_net_mean"),
                "independent_hit_rate": independent.get("hit_rate"),
                "independent_profit_factor": independent.get("profit_factor_gross"),
            }
        )
    return rows


def _fmt(value: Any, digits: int = 2) -> str:
    number = _safe_float(value)
    return "na" if number is None else f"{number:.{digits}f}"


def aggregate(args: argparse.Namespace) -> None:
    root = Path(args.results_root)
    paths = sorted(root.rglob("result.json"))
    if not paths:
        raise RuntimeError(f"no result.json under {root}")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    event_frames = [_normalize_event_times(pd.DataFrame(payload["events"])) for payload in payloads]
    events = pd.concat(event_frames, ignore_index=True).sort_values(["event_time", "symbol"])

    grouped: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    group_specs: list[tuple[str, str, str, pd.DataFrame]] = []

    def add_phase_groups(split: str, asset: str, frame: pd.DataFrame) -> None:
        for phase, phase_frame in frame.groupby("phase", sort=True):
            group_specs.append((split, asset, str(phase), phase_frame))
        placebo = frame[frame["phase"].ne("quarter_hour")].copy()
        if not placebo.empty:
            group_specs.append((split, asset, "PLACEBO_POOLED", placebo))

    add_phase_groups("all", "ALL", events)
    for split, split_frame in events.groupby("split", sort=True):
        add_phase_groups(str(split), "ALL", split_frame)
        for symbol, asset_frame in split_frame.groupby("symbol", sort=True):
            add_phase_groups(str(split), str(symbol), asset_frame)
    for split, asset, phase, frame in group_specs:
        key = f"{split}:{asset}:{phase}"
        summary = scenario_summaries(frame)
        grouped[key] = {
            "split": split,
            "asset": asset,
            "phase": phase,
            "event_count": int(len(frame)),
            "days": int(frame["event_time"].dt.date.nunique()),
            "scenario_summaries": summary,
        }
        for return_column in ("dir_bps_d30_h240", "dir_bps_d30_h480", "dir_bps_d30_h720"):
            rows.extend(_summary_rows(summary, split, asset, phase, return_column))

    table = pd.DataFrame(rows)
    ranking = table[
        (table["asset"] == "ALL")
        & (table["phase"] == "quarter_hour")
        & (table["return_column"] == "dir_bps_d30_h480")
        & (table["independent_events"] >= 5)
    ].copy()
    ranking = ranking.sort_values(
        ["split", "independent_net10_mean_bps", "independent_events"],
        ascending=[True, False, False],
    )

    spread_source = table[
        (table["asset"] == "ALL")
        & (table["return_column"] == "dir_bps_d30_h480")
        & (table["phase"].isin(PHASE_OFFSETS))
    ].copy()
    spread_wide = spread_source.pivot_table(
        index=["split", "scenario"],
        columns="phase",
        values=["independent_mean_bps", "independent_net10_mean_bps", "independent_events"],
        aggfunc="first",
    )
    spread_wide.columns = [f"{metric}_{phase}" for metric, phase in spread_wide.columns]
    spread_wide = spread_wide.reset_index()
    placebo_phases = [phase for phase in PHASE_OFFSETS if phase != "quarter_hour"]
    for metric in ("independent_mean_bps", "independent_net10_mean_bps", "independent_events"):
        columns = [f"{metric}_{phase}" for phase in placebo_phases if f"{metric}_{phase}" in spread_wide]
        if columns:
            spread_wide[f"{metric}_placebo_median"] = spread_wide[columns].median(axis=1, skipna=True)
    if {
        "independent_mean_bps_quarter_hour",
        "independent_mean_bps_placebo_median",
    } <= set(spread_wide.columns):
        spread_wide["gross_phase_spread_bps"] = (
            spread_wide["independent_mean_bps_quarter_hour"]
            - spread_wide["independent_mean_bps_placebo_median"]
        )
        spread_wide["net10_phase_spread_bps"] = (
            spread_wide["independent_net10_mean_bps_quarter_hour"]
            - spread_wide["independent_net10_mean_bps_placebo_median"]
        )
    phase_spread = spread_wide.sort_values(
        ["split", "net10_phase_spread_bps"], ascending=[True, False]
    ) if "net10_phase_spread_bps" in spread_wide else spread_wide


    result = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "paper": PAPER,
        "source_result_count": len(payloads),
        "event_count": int(len(events)),
        "symbols": sorted(events["symbol"].unique().tolist()),
        "period_labels": sorted(events["period_label"].unique().tolist()),
        "splits": sorted(events["split"].unique().tolist()),
        "grouped": grouped,
        "ranking": ranking.to_dict(orient="records"),
        "phase_spread": phase_spread.to_dict(orient="records"),
        "source_manifests": [
            {k: payload.get(k) for k in ("symbol", "period_label", "split", "start", "end", "event_count", "opening_flow")}
            for payload in payloads
        ],
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )

    md: list[str] = [
        "# Quarter-hour causal episode anatomy",
        "",
        f"- source runs: {len(payloads)}",
        f"- events: {len(events)}",
        f"- symbols: {', '.join(result['symbols'])}",
        f"- periods: {', '.join(result['period_labels'])}",
        "- signal: signed first-ten-second taker notional imbalance in minutes 00/15/30/45",
        "- placebos: identical first-ten-second construction at minute offsets 03/07/11",
        "- causal availability: quarter-hour minute close; d30 entries use state observed through minute 29",
        "- figures below are directional gross basis points, not a NAV backtest",
        "",
        "## 30-minute-delay to 8-hour-boundary ranking",
        "",
        "| split | scenario | independent n | n/day | gross mean bp | after 10bp | hit | PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in ranking.head(40).iterrows():
        md.append(
            "| {split} | {scenario} | {n} | {rate} | {gross} | {net} | {hit} | {pf} |".format(
                split=row["split"],
                scenario=row["scenario"],
                n=int(row["independent_events"]),
                rate=_fmt(row["independent_events_per_day"], 3),
                gross=_fmt(row["independent_mean_bps"]),
                net=_fmt(row["independent_net10_mean_bps"]),
                hit=_fmt(None if pd.isna(row["independent_hit_rate"]) else 100.0 * row["independent_hit_rate"], 1),
                pf=_fmt(row["independent_profit_factor"]),
            )
        )
    md.extend(
        [
            "",
            "## Quarter-hour minus median placebo-phase spread (d30 to h480)",
            "",
            "| split | scenario | qh independent n | placebo median n | gross spread bp | net10 spread bp |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in phase_spread.head(40).iterrows():
        md.append(
            "| {split} | {scenario} | {qn} | {pn} | {gross} | {net} |".format(
                split=row["split"],
                scenario=row["scenario"],
                qn=int(row.get("independent_events_quarter_hour", 0) or 0),
                pn=int(row.get("independent_events_placebo_median", 0) or 0),
                gross=_fmt(row.get("gross_phase_spread_bps")),
                net=_fmt(row.get("net10_phase_spread_bps")),
            )
        )
    md.extend(
        [
            "",
            "## Interpretation contract",
            "",
            "A positive conditional mean is only a mechanism clue. Promotion requires a NautilusTrader strategy with executable entry timing, fees, slippage, market impact, funding, one global position, risk-sized quantity, and continuous NAV. Overlapping quarter-hour observations are not counted as independent trades; the ranking uses a greedy non-overlap view for the stated horizon.",
            "",
        ]
    )
    (output / "ANATOMY.md").write_text("\n".join(md), encoding="utf-8")
    table.to_csv(output / "SUMMARY.csv", index=False)
    print(json.dumps({"source_runs": len(payloads), "events": len(events), "output": str(output)}, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--symbol", required=True)
    run.add_argument("--start", required=True)
    run.add_argument("--end", required=True)
    run.add_argument("--period-label", required=True)
    run.add_argument("--split", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--cache", default=".cache/candidate-51-quarter-hour")
    run.add_argument("--source-path", default="research/candidate-05")
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
