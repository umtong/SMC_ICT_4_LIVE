"""Causal one-day L1/order-flow diagnostic for candidate 10.

This is not a backtest or execution engine. It checksum-verifies the official
Binance USD-M aggregate-trade and bookTicker archives, joins each trade only to
the latest quote whose event timestamp is already known, builds the same causal
event-notional bars used by candidate 10, and measures forward price response by
rolling, pre-event feature regimes. The output selects variables for the next
NautilusTrader state machine; it never reports trading PnL.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
from statistics import median
import sys
import time
from typing import Any, Iterator
from urllib.request import urlopen
import zipfile

import pandas as pd

DATE = "2023-10-16"
SYMBOL = "BTCUSDT"
BOOK_ROOT = "https://data.binance.vision/data/futures/um/daily/bookTicker"
TRADE_ROOT = "https://data.binance.vision/data/futures/um/daily/aggTrades"
TICK_SIZE = 0.1
MINUTE_NS = 60_000_000_000
MINUTE_LOOKBACK = 60
MINIMUM_MINUTES = 30
EVENT_NOTIONAL_FRACTION = 0.25
FEATURE_LOOKBACK = 240
MINIMUM_FEATURES = 80
HORIZONS = (1, 3, 5)


def _download(url: str, path: Path) -> None:
    if path.exists():
        return
    with urlopen(url, timeout=180) as response:
        path.write_bytes(response.read())


def _download_verified(root: str, stem: str, directory: Path) -> tuple[Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f"{stem}.zip"
    checksum = directory / f"{stem}.zip.CHECKSUM"
    url = f"{root}/{SYMBOL}/{stem}.zip"
    _download(url + ".CHECKSUM", checksum)
    _download(url, archive)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0]
    actual = sha256(archive.read_bytes()).hexdigest()
    if expected.lower() != actual.lower():
        raise RuntimeError(f"checksum mismatch for {archive.name}: {actual} != {expected}")
    return archive, actual


def _to_ns(raw: str) -> int:
    value = int(raw)
    if value < 10_000_000_000_000:
        return value * 1_000_000
    if value < 10_000_000_000_000_000:
        return value * 1_000
    return value


def _bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"unexpected boolean: {raw!r}")


@dataclass(frozen=True, slots=True)
class Quote:
    update_id: int
    ts_ns: int
    bid: float
    bid_size: float
    ask: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) * 0.5

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def imbalance(self) -> float:
        total = self.bid_size + self.ask_size
        return (self.bid_size - self.ask_size) / total if total > 0.0 else 0.0

    @property
    def microprice(self) -> float:
        total = self.bid_size + self.ask_size
        if total <= 0.0:
            return self.mid
        return (self.ask * self.bid_size + self.bid * self.ask_size) / total


@dataclass(frozen=True, slots=True)
class Trade:
    agg_id: int
    ts_ns: int
    price: float
    quantity: float
    aggressor: int

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass(slots=True)
class DiagnosticBar:
    sequence: int
    threshold_notional: float
    start_ns: int
    end_ns: int
    open: float
    high: float
    low: float
    close: float
    previous_price: float
    path_travel: float
    notional: float
    buyer_notional: float
    seller_notional: float
    trade_count: int
    start_quote: Quote
    end_quote: Quote
    quote_lag_ns_sum: int
    quote_lag_ns_max: int
    weighted_imbalance_sum: float
    weighted_micro_edge_ticks_sum: float

    @classmethod
    def from_trade(
        cls,
        *,
        sequence: int,
        threshold_notional: float,
        trade: Trade,
        quote: Quote,
    ) -> "DiagnosticBar":
        buyer = trade.notional if trade.aggressor > 0 else 0.0
        seller = trade.notional if trade.aggressor < 0 else 0.0
        lag = max(0, trade.ts_ns - quote.ts_ns)
        micro_edge_ticks = (quote.microprice - quote.mid) / TICK_SIZE
        return cls(
            sequence=sequence,
            threshold_notional=threshold_notional,
            start_ns=trade.ts_ns,
            end_ns=trade.ts_ns,
            open=trade.price,
            high=trade.price,
            low=trade.price,
            close=trade.price,
            previous_price=trade.price,
            path_travel=0.0,
            notional=trade.notional,
            buyer_notional=buyer,
            seller_notional=seller,
            trade_count=1,
            start_quote=quote,
            end_quote=quote,
            quote_lag_ns_sum=lag,
            quote_lag_ns_max=lag,
            weighted_imbalance_sum=quote.imbalance * trade.notional,
            weighted_micro_edge_ticks_sum=micro_edge_ticks * trade.notional,
        )

    def update(self, trade: Trade, quote: Quote) -> None:
        self.path_travel += abs(trade.price - self.previous_price)
        self.previous_price = trade.price
        self.end_ns = trade.ts_ns
        self.high = max(self.high, trade.price)
        self.low = min(self.low, trade.price)
        self.close = trade.price
        self.notional += trade.notional
        if trade.aggressor > 0:
            self.buyer_notional += trade.notional
        else:
            self.seller_notional += trade.notional
        self.trade_count += 1
        self.end_quote = quote
        lag = max(0, trade.ts_ns - quote.ts_ns)
        self.quote_lag_ns_sum += lag
        self.quote_lag_ns_max = max(self.quote_lag_ns_max, lag)
        self.weighted_imbalance_sum += quote.imbalance * trade.notional
        self.weighted_micro_edge_ticks_sum += (
            (quote.microprice - quote.mid) / TICK_SIZE * trade.notional
        )

    @property
    def delta_ratio(self) -> float:
        return (
            (self.buyer_notional - self.seller_notional) / self.notional
            if self.notional > 0.0
            else 0.0
        )

    @property
    def efficiency(self) -> float:
        return abs(self.close - self.open) / self.path_travel if self.path_travel > 0.0 else 0.0

    def record(self) -> dict[str, Any]:
        sign = 1 if self.delta_ratio > 0.0 else -1 if self.delta_ratio < 0.0 else 0
        if sign > 0:
            opposite_depletion = (
                self.start_quote.ask_size - self.end_quote.ask_size
            ) / max(self.start_quote.ask_size, 1e-12)
            same_replenishment = (
                self.end_quote.bid_size - self.start_quote.bid_size
            ) / max(self.start_quote.bid_size, 1e-12)
        elif sign < 0:
            opposite_depletion = (
                self.start_quote.bid_size - self.end_quote.bid_size
            ) / max(self.start_quote.bid_size, 1e-12)
            same_replenishment = (
                self.end_quote.ask_size - self.start_quote.ask_size
            ) / max(self.start_quote.ask_size, 1e-12)
        else:
            opposite_depletion = 0.0
            same_replenishment = 0.0
        mean_imbalance = self.weighted_imbalance_sum / self.notional
        mean_micro_edge = self.weighted_micro_edge_ticks_sum / self.notional
        mid_move_ticks = (self.end_quote.mid - self.start_quote.mid) / TICK_SIZE
        micro_move_ticks = (
            self.end_quote.microprice - self.start_quote.microprice
        ) / TICK_SIZE
        return {
            "sequence": self.sequence,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "threshold_notional": self.threshold_notional,
            "notional": self.notional,
            "trade_count": self.trade_count,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "delta_ratio": self.delta_ratio,
            "efficiency": self.efficiency,
            "start_mid": self.start_quote.mid,
            "end_mid": self.end_quote.mid,
            "mid_move_ticks": mid_move_ticks,
            "micro_move_ticks": micro_move_ticks,
            "start_spread_ticks": self.start_quote.spread / TICK_SIZE,
            "end_spread_ticks": self.end_quote.spread / TICK_SIZE,
            "start_imbalance": self.start_quote.imbalance,
            "end_imbalance": self.end_quote.imbalance,
            "mean_notional_weighted_imbalance": mean_imbalance,
            "mean_notional_weighted_micro_edge_ticks": mean_micro_edge,
            "flow_queue_alignment": sign * self.end_quote.imbalance,
            "flow_mean_queue_alignment": sign * mean_imbalance,
            "flow_microprice_alignment_ticks": sign
            * (self.end_quote.microprice - self.end_quote.mid)
            / TICK_SIZE,
            "opposite_queue_depletion": opposite_depletion,
            "same_queue_replenishment": same_replenishment,
            "mean_quote_lag_ms": self.quote_lag_ns_sum / self.trade_count / 1_000_000,
            "max_quote_lag_ms": self.quote_lag_ns_max / 1_000_000,
        }


def _iter_csv(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected one CSV member in {path}, got {members}")
        stream = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8")
        for raw in csv.reader(stream):
            if raw:
                yield [item.strip() for item in raw]


def iter_quotes(path: Path, diagnostics: Counter[str]) -> Iterator[Quote]:
    previous_id: int | None = None
    previous_ts: int | None = None
    for row in _iter_csv(path):
        if not row[0].lstrip("-").isdigit():
            continue
        if len(row) != 7:
            raise RuntimeError(f"unexpected bookTicker width {len(row)}")
        quote = Quote(
            update_id=int(row[0]),
            bid=float(row[1]),
            bid_size=float(row[2]),
            ask=float(row[3]),
            ask_size=float(row[4]),
            ts_ns=_to_ns(row[6]),
        )
        diagnostics["quote_rows"] += 1
        if quote.ask <= quote.bid:
            diagnostics["nonpositive_spread"] += 1
        if previous_id is not None and quote.update_id == previous_id:
            diagnostics["duplicate_quote_id"] += 1
        if previous_ts is not None and quote.ts_ns < previous_ts:
            diagnostics["nonmonotonic_quote_ts"] += 1
        previous_id = quote.update_id
        previous_ts = quote.ts_ns
        yield quote


def iter_trades(path: Path, diagnostics: Counter[str]) -> Iterator[Trade]:
    previous_id: int | None = None
    previous_ts: int | None = None
    for row in _iter_csv(path):
        if not row[0].lstrip("-").isdigit():
            continue
        if len(row) not in {7, 8}:
            raise RuntimeError(f"unexpected aggTrade width {len(row)}")
        buyer_maker = _bool(row[6])
        trade = Trade(
            agg_id=int(row[0]),
            price=float(row[1]),
            quantity=float(row[2]),
            ts_ns=_to_ns(row[5]),
            aggressor=-1 if buyer_maker else 1,
        )
        diagnostics["trade_rows"] += 1
        if previous_id is not None and trade.agg_id == previous_id:
            diagnostics["duplicate_trade_id"] += 1
        if previous_ts is not None and trade.ts_ns < previous_ts:
            diagnostics["nonmonotonic_trade_ts"] += 1
        previous_id = trade.agg_id
        previous_ts = trade.ts_ns
        yield trade


def _quantile(values: deque[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    location = (len(ordered) - 1) * probability
    lower = int(location)
    upper = min(len(ordered) - 1, lower + 1)
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def build_records(book: Path, trades: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics: Counter[str] = Counter()
    quotes = iter_quotes(book, diagnostics)
    next_quote = next(quotes, None)
    latest_quote: Quote | None = None

    minute_bucket: int | None = None
    minute_notional = 0.0
    minute_history: deque[float] = deque(maxlen=MINUTE_LOOKBACK)
    current: DiagnosticBar | None = None
    sequence = 0
    records: list[dict[str, Any]] = []

    abs_delta_history: deque[float] = deque(maxlen=FEATURE_LOOKBACK)
    efficiency_history: deque[float] = deque(maxlen=FEATURE_LOOKBACK)
    alignment_history: deque[float] = deque(maxlen=FEATURE_LOOKBACK)

    for trade in iter_trades(trades, diagnostics):
        while next_quote is not None and next_quote.ts_ns <= trade.ts_ns:
            latest_quote = next_quote
            next_quote = next(quotes, None)
        if latest_quote is None:
            diagnostics["trades_before_first_quote"] += 1
            continue

        bucket = trade.ts_ns // MINUTE_NS
        if minute_bucket is None:
            minute_bucket = bucket
        elif bucket != minute_bucket:
            minute_history.append(minute_notional)
            gap = max(0, bucket - minute_bucket - 1)
            for _ in range(min(gap, MINUTE_LOOKBACK)):
                minute_history.append(0.0)
            minute_bucket = bucket
            minute_notional = 0.0
        minute_notional += trade.notional

        if current is None:
            if len(minute_history) < MINIMUM_MINUTES:
                continue
            threshold = median(minute_history) * EVENT_NOTIONAL_FRACTION
            if threshold <= 0.0:
                continue
            current = DiagnosticBar.from_trade(
                sequence=sequence,
                threshold_notional=threshold,
                trade=trade,
                quote=latest_quote,
            )
        else:
            current.update(trade, latest_quote)

        if current.notional < current.threshold_notional:
            continue

        record = current.record()
        if (
            len(abs_delta_history) >= MINIMUM_FEATURES
            and len(efficiency_history) >= MINIMUM_FEATURES
            and len(alignment_history) >= MINIMUM_FEATURES
        ):
            delta_q75 = _quantile(abs_delta_history, 0.75)
            efficiency_q25 = _quantile(efficiency_history, 0.25)
            efficiency_q75 = _quantile(efficiency_history, 0.75)
            alignment_q25 = _quantile(alignment_history, 0.25)
            alignment_q75 = _quantile(alignment_history, 0.75)
            strong = abs(record["delta_ratio"]) >= delta_q75
            efficient = record["efficiency"] >= efficiency_q75
            inefficient = record["efficiency"] <= efficiency_q25
            aligned = record["flow_queue_alignment"] >= alignment_q75
            opposed = record["flow_queue_alignment"] <= alignment_q25
            if strong and efficient and aligned:
                regime = "STRONG_EFFICIENT_L1_ALIGNED"
            elif strong and efficient:
                regime = "STRONG_EFFICIENT_L1_NOT_ALIGNED"
            elif strong and inefficient and opposed:
                regime = "STRONG_INEFFICIENT_L1_OPPOSED"
            elif strong and inefficient:
                regime = "STRONG_INEFFICIENT_L1_NOT_OPPOSED"
            else:
                regime = "OTHER"
            record.update(
                {
                    "regime": regime,
                    "causal_delta_q75": delta_q75,
                    "causal_efficiency_q25": efficiency_q25,
                    "causal_efficiency_q75": efficiency_q75,
                    "causal_alignment_q25": alignment_q25,
                    "causal_alignment_q75": alignment_q75,
                },
            )
        else:
            record["regime"] = "WARMUP"

        records.append(record)
        abs_delta_history.append(abs(record["delta_ratio"]))
        efficiency_history.append(record["efficiency"])
        alignment_history.append(record["flow_queue_alignment"])
        sequence += 1
        current = None

    # Exhaust the quote generator so integrity diagnostics cover the entire day.
    while next_quote is not None:
        latest_quote = next_quote
        next_quote = next(quotes, None)

    return records, dict(diagnostics)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(records)
    if frame.empty:
        raise RuntimeError("no event records built")
    sign = frame["delta_ratio"].apply(lambda value: 1 if value > 0 else -1 if value < 0 else 0)
    for horizon in HORIZONS:
        future_mid = frame["end_mid"].shift(-horizon)
        directional = sign * (future_mid - frame["end_mid"]) / TICK_SIZE
        frame[f"continuation_ticks_h{horizon}"] = directional
        frame[f"reversal_ticks_h{horizon}"] = -directional

    groups: dict[str, Any] = {}
    for regime, subset in frame.groupby("regime"):
        if regime == "WARMUP":
            continue
        item: dict[str, Any] = {"count": int(len(subset))}
        for horizon in HORIZONS:
            values = subset[f"continuation_ticks_h{horizon}"].dropna()
            item[f"continuation_hit_rate_h{horizon}"] = (
                float((values > 0).mean()) if len(values) else None
            )
            item[f"continuation_mean_ticks_h{horizon}"] = (
                float(values.mean()) if len(values) else None
            )
            item[f"continuation_median_ticks_h{horizon}"] = (
                float(values.median()) if len(values) else None
            )
            item[f"reversal_hit_rate_h{horizon}"] = (
                float((values < 0).mean()) if len(values) else None
            )
        groups[str(regime)] = item

    causal = frame[frame["regime"] != "WARMUP"].copy()
    correlations: dict[str, Any] = {}
    features = [
        "delta_ratio",
        "efficiency",
        "flow_queue_alignment",
        "flow_mean_queue_alignment",
        "flow_microprice_alignment_ticks",
        "opposite_queue_depletion",
        "same_queue_replenishment",
        "end_spread_ticks",
    ]
    for horizon in HORIZONS:
        target = f"continuation_ticks_h{horizon}"
        correlations[f"h{horizon}"] = {
            feature: (
                None
                if causal[[feature, target]].dropna().empty
                else float(causal[[feature, target]].corr(method="spearman").iloc[0, 1])
            )
            for feature in features
        }

    quantile_columns = [
        "delta_ratio",
        "efficiency",
        "flow_queue_alignment",
        "flow_mean_queue_alignment",
        "flow_microprice_alignment_ticks",
        "opposite_queue_depletion",
        "same_queue_replenishment",
        "mean_quote_lag_ms",
        "max_quote_lag_ms",
        "start_spread_ticks",
        "end_spread_ticks",
    ]
    quantiles = {
        column: {
            "q01": float(frame[column].quantile(0.01)),
            "q25": float(frame[column].quantile(0.25)),
            "q50": float(frame[column].quantile(0.50)),
            "q75": float(frame[column].quantile(0.75)),
            "q99": float(frame[column].quantile(0.99)),
        }
        for column in quantile_columns
    }
    return {
        "event_bar_count": int(len(frame)),
        "warmup_event_bars": int((frame["regime"] == "WARMUP").sum()),
        "regime_counts": dict(Counter(frame["regime"].astype(str))),
        "regime_forward_response": groups,
        "spearman_feature_vs_continuation": correlations,
        "feature_quantiles": quantiles,
        "event_notional_definition": {
            "completed_minute_lookback": MINUTE_LOOKBACK,
            "minimum_completed_minutes": MINIMUM_MINUTES,
            "fraction_of_rolling_median_minute_notional": EVENT_NOTIONAL_FRACTION,
            "feature_lookback_event_bars": FEATURE_LOOKBACK,
            "minimum_feature_history": MINIMUM_FEATURES,
            "classification_thresholds": "rolling pre-event empirical quartiles",
        },
        "important_limit": (
            "bookTicker provides only best bid/ask state, not full queue priority, "
            "individual limit submissions/cancellations, or hidden liquidity"
        ),
        "frame": frame,
    }


def main() -> int:
    output = Path("artifacts/candidate-10-l1-diagnostic")
    output.mkdir(parents=True, exist_ok=True)
    data = Path("/tmp/candidate-10-l1-diagnostic")
    data.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    book_stem = f"{SYMBOL}-bookTicker-{DATE}"
    trade_stem = f"{SYMBOL}-aggTrades-{DATE}"
    book, book_sha = _download_verified(BOOK_ROOT, book_stem, data)
    trades, trade_sha = _download_verified(TRADE_ROOT, trade_stem, data)
    records, integrity = build_records(book, trades)
    summary = summarize(records)
    frame = summary.pop("frame")
    frame.to_csv(output / "event_bars.csv", index=False)

    report = {
        "date": DATE,
        "symbol": SYMBOL,
        "bookticker_sha256": book_sha,
        "aggtrades_sha256": trade_sha,
        "bookticker_archive_bytes": book.stat().st_size,
        "aggtrades_archive_bytes": trades.stat().st_size,
        "integrity": integrity,
        "analysis": summary,
        "elapsed_seconds": time.perf_counter() - started,
        "purpose": "causal feature selection only; no PnL or execution claim",
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
