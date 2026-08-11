"""Strict-as-of public TrendRider informative-timeframe context.

The external source uses each pair's completed 4h trend/ADX and completed daily
EMA200, plus BTC context.  This module downloads only public Binance USD-M
klines, computes the visible source indicators, writes a compact sidecar, and
serves the latest row whose close timestamp is not later than the decision.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time as time_module
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from router_picasso import BarObservation, _adx, _ema

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
BASE_URL = "https://fapi.binance.com/fapi/v1/klines"


@dataclass(frozen=True, slots=True)
class MTFObservation:
    observed_time_ns: int
    ready: bool
    daily_ema_200: float = math.nan
    pair_4h_is_bull: int = 0
    pair_4h_adx: float = math.nan


class MTFContextStore:
    def __init__(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(payload.get("schema_version") or 0) != 2:
            raise RuntimeError("invalid TrendRider MTF sidecar schema")
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._times: dict[str, list[int]] = {}
        for symbol in SYMBOLS:
            rows = list((payload.get("symbols") or {}).get(symbol) or [])
            times = [int(row["observed_time_ns"]) for row in rows]
            if not rows or any(right <= left for left, right in zip(times, times[1:])):
                raise RuntimeError(f"invalid or non-monotonic MTF rows for {symbol}")
            self._rows[symbol] = rows
            self._times[symbol] = times
        self.metadata = payload.get("metadata") or {}

    def observation(self, symbol: str, ts_event: int) -> MTFObservation:
        times = self._times.get(symbol)
        rows = self._rows.get(symbol)
        if not times or not rows:
            return MTFObservation(0, False)
        index = bisect_right(times, int(ts_event)) - 1
        if index < 0:
            return MTFObservation(0, False)
        row = rows[index]
        observed = int(row["observed_time_ns"])
        if observed > int(ts_event):
            raise RuntimeError("future TrendRider MTF row reached the router")
        daily = _number(row.get("daily_ema_200"))
        adx = _number(row.get("pair_4h_adx"))
        ready = bool(row.get("ready")) and math.isfinite(daily) and math.isfinite(adx)
        return MTFObservation(
            observed_time_ns=observed,
            ready=ready,
            daily_ema_200=daily,
            pair_4h_is_bull=int(row.get("pair_4h_is_bull") or 0),
            pair_4h_adx=adx,
        )


_STORE: MTFContextStore | None = None


def configure_context(path: str | Path) -> None:
    global _STORE
    _STORE = MTFContextStore(path)


def context_observation(symbol: str, ts_event: int) -> MTFObservation:
    if _STORE is None:
        return MTFObservation(0, False)
    return _STORE.observation(symbol, ts_event)


def _number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _timestamp_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp() * 1000)


def _fetch_page(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    query = urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
            "limit": 1500,
        }
    )
    request = Request(
        f"{BASE_URL}?{query}",
        headers={"User-Agent": "SMC-ICT-4-candidate57/TrendRider-source-fidelity"},
    )
    error: Exception | None = None
    for attempt in range(6):
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310 - frozen Binance endpoint
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                raise RuntimeError(f"unexpected Binance response: {data}")
            return data
        except Exception as exc:  # pragma: no cover - network retry path
            error = exc
            time_module.sleep(min(2**attempt, 12))
    raise RuntimeError(f"Binance kline request failed for {symbol} {interval}: {error}")


def fetch_klines(symbol: str, interval: str, start: date, end: date) -> list[list[Any]]:
    start_ms = _timestamp_ms(start)
    end_ms = _timestamp_ms(end + timedelta(days=1)) - 1
    rows: list[list[Any]] = []
    cursor = start_ms
    while cursor <= end_ms:
        page = _fetch_page(symbol, interval, cursor, end_ms)
        if not page:
            break
        for row in page:
            open_time = int(row[0])
            close_time = int(row[6])
            if open_time < start_ms or open_time > end_ms:
                continue
            rows.append(row)
        next_cursor = int(page[-1][6]) + 1
        if next_cursor <= cursor:
            raise RuntimeError("Binance kline cursor did not advance")
        cursor = next_cursor
        if len(page) < 1500:
            break
    rows.sort(key=lambda row: int(row[0]))
    if any(int(right[0]) <= int(left[0]) for left, right in zip(rows, rows[1:])):
        raise RuntimeError(f"non-monotonic Binance klines for {symbol} {interval}")
    return rows


def _bars(rows: Iterable[list[Any]]) -> list[BarObservation]:
    return [
        BarObservation(
            ts_event=int(row[6]) * 1_000_000,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in rows
    ]


def _source_rows(symbol: str, start: date, end: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Public daily EMA200 needs a long causal seed.  280 calendar days gives
    # enough completed daily bars without fitting a lookback to outcomes.
    daily_start = start - timedelta(days=280)
    four_hour_start = start - timedelta(days=55)
    daily_raw = fetch_klines(symbol, "1d", daily_start, end)
    four_raw = fetch_klines(symbol, "4h", four_hour_start, end)
    daily = _bars(daily_raw)
    four = _bars(four_raw)
    daily_ema = _ema([float(bar.close) for bar in daily], 200)
    four_ema_50 = _ema([float(bar.close) for bar in four], 50)
    four_ema_200 = _ema([float(bar.close) for bar in four], 200)
    four_adx = _adx(four, 14)

    daily_points = [
        (int(bar.ts_event), float(daily_ema[index]))
        for index, bar in enumerate(daily)
        if math.isfinite(float(daily_ema[index]))
    ]
    four_points = [
        (
            int(bar.ts_event),
            int(
                float(bar.close) > float(four_ema_200[index])
                and float(four_ema_50[index]) > float(four_ema_200[index])
            ),
            float(four_adx[index]),
        )
        for index, bar in enumerate(four)
        if all(
            math.isfinite(float(value))
            for value in (four_ema_50[index], four_ema_200[index], four_adx[index])
        )
    ]
    if not daily_points or not four_points:
        raise RuntimeError(f"insufficient public informative history for {symbol}")

    boundaries = sorted(
        {
            timestamp
            for timestamp, *_ in four_points
            if _timestamp_ms(start) * 1_000_000 <= timestamp
            <= (_timestamp_ms(end + timedelta(days=1)) - 1) * 1_000_000
        }
    )
    daily_times = [item[0] for item in daily_points]
    four_times = [item[0] for item in four_points]
    output: list[dict[str, Any]] = []
    for timestamp in boundaries:
        daily_index = bisect_right(daily_times, timestamp) - 1
        four_index = bisect_right(four_times, timestamp) - 1
        if daily_index < 0 or four_index < 0:
            continue
        daily_ts, daily_value = daily_points[daily_index]
        four_ts, four_bull, adx_value = four_points[four_index]
        observed = max(int(daily_ts), int(four_ts))
        if observed > timestamp:
            raise RuntimeError("future informative timestamp generated")
        output.append(
            {
                "observed_time_ns": int(timestamp),
                "daily_observed_time_ns": int(daily_ts),
                "pair_4h_observed_time_ns": int(four_ts),
                "daily_ema_200": float(daily_value),
                "pair_4h_is_bull": int(four_bull),
                "pair_4h_adx": float(adx_value),
                "ready": True,
            }
        )
    if not output:
        raise RuntimeError(f"no aligned informative rows for {symbol}")
    raw_digest = hashlib.sha256(
        json.dumps(
            {"daily": daily_raw, "four_hour": four_raw},
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return output, {
        "daily_rows": len(daily_raw),
        "four_hour_rows": len(four_raw),
        "aligned_rows": len(output),
        "raw_sha256": raw_digest,
        "daily_seed_start": str(daily_start),
        "four_hour_seed_start": str(four_hour_start),
    }


def build_sidecar(path: str | Path, start: date, end: date) -> Path:
    destination = Path(path)
    symbols: dict[str, Any] = {}
    metadata: dict[str, Any] = {
        "source": BASE_URL,
        "source_semantics": "completed Binance USD-M 4h and 1d klines",
        "start": str(start),
        "end": str(end),
        "future_information_used": False,
        "daily_ema_period": 200,
        "pair_4h_ema_fast": 50,
        "pair_4h_ema_slow": 200,
        "pair_4h_adx_period": 14,
        "symbols": {},
    }
    for symbol in SYMBOLS:
        rows, record = _source_rows(symbol, start, end)
        symbols[symbol] = rows
        metadata["symbols"][symbol] = record
    payload = {
        "schema_version": 2,
        "metadata": metadata,
        "symbols": symbols,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    # Load it immediately to enforce the same monotonic/ready contract the
    # strategy will use.
    MTFContextStore(destination)
    return destination


__all__ = [
    "BASE_URL",
    "MTFContextStore",
    "MTFObservation",
    "SYMBOLS",
    "build_sidecar",
    "configure_context",
    "context_observation",
    "fetch_klines",
]
