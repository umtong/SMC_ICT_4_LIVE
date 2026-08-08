#!/usr/bin/env python3
"""Causal Binance/Bybit same-asset residual-transfer diagnostic.

This is an information-value screen, not an account backtest.  It downloads
official public trade archives, aggregates active flow to completed one-second
observations, admits one parent per cross-venue basis excursion, and checks
whether a separately confirmed Binance catch-up still has executable geometry
under the project's conservative 20 bp round-trip cost assumption.

No NAV, portfolio, fill or position state is constructed here.  A positive
screen must be promoted to NautilusTrader before any performance claim.
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
from typing import Iterable
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

import features as candidate05_features


ROUND_TRIP_COST_BPS = 20.0
MIN_NET_R = 1.15
BASELINE_SECONDS = 3_600
MIN_BASELINE_SECONDS = 1_800
MAX_CONFIRM_SECONDS = 3
MAX_HOLD_SECONDS = 120
EXCURSION_OPEN_BPS = 20.0
EXCURSION_RESET_BPS = 5.0


@dataclass(frozen=True, slots=True)
class RawFile:
    venue: str
    day: str
    url: str
    local_path: str
    size_bytes: int
    sha256: str


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
                headers={"User-Agent": "SMC-ICT-4-cross-venue-research"},
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


def _merge_grouped(parts: list[pd.DataFrame]) -> pd.DataFrame:
    if not parts:
        raise RuntimeError("trade archive produced no observations")
    frame = pd.concat(parts).sort_index(kind="stable")
    result = frame.groupby(level=0, sort=True).agg(
        close=("close", "last"),
        notional=("notional", "sum"),
        signed_notional=("signed_notional", "sum"),
        trades=("trades", "sum"),
    )
    result["flow"] = result["signed_notional"] / result["notional"].replace(0.0, np.nan)
    return result


def aggregate_binance(path: Path) -> pd.DataFrame:
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
                "signed_notional": signed,
            },
        ).sort_values("second", kind="stable")
        parts.append(
            work.groupby("second", sort=True).agg(
                close=("price", "last"),
                notional=("notional", "sum"),
                signed_notional=("signed_notional", "sum"),
                trades=("price", "size"),
            ),
        )
    return _merge_grouped(parts)


def _bybit_reader(path: Path, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    return pd.read_csv(
        path,
        compression="gzip",
        usecols=lambda column: column in {"timestamp", "side", "size", "price"},
        chunksize=chunksize,
    )


def _bybit_timestamp(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise")
    first = abs(float(numeric.iloc[0]))
    if first > 10**17:
        return pd.to_datetime(numeric, unit="ns", utc=True)
    if first > 10**14:
        return pd.to_datetime(numeric, unit="us", utc=True)
    if first > 10**11:
        return pd.to_datetime(numeric, unit="ms", utc=True)
    return pd.to_datetime(numeric, unit="s", utc=True)


def aggregate_bybit(path: Path) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for chunk in _bybit_reader(path):
        required = {"timestamp", "side", "size", "price"}
        if not required.issubset(chunk.columns):
            raise RuntimeError(f"unexpected Bybit schema: {list(chunk.columns)}")
        timestamp = _bybit_timestamp(chunk["timestamp"])
        price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
        size = pd.to_numeric(chunk["size"], errors="raise").astype(float)
        side = chunk["side"].astype(str).str.strip().str.lower()
        if not side.isin({"buy", "sell"}).all():
            raise RuntimeError("unexpected Bybit active-side value")
        notional = price * size
        signed = np.where(side.eq("buy").to_numpy(), notional.to_numpy(), -notional.to_numpy())
        work = pd.DataFrame(
            {
                "second": timestamp.dt.floor("s"),
                "price": price.to_numpy(),
                "notional": notional.to_numpy(),
                "signed_notional": signed,
            },
        ).sort_values("second", kind="stable")
        parts.append(
            work.groupby("second", sort=True).agg(
                close=("price", "last"),
                notional=("notional", "sum"),
                signed_notional=("signed_notional", "sum"),
                trades=("price", "size"),
            ),
        )
    return _merge_grouped(parts)


def _archives(symbol: str, day: date, cache: Path) -> tuple[Path, Path, list[RawFile]]:
    stamp = day.isoformat()
    binance_url = (
        "https://data.binance.vision/data/futures/um/daily/aggTrades/"
        f"{symbol}/{symbol}-aggTrades-{stamp}.zip"
    )
    bybit_url = f"https://public.bybit.com/trading/{symbol}/{symbol}{stamp}.csv.gz"
    binance = _download(
        binance_url,
        cache / "binance" / f"{symbol}-aggTrades-{stamp}.zip",
    )
    bybit = _download(
        bybit_url,
        cache / "bybit" / f"{symbol}{stamp}.csv.gz",
    )
    evidence = [
        RawFile("BINANCE", stamp, binance_url, str(binance), binance.stat().st_size, _sha256(binance)),
        RawFile("BYBIT", stamp, bybit_url, str(bybit), bybit.stat().st_size, _sha256(bybit)),
    ]
    return binance, bybit, evidence


def load_range(symbol: str, start: date, end: date, cache: Path) -> tuple[pd.DataFrame, list[RawFile]]:
    frames: list[pd.DataFrame] = []
    evidence: list[RawFile] = []
    day = start
    while day <= end:
        binance_path, bybit_path, raw = _archives(symbol, day, cache)
        binance = aggregate_binance(binance_path).add_prefix("binance_")
        bybit = aggregate_bybit(bybit_path).add_prefix("bybit_")
        joined = binance.join(bybit, how="inner")
        joined["day"] = day.isoformat()
        frames.append(joined)
        evidence.extend(raw)
        day += timedelta(days=1)
    frame = pd.concat(frames).sort_index()
    if frame.index.duplicated().any() or not frame.index.is_monotonic_increasing:
        raise RuntimeError("cross-venue seconds are duplicated or non-monotonic")
    return frame, evidence


def build_state(frame: pd.DataFrame) -> pd.DataFrame:
    state = frame.copy()
    state["binance_ret_bps"] = np.log(state["binance_close"] / state["binance_close"].shift(1)) * 10_000.0
    state["bybit_ret_bps"] = np.log(state["bybit_close"] / state["bybit_close"].shift(1)) * 10_000.0
    state["basis_bps"] = np.log(state["bybit_close"] / state["binance_close"]) * 10_000.0
    state["basis_baseline_bps"] = (
        state["basis_bps"].shift(1).rolling(BASELINE_SECONDS, min_periods=MIN_BASELINE_SECONDS).median()
    )
    state["gap_bps"] = state["basis_bps"] - state["basis_baseline_bps"]
    state["gap_tail_bps"] = (
        state["gap_bps"].abs().shift(1).rolling(BASELINE_SECONDS, min_periods=MIN_BASELINE_SECONDS).quantile(0.99)
    )
    return state.replace([np.inf, -np.inf], np.nan)


def _log_distance_bps(a: float, b: float) -> float:
    return abs(math.log(a / b)) * 10_000.0


def screen(state: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    records: list[dict[str, object]] = []
    values = state.reset_index(names="ts")
    active_excursion = False
    excursion_direction = 0

    for i in range(1, len(values) - MAX_HOLD_SECONDS - MAX_CONFIRM_SECONDS - 1):
        row = values.iloc[i]
        gap = float(row["gap_bps"]) if math.isfinite(float(row["gap_bps"])) else math.nan
        if not math.isfinite(gap):
            continue
        direction = 1 if gap > 0.0 else -1
        if active_excursion:
            if abs(gap) <= EXCURSION_RESET_BPS or direction != excursion_direction:
                active_excursion = False
                excursion_direction = 0
            else:
                continue

        tail = float(row["gap_tail_bps"])
        bybit_ret = float(row["bybit_ret_bps"])
        binance_ret = float(row["binance_ret_bps"])
        bybit_flow = float(row["bybit_flow"])
        parent = (
            math.isfinite(tail)
            and abs(gap) > EXCURSION_OPEN_BPS
            and abs(gap) >= tail
            and direction * bybit_ret > 0.0
            and abs(bybit_ret) > abs(binance_ret)
            and direction * bybit_flow > 0.0
        )
        if not parent:
            continue

        active_excursion = True
        excursion_direction = direction
        pre_price = float(values.iloc[i - 1]["binance_close"])
        confirmation_index: int | None = None
        for j in range(i + 1, i + 1 + MAX_CONFIRM_SECONDS):
            later = values.iloc[j]
            later_gap = float(later["gap_bps"])
            if not math.isfinite(later_gap) or (1 if later_gap > 0.0 else -1) != direction:
                break
            if (
                abs(later_gap) > EXCURSION_OPEN_BPS
                and direction * float(later["binance_flow"]) > 0.0
                and direction * float(later["binance_ret_bps"]) > 0.0
                and direction * float(later["bybit_ret_bps"]) >= 0.0
            ):
                confirmation_index = j
                break
        if confirmation_index is None:
            records.append(
                {
                    "parent_ts": row["ts"],
                    "direction": direction,
                    "classification": "NO_LATER_BINANCE_INITIATIVE",
                    "event_gap_bps": gap,
                },
            )
            continue

        j = confirmation_index
        confirmation = values.iloc[j]
        entry = float(confirmation["binance_close"])
        baseline = float(confirmation["basis_baseline_bps"])
        bybit_price = float(confirmation["bybit_close"])
        fair = bybit_price / math.exp(baseline / 10_000.0)
        target_distance = direction * math.log(fair / entry) * 10_000.0
        segment = values.iloc[i : j + 1]["binance_close"].astype(float)
        stop = min(pre_price, float(segment.min())) if direction > 0 else max(pre_price, float(segment.max()))
        stop_distance = direction * math.log(entry / stop) * 10_000.0
        if not (
            math.isfinite(target_distance)
            and math.isfinite(stop_distance)
            and target_distance > ROUND_TRIP_COST_BPS
            and stop_distance > 0.0
        ):
            records.append(
                {
                    "parent_ts": row["ts"],
                    "confirmation_ts": confirmation["ts"],
                    "direction": direction,
                    "classification": "INVALID_EXECUTABLE_GEOMETRY",
                    "event_gap_bps": gap,
                    "remaining_gap_bps": target_distance,
                    "raw_stop_bps": stop_distance,
                },
            )
            continue

        planned_loss_bps = stop_distance + ROUND_TRIP_COST_BPS
        target_net_bps = target_distance - ROUND_TRIP_COST_BPS
        planned_net_r = target_net_bps / planned_loss_bps
        if planned_net_r < MIN_NET_R:
            records.append(
                {
                    "parent_ts": row["ts"],
                    "confirmation_ts": confirmation["ts"],
                    "direction": direction,
                    "classification": "INSUFFICIENT_NET_R_AFTER_COSTS",
                    "event_gap_bps": gap,
                    "remaining_gap_bps": target_distance,
                    "raw_stop_bps": stop_distance,
                    "planned_net_r": planned_net_r,
                },
            )
            continue

        outcome = "TIMEOUT"
        outcome_index = j + MAX_HOLD_SECONDS
        exit_price = float(values.iloc[outcome_index]["binance_close"])
        for k in range(j + 1, j + 1 + MAX_HOLD_SECONDS):
            price = float(values.iloc[k]["binance_close"])
            stop_hit = price <= stop if direction > 0 else price >= stop
            target_hit = price >= fair if direction > 0 else price <= fair
            if stop_hit:
                outcome = "STOP_FIRST"
                outcome_index = k
                exit_price = stop
                break
            if target_hit:
                outcome = "TARGET_FIRST"
                outcome_index = k
                exit_price = fair
                break
        gross_bps = direction * math.log(exit_price / entry) * 10_000.0
        net_bps = gross_bps - ROUND_TRIP_COST_BPS
        realized_r = net_bps / planned_loss_bps
        records.append(
            {
                "parent_ts": row["ts"],
                "confirmation_ts": confirmation["ts"],
                "exit_ts": values.iloc[outcome_index]["ts"],
                "direction": direction,
                "classification": "EXECUTABLE",
                "outcome": outcome,
                "event_gap_bps": gap,
                "remaining_gap_bps": target_distance,
                "raw_stop_bps": stop_distance,
                "planned_loss_bps": planned_loss_bps,
                "planned_net_r": planned_net_r,
                "net_bps": net_bps,
                "realized_r": realized_r,
                "hold_seconds": outcome_index - j,
                "bybit_parent_return_bps": bybit_ret,
                "binance_parent_return_bps": binance_ret,
                "bybit_parent_flow": bybit_flow,
                "binance_confirmation_flow": float(confirmation["binance_flow"]),
            },
        )

    result = pd.DataFrame(records)
    executable = result[result.get("classification", pd.Series(dtype=str)).eq("EXECUTABLE")].copy()
    wins = int(executable.get("outcome", pd.Series(dtype=str)).eq("TARGET_FIRST").sum())
    losses = int(executable.get("outcome", pd.Series(dtype=str)).eq("STOP_FIRST").sum())
    timeouts = int(executable.get("outcome", pd.Series(dtype=str)).eq("TIMEOUT").sum())
    summary: dict[str, object] = {
        "schema": "candidate-11-cross-venue-residual-screen-v1",
        "classification": "DIAGNOSTIC_SCREEN_ONLY",
        "success_claim": False,
        "parameters": {
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "minimum_net_r": MIN_NET_R,
            "basis_baseline_seconds": BASELINE_SECONDS,
            "basis_tail_quantile": 0.99,
            "maximum_confirmation_seconds": MAX_CONFIRM_SECONDS,
            "maximum_hold_seconds": MAX_HOLD_SECONDS,
            "excursion_open_bps": EXCURSION_OPEN_BPS,
            "excursion_reset_bps": EXCURSION_RESET_BPS,
        },
        "seconds": int(len(state)),
        "calendar_days": int(state.index.normalize().nunique()),
        "parent_excursions": int(len(result)),
        "classification_counts": result.get("classification", pd.Series(dtype=str)).value_counts().to_dict(),
        "executable_episodes": int(len(executable)),
        "target_first": wins,
        "stop_first": losses,
        "timeouts": timeouts,
        "target_first_rate": (wins / len(executable)) if len(executable) else 0.0,
        "median_planned_net_r": float(executable["planned_net_r"].median()) if len(executable) else 0.0,
        "mean_realized_r": float(executable["realized_r"].mean()) if len(executable) else 0.0,
        "median_realized_r": float(executable["realized_r"].median()) if len(executable) else 0.0,
        "sum_net_bps": float(executable["net_bps"].sum()) if len(executable) else 0.0,
        "minimum_frequency_pass": bool(len(executable) >= state.index.normalize().nunique()),
        "screen_pass": bool(
            len(executable) >= state.index.normalize().nunique()
            and len(executable) > 0
            and wins / len(executable) >= 0.80
            and float(executable["realized_r"].mean()) > 0.0
        ),
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
    frame, raw = load_range(args.symbol, start, end, args.cache)
    state = build_state(frame)
    episodes, summary = screen(state)
    summary["symbol"] = args.symbol
    summary["start"] = start.isoformat()
    summary["end"] = end.isoformat()
    summary["raw_files"] = [asdict(item) for item in raw]
    state.to_parquet(args.output / "cross_venue_seconds.parquet", compression="zstd")
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
