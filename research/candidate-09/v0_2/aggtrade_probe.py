"""Probe Binance USD-M futures aggTrades for candidate-09 v0.2.

This is a data-contract diagnostic, not a backtest. It downloads one official
Binance Vision daily archive and checksum, validates the event stream, aggregates
causal one-second observations, and reconciles them against the corresponding
one-minute kline archive.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

BASE = "https://data.binance.vision/data/futures/um/daily"


def download(url: str, attempts: int = 4) -> bytes:
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-09/0.2"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=90) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"download failed: {url}: {last_error}")


def verify_checksum(payload: bytes, checksum_payload: bytes, filename: str) -> str:
    text = checksum_payload.decode("utf-8").strip()
    expected = text.split()[0].lower()
    actual = sha256(payload).hexdigest()
    if len(expected) != 64 or expected != actual:
        raise ValueError(f"checksum mismatch for {filename}: expected={expected}, actual={actual}")
    return actual


def csv_text(payload: bytes, source: str) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"{source}: expected one CSV, got {names}")
        return archive.read(names[0]).decode("utf-8")


def ts_ns(raw: str) -> int:
    value = int(raw)
    size = abs(value)
    if size >= 100_000_000_000_000_000:
        return value
    if size >= 100_000_000_000_000:
        return value * 1_000
    if size >= 100_000_000_000:
        return value * 1_000_000
    if size >= 1_000_000_000:
        return value * 1_000_000_000
    raise ValueError(f"unrecognized timestamp: {raw}")


def quantile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(slots=True)
class SecondBar:
    second_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    buy_volume: float
    sell_volume: float
    signed_volume: float
    aggtrade_count: int
    underlying_trade_count: int
    first_agg_id: int
    last_agg_id: int
    first_trade_id: int
    last_trade_id: int

    @classmethod
    def start(
        cls,
        *,
        second_ns: int,
        price: float,
        qty: float,
        is_buyer_maker: bool,
        agg_id: int,
        first_trade_id: int,
        last_trade_id: int,
    ) -> "SecondBar":
        buy = 0.0 if is_buyer_maker else qty
        sell = qty if is_buyer_maker else 0.0
        return cls(
            second_ns=second_ns,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=qty,
            quote_volume=price * qty,
            buy_volume=buy,
            sell_volume=sell,
            signed_volume=buy - sell,
            aggtrade_count=1,
            underlying_trade_count=last_trade_id - first_trade_id + 1,
            first_agg_id=agg_id,
            last_agg_id=agg_id,
            first_trade_id=first_trade_id,
            last_trade_id=last_trade_id,
        )

    def add(
        self,
        *,
        price: float,
        qty: float,
        is_buyer_maker: bool,
        agg_id: int,
        first_trade_id: int,
        last_trade_id: int,
    ) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += qty
        self.quote_volume += price * qty
        if is_buyer_maker:
            self.sell_volume += qty
            self.signed_volume -= qty
        else:
            self.buy_volume += qty
            self.signed_volume += qty
        self.aggtrade_count += 1
        self.underlying_trade_count += last_trade_id - first_trade_id + 1
        self.last_agg_id = agg_id
        self.last_trade_id = last_trade_id


def parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"invalid boolean: {raw}")


def iter_aggtrades(text: str) -> Iterable[tuple[int, float, float, int, int, int, bool]]:
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        if not row[0].lstrip("-").isdigit():
            continue
        if len(row) < 7:
            raise ValueError(f"aggTrade row has {len(row)} columns")
        yield (
            int(row[0]),
            float(row[1]),
            float(row[2]),
            int(row[3]),
            int(row[4]),
            ts_ns(row[5]),
            parse_bool(row[6]),
        )


def aggregate_seconds(text: str) -> tuple[list[SecondBar], dict[str, Any]]:
    seconds: list[SecondBar] = []
    current: SecondBar | None = None
    rows = 0
    last_agg_id: int | None = None
    last_trade_id: int | None = None
    last_ts = -1
    agg_id_gaps = 0
    trade_id_gaps = 0
    timestamp_regressions = 0
    same_timestamp_events = 0
    prior_ts = -1

    run_side: bool | None = None
    run_qty = 0.0
    run_events = 0
    run_start_ts = 0
    run_stats: list[tuple[float, int, int]] = []

    first_record: dict[str, Any] | None = None
    last_record: dict[str, Any] | None = None

    for agg_id, price, qty, first_trade, final_trade, event_ns, buyer_maker in iter_aggtrades(text):
        rows += 1
        if qty <= 0.0 or price <= 0.0:
            raise ValueError(f"non-positive price/quantity at agg_id={agg_id}")
        if first_trade > final_trade:
            raise ValueError(f"first trade exceeds last trade at agg_id={agg_id}")
        if last_agg_id is not None and agg_id != last_agg_id + 1:
            agg_id_gaps += 1
        if last_trade_id is not None and first_trade != last_trade_id + 1:
            trade_id_gaps += 1
        if event_ns < last_ts:
            timestamp_regressions += 1
        if event_ns == prior_ts:
            same_timestamp_events += 1
        last_agg_id = agg_id
        last_trade_id = final_trade
        last_ts = max(last_ts, event_ns)
        prior_ts = event_ns

        if first_record is None:
            first_record = {
                "agg_id": agg_id,
                "trade_id": first_trade,
                "timestamp_ns": event_ns,
                "price": price,
            }
        last_record = {
            "agg_id": agg_id,
            "trade_id": final_trade,
            "timestamp_ns": event_ns,
            "price": price,
        }

        side = not buyer_maker
        if run_side is None or side == run_side:
            if run_side is None:
                run_start_ts = event_ns
            run_side = side
            run_qty += qty
            run_events += 1
        else:
            run_stats.append((run_qty, run_events, max(0, event_ns - run_start_ts)))
            run_side = side
            run_qty = qty
            run_events = 1
            run_start_ts = event_ns

        second_ns = (event_ns // 1_000_000_000) * 1_000_000_000
        if current is None or second_ns != current.second_ns:
            if current is not None:
                seconds.append(current)
            current = SecondBar.start(
                second_ns=second_ns,
                price=price,
                qty=qty,
                is_buyer_maker=buyer_maker,
                agg_id=agg_id,
                first_trade_id=first_trade,
                last_trade_id=final_trade,
            )
        else:
            current.add(
                price=price,
                qty=qty,
                is_buyer_maker=buyer_maker,
                agg_id=agg_id,
                first_trade_id=first_trade,
                last_trade_id=final_trade,
            )

    if current is not None:
        seconds.append(current)
    if run_side is not None:
        run_stats.append((run_qty, run_events, max(0, last_ts - run_start_ts)))
    if not seconds or first_record is None or last_record is None:
        raise ValueError("aggTrade archive contained no records")

    run_qtys = [item[0] for item in run_stats]
    run_events = [float(item[1]) for item in run_stats]
    run_durations_ms = [item[2] / 1_000_000 for item in run_stats]
    diagnostics = {
        "aggtrade_rows": rows,
        "first_record": first_record,
        "last_record": last_record,
        "agg_id_gap_count": agg_id_gaps,
        "underlying_trade_id_gap_count": trade_id_gaps,
        "timestamp_regression_count": timestamp_regressions,
        "same_timestamp_event_count": same_timestamp_events,
        "side_run_count": len(run_stats),
        "side_run_qty_p50": quantile(run_qtys, 0.50),
        "side_run_qty_p95": quantile(run_qtys, 0.95),
        "side_run_qty_p99": quantile(run_qtys, 0.99),
        "side_run_events_p95": quantile(run_events, 0.95),
        "side_run_duration_ms_p95": quantile(run_durations_ms, 0.95),
        "side_run_max_qty": max(run_qtys),
        "side_run_max_events": int(max(run_events)),
        "side_run_max_duration_ms": max(run_durations_ms),
    }
    return seconds, diagnostics


def parse_klines(text: str) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        if not row[0].lstrip("-").isdigit():
            continue
        if len(row) < 11:
            raise ValueError("malformed kline row")
        open_ns = ts_ns(row[0])
        minute_ns = (open_ns // 60_000_000_000) * 60_000_000_000
        result[minute_ns] = {
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "trades": float(row[8]),
            "taker_buy_volume": float(row[9]),
        }
    return result


def aggregate_minutes(seconds: list[SecondBar]) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    for bar in seconds:
        key = (bar.second_ns // 60_000_000_000) * 60_000_000_000
        row = result.get(key)
        if row is None:
            result[key] = {
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "trades": float(bar.underlying_trade_count),
                "taker_buy_volume": bar.buy_volume,
            }
        else:
            row["high"] = max(row["high"], bar.high)
            row["low"] = min(row["low"], bar.low)
            row["close"] = bar.close
            row["volume"] += bar.volume
            row["trades"] += bar.underlying_trade_count
            row["taker_buy_volume"] += bar.buy_volume
    return result


def reconcile(minutes: dict[int, dict[str, float]], klines: dict[int, dict[str, float]]) -> dict[str, Any]:
    common = sorted(set(minutes) & set(klines))
    missing_from_agg = sorted(set(klines) - set(minutes))
    extra_in_agg = sorted(set(minutes) - set(klines))
    fields = ["open", "high", "low", "close", "volume", "trades", "taker_buy_volume"]
    max_abs = {field: 0.0 for field in fields}
    mismatch_count = {field: 0 for field in fields}
    tolerance = {
        "open": 1e-8,
        "high": 1e-8,
        "low": 1e-8,
        "close": 1e-8,
        "volume": 1e-6,
        "trades": 0.0,
        "taker_buy_volume": 1e-6,
    }
    for key in common:
        for field in fields:
            diff = abs(minutes[key][field] - klines[key][field])
            max_abs[field] = max(max_abs[field], diff)
            if diff > tolerance[field]:
                mismatch_count[field] += 1
    return {
        "common_minutes": len(common),
        "missing_from_aggtrade_minutes": len(missing_from_agg),
        "extra_aggtrade_minutes": len(extra_in_agg),
        "max_absolute_difference": max_abs,
        "mismatch_minutes": mismatch_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--date", default="2024-10-14")
    parser.add_argument("--output", default="artifacts/candidate-09-aggprobe")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    symbol = args.symbol.upper()
    day = args.date

    agg_name = f"{symbol}-aggTrades-{day}.zip"
    agg_url = f"{BASE}/aggTrades/{symbol}/{agg_name}"
    agg_payload = download(agg_url)
    agg_checksum = download(f"{agg_url}.CHECKSUM")
    agg_sha = verify_checksum(agg_payload, agg_checksum, agg_name)
    agg_text = csv_text(agg_payload, agg_name)
    seconds, event_diagnostics = aggregate_seconds(agg_text)

    kline_name = f"{symbol}-1m-{day}.zip"
    kline_url = f"{BASE}/klines/{symbol}/1m/{kline_name}"
    kline_payload = download(kline_url)
    kline_checksum = download(f"{kline_url}.CHECKSUM")
    kline_sha = verify_checksum(kline_payload, kline_checksum, kline_name)
    klines = parse_klines(csv_text(kline_payload, kline_name))
    minutes = aggregate_minutes(seconds)

    volumes = [bar.volume for bar in seconds]
    signed = [bar.signed_volume for bar in seconds]
    abs_imbalance = [abs(bar.signed_volume) / bar.volume if bar.volume > 0 else 0.0 for bar in seconds]
    ranges_bps = [
        10_000.0 * (bar.high - bar.low) / bar.open if bar.open > 0.0 else 0.0
        for bar in seconds
    ]
    event_counts = [float(bar.aggtrade_count) for bar in seconds]
    underlying_counts = [float(bar.underlying_trade_count) for bar in seconds]

    first_second = seconds[0].second_ns
    last_second = seconds[-1].second_ns
    span_seconds = (last_second - first_second) // 1_000_000_000 + 1
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "date": day,
        "sources": {
            "aggtrades": {"url": agg_url, "sha256": agg_sha, "zip_bytes": len(agg_payload)},
            "klines": {"url": kline_url, "sha256": kline_sha, "zip_bytes": len(kline_payload)},
        },
        "event_stream": event_diagnostics,
        "one_second": {
            "nonempty_seconds": len(seconds),
            "span_seconds": span_seconds,
            "empty_seconds": int(span_seconds - len(seconds)),
            "first_second_ns": first_second,
            "last_second_ns": last_second,
            "volume_total": sum(volumes),
            "buy_volume_total": sum(bar.buy_volume for bar in seconds),
            "sell_volume_total": sum(bar.sell_volume for bar in seconds),
            "volume_p50": quantile(volumes, 0.50),
            "volume_p95": quantile(volumes, 0.95),
            "volume_p99": quantile(volumes, 0.99),
            "absolute_signed_volume_p95": quantile([abs(x) for x in signed], 0.95),
            "absolute_imbalance_p50": quantile(abs_imbalance, 0.50),
            "absolute_imbalance_p95": quantile(abs_imbalance, 0.95),
            "range_bps_p50": quantile(ranges_bps, 0.50),
            "range_bps_p95": quantile(ranges_bps, 0.95),
            "range_bps_p99": quantile(ranges_bps, 0.99),
            "aggtrade_count_p95": quantile(event_counts, 0.95),
            "underlying_trade_count_p95": quantile(underlying_counts, 0.95),
        },
        "kline_reconciliation": reconcile(minutes, klines),
    }

    with (output / "seconds.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = list(asdict(seconds[0]).keys())
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for bar in seconds:
            writer.writerow(asdict(bar))
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
