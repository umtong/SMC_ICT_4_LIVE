#!/usr/bin/env python3
"""Event-level liquidation failure screen on an immutable microbar dataset.

The public Binance bulk liquidation files used by the first diagnostic were
removed.  This loader reuses the immutable Mindbyte-89/btcusdt-microbar-v2
snapshot at revision 1d41abb.  It combines actual forceOrder events, individual
trades and best bid/ask updates by exchange timestamp.

One same-side liquidation cascade is one causal parent.  A reversal requires a
strictly later opposite trade initiative and independent top-of-book support.
The screen is not an account and cannot authorize a success claim.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable

from huggingface_hub import snapshot_download
import numpy as np
import pandas as pd


DATASET_REPO = "Mindbyte-89/btcusdt-microbar-v2"
DATASET_REVISION = "1d41abb"
ROUND_TRIP_COST_BPS = 20.0
MIN_NET_R = 1.15
CASCADE_GAP_SECONDS = 3
MIN_PRIOR_CASCADES = 20
TAIL_QUANTILE = 0.95
MAX_CONFIRM_SECONDS = 12
MAX_HOLD_SECONDS = 300
STOP_BUFFER_BPS = 1.0
MIN_EXECUTABLE_EPISODES = 3


@dataclass(slots=True)
class Cascade:
    start: pd.Timestamp
    end: pd.Timestamp
    direction: int
    notional: float
    snapshots: int


@dataclass(frozen=True, slots=True)
class DatasetEvidence:
    repo_id: str
    revision: str
    day: str
    trade_files: int
    liquidation_files: int
    book_files: int
    first_observation: str
    last_observation: str


def _parquet_files(root: Path, kind: str, day: str) -> list[Path]:
    files = sorted((root / kind / day).glob("*.parquet"))
    if not files:
        raise RuntimeError(f"no {kind} files for {day}")
    return files


def _read_trade_seconds(files: Iterable[Path]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in files:
        raw = pd.read_parquet(path)
        required = {"timestamp_ms", "price", "quantity", "is_buyer_maker"}
        if not required.issubset(raw.columns):
            raise RuntimeError(f"trade schema drifted: {list(raw.columns)}")
        timestamp = pd.to_datetime(raw["timestamp_ms"], unit="ms", utc=True)
        price = pd.to_numeric(raw["price"], errors="raise").astype(float)
        quantity = pd.to_numeric(raw["quantity"], errors="raise").astype(float)
        maker = raw["is_buyer_maker"].astype(bool)
        notional = price * quantity
        work = pd.DataFrame(
            {
                "second": timestamp.dt.floor("s"),
                "price": price.to_numpy(),
                "notional": notional.to_numpy(),
                "signed": np.where(
                    maker.to_numpy(),
                    -notional.to_numpy(),
                    notional.to_numpy(),
                ),
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


def _read_book_seconds(files: Iterable[Path]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in files:
        raw = pd.read_parquet(path)
        required = {
            "timestamp_ms",
            "bid_price",
            "bid_qty",
            "ask_price",
            "ask_qty",
        }
        if not required.issubset(raw.columns):
            raise RuntimeError(f"book schema drifted: {list(raw.columns)}")
        work = raw[list(required)].copy()
        work["second"] = pd.to_datetime(
            work["timestamp_ms"],
            unit="ms",
            utc=True,
        ).dt.floor("s")
        parts.append(
            work.sort_values("timestamp_ms", kind="stable")
            .groupby("second", sort=True)
            .last()[["bid_price", "bid_qty", "ask_price", "ask_qty"]],
        )
    frame = pd.concat(parts).sort_index(kind="stable")
    frame = frame.groupby(level=0, sort=True).last()
    bid = pd.to_numeric(frame["bid_price"], errors="raise").astype(float)
    ask = pd.to_numeric(frame["ask_price"], errors="raise").astype(float)
    bid_qty = pd.to_numeric(frame["bid_qty"], errors="raise").astype(float)
    ask_qty = pd.to_numeric(frame["ask_qty"], errors="raise").astype(float)
    denominator = (bid_qty + ask_qty).replace(0.0, np.nan)
    mid = (bid + ask) / 2.0
    microprice = (ask * bid_qty + bid * ask_qty) / denominator
    frame["mid"] = mid
    frame["spread_bps"] = np.log(ask / bid) * 10_000.0
    frame["imbalance"] = (bid_qty - ask_qty) / denominator
    frame["microprice_premium_bps"] = np.log(microprice / mid) * 10_000.0
    return frame.replace([np.inf, -np.inf], np.nan)


def _read_liquidations(files: Iterable[Path]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for path in files:
        raw = pd.read_parquet(path)
        required = {"timestamp_ms", "price", "quantity", "side"}
        if not required.issubset(raw.columns):
            raise RuntimeError(f"liquidation schema drifted: {list(raw.columns)}")
        side = raw["side"].astype(str).str.strip().str.upper()
        if not side.isin({"BUY", "SELL"}).all():
            raise RuntimeError("unexpected liquidation side")
        price = pd.to_numeric(raw["price"], errors="raise").astype(float)
        quantity = pd.to_numeric(raw["quantity"], errors="raise").astype(float)
        parts.append(
            pd.DataFrame(
                {
                    "ts": pd.to_datetime(
                        raw["timestamp_ms"],
                        unit="ms",
                        utc=True,
                    ),
                    "direction": np.where(side.eq("BUY"), 1, -1),
                    "notional": price * quantity,
                },
            ),
        )
    frame = pd.concat(parts).sort_values("ts", kind="stable")
    return frame[frame["notional"].gt(0.0)].reset_index(drop=True)


def group_cascades(liquidations: pd.DataFrame) -> list[Cascade]:
    cascades: list[Cascade] = []
    current: Cascade | None = None
    for row in liquidations.itertuples(index=False):
        timestamp = pd.Timestamp(row.ts)
        direction = int(row.direction)
        notional = float(row.notional)
        if (
            current is None
            or current.direction != direction
            or (timestamp - current.end).total_seconds() > CASCADE_GAP_SECONDS
        ):
            current = Cascade(timestamp, timestamp, direction, notional, 1)
            cascades.append(current)
        else:
            current.end = timestamp
            current.notional += notional
            current.snapshots += 1
    return cascades


def load_day(day: str, cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[Cascade], DatasetEvidence]:
    local = Path(
        snapshot_download(
            repo_id=DATASET_REPO,
            repo_type="dataset",
            revision=DATASET_REVISION,
            cache_dir=cache,
            allow_patterns=[
                f"trades/{day}/*.parquet",
                f"liquidations/{day}/*.parquet",
                f"book_ticks/{day}/*.parquet",
            ],
        ),
    )
    trade_files = _parquet_files(local, "trades", day)
    liquidation_files = _parquet_files(local, "liquidations", day)
    book_files = _parquet_files(local, "book_ticks", day)
    trades = _read_trade_seconds(trade_files)
    book = _read_book_seconds(book_files)
    liquidations = _read_liquidations(liquidation_files)
    cascades = group_cascades(liquidations)
    first = min(trades.index.min(), book.index.min(), liquidations["ts"].min())
    last = max(trades.index.max(), book.index.max(), liquidations["ts"].max())
    evidence = DatasetEvidence(
        repo_id=DATASET_REPO,
        revision=DATASET_REVISION,
        day=day,
        trade_files=len(trade_files),
        liquidation_files=len(liquidation_files),
        book_files=len(book_files),
        first_observation=str(first),
        last_observation=str(last),
    )
    return trades, book, cascades, evidence


def _book_at(book: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series | None:
    position = int(book.index.searchsorted(timestamp.floor("s"), side="right")) - 1
    if position < 0:
        return None
    row = book.iloc[position]
    age = (timestamp.floor("s") - book.index[position]).total_seconds()
    if age > 1.0:
        return None
    return row


def screen(
    trades: pd.DataFrame,
    book: pd.DataFrame,
    cascades: list[Cascade],
) -> tuple[pd.DataFrame, dict[str, object]]:
    records: list[dict[str, object]] = []
    index = trades.index
    notionals = np.array([item.notional for item in cascades], dtype=float)
    for sequence, cascade in enumerate(cascades):
        history = notionals[:sequence]
        if len(history) < MIN_PRIOR_CASCADES:
            continue
        threshold = float(np.quantile(history, TAIL_QUANTILE))
        if cascade.notional < threshold:
            continue
        start_i = int(index.searchsorted(cascade.start.floor("s"), side="left"))
        end_i = int(index.searchsorted(cascade.end.floor("s"), side="right")) - 1
        if start_i <= 0 or end_i < start_i or end_i >= len(index):
            continue
        pre = float(trades.iloc[start_i - 1]["close"])
        parent = trades.iloc[start_i : end_i + 1]
        end_price = float(parent.iloc[-1]["close"])
        direction = cascade.direction
        side = -direction
        delivered_bps = direction * math.log(end_price / pre) * 10_000.0
        parent_flow = float(parent["signed"].sum() / parent["notional"].sum())
        extreme = (
            float(parent["high"].max())
            if direction > 0
            else float(parent["low"].min())
        )
        if delivered_bps <= 0.0 or direction * parent_flow <= 0.0:
            records.append(
                {
                    "cascade_start": cascade.start,
                    "classification": "FORCED_ORDER_NOT_PRICE_DELIVERED",
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "tail_threshold": threshold,
                    "delivered_bps": delivered_bps,
                },
            )
            continue

        parent_book = _book_at(book, cascade.end)
        confirmation_i: int | None = None
        confirmation_book: pd.Series | None = None
        for j in range(end_i + 1, min(len(index), end_i + 1 + MAX_CONFIRM_SECONDS)):
            later = trades.iloc[j]
            book_row = _book_at(book, index[j])
            if book_row is None:
                continue
            left_extreme = (
                float(later["close"]) < extreme
                if direction > 0
                else float(later["close"]) > extreme
            )
            opposite_trade = (
                side * float(later["flow"]) > 0.0
                and side * float(later["ret_bps"]) > 0.0
            )
            opposite_book = (
                side * float(book_row["imbalance"]) > 0.0
                and side * float(book_row["microprice_premium_bps"]) > 0.0
            )
            spread_not_worse = True
            if parent_book is not None:
                spread_not_worse = float(book_row["spread_bps"]) <= max(
                    float(parent_book["spread_bps"]),
                    float(book["spread_bps"].shift(1).rolling(60, min_periods=20).median().reindex([index[j]], method="ffill").iloc[0]),
                )
            if left_extreme and opposite_trade and opposite_book and spread_not_worse:
                confirmation_i = j
                confirmation_book = book_row
                break
        if confirmation_i is None or confirmation_book is None:
            records.append(
                {
                    "cascade_start": cascade.start,
                    "classification": "NO_LATER_TRADE_AND_BOOK_REVERSAL",
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "tail_threshold": threshold,
                    "delivered_bps": delivered_bps,
                },
            )
            continue

        entry = float(trades.iloc[confirmation_i]["close"])
        target = pre
        stop = extreme * math.exp(-side * STOP_BUFFER_BPS / 10_000.0)
        target_distance = side * math.log(target / entry) * 10_000.0
        stop_distance = side * math.log(entry / stop) * 10_000.0
        if target_distance <= ROUND_TRIP_COST_BPS or stop_distance <= 0.0:
            classification = "INVALID_EXECUTABLE_GEOMETRY"
        else:
            planned_loss_bps = stop_distance + ROUND_TRIP_COST_BPS
            target_net_bps = target_distance - ROUND_TRIP_COST_BPS
            planned_net_r = target_net_bps / planned_loss_bps
            classification = (
                "EXECUTABLE"
                if planned_net_r >= MIN_NET_R
                else "INSUFFICIENT_NET_R_AFTER_COSTS"
            )
        if classification != "EXECUTABLE":
            records.append(
                {
                    "cascade_start": cascade.start,
                    "confirmation_ts": index[confirmation_i],
                    "classification": classification,
                    "direction": direction,
                    "cascade_notional": cascade.notional,
                    "delivered_bps": delivered_bps,
                    "target_distance_bps": target_distance,
                    "stop_distance_bps": stop_distance,
                    "planned_net_r": (
                        planned_net_r
                        if target_distance > ROUND_TRIP_COST_BPS and stop_distance > 0.0
                        else None
                    ),
                },
            )
            continue

        outcome = "TIMEOUT"
        exit_i = min(len(index) - 1, confirmation_i + MAX_HOLD_SECONDS)
        exit_price = float(trades.iloc[exit_i]["close"])
        for k in range(
            confirmation_i + 1,
            min(len(index), confirmation_i + 1 + MAX_HOLD_SECONDS),
        ):
            second = trades.iloc[k]
            stop_hit = (
                float(second["low"]) <= stop
                if side > 0
                else float(second["high"]) >= stop
            )
            target_hit = (
                float(second["high"]) >= target
                if side > 0
                else float(second["low"]) <= target
            )
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
        records.append(
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
                "parent_flow": parent_flow,
                "confirmation_book_imbalance": float(confirmation_book["imbalance"]),
                "confirmation_microprice_premium_bps": float(
                    confirmation_book["microprice_premium_bps"],
                ),
                "target_distance_bps": target_distance,
                "stop_distance_bps": stop_distance,
                "planned_loss_bps": planned_loss_bps,
                "planned_net_r": planned_net_r,
                "net_bps": net_bps,
                "realized_r": net_bps / planned_loss_bps,
                "hold_seconds": exit_i - confirmation_i,
            },
        )

    result = pd.DataFrame(records)
    executable = result[
        result.get("classification", pd.Series(dtype=str)).eq("EXECUTABLE")
    ].copy()
    wins = int(executable.get("outcome", pd.Series(dtype=str)).eq("TARGET_FIRST").sum())
    losses = int(executable.get("outcome", pd.Series(dtype=str)).eq("STOP_FIRST").sum())
    timeouts = int(executable.get("outcome", pd.Series(dtype=str)).eq("TIMEOUT").sum())
    summary: dict[str, object] = {
        "schema": "candidate-11-liquidation-exhaustion-hf-screen-v1",
        "classification": "DIAGNOSTIC_SCREEN_ONLY",
        "success_claim": False,
        "trade_seconds": int(len(trades)),
        "book_seconds": int(len(book)),
        "raw_cascades": int(len(cascades)),
        "tail_parent_records": int(len(result)),
        "classification_counts": result.get(
            "classification",
            pd.Series(dtype=str),
        ).value_counts().to_dict(),
        "executable_episodes": int(len(executable)),
        "target_first": wins,
        "stop_first": losses,
        "timeouts": timeouts,
        "target_first_rate": wins / len(executable) if len(executable) else 0.0,
        "mean_realized_r": (
            float(executable["realized_r"].mean()) if len(executable) else 0.0
        ),
        "median_realized_r": (
            float(executable["realized_r"].median()) if len(executable) else 0.0
        ),
        "screen_pass": bool(
            len(executable) >= MIN_EXECUTABLE_EPISODES
            and wins / len(executable) >= 0.80
            and float(executable["realized_r"].mean()) > 0.0
        ),
        "parameters": {
            "cascade_gap_seconds": CASCADE_GAP_SECONDS,
            "minimum_prior_cascades": MIN_PRIOR_CASCADES,
            "tail_quantile": TAIL_QUANTILE,
            "max_confirmation_seconds": MAX_CONFIRM_SECONDS,
            "max_hold_seconds": MAX_HOLD_SECONDS,
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "minimum_net_r": MIN_NET_R,
            "minimum_executable_episodes": MIN_EXECUTABLE_EPISODES,
        },
    }
    return result, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-mode", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    trades, book, cascades, evidence = load_day(args.day, args.cache)
    episodes, summary = screen(trades, book, cascades)
    summary["day"] = args.day
    summary["validation_mode"] = args.validation_mode
    summary["dataset_evidence"] = asdict(evidence)
    episodes.to_csv(args.output / "episodes.csv", index=False)
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / "dataset_evidence.json").write_text(
        json.dumps(asdict(evidence), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
