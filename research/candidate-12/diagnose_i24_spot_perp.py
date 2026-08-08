#!/usr/bin/env python3
"""Spot-perpetual price-discovery diagnostic for Candidate 12.

Development-only diagnostic. It reuses the frozen I19 completed-session
context and separates three latent states using synchronized Binance spot and
USD-M perpetual prices:

* PERP_DISLOCATION_FAILURE: perpetual accepts first, directional basis widens,
  spot does not confirm, and the perpetual returns inside as basis unwinds;
* SPOT_LED_ACCEPTANCE: spot accepts first and the perpetual later accepts;
* PERP_LED_CONFIRMED_ACCEPTANCE: perpetual accepts first, spot later confirms,
  perpetual remains outside and the directional basis normalizes by spot catch-up.

The ownership state is defined by market ordering and basis path. A strictly
later BTC perpetual local break is the execution transition. Entry, stop and
target remain BTC-perpetual-native. All observations use completed bars. This
file owns no matching, portfolio, account or PnL engine and is not performance
evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZipFile

import numpy as np
import pandas as pd

from data_loader import COLUMNS, _download, load_binance_bars


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


def load_spot_bars(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Load immutable Binance spot one-minute klines at causal close time."""
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end_inclusive:
        filename = f"{symbol}-1m-{cursor.isoformat()}.zip"
        url = (
            "https://data.binance.vision/data/spot/daily/klines/"
            f"{symbol}/1m/{filename}"
        )
        path = data_dir / "spot" / symbol / filename
        _download(url, path)
        digest = sha256(path.read_bytes()).hexdigest()
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != 1 or not members[0].lower().endswith(".csv"):
                raise RuntimeError(f"unexpected ZIP members in {filename}: {members}")
            with archive.open(members[0]) as stream:
                frame = pd.read_csv(stream, header=None, names=COLUMNS)
        frame = frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()
        if len(frame.index) not in (1439, 1440, 1441):
            raise RuntimeError(f"unexpected spot row count {len(frame.index)} for {filename}")
        frames.append(frame)
        manifest.append(
            {
                "market": "spot",
                "symbol": symbol,
                "date": cursor.isoformat(),
                "url": url,
                "file": str(path.relative_to(data_dir)),
                "bytes": path.stat().st_size,
                "sha256": digest,
                "rows": len(frame.index),
            }
        )
        cursor += timedelta(days=1)

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last")
    raw = raw.sort_values("open_time", kind="stable").reset_index(drop=True)
    open_time = pd.to_numeric(raw["open_time"], errors="raise")
    first = int(open_time.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported Binance spot timestamp magnitude: {first}")
    index = pd.to_datetime(open_time, unit=unit, utc=True) + pd.Timedelta(minutes=1)
    result = pd.DataFrame(
        {
            name: pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
            for name in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "taker_buy_volume",
            )
        },
        index=index,
    )
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise RuntimeError("spot timestamps are not strictly increasing and unique")
    if (result["volume"] < 0).any() or (result["taker_buy_volume"] < 0).any():
        raise RuntimeError("negative volume in spot source data")
    if (result["taker_buy_volume"] > result["volume"] + 1e-9).any():
        raise RuntimeError("spot taker-buy volume exceeds total volume")
    return result, manifest


def aggregate_five(one: pd.DataFrame, prefix: str) -> pd.DataFrame:
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
    out["atr"] = true_range.rolling(36, min_periods=36).mean()
    out["body"] = (out["close"] - out["open"]).abs()
    spread = out["high"] - out["low"]
    out["close_location"] = np.where(
        spread > 0,
        (out["close"] - out["low"]) / spread,
        0.5,
    )
    out["signed_flow"] = np.where(
        out["volume"] > 0,
        2.0 * out["taker_buy_volume"] / out["volume"] - 1.0,
        0.0,
    ).clip(-1.0, 1.0)
    return out.rename(columns={name: f"{prefix}_{name}" for name in out.columns})


def joined_market(perp_one: pd.DataFrame, spot_one: pd.DataFrame) -> pd.DataFrame:
    perp = aggregate_five(perp_one, "perp")
    spot = aggregate_five(spot_one, "spot")
    joined = perp.join(spot, how="inner")
    if joined.empty:
        raise RuntimeError("no synchronized spot/perpetual five-minute bars")
    joined["basis_bps"] = (
        np.log(joined["perp_close"] / joined["spot_close"]) * 10_000.0
    )
    if not np.isfinite(joined["basis_bps"]).all():
        raise RuntimeError("non-finite synchronized spot/perpetual basis")
    return joined


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
    logic: dict[str, Any],
) -> tuple[dict[str, float] | None, str]:
    tick = float(logic["price_increment"])
    if direction == "LONG":
        entry = round_tick(entry_raw, tick, "UP")
        stop = round_tick(stop_raw, tick, "DOWN")
        target = round_tick(target_raw, tick, "DOWN")
        structural_loss = entry - stop
        structural_profit = target - entry
        target_preconsumed = float(decision.perp_high) >= target
    else:
        entry = round_tick(entry_raw, tick, "DOWN")
        stop = round_tick(stop_raw, tick, "UP")
        target = round_tick(target_raw, tick, "UP")
        structural_loss = stop - entry
        structural_profit = entry - target
        target_preconsumed = float(decision.perp_low) <= target
    atr = float(decision.perp_atr)
    if not np.isfinite(atr):
        return None, "ATR_UNAVAILABLE"
    if structural_loss <= 0:
        return None, "INVALID_STRUCTURAL_STOP"
    if structural_loss > float(logic["max_stop_atr"]) * atr:
        return None, "STOP_EXCEEDS_MAX_ATR"
    if structural_profit <= 0:
        return None, "INVALID_STRUCTURAL_TARGET"
    if target_preconsumed:
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
            "structural_loss": structural_loss,
            "structural_profit": structural_profit,
        },
        "OK",
    )


def first_touch(
    perp_one: pd.DataFrame,
    *,
    observed: pd.Timestamp,
    end: pd.Timestamp,
    direction: str,
    stop: float,
    target: float,
) -> dict[str, Any]:
    future = perp_one[(perp_one.index > observed) & (perp_one.index <= end)]
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


def ranges_for_session(
    day: date,
    session: SessionSpec,
    joined: pd.DataFrame,
) -> dict[str, float] | None:
    day_start = pd.Timestamp(day, tz="UTC")
    start = day_start + pd.Timedelta(minutes=session.build_start)
    end = day_start + pd.Timedelta(minutes=session.build_end)
    build = joined[(joined.index > start) & (joined.index <= end)]
    if build.empty or end not in joined.index:
        return None
    result = {
        "perp_high": float(build.perp_high.max()),
        "perp_low": float(build.perp_low.min()),
        "spot_high": float(build.spot_high.max()),
        "spot_low": float(build.spot_low.min()),
        "anchor_basis_bps": float(joined.loc[end, "basis_bps"]),
    }
    result["perp_width"] = result["perp_high"] - result["perp_low"]
    result["spot_width"] = result["spot_high"] - result["spot_low"]
    if result["perp_width"] <= 0 or result["spot_width"] <= 0:
        return None
    return result


def outside(row: pd.Series, ranges: dict[str, float], market: str, side: str) -> bool:
    close = float(row[f"{market}_close"])
    boundary = ranges[f"{market}_{'high' if side == 'HIGH' else 'low'}"]
    return close > boundary if side == "HIGH" else close < boundary


def crossed(row: pd.Series, ranges: dict[str, float], market: str, side: str) -> bool:
    value = float(row[f"{market}_{'high' if side == 'HIGH' else 'low'}"])
    boundary = ranges[f"{market}_{'high' if side == 'HIGH' else 'low'}"]
    return value > boundary if side == "HIGH" else value < boundary


def basis_move(row: pd.Series, ranges: dict[str, float], side: str) -> float:
    sign = 1.0 if side == "HIGH" else -1.0
    return sign * (float(row.basis_bps) - ranges["anchor_basis_bps"])


def emit(
    *,
    records: list[dict[str, Any]],
    week: str,
    day: date,
    session: SessionSpec,
    route: str,
    ownership: str,
    direction: str,
    observed: pd.Timestamp,
    decision: pd.Series,
    stop_raw: float,
    target_raw: float,
    ranges: dict[str, float],
    state_details: dict[str, Any],
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> None:
    geometry, reason = costed_geometry(
        direction=direction,
        entry_raw=float(decision.perp_close),
        stop_raw=stop_raw,
        target_raw=target_raw,
        decision=decision,
        logic=logic,
    )
    record: dict[str, Any] = {
        "week": week,
        "day": day.isoformat(),
        "session": session.name,
        "route": route,
        "ownership_state": ownership,
        "direction": direction,
        "observed_ts": observed.isoformat(),
        "ranges": ranges,
        "decision": {
            "perp_open": float(decision.perp_open),
            "perp_high": float(decision.perp_high),
            "perp_low": float(decision.perp_low),
            "perp_close": float(decision.perp_close),
            "perp_atr": float(decision.perp_atr),
            "spot_close": float(decision.spot_close),
            "basis_bps": float(decision.basis_bps),
            "basis_directional_move_bps": basis_move(
                decision,
                ranges,
                "HIGH" if direction == "LONG" else "LOW",
            ),
        },
        "state_details": state_details,
        "geometry": geometry,
        "geometry_reason": reason,
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
            perp_one,
            observed=observed,
            end=pd.Timestamp(day, tz="UTC")
            + pd.Timedelta(minutes=session.trade_end),
            direction=direction,
            stop=geometry["stop"],
            target=geometry["target"],
        )
        record.update(terminal)
        record["overlaps_baseline"] = interval_overlaps(
            observed,
            pd.Timestamp(terminal["terminal_ts"]),
            occupied,
        )
    records.append(record)


def screen_dislocation_failure(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    trade: pd.DataFrame,
    ranges: dict[str, float],
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    records: list[dict[str, Any]],
) -> None:
    rows = list(trade.iterrows())
    for side, direction in (("HIGH", "SHORT"), ("LOW", "LONG")):
        lead_idx: int | None = None
        lead_ts: pd.Timestamp | None = None
        lead_row: pd.Series | None = None
        extreme: float | None = None
        max_directional_basis: float | None = None
        spot_confirmed = False
        reclaim_idx: int | None = None
        reclaim_row: pd.Series | None = None
        for index, (ts, row) in enumerate(rows):
            perp_outside = outside(row, ranges, "perp", side)
            spot_outside = outside(row, ranges, "spot", side)
            if lead_idx is None:
                if not perp_outside or spot_outside:
                    continue
                directional_basis = basis_move(row, ranges, side)
                if directional_basis <= 0.0:
                    continue
                lead_idx = index
                lead_ts = ts
                lead_row = row
                extreme = (
                    float(row.perp_high) if side == "HIGH" else float(row.perp_low)
                )
                max_directional_basis = directional_basis
                continue
            assert lead_ts is not None and lead_row is not None
            assert extreme is not None and max_directional_basis is not None
            extreme = (
                max(extreme, float(row.perp_high))
                if side == "HIGH"
                else min(extreme, float(row.perp_low))
            )
            max_directional_basis = max(
                max_directional_basis,
                basis_move(row, ranges, side),
            )
            if spot_outside:
                spot_confirmed = True
                break
            if index - lead_idx > int(logic["reclaim_max_bars"]):
                break
            if not perp_outside:
                # The derivative-led premium/discount must have actually
                # unwound before this can be called a failed dislocation.
                if basis_move(row, ranges, side) >= max_directional_basis:
                    break
                reclaim_idx = index
                reclaim_row = row
                break
        if (
            lead_idx is None
            or reclaim_idx is None
            or reclaim_row is None
            or spot_confirmed
        ):
            continue
        for index in range(reclaim_idx + 1, min(len(rows), reclaim_idx + 4)):
            ts, row = rows[index]
            mss = (
                float(row.perp_close) < float(reclaim_row.perp_low)
                if direction == "SHORT"
                else float(row.perp_close) > float(reclaim_row.perp_high)
            )
            if not mss:
                continue
            # A later close outside again invalidates the failed-dislocation
            # state rather than allowing repeated entry from the same event.
            if outside(row, ranges, "perp", side) or outside(
                row,
                ranges,
                "spot",
                side,
            ):
                break
            stop_raw = (
                extreme
                + float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
                if direction == "SHORT"
                else extreme
                - float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=f"SPOT_PERP_{side}_DISLOCATION_FAILURE",
                ownership="PERP_LED_SPOT_UNCONFIRMED_BASIS_UNWIND",
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=(
                    ranges["perp_low"]
                    if direction == "SHORT"
                    else ranges["perp_high"]
                ),
                ranges=ranges,
                state_details={
                    "perp_lead_ts": lead_ts.isoformat(),
                    "perp_lead_close": float(lead_row.perp_close),
                    "spot_close_at_lead": float(lead_row.spot_close),
                    "lead_directional_basis_bps": basis_move(
                        lead_row,
                        ranges,
                        side,
                    ),
                    "maximum_directional_basis_bps": max_directional_basis,
                    "reclaim_ts": rows[reclaim_idx][0].isoformat(),
                    "reclaim_directional_basis_bps": basis_move(
                        reclaim_row,
                        ranges,
                        side,
                    ),
                    "excursion_extreme": extreme,
                    "local_mss": True,
                    "target_semantics": "OPPOSITE_COMPLETED_PERP_SESSION_BOUNDARY",
                },
                logic=logic,
                perp_one=perp_one,
                occupied=occupied,
            )
            break


def screen_acceptance_delivery(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    trade: pd.DataFrame,
    ranges: dict[str, float],
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
    records: list[dict[str, Any]],
) -> None:
    rows = list(trade.iterrows())
    for side, direction in (("HIGH", "LONG"), ("LOW", "SHORT")):
        spot_run = 0
        perp_run = 0
        spot_accept_idx: int | None = None
        spot_accept_ts: pd.Timestamp | None = None
        perp_accept_idx: int | None = None
        perp_accept_ts: pd.Timestamp | None = None
        first_spot_outside: pd.Timestamp | None = None
        first_perp_outside: pd.Timestamp | None = None
        ownership: str | None = None
        state_basis_peak: float | None = None
        pullback_idx: int | None = None
        pullback_row: pd.Series | None = None
        for index, (ts, row) in enumerate(rows):
            spot_outside = outside(row, ranges, "spot", side)
            perp_outside = outside(row, ranges, "perp", side)
            if first_spot_outside is None and spot_outside:
                first_spot_outside = ts
            if first_perp_outside is None and perp_outside:
                first_perp_outside = ts
            spot_run = spot_run + 1 if spot_outside else 0
            perp_run = perp_run + 1 if perp_outside else 0
            if (
                spot_accept_idx is None
                and spot_run >= int(logic["acceptance_closes"])
            ):
                spot_accept_idx = index
                spot_accept_ts = ts
            if (
                perp_accept_idx is None
                and perp_run >= int(logic["acceptance_closes"])
            ):
                perp_accept_idx = index
                perp_accept_ts = ts
                state_basis_peak = basis_move(row, ranges, side)

            if spot_accept_idx is None or perp_accept_idx is None:
                continue
            if ownership is None:
                if spot_accept_idx < perp_accept_idx:
                    ownership = "SPOT_LED_THEN_PERP_ACCEPTED"
                elif perp_accept_idx < spot_accept_idx:
                    ownership = "PERP_LED_THEN_SPOT_CONFIRMED"
                else:
                    # Simultaneous acceptance is unresolved because the data
                    # cannot identify which venue supplied the information.
                    break

            assert state_basis_peak is not None
            if not spot_outside or not perp_outside:
                break
            if index - max(spot_accept_idx, perp_accept_idx) > int(
                logic["acceptance_retest_expiry_bars"]
            ):
                break

            if ownership == "PERP_LED_THEN_SPOT_CONFIRMED":
                # Spot catch-up must reduce the derivative-led directional basis
                # while the perpetual remains outside.
                if basis_move(row, ranges, side) >= state_basis_peak:
                    continue
            else:
                # Spot leadership should not be replaced by a new derivative
                # premium/discount expansion before the pullback is tradable.
                if basis_move(row, ranges, side) > 0.0:
                    continue

            boundary_distance = (
                float(row.perp_low) - ranges["perp_high"]
                if side == "HIGH"
                else ranges["perp_low"] - float(row.perp_high)
            )
            near_boundary = boundary_distance <= (
                float(logic["fvg_boundary_tolerance_atr"])
                * float(row.perp_atr)
            )
            if pullback_idx is None and near_boundary:
                pullback_idx = index
                pullback_row = row
                continue
            if pullback_idx is None or pullback_row is None:
                continue
            if index - pullback_idx > int(logic["reclaim_max_bars"]) + 1:
                break
            local_break = (
                float(row.perp_close) > float(pullback_row.perp_high)
                if direction == "LONG"
                else float(row.perp_close) < float(pullback_row.perp_low)
            )
            if not local_break:
                continue
            if not outside(row, ranges, "spot", side) or not outside(
                row,
                ranges,
                "perp",
                side,
            ):
                break
            stop_raw = (
                float(pullback_row.perp_low)
                - float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
                if direction == "LONG"
                else float(pullback_row.perp_high)
                + float(logic["fvg_stop_buffer_atr"]) * float(row.perp_atr)
            )
            target_raw = (
                ranges["perp_high"] + ranges["perp_width"]
                if direction == "LONG"
                else ranges["perp_low"] - ranges["perp_width"]
            )
            emit(
                records=records,
                week=week,
                day=day,
                session=session,
                route=(
                    f"SPOT_PERP_{side}_SPOT_LED_ACCEPTANCE"
                    if ownership == "SPOT_LED_THEN_PERP_ACCEPTED"
                    else f"SPOT_PERP_{side}_PERP_LED_CONFIRMED_ACCEPTANCE"
                ),
                ownership=ownership,
                direction=direction,
                observed=ts,
                decision=row,
                stop_raw=stop_raw,
                target_raw=target_raw,
                ranges=ranges,
                state_details={
                    "first_spot_outside": (
                        None
                        if first_spot_outside is None
                        else first_spot_outside.isoformat()
                    ),
                    "first_perp_outside": (
                        None
                        if first_perp_outside is None
                        else first_perp_outside.isoformat()
                    ),
                    "spot_accept_ts": (
                        None if spot_accept_ts is None else spot_accept_ts.isoformat()
                    ),
                    "perp_accept_ts": (
                        None if perp_accept_ts is None else perp_accept_ts.isoformat()
                    ),
                    "directional_basis_at_perp_accept": state_basis_peak,
                    "directional_basis_at_decision": basis_move(row, ranges, side),
                    "pullback_high": float(pullback_row.perp_high),
                    "pullback_low": float(pullback_row.perp_low),
                    "local_break": True,
                    "target_semantics": "ONE_COMPLETED_PERP_SESSION_RANGE_PROJECTION",
                },
                logic=logic,
                perp_one=perp_one,
                occupied=occupied,
            )
            break


def screen_session(
    *,
    week: str,
    day: date,
    session: SessionSpec,
    joined: pd.DataFrame,
    logic: dict[str, Any],
    perp_one: pd.DataFrame,
    occupied: list[tuple[pd.Timestamp, pd.Timestamp]],
) -> list[dict[str, Any]]:
    ranges = ranges_for_session(day, session, joined)
    if ranges is None:
        return []
    day_start = pd.Timestamp(day, tz="UTC")
    build_end = day_start + pd.Timedelta(minutes=session.build_end)
    trade_end = day_start + pd.Timedelta(minutes=session.trade_end)
    trade = joined[(joined.index > build_end) & (joined.index <= trade_end)]
    if trade.empty:
        return []
    records: list[dict[str, Any]] = []
    screen_dislocation_failure(
        week=week,
        day=day,
        session=session,
        trade=trade,
        ranges=ranges,
        logic=logic,
        perp_one=perp_one,
        occupied=occupied,
        records=records,
    )
    screen_acceptance_delivery(
        week=week,
        day=day,
        session=session,
        trade=trade,
        ranges=ranges,
        logic=logic,
        perp_one=perp_one,
        occupied=occupied,
        records=records,
    )
    # One observation time/direction/session is one causal opportunity even if
    # more than one descriptive family can name it.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (record["observed_ts"], record["direction"], record["session"])
        unique.setdefault(key, record)
    return list(unique.values())


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    costed = [record for record in records if record["geometry"] is not None]
    additive = [record for record in costed if not record["overlaps_baseline"]]
    outcomes: dict[str, int] = {}
    routes: dict[str, dict[str, int]] = {}
    geometry_reasons: dict[str, int] = {}
    for record in records:
        reason = str(record["geometry_reason"])
        geometry_reasons[reason] = geometry_reasons.get(reason, 0) + 1
    for record in additive:
        outcome = str(record["outcome"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        route = str(record["route"])
        bucket = routes.setdefault(route, {})
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
                "day": record["day"],
                "session": record["session"],
                "route": record["route"],
                "direction": record["direction"],
                "observed_ts": record["observed_ts"],
                "net_r": record["geometry"]["net_r"],
                "terminal_ts": record["terminal_ts"],
            }
            for record in additive
            if record["outcome"] == "TARGET"
        ],
        "additive_stops": [
            {
                "day": record["day"],
                "session": record["session"],
                "route": record["route"],
                "direction": record["direction"],
                "observed_ts": record["observed_ts"],
                "net_r": record["geometry"]["net_r"],
                "terminal_ts": record["terminal_ts"],
            }
            for record in additive
            if record["outcome"] == "STOP"
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
        "schema": "candidate-12-i24-spot-perp-ownership-v1",
        "candidate_source": config["candidate"],
        "not_performance_evidence": True,
        "policy": (
            "completed spot/perpetual session ranges -> market acceptance order "
            "and directional basis path -> strictly later BTC-perpetual local "
            "transition -> BTC-perpetual-native geometry"
        ),
        "weeks": {},
    }
    for week in args.weeks:
        spec = config["selection"]["weeks"][week]
        evaluation_start = date.fromisoformat(spec["start"])
        evaluation_end = date.fromisoformat(spec["end_exclusive"])
        warmup = evaluation_start - timedelta(
            days=int(config["selection"]["warmup_days"])
        )
        perp_one, perp_manifest = load_binance_bars(
            "BTCUSDT",
            warmup,
            evaluation_end,
            args.data_dir / "perpetual",
        )
        spot_one, spot_manifest = load_spot_bars(
            "BTCUSDT",
            warmup,
            evaluation_end,
            args.data_dir,
        )
        joined = joined_market(perp_one, spot_one)
        occupied = parse_positions(
            args.baseline_root / f"BTCUSDT-{week}" / "positions.csv"
        )
        records: list[dict[str, Any]] = []
        cursor = evaluation_start
        while cursor < evaluation_end:
            if cursor.weekday() < 5:
                for session in SESSIONS:
                    records.extend(
                        screen_session(
                            week=week,
                            day=cursor,
                            session=session,
                            joined=joined,
                            logic=logic,
                            perp_one=perp_one,
                            occupied=occupied,
                        )
                    )
            cursor += timedelta(days=1)
        result["weeks"][week] = {
            "evaluation_start": evaluation_start.isoformat(),
            "evaluation_end_exclusive": evaluation_end.isoformat(),
            "perpetual_manifest": perp_manifest,
            "spot_manifest": spot_manifest,
            "baseline_positions": [
                {"opened": start.isoformat(), "closed": end.isoformat()}
                for start, end in occupied
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
