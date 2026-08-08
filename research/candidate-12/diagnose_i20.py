#!/usr/bin/env python3
"""Screen independent initial-balance auction scenarios for Candidate 12.

This is a development diagnostic, not a performance claim. It reuses the
immutable Binance loader and only completed bars. The purpose is to answer a
single structural question before production code is changed: can a completed
30-minute opening balance create a separate failed-auction or true-acceptance
leg which is not already occupied by the current completed-session router?
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from data_loader import load_binance_bars


@dataclass(frozen=True, slots=True)
class OpeningBalance:
    name: str
    start_minute: int
    end_minute: int
    trade_end_minute: int


@dataclass(slots=True)
class BreakEpisode:
    side: str
    first_index: int
    first_ts: pd.Timestamp
    outside_closes: int
    extreme: float
    pv: float
    volume: float
    accepted: bool = False
    accepted_index: int | None = None
    reclaim_index: int | None = None
    reclaim_ts: pd.Timestamp | None = None
    reclaim_high: float | None = None
    reclaim_low: float | None = None
    reclaim_close: float | None = None
    reclaim_vwap: float | None = None


BALANCES = (
    OpeningBalance("ASIA_OPENING_BALANCE", 0, 30, 360),
    OpeningBalance("LONDON_OPENING_BALANCE", 360, 390, 720),
    OpeningBalance("NEW_YORK_OPENING_BALANCE", 720, 750, 1080),
)


def aggregate_five(one: pd.DataFrame) -> pd.DataFrame:
    grouped = one.resample("5min", label="right", closed="right")
    out = grouped.agg(
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
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous).abs(),
            (out["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(36, min_periods=36).mean()
    out["body"] = (out["close"] - out["open"]).abs()
    spread = out["high"] - out["low"]
    out["close_location"] = np.where(
        spread > 0,
        (out["close"] - out["low"]) / spread,
        0.5,
    )
    out["typical"] = (out["high"] + out["low"] + out["close"]) / 3.0
    out["signed_flow"] = np.where(
        out["volume"] > 0,
        2.0 * out["taker_buy_volume"] / out["volume"] - 1.0,
        0.0,
    ).clip(-1.0, 1.0)
    return out


def parse_positions(path: Path) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for row in frame.itertuples(index=False):
        opened = pd.Timestamp(row.ts_opened)
        closed = pd.Timestamp(row.ts_closed)
        opened = opened.tz_localize("UTC") if opened.tzinfo is None else opened.tz_convert("UTC")
        closed = closed.tz_localize("UTC") if closed.tzinfo is None else closed.tz_convert("UTC")
        intervals.append((opened, closed))
    return intervals


def overlaps(ts: pd.Timestamp, intervals: Iterable[tuple[pd.Timestamp, pd.Timestamp]]) -> bool:
    return any(start <= ts <= end for start, end in intervals)


def round_tick(value: float, tick: float, side: str) -> float:
    units = value / tick
    if side == "UP":
        return float(np.ceil(units - 1e-12) * tick)
    return float(np.floor(units + 1e-12) * tick)


def costed_geometry(
    *,
    direction: str,
    entry_raw: float,
    stop_raw: float,
    target_raw: float,
    decision: pd.Series,
    config: dict[str, Any],
) -> dict[str, float] | None:
    tick = float(config["price_increment"])
    if direction == "LONG":
        entry = round_tick(entry_raw, tick, "UP")
        stop = round_tick(stop_raw, tick, "DOWN")
        target = round_tick(target_raw, tick, "DOWN")
        structural_loss = entry - stop
        structural_profit = target - entry
        preconsumed = float(decision.high) >= target
    else:
        entry = round_tick(entry_raw, tick, "DOWN")
        stop = round_tick(stop_raw, tick, "UP")
        target = round_tick(target_raw, tick, "UP")
        structural_loss = stop - entry
        structural_profit = entry - target
        preconsumed = float(decision.low) <= target
    atr = float(decision.atr)
    if (
        not np.isfinite(atr)
        or structural_loss <= 0
        or structural_loss > float(config["max_stop_atr"]) * atr
        or structural_profit <= 0
        or preconsumed
    ):
        return None
    entry_cost = entry * float(config["effective_taker_rate"])
    stop_cost = stop * float(config["effective_taker_rate"])
    target_cost = target * float(config["effective_maker_rate"])
    slippage = float(config["tick_slippage_units"]) * tick
    loss = structural_loss + entry_cost + stop_cost + slippage
    profit = structural_profit - entry_cost - target_cost - slippage
    if loss <= 0 or profit <= 0:
        return None
    net_r = profit / loss
    if net_r < float(config["min_net_r"]):
        return None
    return {
        "entry": entry,
        "stop": stop,
        "target": target,
        "loss_per_unit": loss,
        "profit_per_unit": profit,
        "net_r": net_r,
    }


def first_touch(
    one: pd.DataFrame,
    *,
    observed: pd.Timestamp,
    end: pd.Timestamp,
    direction: str,
    stop: float,
    target: float,
) -> dict[str, Any]:
    future = one[(one.index > observed) & (one.index <= end)]
    for ts, row in future.iterrows():
        if direction == "LONG":
            hit_stop = float(row.low) <= stop
            hit_target = float(row.high) >= target
        else:
            hit_stop = float(row.high) >= stop
            hit_target = float(row.low) <= target
        if hit_stop and hit_target:
            return {"outcome": "AMBIGUOUS_SAME_MINUTE", "terminal_ts": ts.isoformat()}
        if hit_target:
            return {"outcome": "TARGET", "terminal_ts": ts.isoformat()}
        if hit_stop:
            return {"outcome": "STOP", "terminal_ts": ts.isoformat()}
    return {"outcome": "UNRESOLVED", "terminal_ts": end.isoformat()}


def fresh_bear_fvg(
    five: pd.DataFrame,
    idx: int,
    body_atr: float,
    close_cut: float,
) -> dict[str, float] | None:
    if idx < 2:
        return None
    first = five.iloc[idx - 2]
    displacement = five.iloc[idx - 1]
    current = five.iloc[idx]
    if not np.isfinite(displacement.atr) or displacement.atr <= 0:
        return None
    if not (
        float(current.high) < float(first.low)
        and float(displacement.close) < float(displacement.open)
        and float(displacement.body) / float(displacement.atr) >= body_atr
        and float(displacement.close_location) <= close_cut
    ):
        return None
    return {
        "lower": float(current.high),
        "upper": float(first.low),
        "displacement_close": float(displacement.close),
        "displacement_low": float(displacement.low),
        "body_atr": float(displacement.body) / float(displacement.atr),
    }


def fresh_bull_fvg(
    five: pd.DataFrame,
    idx: int,
    body_atr: float,
    close_cut: float,
) -> dict[str, float] | None:
    if idx < 2:
        return None
    first = five.iloc[idx - 2]
    displacement = five.iloc[idx - 1]
    current = five.iloc[idx]
    if not np.isfinite(displacement.atr) or displacement.atr <= 0:
        return None
    if not (
        float(current.low) > float(first.high)
        and float(displacement.close) > float(displacement.open)
        and float(displacement.body) / float(displacement.atr) >= body_atr
        and float(displacement.close_location) >= close_cut
    ):
        return None
    return {
        "lower": float(first.high),
        "upper": float(current.low),
        "displacement_close": float(displacement.close),
        "displacement_high": float(displacement.high),
        "body_atr": float(displacement.body) / float(displacement.atr),
    }


def screen_balance(
    *,
    day: date,
    spec: OpeningBalance,
    five_all: pd.DataFrame,
    one: pd.DataFrame,
    logic: dict[str, Any],
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> list[dict[str, Any]]:
    day_start = pd.Timestamp(day, tz="UTC")
    build_start = day_start + pd.Timedelta(minutes=spec.start_minute)
    build_end = day_start + pd.Timedelta(minutes=spec.end_minute)
    trade_end = day_start + pd.Timedelta(minutes=spec.trade_end_minute)
    build = five_all[(five_all.index > build_start) & (five_all.index <= build_end)]
    trade = five_all[(five_all.index > build_end) & (five_all.index <= trade_end)]
    if len(build) != 6 or trade.empty:
        return []
    high = float(build.high.max())
    low = float(build.low.min())
    volume = float(build.volume.sum())
    if high <= low or volume <= 0:
        return []
    vwap = float((build.typical * build.volume).sum() / volume)
    width = high - low
    events: list[dict[str, Any]] = []
    states: dict[str, BreakEpisode | None] = {"HIGH": None, "LOW": None}
    done = {"HIGH": False, "LOW": False}
    trade_index = list(trade.index)

    def finish_candidate(
        *,
        route: str,
        side: str,
        ep: BreakEpisode,
        idx: int,
        stop_raw: float,
        target_raw: float,
        confirmation: dict[str, Any],
    ) -> None:
        decision = five_all.iloc[idx]
        direction = "SHORT" if side == "HIGH" else "LONG"
        geometry = costed_geometry(
            direction=direction,
            entry_raw=float(decision.close),
            stop_raw=stop_raw,
            target_raw=target_raw,
            decision=decision,
            config=logic,
        )
        record: dict[str, Any] = {
            "day": day.isoformat(),
            "opening_balance": spec.name,
            "route": route,
            "side": side,
            "direction": direction,
            "observed_ts": decision.name.isoformat(),
            "build_start": build_start.isoformat(),
            "build_end": build_end.isoformat(),
            "trade_end": trade_end.isoformat(),
            "balance_high": high,
            "balance_low": low,
            "balance_vwap": vwap,
            "balance_width": width,
            "outside_closes": ep.outside_closes,
            "episode_extreme": ep.extreme,
            "episode_vwap": ep.pv / ep.volume if ep.volume > 0 else None,
            "reclaim_ts": None if ep.reclaim_ts is None else ep.reclaim_ts.isoformat(),
            "reclaim_vwap": ep.reclaim_vwap,
            "confirmation": confirmation,
            "overlaps_current_position": overlaps(decision.name, occupied),
            "geometry": geometry,
        }
        if geometry is not None:
            record.update(
                first_touch(
                    one,
                    observed=decision.name,
                    end=trade_end,
                    direction=direction,
                    stop=geometry["stop"],
                    target=geometry["target"],
                )
            )
        else:
            record.update({"outcome": "REJECTED_GEOMETRY", "terminal_ts": None})
        events.append(record)

    for ts in trade_index:
        idx = five_all.index.get_loc(ts)
        row = five_all.iloc[idx]
        if not np.isfinite(row.atr) or row.atr <= 0:
            continue
        typical = float(row.typical)
        vol = float(row.volume)
        for side in ("HIGH", "LOW"):
            if done[side]:
                continue
            outside = float(row.close) > high if side == "HIGH" else float(row.close) < low
            crossed = float(row.high) > high if side == "HIGH" else float(row.low) < low
            ep = states[side]
            if ep is None:
                if not crossed:
                    continue
                states[side] = BreakEpisode(
                    side=side,
                    first_index=idx,
                    first_ts=ts,
                    outside_closes=1 if outside else 0,
                    extreme=float(row.high) if side == "HIGH" else float(row.low),
                    pv=typical * vol,
                    volume=vol,
                )
                ep = states[side]
                assert ep is not None
                if not outside:
                    ep.reclaim_index = idx
                    ep.reclaim_ts = ts
                    ep.reclaim_high = float(row.high)
                    ep.reclaim_low = float(row.low)
                    ep.reclaim_close = float(row.close)
                    ep.reclaim_vwap = ep.pv / ep.volume if ep.volume else None
                continue

            ep.extreme = max(ep.extreme, float(row.high)) if side == "HIGH" else min(ep.extreme, float(row.low))
            ep.pv += typical * vol
            ep.volume += vol
            episode_vwap = ep.pv / ep.volume if ep.volume else (high if side == "HIGH" else low)
            if outside:
                ep.outside_closes += 1
                if ep.outside_closes >= int(logic["acceptance_closes"]):
                    value_outside = episode_vwap > high if side == "HIGH" else episode_vwap < low
                    if value_outside:
                        ep.accepted = True
                        ep.accepted_index = idx
                continue

            if ep.reclaim_index is None:
                if ep.accepted and ep.accepted_index is not None:
                    if idx - ep.accepted_index > int(logic["reclaim_max_bars"]):
                        done[side] = True
                        continue
                ep.reclaim_index = idx
                ep.reclaim_ts = ts
                ep.reclaim_high = float(row.high)
                ep.reclaim_low = float(row.low)
                ep.reclaim_close = float(row.close)
                ep.reclaim_vwap = episode_vwap

            assert ep.reclaim_index is not None
            if idx - ep.reclaim_index > int(logic["delayed_rejection_expiry_bars"]):
                done[side] = True
                continue
            value_inside = episode_vwap <= high if side == "HIGH" else episode_vwap >= low
            if not value_inside:
                continue

            if side == "HIGH":
                fvg = fresh_bear_fvg(
                    five_all,
                    idx,
                    float(logic["delayed_rejection_fvg_body_atr"]),
                    float(logic["delayed_rejection_fvg_max_close_location"]),
                )
                if fvg is None or ep.reclaim_low is None:
                    continue
                if not (fvg["displacement_close"] < ep.reclaim_low and float(row.close) < high):
                    continue
                stop_raw = ep.extreme + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                route = (
                    "IB_ACCEPTANCE_FAILURE_VALUE_REENTRY_BEARISH_FVG"
                    if ep.accepted
                    else "IB_SWEEP_VALUE_REENTRY_BEARISH_FVG"
                )
                finish_candidate(
                    route=route,
                    side=side,
                    ep=ep,
                    idx=idx,
                    stop_raw=stop_raw,
                    target_raw=low,
                    confirmation=fvg,
                )
                done[side] = True
            else:
                fvg = fresh_bull_fvg(
                    five_all,
                    idx,
                    float(logic["acceptance_displacement_body_atr"]),
                    float(logic["acceptance_displacement_min_close_location"]),
                )
                if fvg is None or ep.reclaim_high is None:
                    continue
                if not (fvg["displacement_close"] > ep.reclaim_high and float(row.close) > low):
                    continue
                stop_raw = ep.extreme - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                route = (
                    "IB_ACCEPTANCE_FAILURE_VALUE_REENTRY_BULLISH_FVG"
                    if ep.accepted
                    else "IB_SWEEP_VALUE_REENTRY_BULLISH_FVG"
                )
                finish_candidate(
                    route=route,
                    side=side,
                    ep=ep,
                    idx=idx,
                    stop_raw=stop_raw,
                    target_raw=high,
                    confirmation=fvg,
                )
                done[side] = True

    # True-acceptance counterpart: value migrates outside, then a pullback
    # holds the boundary and a fresh same-direction FVG opens a new leg.
    for side in ("HIGH", "LOW"):
        outside_count = 0
        pv = 0.0
        vv = 0.0
        accepted_at: int | None = None
        pullback_extreme: float | None = None
        prior_peak: float | None = None
        for ts in trade_index:
            idx = five_all.index.get_loc(ts)
            row = five_all.iloc[idx]
            if not np.isfinite(row.atr) or row.atr <= 0:
                continue
            outside = float(row.close) > high if side == "HIGH" else float(row.close) < low
            pv += float(row.typical) * float(row.volume)
            vv += float(row.volume)
            session_vwap = pv / vv if vv else (high if side == "HIGH" else low)
            if accepted_at is None:
                outside_count = outside_count + 1 if outside else 0
                value_outside = session_vwap > high if side == "HIGH" else session_vwap < low
                if outside_count >= int(logic["acceptance_closes"]) and value_outside:
                    accepted_at = idx
                    prior_peak = float(row.high) if side == "HIGH" else float(row.low)
                continue
            if idx - accepted_at > int(logic["acceptance_retest_expiry_bars"]):
                break
            if side == "HIGH":
                prior_peak = max(float(prior_peak), float(row.high)) if prior_peak is not None else float(row.high)
                if float(row.close) <= high:
                    break
                if float(row.low) <= high + float(logic["fvg_boundary_tolerance_atr"]) * float(row.atr):
                    pullback_extreme = float(row.low) if pullback_extreme is None else min(pullback_extreme, float(row.low))
                if pullback_extreme is None:
                    continue
                fvg = fresh_bull_fvg(
                    five_all,
                    idx,
                    float(logic["acceptance_displacement_body_atr"]),
                    float(logic["acceptance_displacement_min_close_location"]),
                )
                if fvg is None or float(row.close) <= high:
                    continue
                stop_raw = pullback_extreme - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                dummy = BreakEpisode(side, accepted_at, five_all.index[accepted_at], outside_count, prior_peak or high, pv, vv, True, accepted_at)
                finish_candidate(
                    route="IB_TRUE_ACCEPTANCE_PULLBACK_BULLISH_FVG",
                    side=side,
                    ep=dummy,
                    idx=idx,
                    stop_raw=stop_raw,
                    target_raw=high + width,
                    confirmation=fvg,
                )
                break
            else:
                prior_peak = min(float(prior_peak), float(row.low)) if prior_peak is not None else float(row.low)
                if float(row.close) >= low:
                    break
                if float(row.high) >= low - float(logic["fvg_boundary_tolerance_atr"]) * float(row.atr):
                    pullback_extreme = float(row.high) if pullback_extreme is None else max(pullback_extreme, float(row.high))
                if pullback_extreme is None:
                    continue
                fvg = fresh_bear_fvg(
                    five_all,
                    idx,
                    float(logic["low_acceptance_displacement_body_atr"]),
                    float(logic["low_acceptance_displacement_max_close_location"]),
                )
                if fvg is None or float(row.close) >= low:
                    continue
                stop_raw = pullback_extreme + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                dummy = BreakEpisode(side, accepted_at, five_all.index[accepted_at], outside_count, prior_peak or low, pv, vv, True, accepted_at)
                finish_candidate(
                    route="IB_TRUE_ACCEPTANCE_PULLBACK_BEARISH_FVG",
                    side=side,
                    ep=dummy,
                    idx=idx,
                    stop_raw=stop_raw,
                    target_raw=low - width,
                    confirmation=fvg,
                )
                break

    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [e for e in events if e["geometry"] is not None]
    nonoverlap = [e for e in eligible if not e["overlaps_current_position"]]
    outcomes: dict[str, int] = {}
    routes: dict[str, dict[str, int]] = {}
    for event in nonoverlap:
        outcome = str(event["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        route = str(event["route"])
        bucket = routes.setdefault(route, {})
        bucket[outcome] = bucket.get(outcome, 0) + 1
    return {
        "raw_candidates": len(events),
        "costed_candidates": len(eligible),
        "nonoverlap_costed_candidates": len(nonoverlap),
        "nonoverlap_outcomes": outcomes,
        "route_outcomes": routes,
        "nonoverlap_target_candidates": [
            {
                "day": e["day"],
                "opening_balance": e["opening_balance"],
                "route": e["route"],
                "direction": e["direction"],
                "observed_ts": e["observed_ts"],
                "net_r": e["geometry"]["net_r"],
                "outcome": e["outcome"],
                "terminal_ts": e["terminal_ts"],
            }
            for e in nonoverlap
            if e["outcome"] == "TARGET"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--weeks", nargs="+", default=["W1", "W12"])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = dict(config["logic"])
    logic["price_increment"] = float(config["symbols"]["BTCUSDT"]["price_increment"])
    all_results: dict[str, Any] = {
        "schema": "candidate-12-i20-opening-balance-diagnostic-v1",
        "candidate_source": config["candidate"],
        "not_performance_evidence": True,
        "causal_policy": (
            "completed 30m opening balance -> accepted breakout or sweep -> "
            "value reentry/hold -> independent MSS/FVG -> structural opposite edge/projection"
        ),
        "weeks": {},
    }
    for week in args.weeks:
        week_spec = config["selection"]["weeks"][week]
        evaluation_start = date.fromisoformat(week_spec["start"])
        evaluation_end = date.fromisoformat(week_spec["end_exclusive"])
        warmup = evaluation_start - timedelta(days=int(config["selection"]["warmup_days"]))
        one, manifest = load_binance_bars("BTCUSDT", warmup, evaluation_end, args.data_dir)
        five = aggregate_five(one)
        occupied = parse_positions(args.baseline_root / f"BTCUSDT-{week}" / "positions.csv")
        events: list[dict[str, Any]] = []
        cursor = evaluation_start
        while cursor < evaluation_end:
            if cursor.weekday() < 5:
                for balance in BALANCES:
                    events.extend(
                        screen_balance(
                            day=cursor,
                            spec=balance,
                            five_all=five,
                            one=one,
                            logic=logic,
                            occupied=occupied,
                        )
                    )
            cursor += timedelta(days=1)
        all_results["weeks"][week] = {
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "source_files": manifest,
            "baseline_positions": [
                {"opened": a.isoformat(), "closed": b.isoformat()} for a, b in occupied
            ],
            "summary": summarize(events),
            "events": events,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(all_results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({w: x["summary"] for w, x in all_results["weeks"].items()}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
