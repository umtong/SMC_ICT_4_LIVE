#!/usr/bin/env python3
"""Causal mechanism screen for second-scale cross-asset fair-value gaps.

This is not a backtest engine and it never produces an account return claim.
It measures whether a marketable opportunity remains after an observed peer
price-discovery burst:

1. Build causally completed one-second Binance USD-M bars.
2. Aggregate non-overlapping five-second observations.
3. Estimate each follower's beta to the median of the other three markets from
   the preceding one hour only.
4. Require a peer-majority displacement with aligned taker flow.
5. Freeze the follower fair value at the event close.
6. Use the next one-second open plus adverse ticks as the earliest executable
   entry and the local two-bar micro-pivot as invalidation.
7. Require the frozen target to remain live and costed structural R >= 1.25.
8. Report whether target or stop is touched first within sixty seconds, using
   stop-first ordering when both occur in the same second.

Only if this causal diagnostic is strong across the three precommitted opened
weeks will the unchanged detector be wired into NautilusTrader for fresh
pre-data holdouts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from hashlib import sha256
from io import BytesIO
import json
from math import exp, log, sqrt
from pathlib import Path
import time
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
META = {
    "BTCUSDT": {"tick": Decimal("0.1")},
    "ETHUSDT": {"tick": Decimal("0.01")},
    "SOLUSDT": {"tick": Decimal("0.001")},
    "XRPUSDT": {"tick": Decimal("0.0001")},
}
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trade_count", "taker_buy_volume",
    "taker_buy_quote", "ignore",
)
BAR_SECONDS = 5
ROLLING_BARS = 720  # one hour of completed 5-second bars
MINIMUM_HISTORY = 360
PEER_FACTOR_Z = 3.0
PEER_RETURN_Z = 1.0
MINIMUM_BETA = 0.15
MAXIMUM_BETA = 2.50
MINIMUM_CORRELATION = 0.25
MINIMUM_NET_R = 1.25
ENTRY_TAKER_RATE = 0.0008
TARGET_MAKER_RATE = 0.0004
STOP_TAKER_RATE = 0.0008
ADVERSE_TICKS = 2
HORIZON_SECONDS = 60


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def download(url: str, destination: Path, retries: int = 5) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return
        except Exception:
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-cross-asset-gap"})
    error: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=180) as response:  # noqa: S310 fixed HTTPS host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"small archive response: {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt ZIP member: {bad}")
            temporary.replace(destination)
            return
        except Exception as exc:
            error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"failed to download {url}: {error}")


def timestamp_unit(value: int) -> str:
    magnitude = abs(int(value))
    if 1_000_000_000_000 <= magnitude < 10_000_000_000_000:
        return "ms"
    if 1_000_000_000_000_000 <= magnitude < 10_000_000_000_000_000:
        return "us"
    if magnitude >= 1_000_000_000_000_000_000:
        return "ns"
    raise ValueError(f"unsupported timestamp magnitude: {value}")


def parse_kline_archive(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members for {path.name}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload), header=None, names=KLINE_COLUMNS)
    if not pd.api.types.is_numeric_dtype(frame["open_time"]):
        frame = pd.read_csv(BytesIO(payload))
        if not set(KLINE_COLUMNS).issubset(frame.columns):
            frame.columns = KLINE_COLUMNS[: len(frame.columns)]
    frame = frame.loc[:, KLINE_COLUMNS].copy()
    for name in (
        "open_time", "open", "high", "low", "close", "volume",
        "quote_volume", "trade_count", "taker_buy_quote",
    ):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna(subset=["open_time", "open", "high", "low", "close"])
    if frame.empty:
        raise RuntimeError(f"empty one-second kline archive: {path.name}")
    unit = timestamp_unit(int(frame["open_time"].iloc[0]))
    # A bar is visible only after its one-second interval has completed.
    frame.index = pd.to_datetime(frame["open_time"].astype("int64"), unit=unit, utc=True) + pd.Timedelta(seconds=1)
    frame = frame[[
        "open", "high", "low", "close", "volume", "quote_volume",
        "trade_count", "taker_buy_quote",
    ]].sort_index(kind="stable")
    return frame[~frame.index.duplicated(keep="last")]


def archive_record(symbol: str, day: date, data_dir: Path) -> tuple[str, date, Path, str]:
    filename = f"{symbol}-1s-{day.isoformat()}.zip"
    url = f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/1s/{filename}"
    return symbol, day, data_dir / symbol / filename, url


def load_symbol(
    symbol: str,
    start: date,
    end_inclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    records: list[tuple[str, date, Path, str]] = []
    cursor = start
    while cursor <= end_inclusive:
        records.append(archive_record(symbol, cursor, data_dir))
        cursor += timedelta(days=1)
    with ThreadPoolExecutor(max_workers=min(8, len(records))) as executor:
        futures = {executor.submit(download, url, path): (day, path, url) for _, day, path, url in records}
        for future in as_completed(futures):
            future.result()

    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for _, day, path, url in records:
        frame = parse_kline_archive(path)
        frames.append(frame)
        manifest.append({
            "symbol": symbol,
            "date": day.isoformat(),
            "url": url,
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "rows": len(frame),
        })
    combined = pd.concat(frames).sort_index(kind="stable")
    combined = combined[~combined.index.duplicated(keep="last")]
    start_index = pd.Timestamp(start, tz="UTC") + pd.Timedelta(seconds=1)
    end_index = pd.Timestamp(end_inclusive + timedelta(days=1), tz="UTC")
    index = pd.date_range(start_index, end_index, freq="1s", tz="UTC")
    combined = combined.reindex(index)
    combined["close"] = combined["close"].ffill().bfill()
    for name in ("open", "high", "low"):
        combined[name] = combined[name].fillna(combined["close"])
    for name in ("volume", "quote_volume", "trade_count", "taker_buy_quote"):
        combined[name] = combined[name].fillna(0.0)
    combined["flow_imbalance"] = np.where(
        combined["quote_volume"] > 0.0,
        (2.0 * combined["taker_buy_quote"] - combined["quote_volume"]) / combined["quote_volume"],
        0.0,
    )
    return combined, manifest


def five_second(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample(
        f"{BAR_SECONDS}s",
        closed="right",
        label="right",
        origin="epoch",
    )
    result = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        trade_count=("trade_count", "sum"),
        taker_buy_quote=("taker_buy_quote", "sum"),
    )
    result["flow_imbalance"] = np.where(
        result["quote_volume"] > 0.0,
        (2.0 * result["taker_buy_quote"] - result["quote_volume"]) / result["quote_volume"],
        0.0,
    )
    result["return"] = np.log(result["close"]).diff()
    rms = np.sqrt(result["return"].pow(2).rolling(ROLLING_BARS, min_periods=MINIMUM_HISTORY).mean()).shift(1)
    result["return_z"] = result["return"] / rms.replace(0.0, np.nan)
    return result


def round_price(value: float, tick: Decimal, direction: str) -> float:
    scaled = Decimal(str(value)) / tick
    rounding = ROUND_FLOOR if direction == "LONG" else ROUND_CEILING
    return float(scaled.to_integral_value(rounding=rounding) * tick)


def costed_geometry(
    *,
    direction: str,
    entry_open: float,
    target_raw: float,
    stop_trigger: float,
    tick: Decimal,
) -> dict[str, float] | None:
    tick_float = float(tick)
    if direction == "LONG":
        entry = entry_open + ADVERSE_TICKS * tick_float
        target = round_price(target_raw, tick, direction)
        stop = stop_trigger - ADVERSE_TICKS * tick_float
        stop_execution = stop - ADVERSE_TICKS * tick_float
        gross_gain = target - entry
        gross_loss = entry - stop_execution
    else:
        entry = entry_open - ADVERSE_TICKS * tick_float
        target = round_price(target_raw, tick, direction)
        stop = stop_trigger + ADVERSE_TICKS * tick_float
        stop_execution = stop + ADVERSE_TICKS * tick_float
        gross_gain = entry - target
        gross_loss = stop_execution - entry
    if min(entry, target, stop, stop_execution) <= 0.0 or gross_gain <= 0.0 or gross_loss <= 0.0:
        return None
    gain = gross_gain - ENTRY_TAKER_RATE * entry - TARGET_MAKER_RATE * target
    loss = gross_loss + ENTRY_TAKER_RATE * entry + STOP_TAKER_RATE * stop_execution
    if gain <= 0.0 or loss <= 0.0:
        return None
    return {
        "entry": entry,
        "target": target,
        "stop_trigger": stop,
        "stop_execution": stop_execution,
        "gain_per_unit": gain,
        "loss_per_unit": loss,
        "net_r": gain / loss,
    }


def path_outcome(
    *,
    frame: pd.DataFrame,
    entry_ts: pd.Timestamp,
    direction: str,
    geometry: dict[str, float],
) -> dict[str, Any]:
    end_ts = entry_ts + pd.Timedelta(seconds=HORIZON_SECONDS - 1)
    path = frame.loc[entry_ts:end_ts]
    if path.empty:
        return {"outcome": "NO_FORWARD_PATH", "realized_r": None}
    entry = geometry["entry"]
    target = geometry["target"]
    stop = geometry["stop_trigger"]
    for ts, row in path.iterrows():
        if direction == "LONG":
            target_hit = float(row["high"]) >= target
            stop_hit = float(row["low"]) <= stop
        else:
            target_hit = float(row["low"]) <= target
            stop_hit = float(row["high"]) >= stop
        if stop_hit:
            return {
                "outcome": "STOP_FIRST" if not target_hit else "BOTH_STOP_FIRST",
                "exit_ts": ts.isoformat(),
                "seconds_to_exit": int((ts - entry_ts).total_seconds()),
                "realized_r": -1.0,
            }
        if target_hit:
            return {
                "outcome": "TARGET_FIRST",
                "exit_ts": ts.isoformat(),
                "seconds_to_exit": int((ts - entry_ts).total_seconds()),
                "realized_r": geometry["net_r"],
            }
    close = float(path["close"].iloc[-1])
    tick_float = float(META[str(frame.attrs["symbol"])]["tick"])
    if direction == "LONG":
        exit_price = close - ADVERSE_TICKS * tick_float
        pnl = exit_price - entry - ENTRY_TAKER_RATE * entry - STOP_TAKER_RATE * exit_price
    else:
        exit_price = close + ADVERSE_TICKS * tick_float
        pnl = entry - exit_price - ENTRY_TAKER_RATE * entry - STOP_TAKER_RATE * exit_price
    return {
        "outcome": "TIMEOUT",
        "exit_ts": path.index[-1].isoformat(),
        "seconds_to_exit": HORIZON_SECONDS - 1,
        "exit_price": exit_price,
        "realized_r": pnl / geometry["loss_per_unit"],
    }


def diagnose(
    *,
    protocol_path: Path,
    interval: str,
    output_dir: Path,
) -> dict[str, Any]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    selected = protocol["weeks"][interval]
    evaluation_start = date.fromisoformat(selected["start"])
    evaluation_end = date.fromisoformat(selected["end_exclusive"])
    warmup_start = evaluation_start - timedelta(days=int(protocol["warmup_days"]))
    output_dir.mkdir(parents=True, exist_ok=True)

    one_second: dict[str, pd.DataFrame] = {}
    five: dict[str, pd.DataFrame] = {}
    manifest: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        frame, records = load_symbol(
            symbol,
            warmup_start,
            evaluation_end - timedelta(days=1),
            output_dir / "data",
        )
        frame.attrs["symbol"] = symbol
        one_second[symbol] = frame
        five[symbol] = five_second(frame)
        manifest.extend(records)
    common_index = five[SYMBOLS[0]].index
    for symbol in SYMBOLS[1:]:
        common_index = common_index.intersection(five[symbol].index)
    five = {symbol: frame.loc[common_index].copy() for symbol, frame in five.items()}

    returns = pd.DataFrame({symbol: five[symbol]["return"] for symbol in SYMBOLS}, index=common_index)
    zscores = pd.DataFrame({symbol: five[symbol]["return_z"] for symbol in SYMBOLS}, index=common_index)
    flows = pd.DataFrame({symbol: five[symbol]["flow_imbalance"] for symbol in SYMBOLS}, index=common_index)

    evaluation_start_ts = pd.Timestamp(evaluation_start, tz="UTC")
    evaluation_end_ts = pd.Timestamp(evaluation_end, tz="UTC")
    events: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()

    for follower in SYMBOLS:
        peers = [symbol for symbol in SYMBOLS if symbol != follower]
        peer_factor = returns[peers].median(axis=1)
        factor_rms = np.sqrt(peer_factor.pow(2).rolling(ROLLING_BARS, min_periods=MINIMUM_HISTORY).mean()).shift(1)
        factor_z = peer_factor / factor_rms.replace(0.0, np.nan)
        covariance = returns[follower].rolling(ROLLING_BARS, min_periods=MINIMUM_HISTORY).cov(peer_factor).shift(1)
        variance = peer_factor.rolling(ROLLING_BARS, min_periods=MINIMUM_HISTORY).var(ddof=0).shift(1)
        beta = covariance / variance.replace(0.0, np.nan)
        correlation = returns[follower].rolling(ROLLING_BARS, min_periods=MINIMUM_HISTORY).corr(peer_factor).shift(1)

        candidate_times = common_index[
            (common_index >= evaluation_start_ts)
            & (common_index < evaluation_end_ts)
            & (factor_z.abs() >= PEER_FACTOR_Z)
        ]
        for ts in candidate_times:
            factor_return = float(peer_factor.loc[ts])
            if not np.isfinite(factor_return) or factor_return == 0.0:
                skips["INVALID_PEER_FACTOR"] += 1
                continue
            sign = 1.0 if factor_return > 0.0 else -1.0
            aligned_returns = sum(
                sign * float(returns.at[ts, peer]) > 0.0
                and abs(float(zscores.at[ts, peer])) >= PEER_RETURN_Z
                for peer in peers
                if np.isfinite(zscores.at[ts, peer])
            )
            if aligned_returns < 2:
                skips["PEER_RETURN_QUORUM"] += 1
                continue
            aligned_flows = sum(sign * float(flows.at[ts, peer]) > 0.0 for peer in peers)
            if aligned_flows < 2:
                skips["PEER_TAKER_FLOW_QUORUM"] += 1
                continue
            beta_value = float(beta.loc[ts])
            correlation_value = float(correlation.loc[ts])
            if not np.isfinite(beta_value) or not (MINIMUM_BETA <= beta_value <= MAXIMUM_BETA):
                skips["BETA_OUT_OF_RANGE"] += 1
                continue
            if not np.isfinite(correlation_value) or correlation_value < MINIMUM_CORRELATION:
                skips["WEAK_CAUSAL_CORRELATION"] += 1
                continue

            observed_return = float(returns.at[ts, follower])
            predicted_return = beta_value * factor_return
            gap_return = predicted_return - observed_return
            if sign * gap_return <= 0.0:
                skips["FOLLOWER_NOT_LAGGING"] += 1
                continue
            follower_z = float(zscores.at[ts, follower])
            if np.isfinite(follower_z) and abs(follower_z) >= abs(float(factor_z.loc[ts])):
                skips["FOLLOWER_ALREADY_DISCOVERING"] += 1
                continue

            prior_ts = ts - pd.Timedelta(seconds=BAR_SECONDS)
            entry_ts = ts + pd.Timedelta(seconds=1)
            if prior_ts not in five[follower].index or entry_ts not in one_second[follower].index:
                skips["MISSING_CAUSAL_PRICE"] += 1
                continue
            start_price = float(five[follower].at[prior_ts, "close"])
            target_raw = start_price * exp(predicted_return)
            entry_open = float(one_second[follower].at[entry_ts, "open"])
            direction = "LONG" if sign > 0.0 else "SHORT"
            if direction == "LONG" and entry_open >= target_raw:
                skips["TARGET_CONSUMED_AT_ENTRY"] += 1
                continue
            if direction == "SHORT" and entry_open <= target_raw:
                skips["TARGET_CONSUMED_AT_ENTRY"] += 1
                continue
            current_low = float(five[follower].at[ts, "low"])
            current_high = float(five[follower].at[ts, "high"])
            prior_low = float(five[follower].at[prior_ts, "low"])
            prior_high = float(five[follower].at[prior_ts, "high"])
            stop_trigger = min(current_low, prior_low) if direction == "LONG" else max(current_high, prior_high)
            geometry = costed_geometry(
                direction=direction,
                entry_open=entry_open,
                target_raw=target_raw,
                stop_trigger=stop_trigger,
                tick=META[follower]["tick"],
            )
            if geometry is None:
                skips["INVALID_COSTED_GEOMETRY"] += 1
                continue
            if geometry["net_r"] < MINIMUM_NET_R:
                skips["INSUFFICIENT_COSTED_R"] += 1
                continue

            outcome = path_outcome(
                frame=one_second[follower],
                entry_ts=entry_ts,
                direction=direction,
                geometry=geometry,
            )
            events.append({
                "scenario_id": f"CAG-{follower}-{int(ts.value)}",
                "follower": follower,
                "peers": peers,
                "event_ts": ts.isoformat(),
                "entry_ts": entry_ts.isoformat(),
                "direction": direction,
                "peer_factor_return": factor_return,
                "peer_factor_z": float(factor_z.loc[ts]),
                "aligned_peer_returns": aligned_returns,
                "aligned_peer_flows": aligned_flows,
                "beta": beta_value,
                "correlation": correlation_value,
                "observed_follower_return": observed_return,
                "predicted_follower_return": predicted_return,
                "fair_value_gap_return": gap_return,
                "follower_return_z": follower_z,
                **geometry,
                **outcome,
            })

    events.sort(key=lambda value: (value["entry_ts"], value["follower"]))
    outcome_counts = Counter(event["outcome"] for event in events)
    realized = [float(event["realized_r"]) for event in events if event.get("realized_r") is not None]
    targets = [event for event in events if event["outcome"] == "TARGET_FIRST"]
    losses = [event for event in events if event["outcome"] in {"STOP_FIRST", "BOTH_STOP_FIRST"}]
    summary = {
        "schema": "candidate-11-cross-asset-gap-diagnostic-v1",
        "candidate": "candidate-11-second-scale-peer-implied-fair-value-gap",
        "interval": interval,
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "mechanism_only": True,
        "account_or_success_claim": False,
        "event_count": len(events),
        "target_first": len(targets),
        "stop_first": len(losses),
        "timeout": outcome_counts.get("TIMEOUT", 0),
        "target_first_rate": len(targets) / len(events) if events else 0.0,
        "mean_realized_r_diagnostic": float(np.mean(realized)) if realized else None,
        "median_realized_r_diagnostic": float(np.median(realized)) if realized else None,
        "median_seconds_to_target": float(np.median([event["seconds_to_exit"] for event in targets])) if targets else None,
        "median_costed_net_r": float(np.median([event["net_r"] for event in events])) if events else None,
        "follower_counts": dict(Counter(event["follower"] for event in events)),
        "direction_counts": dict(Counter(event["direction"] for event in events)),
        "outcome_counts": dict(outcome_counts),
        "skip_reasons": dict(skips),
        "thresholds": {
            "bar_seconds": BAR_SECONDS,
            "rolling_bars": ROLLING_BARS,
            "minimum_history": MINIMUM_HISTORY,
            "peer_factor_z": PEER_FACTOR_Z,
            "peer_return_z": PEER_RETURN_Z,
            "minimum_beta": MINIMUM_BETA,
            "maximum_beta": MAXIMUM_BETA,
            "minimum_correlation": MINIMUM_CORRELATION,
            "minimum_net_r": MINIMUM_NET_R,
            "entry_taker_rate": ENTRY_TAKER_RATE,
            "target_maker_rate": TARGET_MAKER_RATE,
            "stop_taker_rate": STOP_TAKER_RATE,
            "adverse_ticks": ADVERSE_TICKS,
            "horizon_seconds": HORIZON_SECONDS,
        },
    }
    write_json(output_dir / "summary.json", summary)
    write_json(output_dir / "events.json", {"events": events})
    write_json(output_dir / "data_manifest.json", {
        "schema": "candidate-11-cross-asset-gap-data-v1",
        "dataset": "Binance USD-M daily one-second klines",
        "bar_visibility": "open_time plus one second",
        "symbols": list(SYMBOLS),
        "warmup_start": warmup_start.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end_exclusive": evaluation_end.isoformat(),
        "files": manifest,
    })
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--interval", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    diagnose(
        protocol_path=args.protocol.resolve(),
        interval=args.interval,
        output_dir=args.output.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
