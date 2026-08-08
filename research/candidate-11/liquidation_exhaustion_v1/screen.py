#!/usr/bin/env python3
"""Actual-liquidation failed-auction diagnostic.

Binance forced-order snapshots are grouped into causal same-side cascades.  A
parent is admitted only when its notional is in the shifted trailing 24-hour
99th percentile and ordinary aggregate trades confirm price delivery in the
liquidation direction.  A reversal is not assumed: a strictly later opposite
price/active-flow initiative must appear before an executable bracket is
measured.

This is a diagnostic screen only.  NautilusTrader remains required before any
account or performance claim.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

import features as candidate05_features


ROUND_TRIP_COST_BPS = 20.0
MIN_NET_R = 1.15
CASCADE_GAP_SECONDS = 10
HISTORY_HOURS = 24
MIN_HISTORY_CASCADES = 50
TAIL_QUANTILE = 0.99
MAX_CONFIRM_SECONDS = 10
MAX_HOLD_SECONDS = 300
STOP_BUFFER_BPS = 1.0


@dataclass(frozen=True, slots=True)
class RawFile:
    endpoint: str
    day: str
    url: str
    local_path: str
    size_bytes: int
    sha256: str


@dataclass(slots=True)
class Cascade:
    start: pd.Timestamp
    end: pd.Timestamp
    direction: int
    notional: float
    snapshots: int


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, path: Path, attempts: int = 5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    temporary = path.with_suffix(path.suffix + ".tmp")
    last_error: Exception | None = None
    for attempt in range(attempts):
        temporary.unlink(missing_ok=True)
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-liquidation-research"},
            )
            with (
                urllib.request.urlopen(request, timeout=180) as response,
                temporary.open("wb") as target,
            ):
                while block := response.read(1 << 20):
                    target.write(block)
            if temporary.stat().st_size <= 0:
                raise RuntimeError(f"empty download: {url}")
            temporary.replace(path)
            return path
        except (
            OSError,
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            RuntimeError,
        ) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed: {url}") from last_error


def aggregate_trades(path: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for chunk in candidate05_features._agg_reader(path):
        price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
        quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
        transact = pd.to_numeric(chunk["transact_time"], errors="raise")
        unit = "us" if float(transact.iloc[0]) > 10**14 else "ms"
        timestamp = pd.to_datetime(transact, unit=unit, utc=True)
        maker = candidate05_features._maker_mask(chunk["is_buyer_maker"])
        notional = price * quantity
        signed = np.where(maker.to_numpy(), -notional.to_numpy(), notional.to_numpy())
        work = pd.DataFrame(
            {
                "second": timestamp.dt.floor("s"),
                "price": price.to_numpy(),
                "notional": notional.to_numpy(),
                "signed": signed,
            },
        )
        parts.append(
            work.groupby("second", sort=True).agg(
                close=("price", "last"),
                high=("price", "max"),
                low=("price", "min"),
                notional=("notional", "sum"),
                signed=("signed", "sum"),
                trades=("price", "size"),
            ),
        )
    if not parts:
        raise RuntimeError("empty aggregate-trade archive")
    frame = pd.concat(parts).sort_index(kind="stable")
    frame = frame.groupby(level=0, sort=True).agg(
        close=("close", "last"),
        high=("high", "max"),
        low=("low", "min"),
        notional=("notional", "sum"),
        signed=("signed", "sum"),
        trades=("trades", "sum"),
    )
    frame["flow"] = frame["signed"] / frame["notional"].replace(0.0, np.nan)
    frame["ret_bps"] = np.log(frame["close"] / frame["close"].shift(1)) * 10_000.0
    return frame.replace([np.inf, -np.inf], np.nan)


def _normalize_columns(columns: list[str]) -> dict[str, str]:
    return {
        str(column).strip().lower().replace(" ", "_"): str(column)
        for column in columns
    }


def read_liquidations(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    normalized = _normalize_columns(list(raw.columns))
    if "trade_time" not in normalized or "side" not in normalized:
        names = [
            "ts",
            "trade_time",
            "symbol",
            "side",
            "order_type",
            "order_status",
            "time_in_force",
            "original_quantity",
            "price",
            "average_price",
            "last_filled_quantity",
            "filled_accumulated_quantity",
        ]
        raw = pd.read_csv(path, compression="zip", header=None, names=names)
        normalized = _normalize_columns(list(raw.columns))
    quantity_name = next(
        (
            normalized[name]
            for name in (
                "filled_accumulated_quantity",
                "filled_quantity",
                "original_quantity",
            )
            if name in normalized
        ),
        None,
    )
    price_name = next(
        (
            normalized[name]
            for name in ("average_price", "price")
            if name in normalized
        ),
        None,
    )
    if quantity_name is None or price_name is None:
        raise RuntimeError(f"unexpected liquidation schema: {list(raw.columns)}")
    trade_time = pd.to_numeric(raw[normalized["trade_time"]], errors="raise")
    unit = "us" if float(trade_time.iloc[0]) > 10**14 else "ms"
    timestamp = pd.to_datetime(trade_time, unit=unit, utc=True)
    side = raw[normalized["side"]].astype(str).str.strip().str.upper()
    if not side.isin({"BUY", "SELL"}).all():
        raise RuntimeError("unexpected liquidation side")
    quantity = pd.to_numeric(raw[quantity_name], errors="raise").astype(float)
    price = pd.to_numeric(raw[price_name], errors="raise").astype(float)
    frame = pd.DataFrame(
        {
            "ts": timestamp,
            # BUY closes shorts and pushes up; SELL closes longs and pushes down.
            "direction": np.where(side.eq("BUY"), 1, -1),
            "notional": quantity * price,
        },
    ).sort_values("ts", kind="stable")
    return frame[frame["notional"].gt(0.0)].reset_index(drop=True)


def group_cascades(liquidations: pd.DataFrame) -> list[Cascade]:
    cascades: list[Cascade] = []
    current: Cascade | None = None
    for row in liquidations.itertuples(index=False):
        ts = pd.Timestamp(row.ts)
        direction = int(row.direction)
        notional = float(row.notional)
        if (
            current is None
            or direction != current.direction
            or (ts - current.end).total_seconds() > CASCADE_GAP_SECONDS
        ):
            current = Cascade(ts, ts, direction, notional, 1)
            cascades.append(current)
        else:
            current.end = ts
            current.notional += notional
            current.snapshots += 1
    return cascades


def load_range(symbol: str, start: date, end: date, cache: Path) -> tuple[pd.DataFrame, list[Cascade], list[RawFile]]:
    trades: list[pd.DataFrame] = []
    liquidation_parts: list[pd.DataFrame] = []
    evidence: list[RawFile] = []
    day = start
    while day <= end:
        stamp = day.isoformat()
        trade_url = (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/"
            f"{symbol}/{symbol}-aggTrades-{stamp}.zip"
        )
        liquidation_url = (
            "https://data.binance.vision/data/futures/um/daily/"
            f"liquidationSnapshot/{symbol}/{symbol}-liquidationSnapshot-{stamp}.zip"
        )
        trade_path = _download(
            trade_url,
            cache / "aggTrades" / f"{symbol}-aggTrades-{stamp}.zip",
        )
        liquidation_path = _download(
            liquidation_url,
            cache / "liquidationSnapshot" / f"{symbol}-liquidationSnapshot-{stamp}.zip",
        )
        trades.append(aggregate_trades(trade_path))
        liquidation_parts.append(read_liquidations(liquidation_path))
        evidence.extend(
            [
                RawFile("aggTrades", stamp, trade_url, str(trade_path), trade_path.stat().st_size, _sha256(trade_path)),
                RawFile("liquidationSnapshot", stamp, liquidation_url, str(liquidation_path), liquidation_path.stat().st_size, _sha256(liquidation_path)),
            ],
        )
        day += timedelta(days=1)
    trade_frame = pd.concat(trades).sort_index()
    liquidation_frame = pd.concat(liquidation_parts).sort_values("ts", kind="stable")
    return trade_frame, group_cascades(liquidation_frame), evidence


def _nearest_index(index: pd.DatetimeIndex, timestamp: pd.Timestamp, side: str) -> int | None:
    position = int(index.searchsorted(timestamp.floor("s"), side=side))
    if position < 0 or position >= len(index):
        return None
    return position


def screen(trades: pd.DataFrame, cascades: list[Cascade]) -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = trades.index
    cascade_times = np.array([item.start.value for item in cascades], dtype=np.int64)
    notionals = np.array([item.notional for item in cascades], dtype=float)

    for sequence, cascade in enumerate(cascades):
        cutoff = cascade.start - pd.Timedelta(hours=HISTORY_HOURS)
        left = int(np.searchsorted(cascade_times, cutoff.value, side="left"))
        history = notionals[left:sequence]
        if len(history) < MIN_HISTORY_CASCADES:
            continue
        threshold = float(np.quantile(history, TAIL_QUANTILE))
        if cascade.notional < threshold:
            continue

        start_i = _nearest_index(index, cascade.start, "left")
        end_i = _nearest_index(index, cascade.end, "right")
        if start_i is None or end_i is None or start_i <= 0 or end_i <= start_i:
            continue
        end_i = min(end_i, len(trades) - 1)
        pre = float(trades.iloc[start_i - 1]["close"])
        segment = trades.iloc[start_i : end_i + 1]
        end_price = float(segment.iloc[-1]["close"])
        direction = cascade.direction
        delivered_bps = direction * math.log(end_price / pre) * 10_000.0
        signed_flow = float(segment["signed"].sum() / segment["notional"].sum())
        extreme = float(segment["low"].min()) if direction < 0 else float(segment["high"].max())
        if delivered_bps <= 0.0 or direction * signed_flow <= 0.0:
            rows.append(
                {
                    "cascade_start": cascade.start,
                    "classification": "LIQUIDATION_NOT_PRICE_DELIVERED",
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "tail_threshold": threshold,
                    "delivered_bps": delivered_bps,
                },
            )
            continue

        confirmation_i: int | None = None
        for j in range(end_i + 1, min(len(trades), end_i + 1 + MAX_CONFIRM_SECONDS)):
            later = trades.iloc[j]
            later_close = float(later["close"])
            inside = later_close > extreme if direction < 0 else later_close < extreme
            if (
                inside
                and -direction * float(later["flow"]) > 0.0
                and -direction * float(later["ret_bps"]) > 0.0
            ):
                confirmation_i = j
                break
        if confirmation_i is None:
            rows.append(
                {
                    "cascade_start": cascade.start,
                    "classification": "NO_STRICTLY_LATER_OPPOSITE_INITIATIVE",
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "tail_threshold": threshold,
                    "delivered_bps": delivered_bps,
                },
            )
            continue

        side = -direction
        confirmation = trades.iloc[confirmation_i]
        entry = float(confirmation["close"])
        target = pre
        stop = extreme * math.exp(-side * STOP_BUFFER_BPS / 10_000.0)
        target_distance = side * math.log(target / entry) * 10_000.0
        stop_distance = side * math.log(entry / stop) * 10_000.0
        if target_distance <= ROUND_TRIP_COST_BPS or stop_distance <= 0.0:
            rows.append(
                {
                    "cascade_start": cascade.start,
                    "confirmation_ts": index[confirmation_i],
                    "classification": "INVALID_EXECUTABLE_GEOMETRY",
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "delivered_bps": delivered_bps,
                    "target_distance_bps": target_distance,
                    "stop_distance_bps": stop_distance,
                },
            )
            continue
        planned_loss_bps = stop_distance + ROUND_TRIP_COST_BPS
        target_net_bps = target_distance - ROUND_TRIP_COST_BPS
        planned_net_r = target_net_bps / planned_loss_bps
        if planned_net_r < MIN_NET_R:
            rows.append(
                {
                    "cascade_start": cascade.start,
                    "confirmation_ts": index[confirmation_i],
                    "classification": "INSUFFICIENT_NET_R_AFTER_COSTS",
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "delivered_bps": delivered_bps,
                    "target_distance_bps": target_distance,
                    "stop_distance_bps": stop_distance,
                    "planned_net_r": planned_net_r,
                },
            )
            continue

        outcome = "TIMEOUT"
        exit_i = min(len(trades) - 1, confirmation_i + MAX_HOLD_SECONDS)
        exit_price = float(trades.iloc[exit_i]["close"])
        for k in range(confirmation_i + 1, min(len(trades), confirmation_i + 1 + MAX_HOLD_SECONDS)):
            bar = trades.iloc[k]
            stop_hit = float(bar["low"]) <= stop if side > 0 else float(bar["high"]) >= stop
            target_hit = float(bar["high"]) >= target if side > 0 else float(bar["low"]) <= target
            if stop_hit:
                outcome = "STOP_FIRST"
                exit_i = k
                exit_price = stop
                break
            if target_hit:
                outcome = "TARGET_FIRST"
                exit_i = k
                exit_price = target
                break
        gross_bps = side * math.log(exit_price / entry) * 10_000.0
        net_bps = gross_bps - ROUND_TRIP_COST_BPS
        rows.append(
            {
                "cascade_start": cascade.start,
                "cascade_end": cascade.end,
                "confirmation_ts": index[confirmation_i],
                "exit_ts": index[exit_i],
                "classification": "EXECUTABLE",
                "outcome": outcome,
                "parent_direction": direction,
                "side": side,
                "cascade_notional": cascade.notional,
                "cascade_snapshots": cascade.snapshots,
                "tail_threshold": threshold,
                "delivered_bps": delivered_bps,
                "cascade_flow": signed_flow,
                "target_distance_bps": target_distance,
                "stop_distance_bps": stop_distance,
                "planned_loss_bps": planned_loss_bps,
                "planned_net_r": planned_net_r,
                "net_bps": net_bps,
                "realized_r": net_bps / planned_loss_bps,
                "hold_seconds": exit_i - confirmation_i,
            },
        )

    result = pd.DataFrame(rows)
    executable = result[result.get("classification", pd.Series(dtype=str)).eq("EXECUTABLE")].copy()
    wins = int(executable.get("outcome", pd.Series(dtype=str)).eq("TARGET_FIRST").sum())
    losses = int(executable.get("outcome", pd.Series(dtype=str)).eq("STOP_FIRST").sum())
    timeouts = int(executable.get("outcome", pd.Series(dtype=str)).eq("TIMEOUT").sum())
    days = int(index.normalize().nunique())
    summary: dict[str, object] = {
        "schema": "candidate-11-liquidation-exhaustion-screen-v1",
        "classification": "DIAGNOSTIC_SCREEN_ONLY",
        "success_claim": False,
        "calendar_days": days,
        "trade_seconds": int(len(trades)),
        "raw_cascades": int(len(cascades)),
        "tail_parent_records": int(len(result)),
        "classification_counts": result.get("classification", pd.Series(dtype=str)).value_counts().to_dict(),
        "executable_episodes": int(len(executable)),
        "target_first": wins,
        "stop_first": losses,
        "timeouts": timeouts,
        "target_first_rate": wins / len(executable) if len(executable) else 0.0,
        "mean_realized_r": float(executable["realized_r"].mean()) if len(executable) else 0.0,
        "median_realized_r": float(executable["realized_r"].median()) if len(executable) else 0.0,
        "median_planned_net_r": float(executable["planned_net_r"].median()) if len(executable) else 0.0,
        "screen_pass": bool(
            len(executable) >= days
            and len(executable) > 0
            and wins / len(executable) >= 0.80
            and float(executable["realized_r"].mean()) > 0.0
        ),
        "parameters": {
            "cascade_gap_seconds": CASCADE_GAP_SECONDS,
            "history_hours": HISTORY_HOURS,
            "tail_quantile": TAIL_QUANTILE,
            "max_confirmation_seconds": MAX_CONFIRM_SECONDS,
            "max_hold_seconds": MAX_HOLD_SECONDS,
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "minimum_net_r": MIN_NET_R,
        },
    }
    return result, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    args.output.mkdir(parents=True, exist_ok=True)
    trades, cascades, raw = load_range(args.symbol, start, end, args.cache)
    episodes, summary = screen(trades, cascades)
    summary["symbol"] = args.symbol
    summary["start"] = start.isoformat()
    summary["end"] = end.isoformat()
    summary["raw_files"] = [asdict(item) for item in raw]
    episodes.to_csv(args.output / "episodes.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / "raw_evidence.json").write_text(
        json.dumps([asdict(item) for item in raw], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
