#!/usr/bin/env python3
"""Cross-market delivery-state diagnostic for Candidate 12.

Development-only. BTC completed-session interactions are routed by whether
ETH, SOL, and XRP confirm the same completed-session boundary transition. The
peer basket is used only as a latent-state classifier; entries, invalidations,
targets, matching, fees, and NAV remain BTC-native.

Families:
* localized sweep / unconfirmed acceptance failure -> BTC reversal;
* broad majority-confirmed acceptance -> BTC continuation.

All observations use completed five-minute bars and all outcomes are evaluated
on one-minute BTC bars. This script is not performance evidence.
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

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
PEERS = SYMBOLS[1:]


@dataclass(frozen=True, slots=True)
class SessionSpec:
    name: str
    build_start: int
    build_end: int
    trade_end: int


SESSIONS = (
    SessionSpec("ASIA", 0, 360, 720),
    SessionSpec("LONDON", 360, 720, 1080),
)


def aggregate_five(one: pd.DataFrame) -> pd.DataFrame:
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
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous).abs(),
            (out["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(36, min_periods=36).mean()
    spread = out["high"] - out["low"]
    out["close_location"] = np.where(
        spread > 0,
        (out["close"] - out["low"]) / spread,
        0.5,
    )
    return out


def parse_positions(path: Path) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    result: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for row in frame.itertuples(index=False):
        opened = pd.Timestamp(row.ts_opened)
        closed = pd.Timestamp(row.ts_closed)
        opened = opened.tz_localize("UTC") if opened.tzinfo is None else opened.tz_convert("UTC")
        closed = closed.tz_localize("UTC") if closed.tzinfo is None else closed.tz_convert("UTC")
        result.append((opened, closed))
    return result


def interval_overlaps(
    start: pd.Timestamp,
    end: pd.Timestamp,
    intervals: Iterable[tuple[pd.Timestamp, pd.Timestamp]],
) -> bool:
    return any(start <= b and end >= a for a, b in intervals)


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
    logic: dict[str, Any],
) -> tuple[dict[str, float] | None, str]:
    tick = float(logic["price_increment"])
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
    if not np.isfinite(atr):
        return None, "ATR_UNAVAILABLE"
    if structural_loss <= 0:
        return None, "INVALID_STOP"
    if structural_loss > float(logic["max_stop_atr"]) * atr:
        return None, "STOP_EXCEEDS_MAX_ATR"
    if structural_profit <= 0:
        return None, "INVALID_TARGET"
    if preconsumed:
        return None, "TARGET_PRECONSUMED"
    entry_cost = entry * float(logic["effective_taker_rate"])
    stop_cost = stop * float(logic["effective_taker_rate"])
    target_cost = target * float(logic["effective_maker_rate"])
    slippage = float(logic["tick_slippage_units"]) * tick
    loss = structural_loss + entry_cost + stop_cost + slippage
    profit = structural_profit - entry_cost - target_cost - slippage
    if loss <= 0 or profit <= 0:
        return None, "NON_POSITIVE_COSTED_EXPECTANCY"
    net_r = profit / loss
    if net_r < float(logic["min_net_r"]):
        return None, "INSUFFICIENT_COSTED_R"
    return (
        {
            "entry": entry,
            "stop": stop,
            "target": target,
            "loss_per_unit": loss,
            "profit_per_unit": profit,
            "net_r": net_r,
        },
        "OK",
    )


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


def session_ranges(
    day: date,
    session: SessionSpec,
    frames: dict[str, pd.DataFrame],
) -> dict[str, dict[str, float]] | None:
    day_start = pd.Timestamp(day, tz="UTC")
    start = day_start + pd.Timedelta(minutes=session.build_start)
    end = day_start + pd.Timedelta(minutes=session.build_end)
    result: dict[str, dict[str, float]] = {}
    for symbol, frame in frames.items():
        build = frame[(frame.index > start) & (frame.index <= end)]
        if build.empty:
            return None
        high = float(build.high.max())
        low = float(build.low.min())
        if high <= low:
            return None
        result[symbol] = {"high": high, "low": low, "width": high - low}
    return result


def peer_snapshot(
    *,
    ts: pd.Timestamp,
    side: str,
    frames: dict[str, pd.DataFrame],
    ranges: dict[str, dict[str, float]],
) -> dict[str, Any]:
    items: dict[str, Any] = {}
    outside_count = 0
    crossed_count = 0
    for symbol in PEERS:
        row = frames[symbol].loc[ts]
        source = ranges[symbol]
        if side == "HIGH":
            crossed = float(row.high) > source["high"]
            outside = float(row.close) > source["high"]
            excursion_atr = (
                (float(row.high) - source["high"]) / float(row.atr)
                if np.isfinite(row.atr) and row.atr > 0
                else None
            )
        else:
            crossed = float(row.low) < source["low"]
            outside = float(row.close) < source["low"]
            excursion_atr = (
                (source["low"] - float(row.low)) / float(row.atr)
                if np.isfinite(row.atr) and row.atr > 0
                else None
            )
        crossed_count += int(crossed)
        outside_count += int(outside)
        items[symbol] = {
            "crossed": crossed,
            "closed_outside": outside,
            "close": float(row.close),
            "source_high": source["high"],
            "source_low": source["low"],
            "excursion_atr": excursion_atr,
        }
    return {
        "outside_count": outside_count,
        "crossed_count": crossed_count,
        "peers": items,
    }


def emit(
    *,
    records: list[dict[str, Any]],
    week: str,
    day: date,
    session: SessionSpec,
    route: str,
    direction: str,
    observed: pd.Timestamp,
    decision: pd.Series,
    stop_raw: float,
    target_raw: float,
    source: dict[str, float],
    peer_state: dict[str, Any],
    logic: dict[str, Any],
    one_btc: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    details: dict[str, Any],
) -> None:
    geometry, reason = costed_geometry(
        direction=direction,
        entry_raw=float(decision.close),
        stop_raw=stop_raw,
        target_raw=target_raw,
        decision=decision,
        logic=logic,
    )
    item: dict[str, Any] = {
        "week": week,
        "day": day.isoformat(),
        "session": session.name,
        "route": route,
        "direction": direction,
        "observed_ts": observed.isoformat(),
        "source_high": source["high"],
        "source_low": source["low"],
        "source_width": source["width"],
        "decision": {
            "open": float(decision.open),
            "high": float(decision.high),
            "low": float(decision.low),
            "close": float(decision.close),
            "atr": float(decision.atr),
        },
        "peer_state": peer_state,
        "geometry": geometry,
        "geometry_reason": reason,
        "details": details,
    }
    if geometry is None:
        item.update(
            {
                "outcome": "REJECTED_GEOMETRY",
                "terminal_ts": None,
                "overlaps_baseline": False,
            }
        )
    else:
        terminal = first_touch(
            one_btc,
            observed=observed,
            end=pd.Timestamp(day, tz="UTC")
            + pd.Timedelta(minutes=session.trade_end),
            direction=direction,
            stop=geometry["stop"],
            target=geometry["target"],
        )
        item.update(terminal)
        item["overlaps_baseline"] = interval_overlaps(
            observed,
            pd.Timestamp(terminal["terminal_ts"]),
            occupied,
        )
    records.append(item)


def screen_session(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    frames: dict[str, pd.DataFrame],
    one_btc: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    logic: dict[str, Any],
) -> list[dict[str, Any]]:
    ranges = session_ranges(day, session, frames)
    if ranges is None:
        return []
    source = ranges["BTCUSDT"]
    day_start = pd.Timestamp(day, tz="UTC")
    build_end = day_start + pd.Timedelta(minutes=session.build_end)
    trade_end = day_start + pd.Timedelta(minutes=session.trade_end)
    common = frames["BTCUSDT"].index
    for symbol in PEERS:
        common = common.intersection(frames[symbol].index)
    trade_index = common[(common > build_end) & (common <= trade_end)]
    if trade_index.empty:
        return []
    records: list[dict[str, Any]] = []
    btc = frames["BTCUSDT"]

    # Localized sweep: majority of peers never closes beyond the same
    # completed-session boundary. The later BTC MSS is the execution trigger.
    for side, direction in (("HIGH", "SHORT"), ("LOW", "LONG")):
        start_idx: int | None = None
        extreme: float | None = None
        reclaim_idx: int | None = None
        reclaim: pd.Series | None = None
        reclaim_peer: dict[str, Any] | None = None
        for local_idx, ts in enumerate(trade_index):
            row = btc.loc[ts]
            crossed = (
                float(row.high) > source["high"]
                if side == "HIGH"
                else float(row.low) < source["low"]
            )
            inside = (
                float(row.close) < source["high"]
                if side == "HIGH"
                else float(row.close) > source["low"]
            )
            if start_idx is None:
                if not crossed:
                    continue
                start_idx = local_idx
                extreme = float(row.high) if side == "HIGH" else float(row.low)
                if inside:
                    peer = peer_snapshot(ts=ts, side=side, frames=frames, ranges=ranges)
                    if peer["outside_count"] <= 1:
                        reclaim_idx = local_idx
                        reclaim = row
                        reclaim_peer = peer
                continue
            assert extreme is not None
            extreme = (
                max(extreme, float(row.high))
                if side == "HIGH"
                else min(extreme, float(row.low))
            )
            if local_idx - start_idx > int(logic["reclaim_max_bars"]):
                break
            if reclaim_idx is None and inside:
                peer = peer_snapshot(ts=ts, side=side, frames=frames, ranges=ranges)
                if peer["outside_count"] <= 1:
                    reclaim_idx = local_idx
                    reclaim = row
                    reclaim_peer = peer
                    continue
            if reclaim_idx is None or reclaim is None or reclaim_peer is None:
                continue
            if local_idx - reclaim_idx > int(logic["confirmation_bars"]) + 2:
                break
            mss = (
                float(row.close) < float(reclaim.low)
                if direction == "SHORT"
                else float(row.close) > float(reclaim.high)
            )
            if not mss:
                continue
            decision_peer = peer_snapshot(
                ts=ts,
                side=side,
                frames=frames,
                ranges=ranges,
            )
            if decision_peer["outside_count"] > 1:
                continue
            stop_raw = (
                extreme + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                if direction == "SHORT"
                else extreme - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=f"CROSS_MARKET_LOCALIZED_{side}_SWEEP_FAILURE",
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=source["low"] if direction == "SHORT" else source["high"],
                source=source,
                peer_state=decision_peer,
                logic=logic,
                one_btc=one_btc,
                occupied=occupied,
                details={
                    "sweep_extreme": extreme,
                    "reclaim_high": float(reclaim.high),
                    "reclaim_low": float(reclaim.low),
                    "reclaim_peer_state": reclaim_peer,
                    "target_semantics": "OPPOSITE_COMPLETED_BTC_SESSION_BOUNDARY",
                },
            )
            break

    # BTC accepts a boundary without majority peer confirmation, then fails
    # back inside. This is a cross-market delivery failure, not a normal wick.
    for side, direction in (("HIGH", "SHORT"), ("LOW", "LONG")):
        outside_closes = 0
        accepted_idx: int | None = None
        accepted_peer: dict[str, Any] | None = None
        extreme: float | None = None
        failure_idx: int | None = None
        failure_bar: pd.Series | None = None
        for local_idx, ts in enumerate(trade_index):
            row = btc.loc[ts]
            outside = (
                float(row.close) > source["high"]
                if side == "HIGH"
                else float(row.close) < source["low"]
            )
            if accepted_idx is None:
                outside_closes = outside_closes + 1 if outside else 0
                if outside:
                    extreme = (
                        max(extreme or source["high"], float(row.high))
                        if side == "HIGH"
                        else min(extreme or source["low"], float(row.low))
                    )
                if outside_closes >= int(logic["acceptance_closes"]):
                    peer = peer_snapshot(ts=ts, side=side, frames=frames, ranges=ranges)
                    if peer["outside_count"] <= 1:
                        accepted_idx = local_idx
                        accepted_peer = peer
                continue
            assert accepted_peer is not None and extreme is not None
            extreme = (
                max(extreme, float(row.high))
                if side == "HIGH"
                else min(extreme, float(row.low))
            )
            if local_idx - accepted_idx > int(logic["acceptance_retest_expiry_bars"]):
                break
            inside = (
                float(row.close) < source["high"]
                if side == "HIGH"
                else float(row.close) > source["low"]
            )
            if failure_idx is None:
                if inside:
                    failure_idx = local_idx
                    failure_bar = row
                continue
            assert failure_bar is not None
            if local_idx - failure_idx > int(logic["confirmation_bars"]) + 2:
                break
            mss = (
                float(row.close) < float(failure_bar.low)
                if direction == "SHORT"
                else float(row.close) > float(failure_bar.high)
            )
            if not mss:
                continue
            decision_peer = peer_snapshot(
                ts=ts,
                side=side,
                frames=frames,
                ranges=ranges,
            )
            if decision_peer["outside_count"] > 1:
                continue
            stop_raw = (
                extreme + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                if direction == "SHORT"
                else extreme - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=f"CROSS_MARKET_UNCONFIRMED_{side}_ACCEPTANCE_FAILURE",
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=source["low"] if direction == "SHORT" else source["high"],
                source=source,
                peer_state=decision_peer,
                logic=logic,
                one_btc=one_btc,
                occupied=occupied,
                details={
                    "outside_closes": outside_closes,
                    "acceptance_peer_state": accepted_peer,
                    "failure_high": float(failure_bar.high),
                    "failure_low": float(failure_bar.low),
                    "acceptance_extreme": extreme,
                    "target_semantics": "OPPOSITE_COMPLETED_BTC_SESSION_BOUNDARY",
                },
            )
            break

    # Broad, majority-confirmed price discovery. The peer basket defines state;
    # BTC boundary hold and the next local break define a new execution leg.
    for side, direction in (("HIGH", "LONG"), ("LOW", "SHORT")):
        outside_closes = 0
        accepted_idx: int | None = None
        accepted_peer: dict[str, Any] | None = None
        pullback_idx: int | None = None
        pullback: pd.Series | None = None
        for local_idx, ts in enumerate(trade_index):
            row = btc.loc[ts]
            outside = (
                float(row.close) > source["high"]
                if side == "HIGH"
                else float(row.close) < source["low"]
            )
            if accepted_idx is None:
                outside_closes = outside_closes + 1 if outside else 0
                if outside_closes >= int(logic["acceptance_closes"]):
                    peer = peer_snapshot(ts=ts, side=side, frames=frames, ranges=ranges)
                    if peer["outside_count"] >= 2:
                        accepted_idx = local_idx
                        accepted_peer = peer
                continue
            assert accepted_peer is not None
            if local_idx - accepted_idx > int(logic["acceptance_retest_expiry_bars"]):
                break
            if not outside:
                break
            boundary_distance = (
                float(row.low) - source["high"]
                if side == "HIGH"
                else source["low"] - float(row.high)
            )
            near = boundary_distance <= (
                float(logic["fvg_boundary_tolerance_atr"]) * float(row.atr)
            )
            if pullback_idx is None and near:
                pullback_idx = local_idx
                pullback = row
                continue
            if pullback_idx is None or pullback is None:
                continue
            if local_idx - pullback_idx > int(logic["reclaim_max_bars"]) + 1:
                break
            local_break = (
                float(row.close) > float(pullback.high)
                if direction == "LONG"
                else float(row.close) < float(pullback.low)
            )
            if not local_break:
                continue
            decision_peer = peer_snapshot(
                ts=ts,
                side=side,
                frames=frames,
                ranges=ranges,
            )
            if decision_peer["outside_count"] < 2:
                continue
            stop_raw = (
                float(pullback.low)
                - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                if direction == "LONG"
                else float(pullback.high)
                + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
            )
            target_raw = (
                source["high"] + source["width"]
                if direction == "LONG"
                else source["low"] - source["width"]
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=f"CROSS_MARKET_BROAD_{side}_ACCEPTANCE_CONTINUATION",
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=target_raw,
                source=source,
                peer_state=decision_peer,
                logic=logic,
                one_btc=one_btc,
                occupied=occupied,
                details={
                    "outside_closes": outside_closes,
                    "acceptance_peer_state": accepted_peer,
                    "pullback_high": float(pullback.high),
                    "pullback_low": float(pullback.low),
                    "target_semantics": "ONE_COMPLETED_BTC_SESSION_RANGE_PROJECTION",
                },
            )
            break

    # Same plan from overlapping family definitions is one causal opportunity.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in records:
        key = (item["observed_ts"], item["direction"], item["session"])
        unique.setdefault(key, item)
    return list(unique.values())


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    costed = [item for item in records if item["geometry"] is not None]
    additive = [item for item in costed if not item["overlaps_baseline"]]
    outcomes: dict[str, int] = {}
    routes: dict[str, dict[str, int]] = {}
    geometry: dict[str, int] = {}
    for item in records:
        reason = str(item["geometry_reason"])
        geometry[reason] = geometry.get(reason, 0) + 1
    for item in additive:
        outcome = str(item["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        route = str(item["route"])
        bucket = routes.setdefault(route, {})
        bucket[outcome] = bucket.get(outcome, 0) + 1
    return {
        "raw_candidates": len(records),
        "costed_candidates": len(costed),
        "additive_costed_candidates": len(additive),
        "additive_outcomes": outcomes,
        "route_outcomes": routes,
        "geometry_reasons": geometry,
        "additive_targets": [
            {
                "day": item["day"],
                "session": item["session"],
                "route": item["route"],
                "direction": item["direction"],
                "observed_ts": item["observed_ts"],
                "net_r": item["geometry"]["net_r"],
                "terminal_ts": item["terminal_ts"],
            }
            for item in additive
            if item["outcome"] == "TARGET"
        ],
        "additive_stops": [
            {
                "day": item["day"],
                "session": item["session"],
                "route": item["route"],
                "direction": item["direction"],
                "observed_ts": item["observed_ts"],
                "net_r": item["geometry"]["net_r"],
                "terminal_ts": item["terminal_ts"],
            }
            for item in additive
            if item["outcome"] == "STOP"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.json"),
    )
    parser.add_argument("--weeks", nargs="+", default=["W1", "W12"])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = dict(config["logic"])
    logic["price_increment"] = float(
        config["symbols"]["BTCUSDT"]["price_increment"]
    )
    result: dict[str, Any] = {
        "schema": "candidate-12-i23-cross-market-delivery-v1",
        "candidate_source": config["candidate"],
        "not_performance_evidence": True,
        "state_policy": (
            "BTC session interaction -> majority peer boundary delivery state -> "
            "BTC local transition -> BTC-native entry, invalidation and target"
        ),
        "weeks": {},
    }

    for week in args.weeks:
        spec = config["selection"]["weeks"][week]
        start = date.fromisoformat(spec["start"])
        end_exclusive = date.fromisoformat(spec["end_exclusive"])
        warmup = start - timedelta(days=int(config["selection"]["warmup_days"]))
        ones: dict[str, pd.DataFrame] = {}
        frames: dict[str, pd.DataFrame] = {}
        manifests: dict[str, Any] = {}
        for symbol in SYMBOLS:
            one, manifest = load_binance_bars(
                symbol,
                warmup,
                end_exclusive,
                args.data_dir,
            )
            ones[symbol] = one
            frames[symbol] = aggregate_five(one)
            manifests[symbol] = manifest
        occupied = parse_positions(
            args.baseline_root / f"BTCUSDT-{week}" / "positions.csv"
        )
        records: list[dict[str, Any]] = []
        cursor = start
        while cursor < end_exclusive:
            if cursor.weekday() < 5:
                for session in SESSIONS:
                    records.extend(
                        screen_session(
                            week=week,
                            day=cursor,
                            session=session,
                            frames=frames,
                            one_btc=ones["BTCUSDT"],
                            occupied=occupied,
                            logic=logic,
                        )
                    )
            cursor += timedelta(days=1)
        result["weeks"][week] = {
            "evaluation_start": start.isoformat(),
            "evaluation_end_exclusive": end_exclusive.isoformat(),
            "manifests": manifests,
            "baseline_positions": [
                {"opened": a.isoformat(), "closed": b.isoformat()}
                for a, b in occupied
            ],
            "summary": summarize(records),
            "records": records,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {week: value["summary"] for week, value in result["weeks"].items()},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
