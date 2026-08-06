#!/usr/bin/env python3
"""Diagnose Binance bookTicker/aggregate-trade timestamp alignment for candidate 10."""
from __future__ import annotations

from collections import Counter
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Iterator
from urllib.request import urlopen
import zipfile

DATE = "2023-10-16"
SYMBOL = "BTCUSDT"
TICK = 0.1
ROOT = Path("artifacts/candidate-10-alignment-probe")
DATA = Path("/tmp/candidate-10-alignment-probe")
BOOK_ROOT = "https://data.binance.vision/data/futures/um/daily/bookTicker"
TRADE_ROOT = "https://data.binance.vision/data/futures/um/daily/aggTrades"
TARGET_NS = [
    1697433402763000000,
    1697433402774000000,
    1697433405059000000,
    1697433405063000000,
    1697433405078000000,
    1697433406213000000,
    1697433408957000000,
    1697433409107000000,
    1697433417574000000,
]
WINDOW_START_NS = 1697433395000000000
WINDOW_END_NS = 1697433430000000000


def sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def download_verified(base: str, stem: str) -> tuple[Path, str]:
    DATA.mkdir(parents=True, exist_ok=True)
    archive = DATA / f"{stem}.zip"
    checksum = DATA / f"{stem}.zip.CHECKSUM"
    for url, path in ((f"{base}/{SYMBOL}/{stem}.zip.CHECKSUM", checksum),
                      (f"{base}/{SYMBOL}/{stem}.zip", archive)):
        if not path.exists():
            with urlopen(url, timeout=180) as response, path.open("wb") as out:
                shutil.copyfileobj(response, out, length=8 * 1024 * 1024)
    expected = checksum.read_text(encoding="utf-8").split()[0]
    actual = sha256_file(archive)
    if actual.lower() != expected.lower():
        raise RuntimeError(f"checksum mismatch: {archive}: {actual} != {expected}")
    return archive, actual


def rows(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as zf:
        names = [name for name in zf.namelist() if name.endswith(".csv")]
        if len(names) != 1:
            raise RuntimeError(f"expected one csv in {path}: {names}")
        with io.TextIOWrapper(zf.open(names[0]), encoding="utf-8") as stream:
            for row in csv.reader(stream):
                if row and row[0].lstrip("-").isdigit():
                    yield row


def quote_rows(path: Path) -> Iterator[dict[str, float | int]]:
    for row in rows(path):
        if len(row) != 7:
            raise RuntimeError(f"bookTicker width {len(row)}")
        yield {
            "update_id": int(row[0]),
            "bid": float(row[1]),
            "bid_qty": float(row[2]),
            "ask": float(row[3]),
            "ask_qty": float(row[4]),
            "transaction_ns": int(row[5]) * 1_000_000,
            "event_ns": int(row[6]) * 1_000_000,
        }


def trade_rows(path: Path) -> Iterator[dict[str, float | int | bool]]:
    for row in rows(path):
        if len(row) not in {7, 8}:
            raise RuntimeError(f"aggTrade width {len(row)}")
        yield {
            "trade_id": int(row[0]),
            "price": float(row[1]),
            "qty": float(row[2]),
            "first_id": int(row[3]),
            "last_id": int(row[4]),
            "transaction_ns": int(row[5]) * 1_000_000,
            "buyer_maker": row[6].strip().lower() == "true",
        }


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {name: None for name in ("q00", "q50", "q90", "q95", "q99", "q999", "q100")}
    values.sort()
    def q(p: float) -> float:
        pos = (len(values) - 1) * p
        lo = int(pos)
        hi = min(len(values) - 1, lo + 1)
        w = pos - lo
        return values[lo] * (1.0 - w) + values[hi] * w
    return {
        "q00": q(0.0), "q50": q(0.5), "q90": q(0.9), "q95": q(0.95),
        "q99": q(0.99), "q999": q(0.999), "q100": q(1.0),
    }


def distance_ticks(price: float, quote: dict[str, float | int]) -> float:
    bid = float(quote["bid"])
    ask = float(quote["ask"])
    if price < bid:
        return (bid - price) / TICK
    if price > ask:
        return (price - ask) / TICK
    return 0.0


def summarize_mode(book: Path, trades: Path, mode: str) -> tuple[dict, list[dict]]:
    if mode not in {"event_ns", "transaction_ns"}:
        raise ValueError(mode)
    qiter = quote_rows(book)
    next_quote = next(qiter, None)
    latest = None
    counts = Counter()
    distances: list[float] = []
    spreads: list[float] = []
    ages_ms: list[float] = []
    anomalies: list[dict] = []
    target_nearest: dict[int, tuple[int, dict] | None] = {target: None for target in TARGET_NS}
    previous_quote_key = None
    nonmonotonic_quotes = 0
    event_minus_transaction_ms: list[float] = []

    for trade in trade_rows(trades):
        tns = int(trade["transaction_ns"])
        while next_quote is not None and int(next_quote[mode]) <= tns:
            key = int(next_quote[mode])
            if previous_quote_key is not None and key < previous_quote_key:
                nonmonotonic_quotes += 1
            previous_quote_key = key
            latest = next_quote
            event_minus_transaction_ms.append(
                (int(latest["event_ns"]) - int(latest["transaction_ns"])) / 1_000_000,
            )
            next_quote = next(qiter, None)
        counts["trade_rows"] += 1
        if latest is None:
            counts["no_quote"] += 1
            continue
        d = distance_ticks(float(trade["price"]), latest)
        s = (float(latest["ask"]) - float(latest["bid"])) / TICK
        age = (tns - int(latest[mode])) / 1_000_000
        distances.append(d)
        spreads.append(s)
        ages_ms.append(age)
        counts["quote_attached"] += 1
        for threshold in (0, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000):
            if d > threshold:
                counts[f"distance_gt_{threshold}_ticks"] += 1
        if WINDOW_START_NS <= tns <= WINDOW_END_NS and (d > 10 or s > 20):
            anomalies.append({
                "mode": mode,
                **trade,
                **{f"quote_{key}": value for key, value in latest.items()},
                "distance_ticks": d,
                "spread_ticks": s,
                "quote_age_ms": age,
            })
        for target in TARGET_NS:
            delta = abs(tns - target)
            current = target_nearest[target]
            if current is None or delta < current[0]:
                target_nearest[target] = (delta, {
                    "target_ns": target,
                    "delta_ms": delta / 1_000_000,
                    "mode": mode,
                    **trade,
                    **{f"quote_{key}": value for key, value in latest.items()},
                    "distance_ticks": d,
                    "spread_ticks": s,
                    "quote_age_ms": age,
                })

    report = {
        "mode": mode,
        "counts": dict(counts),
        "nonmonotonic_quote_join_timestamps": nonmonotonic_quotes,
        "trade_to_selected_bbo_distance_ticks": quantiles(distances),
        "selected_spread_ticks": quantiles(spreads),
        "selected_quote_age_ms": quantiles(ages_ms),
        "book_event_minus_transaction_ms": quantiles(event_minus_transaction_ms),
        "target_nearest": [value[1] for value in target_nearest.values() if value is not None],
    }
    return report, anomalies


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    book, book_sha = download_verified(BOOK_ROOT, f"{SYMBOL}-bookTicker-{DATE}")
    trades, trade_sha = download_verified(TRADE_ROOT, f"{SYMBOL}-aggTrades-{DATE}")
    reports = []
    anomalies = []
    for mode in ("event_ns", "transaction_ns"):
        report, mode_anomalies = summarize_mode(book, trades, mode)
        reports.append(report)
        anomalies.extend(mode_anomalies)
    anomalies.sort(key=lambda item: (int(item["transaction_ns"]), str(item["mode"])))
    if anomalies:
        with (ROOT / "burst_anomalies.csv").open("w", newline="", encoding="utf-8") as out:
            writer = csv.DictWriter(out, fieldnames=sorted({k for row in anomalies for k in row}))
            writer.writeheader()
            writer.writerows(anomalies)
    output = {
        "date": DATE,
        "symbol": SYMBOL,
        "book_sha256": book_sha,
        "trade_sha256": trade_sha,
        "book_bytes": book.stat().st_size,
        "trade_bytes": trades.stat().st_size,
        "reports": reports,
        "elapsed_seconds": time.perf_counter() - started,
        "purpose": "timestamp/data-path implementation diagnosis only; no PnL claim",
    }
    (ROOT / "report.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
