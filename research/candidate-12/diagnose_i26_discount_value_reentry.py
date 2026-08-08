#!/usr/bin/env python3
"""Discount-close failed-acceptance value-reentry diagnostic.

Development-only. The frozen I19 router already handles failed downside
acceptance when the completed source session closed in mid value, targeting the
opposite boundary after a new sell-side sweep and bullish MSS/FVG. It correctly
does not reuse that full-range objective when the source session itself closed
near its low.

This diagnostic tests a separate auction leg for that omitted state:

completed session closes in discount
-> later downside acceptance below the completed low
-> acceptance fails back inside
-> strictly later bullish displacement breaks the failure-bar high and creates
   a fresh bullish FVG
-> market entry at the completed confirmation close
-> invalidation at the local displacement leg low
-> objective is the source session VWAP fixed before the failure.

The source VWAP, failure event and all confirmation bars are observed before the
plan. Existing I19 thresholds and cost model are reused; no new fitted numeric
parameter is introduced. Matching, fills, account state and NAV are not modeled
here, so this is not performance evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_loader import load_binance_bars

NS_MINUTE = 60_000_000_000


def aggregate_five(one: pd.DataFrame, atr_period: int) -> pd.DataFrame:
    out = one.resample("5min", label="right", closed="right").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        taker_buy_volume=("taker_buy_volume", "sum"),
        count=("close", "count"),
    )
    out = out[out["count"] == 5].drop(columns=["count"])
    previous = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous).abs(),
            (out["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.rolling(atr_period, min_periods=atr_period).mean()
    out["body"] = (out["close"] - out["open"]).abs()
    spread = out["high"] - out["low"]
    out["close_location"] = np.where(
        spread > 0,
        (out["close"] - out["low"]) / spread,
        0.5,
    )
    return out


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_positions(path: Path) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    frame = pd.read_csv(path)
    result: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for row in frame.itertuples(index=False):
        start = pd.Timestamp(row.ts_opened)
        end = pd.Timestamp(row.ts_closed)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
        result.append((start, end))
    return result


def overlaps(
    start: pd.Timestamp,
    end: pd.Timestamp,
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> bool:
    return any(start <= b and end >= a for a, b in intervals)


def round_tick(value: float, tick: float, side: str) -> float:
    units = value / tick
    if side == "UP":
        return float(np.ceil(units - 1e-12) * tick)
    return float(np.floor(units + 1e-12) * tick)


def costed_geometry(
    *,
    entry_raw: float,
    stop_raw: float,
    target_raw: float,
    decision: pd.Series,
    config: dict[str, Any],
) -> tuple[dict[str, float] | None, str]:
    tick = float(config["price_increment"])
    entry = round_tick(entry_raw, tick, "UP")
    stop = round_tick(stop_raw, tick, "DOWN")
    target = round_tick(target_raw, tick, "DOWN")
    loss_distance = entry - stop
    profit_distance = target - entry
    atr = float(decision.atr)
    if not np.isfinite(atr):
        return None, "ATR_UNAVAILABLE"
    if loss_distance <= 0:
        return None, "INVALID_LOCAL_STOP"
    if loss_distance > float(config["max_stop_atr"]) * atr:
        return None, "STOP_EXCEEDS_MAX_ATR"
    if profit_distance <= 0:
        return None, "SOURCE_VWAP_NOT_ABOVE_ENTRY"
    if float(decision.high) >= target:
        return None, "SOURCE_VWAP_PRECONSUMED"
    entry_cost = entry * float(config["effective_taker_rate"])
    stop_cost = stop * float(config["effective_taker_rate"])
    target_cost = target * float(config["effective_maker_rate"])
    slippage = float(config["tick_slippage_units"]) * tick
    loss = loss_distance + entry_cost + stop_cost + slippage
    profit = profit_distance - entry_cost - target_cost - slippage
    if loss <= 0 or profit <= 0:
        return None, "NON_POSITIVE_COSTED_EXPECTANCY"
    net_r = profit / loss
    if net_r < float(config["min_net_r"]):
        return None, "INSUFFICIENT_COSTED_R"
    return (
        {
            "entry": entry,
            "stop": stop,
            "target": target,
            "loss_per_unit": loss,
            "profit_per_unit": profit,
            "net_r": net_r,
            "structural_loss": loss_distance,
            "structural_profit": profit_distance,
        },
        "OK",
    )


def first_touch(
    one: pd.DataFrame,
    *,
    observed: pd.Timestamp,
    end: pd.Timestamp,
    stop: float,
    target: float,
) -> dict[str, Any]:
    future = one[(one.index > observed) & (one.index <= end)]
    for ts, row in future.iterrows():
        hit_stop = float(row.low) <= stop
        hit_target = float(row.high) >= target
        if hit_stop and hit_target:
            return {"outcome": "AMBIGUOUS_SAME_MINUTE", "terminal_ts": ts.isoformat()}
        if hit_target:
            return {"outcome": "TARGET", "terminal_ts": ts.isoformat()}
        if hit_stop:
            return {"outcome": "STOP", "terminal_ts": ts.isoformat()}
    return {"outcome": "UNRESOLVED", "terminal_ts": end.isoformat()}


def session_vwap(
    one: pd.DataFrame,
    *,
    frozen_ts: pd.Timestamp,
    label: str,
) -> tuple[float, pd.Timestamp, pd.Timestamp]:
    if label == "ASIA":
        start = frozen_ts.normalize()
        end = frozen_ts
    elif label == "LONDON":
        end = frozen_ts
        start = end - pd.Timedelta(hours=6)
    else:
        raise ValueError(label)
    frame = one[(one.index > start) & (one.index <= end)]
    if frame.empty or float(frame.volume.sum()) <= 0:
        raise RuntimeError(f"missing completed {label} session volume at {frozen_ts}")
    typical = (frame.high + frame.low + frame.close) / 3.0
    value = float((typical * frame.volume).sum() / frame.volume.sum())
    return value, start, end


def fresh_bull_fvg(
    five: pd.DataFrame,
    index: int,
    body_atr: float,
    min_close_location: float,
) -> dict[str, float] | None:
    if index < 2:
        return None
    first = five.iloc[index - 2]
    displacement = five.iloc[index - 1]
    current = five.iloc[index]
    if not np.isfinite(displacement.atr) or float(displacement.atr) <= 0:
        return None
    if not (
        float(current.low) > float(first.high)
        and float(displacement.close) > float(displacement.open)
        and float(displacement.body) / float(displacement.atr) >= body_atr
        and float(displacement.close_location) >= min_close_location
    ):
        return None
    return {
        "lower": float(first.high),
        "upper": float(current.low),
        "displacement_low": float(displacement.low),
        "displacement_close": float(displacement.close),
        "body_atr": float(displacement.body) / float(displacement.atr),
        "close_location": float(displacement.close_location),
    }


def diagnose_week(
    *,
    week: str,
    config: dict[str, Any],
    baseline_root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    week_spec = config["selection"]["weeks"][week]
    evaluation_start = date.fromisoformat(week_spec["start"])
    evaluation_end = date.fromisoformat(week_spec["end_exclusive"])
    warmup = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
    one, manifest = load_binance_bars("BTCUSDT", warmup, evaluation_end, data_dir)
    logic = dict(config["logic"])
    logic["price_increment"] = float(config["symbols"]["BTCUSDT"]["price_increment"])
    five = aggregate_five(one, int(logic["atr_period"]))
    events = read_events(baseline_root / f"BTCUSDT-{week}" / "scenario_events.jsonl")
    occupied = parse_positions(baseline_root / f"BTCUSDT-{week}" / "positions.csv")

    ranges: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if event["event_type"] != "SESSION_RANGE_FROZEN":
            continue
        ts = pd.Timestamp(event["observed_time_ns"], unit="ns", tz="UTC")
        details = event["details"]
        label = str(details["label"])
        vwap, start, end = session_vwap(one, frozen_ts=ts, label=label)
        ranges[(ts.date().isoformat(), label)] = {
            "label": label,
            "frozen_ts": ts,
            "start": start,
            "end": end,
            "high": float(details["high"]),
            "low": float(details["low"]),
            "close": float(details["close"]),
            "close_location": float(details["close_location"]),
            "width": float(details["width"]),
            "vwap": vwap,
            "trade_end": ts.normalize()
            + pd.Timedelta(
                minutes=int(details["trade_end_minute"])
            ),
        }

    records: list[dict[str, Any]] = []
    for event in events:
        if event["event_type"] != "LOW_ACCEPTANCE_FAILED_BACK_INSIDE":
            continue
        failure_ts = pd.Timestamp(event["observed_time_ns"], unit="ns", tz="UTC")
        label = str(event["details"]["source"])
        source = ranges.get((failure_ts.date().isoformat(), label))
        if source is None:
            continue
        # Mid-value failures already belong to the I19 full-range family.
        # Premium-close failures belong to the existing reacceptance family.
        if source["close_location"] >= float(logic["low_acceptance_discount_close_cutoff"]):
            continue
        if failure_ts not in five.index:
            continue
        failure_index = int(five.index.get_loc(failure_ts))
        failure_bar = five.iloc[failure_index]
        expiry = min(
            len(five) - 1,
            failure_index + int(logic["delayed_rejection_expiry_bars"]),
        )
        base_record = {
            "week": week,
            "failure_ts": failure_ts.isoformat(),
            "source": source,
            "failure_bar": {
                "open": float(failure_bar.open),
                "high": float(failure_bar.high),
                "low": float(failure_bar.low),
                "close": float(failure_bar.close),
                "atr": float(failure_bar.atr),
            },
            "confirmation": None,
            "geometry": None,
            "geometry_reason": "NO_LOCAL_BULLISH_MSS_FVG_BEFORE_EXPIRY",
            "outcome": "NO_TRADE",
            "terminal_ts": None,
            "overlaps_i19": False,
        }
        for index in range(failure_index + 1, expiry + 1):
            ts = five.index[index]
            if ts > source["trade_end"]:
                break
            row = five.iloc[index]
            fresh = fresh_bull_fvg(
                five,
                index,
                float(logic["delayed_rejection_fvg_body_atr"]),
                1.0 - float(logic["delayed_rejection_fvg_max_close_location"]),
            )
            if fresh is None:
                continue
            if not (
                fresh["displacement_close"] > float(failure_bar.high)
                and float(row.close) > source["low"]
            ):
                continue
            local_stop = (
                min(fresh["displacement_low"], float(row.low))
                - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
            )
            geometry, reason = costed_geometry(
                entry_raw=float(row.close),
                stop_raw=local_stop,
                target_raw=float(source["vwap"]),
                decision=row,
                config=logic,
            )
            base_record["confirmation"] = {
                "observed_ts": ts.isoformat(),
                "fvg": fresh,
                "decision_open": float(row.open),
                "decision_high": float(row.high),
                "decision_low": float(row.low),
                "decision_close": float(row.close),
                "decision_atr": float(row.atr),
                "structural_stop": local_stop,
            }
            base_record["geometry"] = geometry
            base_record["geometry_reason"] = reason
            if geometry is None:
                base_record["outcome"] = "REJECTED_GEOMETRY"
            else:
                terminal = first_touch(
                    one,
                    observed=ts,
                    end=source["trade_end"],
                    stop=geometry["stop"],
                    target=geometry["target"],
                )
                base_record.update(terminal)
                base_record["overlaps_i19"] = overlaps(
                    ts,
                    pd.Timestamp(terminal["terminal_ts"]),
                    occupied,
                )
            break
        records.append(base_record)

    costed = [r for r in records if r["geometry"] is not None]
    additive = [r for r in costed if not r["overlaps_i19"]]
    return {
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "manifest": manifest,
        "raw_failure_states": len(records),
        "costed_candidates": len(costed),
        "additive_costed_candidates": len(additive),
        "additive_outcomes": {
            name: sum(1 for r in additive if r["outcome"] == name)
            for name in ("TARGET", "STOP", "UNRESOLVED", "AMBIGUOUS_SAME_MINUTE")
        },
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.json"),
    )
    parser.add_argument("--weeks", nargs="+", default=["W1", "W12"])
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    result = {
        "schema": "candidate-12-i26-discount-value-reentry-v1",
        "candidate_source": config["candidate"],
        "not_performance_evidence": True,
        "policy": (
            "discount-close completed session -> failed downside acceptance -> "
            "later local bullish MSS/FVG -> local stop -> pre-frozen session VWAP"
        ),
        "weeks": {},
    }
    for week in args.weeks:
        result["weeks"][week] = diagnose_week(
            week=week,
            config=config,
            baseline_root=args.baseline_root,
            data_dir=args.data_dir,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                week: {
                    key: value
                    for key, value in data.items()
                    if key not in {"manifest", "records"}
                }
                for week, data in result["weeks"].items()
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
