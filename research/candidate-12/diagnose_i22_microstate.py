#!/usr/bin/env python3
"""Causal microstructure-state diagnostic for Candidate 12.

Development-only diagnostic. It combines the frozen I19 completed-session
context with independent position-sponsorship and order-book state observed at
each completed five-minute close. Forward-label columns in the external feature
files are explicitly removed and never referenced.

Two independent auction families are screened:

1. sponsored price discovery:
   completed session boundary acceptance -> OI expansion + supportive depth ->
   boundary pullback hold -> renewed directional order flow and local break;

2. position-flush failed auction:
   boundary sweep -> OI contraction -> value returns inside -> supportive
   opposite-side depth/order flow -> local MSS.

The script emits candidate plans and one-minute first-touch diagnostics. It is
not performance evidence and does not modify the production strategy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from data_loader import load_binance_bars

FORWARD_COLUMNS = {
    "fwd_ret_5m",
    "fwd_ret_15m",
    "fwd_ret_60m",
    "fwd_direction_5m",
}


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
    return out


def download_feature_day(day: date, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 100:
        if destination.read_bytes()[:4] == b"PAR1":
            return destination
    names = (
        "ibrahimdaud/binance-btcusdt",
        "ibrahimdaud/btcusdt-futures-features",
    )
    errors: list[str] = []
    for repo in names:
        url = (
            f"https://huggingface.co/datasets/{repo}/resolve/main/"
            f"features/BTCUSDT/{day.isoformat()}.parquet?download=true"
        )
        try:
            request = Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-candidate-12-microstate/1.0"},
            )
            with urlopen(request, timeout=90) as response:  # noqa: S310 fixed HTTPS hosts
                payload = response.read()
            if len(payload) <= 100 or payload[:4] != b"PAR1":
                raise RuntimeError(
                    f"unexpected payload bytes={len(payload)} magic={payload[:4]!r}"
                )
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(destination)
            return destination
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("feature download failed: " + " | ".join(errors))


def load_features(
    start: date,
    end_exclusive: date,
    feature_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor < end_exclusive:
        path = download_feature_day(
            cursor,
            feature_dir / f"{cursor.isoformat()}.parquet",
        )
        frame = pd.read_parquet(path)
        forbidden_present = sorted(FORWARD_COLUMNS.intersection(frame.columns))
        frame = frame.drop(columns=forbidden_present, errors="ignore")
        required = {
            "bar_time_ms",
            "close",
            "depth_imbalance_1pct",
            "hawkes_net",
            "oi_change_1h",
            "taker_ls_vol_ratio",
        }
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise RuntimeError(f"{path} missing feature columns {missing}")
        observed = (
            pd.to_datetime(frame["bar_time_ms"], unit="ms", utc=True)
            + pd.Timedelta(minutes=5)
        )
        frame = frame.copy()
        frame.index = observed
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frames.append(frame)
        manifest.append(
            {
                "date": cursor.isoformat(),
                "path": str(path),
                "bytes": path.stat().st_size,
                "rows": len(frame.index),
                "forward_columns_removed": forbidden_present,
            }
        )
        cursor += timedelta(days=1)
    result = pd.concat(frames).sort_index()
    if result.index.has_duplicates:
        raise RuntimeError("external feature timestamps are duplicated")
    return result, manifest


def parse_positions(path: Path) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for row in frame.itertuples(index=False):
        start = pd.Timestamp(row.ts_opened)
        end = pd.Timestamp(row.ts_closed)
        start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
        end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
        intervals.append((start, end))
    return intervals


def interval_overlaps(
    start: pd.Timestamp,
    end: pd.Timestamp,
    intervals: Iterable[tuple[pd.Timestamp, pd.Timestamp]],
) -> bool:
    return any(start <= other_end and end >= other_start for other_start, other_end in intervals)


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
) -> tuple[dict[str, float] | None, str]:
    tick = float(config["price_increment"])
    if direction == "LONG":
        entry = round_tick(entry_raw, tick, "UP")
        stop = round_tick(stop_raw, tick, "DOWN")
        target = round_tick(target_raw, tick, "DOWN")
        structural_loss = entry - stop
        structural_profit = target - entry
        target_preconsumed = float(decision.high) >= target
    else:
        entry = round_tick(entry_raw, tick, "DOWN")
        stop = round_tick(stop_raw, tick, "UP")
        target = round_tick(target_raw, tick, "UP")
        structural_loss = stop - entry
        structural_profit = entry - target
        target_preconsumed = float(decision.low) <= target
    atr = float(decision.atr)
    if not np.isfinite(atr):
        return None, "ATR_UNAVAILABLE"
    if structural_loss <= 0:
        return None, "INVALID_STRUCTURAL_STOP"
    if structural_loss > float(config["max_stop_atr"]) * atr:
        return None, "STOP_EXCEEDS_MAX_ATR"
    if structural_profit <= 0:
        return None, "INVALID_STRUCTURAL_TARGET"
    if target_preconsumed:
        return None, "TARGET_PRECONSUMED"
    entry_cost = entry * float(config["effective_taker_rate"])
    stop_cost = stop * float(config["effective_taker_rate"])
    target_cost = target * float(config["effective_maker_rate"])
    slippage = float(config["tick_slippage_units"]) * tick
    loss = structural_loss + entry_cost + stop_cost + slippage
    profit = structural_profit - entry_cost - target_cost - slippage
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
            "structural_loss": structural_loss,
            "structural_profit": structural_profit,
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


def finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def micro_snapshot(row: pd.Series) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name in (
        "depth_imbalance_1pct",
        "hawkes_net",
        "oi_change_1h",
        "taker_ls_vol_ratio",
        "vpin_50",
        "vpin_bucket_imbalance",
        "trade_count_5m",
        "avg_trade_size_5m",
    ):
        value = row.get(name)
        result[name] = float(value) if finite(value) else None
    return result


def sponsored_state(row: pd.Series, direction: str) -> bool:
    """Position sponsorship and passive-liquidity support at state creation."""
    oi = row.get("oi_change_1h")
    depth = row.get("depth_imbalance_1pct")
    if not all(finite(value) for value in (oi, depth)):
        return False
    sign = 1.0 if direction == "LONG" else -1.0
    # OI expansion indicates new gross positioning regardless of direction;
    # price direction and book imbalance determine which discovery leg it backs.
    return bool(float(oi) > 0 and float(depth) * sign > 0)


def flush_state(row: pd.Series) -> bool:
    """Gross position contraction visible by the reclaim close."""
    oi = row.get("oi_change_1h")
    return bool(finite(oi) and float(oi) < 0)


def directional_trigger(row: pd.Series, direction: str) -> bool:
    """Independent execution confirmation from depth and aggressor arrival."""
    depth = row.get("depth_imbalance_1pct")
    hawkes = row.get("hawkes_net")
    taker = row.get("taker_ls_vol_ratio")
    if not all(finite(value) for value in (depth, hawkes, taker)):
        return False
    sign = 1.0 if direction == "LONG" else -1.0
    depth_ok = float(depth) * sign > 0
    aggressor_ok = (
        float(hawkes) * sign > 0
        or ((float(taker) - 1.0) * sign > 0)
    )
    return bool(depth_ok and aggressor_ok)


def joined_bars(
    one: pd.DataFrame,
    features: pd.DataFrame,
    tick: float,
) -> pd.DataFrame:
    five = aggregate_five(one)
    feature_columns = [
        name
        for name in features.columns
        if name not in {"symbol", "close"}
        and name not in FORWARD_COLUMNS
    ]
    joined = five.join(features[feature_columns], how="left")
    external_close = features[["close"]].rename(columns={"close": "feature_close"})
    joined = joined.join(external_close, how="left")
    available = joined["feature_close"].notna()
    if not available.any():
        raise RuntimeError("no external features aligned to completed five-minute bars")
    mismatch = (
        joined.loc[available, "close"] - joined.loc[available, "feature_close"]
    ).abs()
    if float(mismatch.max()) > max(tick, 1e-8):
        raise RuntimeError(
            f"external/price bar alignment mismatch max={float(mismatch.max())}"
        )
    return joined


def emit_candidate(
    *,
    records: list[dict[str, Any]],
    one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    week: str,
    day: date,
    session: SessionSpec,
    route: str,
    direction: str,
    source_high: float,
    source_low: float,
    observed: pd.Timestamp,
    decision: pd.Series,
    stop_raw: float,
    target_raw: float,
    logic: dict[str, Any],
    state_bar: pd.Series,
    decision_bar: pd.Series,
    details: dict[str, Any],
) -> None:
    geometry, geometry_reason = costed_geometry(
        direction=direction,
        entry_raw=float(decision.close),
        stop_raw=stop_raw,
        target_raw=target_raw,
        decision=decision,
        config=logic,
    )
    record: dict[str, Any] = {
        "week": week,
        "day": day.isoformat(),
        "session": session.name,
        "route": route,
        "direction": direction,
        "observed_ts": observed.isoformat(),
        "source_high": source_high,
        "source_low": source_low,
        "source_width": source_high - source_low,
        "decision_ohlc": {
            "open": float(decision.open),
            "high": float(decision.high),
            "low": float(decision.low),
            "close": float(decision.close),
            "atr": float(decision.atr),
        },
        "state_micro": micro_snapshot(state_bar),
        "decision_micro": micro_snapshot(decision_bar),
        "geometry": geometry,
        "geometry_reason": geometry_reason,
        "details": details,
    }
    if geometry is None:
        record.update(
            {
                "outcome": "REJECTED_GEOMETRY",
                "terminal_ts": None,
                "overlaps_baseline": False,
            }
        )
    else:
        terminal = first_touch(
            one,
            observed=observed,
            end=pd.Timestamp(day, tz="UTC")
            + pd.Timedelta(minutes=session.trade_end),
            direction=direction,
            stop=geometry["stop"],
            target=geometry["target"],
        )
        record.update(terminal)
        terminal_ts = pd.Timestamp(terminal["terminal_ts"])
        record["overlaps_baseline"] = interval_overlaps(
            observed,
            terminal_ts,
            occupied,
        )
    records.append(record)


def screen_session(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    joined: pd.DataFrame,
    one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    logic: dict[str, Any],
) -> list[dict[str, Any]]:
    day_start = pd.Timestamp(day, tz="UTC")
    build_start = day_start + pd.Timedelta(minutes=session.build_start)
    build_end = day_start + pd.Timedelta(minutes=session.build_end)
    trade_end = day_start + pd.Timedelta(minutes=session.trade_end)
    build = joined[(joined.index > build_start) & (joined.index <= build_end)]
    trade = joined[(joined.index > build_end) & (joined.index <= trade_end)]
    if build.empty or trade.empty:
        return []
    source_high = float(build.high.max())
    source_low = float(build.low.min())
    if source_high <= source_low:
        return []
    width = source_high - source_low
    records: list[dict[str, Any]] = []
    rows = list(trade.iterrows())

    # Position-flush failed auction.
    for side, direction in (("HIGH", "SHORT"), ("LOW", "LONG")):
        episode_start: int | None = None
        extreme: float | None = None
        reclaim_idx: int | None = None
        reclaim_bar: pd.Series | None = None
        state_bar: pd.Series | None = None
        for local_idx, (ts, row) in enumerate(rows):
            crossed = (
                float(row.high) > source_high
                if side == "HIGH"
                else float(row.low) < source_low
            )
            inside_close = (
                float(row.close) < source_high
                if side == "HIGH"
                else float(row.close) > source_low
            )
            if episode_start is None:
                if not crossed:
                    continue
                episode_start = local_idx
                extreme = float(row.high) if side == "HIGH" else float(row.low)
                state_bar = row
                if inside_close:
                    reclaim_idx = local_idx
                    reclaim_bar = row
                continue
            assert extreme is not None
            extreme = (
                max(extreme, float(row.high))
                if side == "HIGH"
                else min(extreme, float(row.low))
            )
            if local_idx - episode_start > int(logic["reclaim_max_bars"]):
                break
            if reclaim_idx is None and inside_close:
                reclaim_idx = local_idx
                reclaim_bar = row
                state_bar = row
                continue
            if reclaim_idx is None or reclaim_bar is None or state_bar is None:
                continue
            if local_idx - reclaim_idx > int(logic["confirmation_bars"]) + 2:
                break
            local_mss = (
                float(row.close) < float(reclaim_bar.low)
                if direction == "SHORT"
                else float(row.close) > float(reclaim_bar.high)
            )
            if not local_mss or not directional_trigger(row, direction):
                continue
            # OI contraction defines the state by reclaim; depth and aggressor
            # flow at the later decision independently confirm execution.
            if not flush_state(state_bar):
                continue
            stop_raw = (
                extreme + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                if direction == "SHORT"
                else extreme - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
            )
            emit_candidate(
                records=records,
                one=one,
                occupied=occupied,
                week=week,
                day=day,
                session=session,
                route=f"MICRO_POSITION_FLUSH_{side}_FAILED_AUCTION",
                direction=direction,
                source_high=source_high,
                source_low=source_low,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=source_low if direction == "SHORT" else source_high,
                logic=logic,
                state_bar=state_bar,
                decision_bar=row,
                details={
                    "sweep_extreme": extreme,
                    "reclaim_close": float(reclaim_bar.close),
                    "reclaim_high": float(reclaim_bar.high),
                    "reclaim_low": float(reclaim_bar.low),
                    "local_mss": True,
                    "target_semantics": "OPPOSITE_COMPLETED_SESSION_BOUNDARY",
                },
            )
            break

    # Sponsored true acceptance: independent position sponsorship defines the
    # state, boundary hold defines the pullback, and the subsequent local break
    # is a new execution leg.
    for side, direction in (("HIGH", "LONG"), ("LOW", "SHORT")):
        outside_closes = 0
        accepted_idx: int | None = None
        state_bar: pd.Series | None = None
        pullback_idx: int | None = None
        pullback_bar: pd.Series | None = None
        prior_extreme: float | None = None
        for local_idx, (ts, row) in enumerate(rows):
            outside = (
                float(row.close) > source_high
                if side == "HIGH"
                else float(row.close) < source_low
            )
            if accepted_idx is None:
                outside_closes = outside_closes + 1 if outside else 0
                if outside:
                    prior_extreme = (
                        max(prior_extreme or source_high, float(row.high))
                        if side == "HIGH"
                        else min(prior_extreme or source_low, float(row.low))
                    )
                if (
                    outside_closes >= int(logic["acceptance_closes"])
                    and sponsored_state(row, direction)
                ):
                    accepted_idx = local_idx
                    state_bar = row
                continue
            assert state_bar is not None
            if local_idx - accepted_idx > int(logic["acceptance_retest_expiry_bars"]):
                break
            if not outside:
                break
            prior_extreme = (
                max(prior_extreme or source_high, float(row.high))
                if side == "HIGH"
                else min(prior_extreme or source_low, float(row.low))
            )
            boundary_distance = (
                float(row.low) - source_high
                if side == "HIGH"
                else source_low - float(row.high)
            )
            near_boundary = boundary_distance <= (
                float(logic["fvg_boundary_tolerance_atr"]) * float(row.atr)
            )
            if pullback_idx is None and near_boundary:
                pullback_idx = local_idx
                pullback_bar = row
                continue
            if pullback_idx is None or pullback_bar is None:
                continue
            if local_idx - pullback_idx > int(logic["reclaim_max_bars"]) + 1:
                break
            local_break = (
                float(row.close) > float(pullback_bar.high)
                if direction == "LONG"
                else float(row.close) < float(pullback_bar.low)
            )
            if not local_break or not directional_trigger(row, direction):
                continue
            stop_raw = (
                float(pullback_bar.low)
                - float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
                if direction == "LONG"
                else float(pullback_bar.high)
                + float(logic["fvg_stop_buffer_atr"]) * float(row.atr)
            )
            target_raw = (
                source_high + width if direction == "LONG" else source_low - width
            )
            emit_candidate(
                records=records,
                one=one,
                occupied=occupied,
                week=week,
                day=day,
                session=session,
                route=f"MICRO_SPONSORED_{side}_ACCEPTANCE_CONTINUATION",
                direction=direction,
                source_high=source_high,
                source_low=source_low,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=target_raw,
                logic=logic,
                state_bar=state_bar,
                decision_bar=row,
                details={
                    "outside_closes_at_state": outside_closes,
                    "pullback_high": float(pullback_bar.high),
                    "pullback_low": float(pullback_bar.low),
                    "prior_acceptance_extreme": prior_extreme,
                    "local_break": True,
                    "target_semantics": "ONE_COMPLETED_SESSION_RANGE_PROJECTION",
                },
            )
            break

    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    costed = [r for r in records if r["geometry"] is not None]
    additive = [r for r in costed if not r["overlaps_baseline"]]
    outcomes: dict[str, int] = {}
    routes: dict[str, dict[str, int]] = {}
    geometry_reasons: dict[str, int] = {}
    for record in records:
        reason = str(record["geometry_reason"])
        geometry_reasons[reason] = geometry_reasons.get(reason, 0) + 1
    for record in additive:
        outcome = str(record["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        bucket = routes.setdefault(str(record["route"]), {})
        bucket[outcome] = bucket.get(outcome, 0) + 1
    return {
        "raw_candidates": len(records),
        "costed_candidates": len(costed),
        "additive_costed_candidates": len(additive),
        "additive_outcomes": outcomes,
        "route_outcomes": routes,
        "geometry_reasons": geometry_reasons,
        "additive_targets": [
            {
                "day": r["day"],
                "session": r["session"],
                "route": r["route"],
                "direction": r["direction"],
                "observed_ts": r["observed_ts"],
                "net_r": r["geometry"]["net_r"],
                "terminal_ts": r["terminal_ts"],
            }
            for r in additive
            if r["outcome"] == "TARGET"
        ],
        "additive_stops": [
            {
                "day": r["day"],
                "session": r["session"],
                "route": r["route"],
                "direction": r["direction"],
                "observed_ts": r["observed_ts"],
                "net_r": r["geometry"]["net_r"],
                "terminal_ts": r["terminal_ts"],
            }
            for r in additive
            if r["outcome"] == "STOP"
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
    parser.add_argument("--feature-dir", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    logic = dict(config["logic"])
    logic["price_increment"] = float(
        config["symbols"]["BTCUSDT"]["price_increment"]
    )
    result: dict[str, Any] = {
        "schema": "candidate-12-i22-causal-microstate-v1",
        "candidate_source": config["candidate"],
        "not_performance_evidence": True,
        "forward_columns_prohibited": sorted(FORWARD_COLUMNS),
        "families": {
            "position_flush_failed_auction": (
                "session sweep -> OI contraction -> close inside -> "
                "opposite depth/aggressor alignment -> local MSS"
            ),
            "sponsored_acceptance_continuation": (
                "two closes outside -> OI expansion and supportive depth -> "
                "boundary pullback hold -> renewed directional flow/local break"
            ),
        },
        "weeks": {},
    }

    for week in args.weeks:
        spec = config["selection"]["weeks"][week]
        start = date.fromisoformat(spec["start"])
        end_exclusive = date.fromisoformat(spec["end_exclusive"])
        warmup = start - timedelta(days=int(config["selection"]["warmup_days"]))
        one, market_manifest = load_binance_bars(
            "BTCUSDT",
            warmup,
            end_exclusive,
            args.data_dir,
        )
        features, feature_manifest = load_features(
            warmup,
            end_exclusive + timedelta(days=1),
            args.feature_dir,
        )
        joined = joined_bars(one, features, float(logic["price_increment"]))
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
                            joined=joined,
                            one=one,
                            occupied=occupied,
                            logic=logic,
                        )
                    )
            cursor += timedelta(days=1)
        result["weeks"][week] = {
            "evaluation_start": start.isoformat(),
            "evaluation_end_exclusive": end_exclusive.isoformat(),
            "market_manifest": market_manifest,
            "feature_manifest": feature_manifest,
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
            {week: data["summary"] for week, data in result["weeks"].items()},
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
