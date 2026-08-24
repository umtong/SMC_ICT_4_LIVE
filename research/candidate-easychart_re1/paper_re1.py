"""Warm-started live/demo strategy wrapper for EasyChart RE1.

The decision and execution classes are identical to the backtest candidate.  A
paper node additionally needs two operational properties which a batch backtest
does not:

* enough closed history before the first live bar, so pivots, channels and
  footprint lifecycles do not cold-start;
* deterministic cross-symbol bar ordering despite asynchronous WebSocket
  delivery.

Warmup candles are built before the node starts and replayed directly through
the same scenario bundles with trading disabled.  Live bars are then held in a
small timestamp reorder buffer and a timestamp is processed only after all four
1-minute bars and every due internal composite bar are present.  Missing data
halts new decisions and flattens exposure rather than silently trading an
incomplete market view.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId

from data import resample, wrangler_frame
from data_re1_flow import FLOW_COLUMNS, load_range_flow, wrangler_flow_frame
from domain import Candle
from easychart_re1_flow import FlowCandle
from execution_re1 import EasyChartMTFConfig, EasyChartRE1Strategy


PUBLIC_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"
LIVE_REORDER_GRACE_NS = 3 * 60 * 1_000_000_000

WarmupMap = dict[str, dict[int, list[Candle | FlowCandle]]]


def _request_json(url: str, attempts: int = 5) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SMC-ICT-4-LIVE-paper/1.0"},
    )
    retryable_status = {408, 425, 429, 500, 502, 503, 504}
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in retryable_status or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts:
                raise
        time.sleep(min(8.0, 2.0 ** (attempt - 1)))
    raise RuntimeError(f"unreachable request failure: {url}")


def _recent_one_minute_klines(
    symbol: str,
    start_open_ms: int,
    end_open_ms: int,
) -> pd.DataFrame:
    rows: list[list[Any]] = []
    cursor = start_open_ms
    while cursor <= end_open_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": "1m",
                "startTime": cursor,
                "endTime": end_open_ms,
                "limit": 1500,
            },
        )
        batch = _request_json(f"{PUBLIC_FUTURES_KLINES}?{query}")
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected Binance kline response for {symbol}: {batch!r}")
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + 60_000
        if next_cursor <= cursor:
            raise RuntimeError(f"non-advancing Binance kline cursor for {symbol}")
        cursor = next_cursor
        if len(batch) < 1500:
            break
    if not rows:
        return pd.DataFrame(
            columns=[*FLOW_COLUMNS, "close_time_ms"],
        )
    frame = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "open_time_dt": pd.to_datetime(frame[0].astype("int64"), unit="ms", utc=True),
            "open": pd.to_numeric(frame[1], errors="raise"),
            "high": pd.to_numeric(frame[2], errors="raise"),
            "low": pd.to_numeric(frame[3], errors="raise"),
            "close": pd.to_numeric(frame[4], errors="raise"),
            "volume": pd.to_numeric(frame[5], errors="raise"),
            "quote_volume": pd.to_numeric(frame[7], errors="raise"),
            "count": pd.to_numeric(frame[8], errors="raise").astype("int64"),
            "taker_buy_volume": pd.to_numeric(frame[9], errors="raise"),
            "taker_buy_quote_volume": pd.to_numeric(frame[10], errors="raise"),
            "close_time_ms": frame[6].astype("int64"),
        },
    )


def load_warmup_frame(
    symbol: str,
    warmup_days: int,
    cache: Path,
    now: datetime | None = None,
) -> pd.DataFrame:
    if warmup_days < 7:
        raise ValueError("warmup_days must be at least 7")
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    current_open = pd.Timestamp(now_utc).floor("min")
    last_complete_open = current_open - pd.Timedelta(minutes=1)
    archive_end = now_utc.date() - timedelta(days=1)
    archive_start = archive_end - timedelta(days=warmup_days + 1)
    archived = load_range_flow(symbol, archive_start, archive_end, cache)

    last_archived_open = pd.Timestamp(archived["open_time_dt"].iloc[-1])
    recent_start = int((last_archived_open + pd.Timedelta(minutes=1)).timestamp() * 1000)
    recent_end = int(last_complete_open.timestamp() * 1000)
    recent = (
        _recent_one_minute_klines(symbol, recent_start, recent_end)
        if recent_start <= recent_end
        else pd.DataFrame()
    )
    if not recent.empty:
        # A REST response can contain the currently forming candle when the
        # local clock moves during pagination. Keep completed bars only.
        now_ms = int(pd.Timestamp(datetime.now(UTC)).timestamp() * 1000)
        recent = recent.loc[recent["close_time_ms"] < now_ms].drop(columns=["close_time_ms"])

    combined = pd.concat([archived, recent], ignore_index=True)
    combined = combined.drop_duplicates("open_time_dt", keep="last").sort_values("open_time_dt")
    cutoff = pd.Timestamp(now_utc) - pd.Timedelta(days=warmup_days)
    combined = combined.loc[combined["open_time_dt"] >= cutoff].reset_index(drop=True)
    if combined.empty:
        raise RuntimeError(f"empty warmup data for {symbol}")
    expected_latest = last_complete_open
    actual_latest = pd.Timestamp(combined["open_time_dt"].iloc[-1])
    if actual_latest < expected_latest:
        raise RuntimeError(
            f"warmup data for {symbol} is stale: {actual_latest} < {expected_latest}",
        )
    return combined[FLOW_COLUMNS]


def _candles(
    frame: pd.DataFrame,
    symbol: str,
    timeframe: int,
    now: datetime,
) -> list[Candle | FlowCandle]:
    if timeframe == 1:
        closed = wrangler_flow_frame(frame)
        latest_complete_close = pd.Timestamp(now).floor("1min")
        closed = closed.loc[closed.index <= latest_complete_close]
        return [
            FlowCandle(
                ts_close_ns=int(row.Index.value),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                quote_volume=float(row.quote_volume),
                trade_count=int(row.count),
                taker_buy_base_volume=float(row.taker_buy_volume),
                taker_buy_quote_volume=float(row.taker_buy_quote_volume),
            )
            for row in closed.itertuples()
        ]
    sampled = resample(frame, timeframe)
    closed = wrangler_frame(sampled, timeframe)
    latest_complete_close = pd.Timestamp(now).floor(f"{timeframe}min")
    closed = closed.loc[closed.index <= latest_complete_close]
    return [
        Candle(
            symbol=symbol,
            timeframe_minutes=timeframe,
            ts_close_ns=int(row.Index.value),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in closed.itertuples()
    ]


def build_warmup_map(
    symbols_by_instrument: dict[InstrumentId, str],
    warmup_days: int,
    cache: Path,
) -> WarmupMap:
    now = datetime.now(UTC)
    output: WarmupMap = {}
    for instrument_id, symbol in symbols_by_instrument.items():
        frame = load_warmup_frame(symbol, warmup_days, cache, now=now)
        output[str(instrument_id)] = {
            timeframe: _candles(frame, symbol, timeframe, now)
            for timeframe in (60, 15, 5, 1)
        }
    return output


class EasyChartRE1PaperStrategy(EasyChartRE1Strategy):
    """Canonical strategy with warmup replay and coherent live bar routing."""

    def __init__(self, config: EasyChartMTFConfig, warmup: WarmupMap) -> None:
        super().__init__(config)
        self._warmup = warmup
        self._warmup_last_ts: dict[tuple[InstrumentId, int], int] = {}
        self._pending_live: dict[int, list[tuple[InstrumentId, int, Bar]]] = defaultdict(list)
        self._pending_live_seen: dict[int, set[tuple[InstrumentId, int]]] = defaultdict(set)
        self._last_live_processed_ts = 0
        self._live_data_halted = False

    def _preload_warmup(self) -> None:
        events: list[tuple[int, int, str, Candle | FlowCandle]] = []
        for instrument_id in self.config.instrument_ids:
            by_timeframe = self._warmup.get(str(instrument_id))
            if not by_timeframe:
                raise RuntimeError(f"missing warmup series for {instrument_id}")
            for timeframe, candles in by_timeframe.items():
                for candle in candles:
                    events.append((candle.ts_close_ns, -timeframe, str(instrument_id), candle))
        for _, negative_timeframe, instrument_text, candle in sorted(events):
            timeframe = -negative_timeframe
            instrument_id = InstrumentId.from_str(instrument_text)
            engine = self.scenario_engines[instrument_id]
            engine.on_bar(timeframe, candle)
            engine.drain_trace()
            self._warmup_last_ts[(instrument_id, timeframe)] = candle.ts_close_ns
        self._record(
            "paper_warmup_complete",
            candles=len(events),
            instruments=len(self.config.instrument_ids),
            earliest_ts_ns=min(item[0] for item in events),
            latest_ts_ns=max(item[0] for item in events),
        )
        # Release bulky raw warmup lists after state construction.
        self._warmup = {}

    def on_start(self) -> None:
        super().on_start()
        self._preload_warmup()

    def _halt_for_incomplete_market_view(self, earliest_ts: int, latest_ts: int) -> None:
        if self._live_data_halted:
            return
        self._live_data_halted = True
        expected = self._expected_bucket_count(earliest_ts)
        actual = len(self._pending_live.get(earliest_ts, ()))
        self._record(
            "live_bar_coherence_fault",
            earliest_incomplete_ts_ns=earliest_ts,
            latest_seen_ts_ns=latest_ts,
            expected_bars=expected,
            actual_bars=actual,
        )
        for instrument_id in self.config.instrument_ids:
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)

    def _drain_live_buckets(self) -> None:
        while self._pending_live:
            earliest_ts = min(self._pending_live)
            expected = self._expected_bucket_count(earliest_ts)
            bucket = self._pending_live[earliest_ts]
            if len(bucket) < expected:
                latest_ts = max(self._pending_live)
                if latest_ts - earliest_ts > LIVE_REORDER_GRACE_NS:
                    self._halt_for_incomplete_market_view(earliest_ts, latest_ts)
                return
            if len(bucket) > expected:
                self._halt_for_incomplete_market_view(earliest_ts, earliest_ts)
                return
            self.bar_bucket_ts = earliest_ts
            self.bar_bucket = bucket
            self.bar_bucket_seen = self._pending_live_seen[earliest_ts]
            del self._pending_live[earliest_ts]
            del self._pending_live_seen[earliest_ts]
            self._flush_bar_bucket()
            self._last_live_processed_ts = earliest_ts

    def on_bar(self, bar: Bar) -> None:
        if self._live_data_halted:
            return
        timeframe = self.timeframe_by_bar_type.get(bar.bar_type)
        if timeframe is None:
            return
        instrument_id = bar.bar_type.instrument_id
        if bar.ts_event <= self._warmup_last_ts.get((instrument_id, timeframe), 0):
            self._record(
                "live_bar_skipped_warmup_duplicate",
                instrument_id=str(instrument_id),
                timeframe_minutes=timeframe,
                bar_ts_ns=bar.ts_event,
            )
            return
        if bar.ts_event <= self._last_live_processed_ts:
            self._halt_for_incomplete_market_view(bar.ts_event, self._last_live_processed_ts)
            return
        key = (instrument_id, timeframe)
        if key in self._pending_live_seen[bar.ts_event]:
            self._record(
                "live_bar_duplicate",
                instrument_id=str(instrument_id),
                timeframe_minutes=timeframe,
                bar_ts_ns=bar.ts_event,
            )
            return
        self._pending_live_seen[bar.ts_event].add(key)
        self._pending_live[bar.ts_event].append((instrument_id, timeframe, bar))
        self._drain_live_buckets()


__all__ = [
    "EasyChartRE1PaperStrategy",
    "WarmupMap",
    "build_warmup_map",
    "load_warmup_frame",
]
