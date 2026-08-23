from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from .binance_public import PublicBinanceClient
from .event_store import EventStore

MINUTE_MS = 60_000
DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class RefreshResult:
    server_time_ms: int
    start_time_ms: int
    end_time_ms: int
    inserted_or_updated: dict[str, int]
    optional_metrics_rows: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_time_ms": self.server_time_ms,
            "start_time_ms": self.start_time_ms,
            "end_time_ms": self.end_time_ms,
            "inserted_or_updated": dict(self.inserted_or_updated),
            "optional_metrics_rows": dict(self.optional_metrics_rows),
        }


class MarketRepository:
    def __init__(self, store: EventStore, client: PublicBinanceClient) -> None:
        self.store = store
        self.client = client

    @staticmethod
    def completed_minute_end(server_time_ms: int) -> int:
        return int(server_time_ms // MINUTE_MS * MINUTE_MS - 1)

    def refresh(
        self,
        symbols: tuple[str, ...],
        *,
        initial_backfill_days: int,
        overlap_minutes: int = 10,
    ) -> RefreshResult:
        server_time = self.client.server_time_ms()
        end_time = self.completed_minute_end(server_time)
        inserted: dict[str, int] = {}
        metrics_count: dict[str, int] = {}
        starts: list[int] = []
        for symbol in symbols:
            for stream in ("futures", "mark", "index"):
                latest = self.store.latest_market_open_ms(stream, symbol)
                if latest is None:
                    start = end_time - int(initial_backfill_days * DAY_MS)
                else:
                    start = max(0, latest - overlap_minutes * MINUTE_MS)
                starts.append(start)
                rows = self.client.klines(
                    symbol,
                    stream=stream,
                    start_time_ms=start,
                    end_time_ms=end_time,
                    server_time_ms=server_time,
                )
                inserted[f"{stream}:{symbol}"] = self.store.put_market_rows(stream, symbol, rows)

            # Five-minute positioning data has a shorter public retention horizon.
            metric_start = max(end_time - 30 * DAY_MS, min(starts[-3:]))
            metrics = self.client.metric_series(
                symbol,
                start_time_ms=metric_start,
                end_time_ms=end_time,
            )
            normalized = []
            for row in metrics:
                timestamp = int(row["timestamp_ms"])
                normalized.append(
                    {
                        "open_time_ms": timestamp,
                        "close_time_ms": timestamp + 5 * MINUTE_MS - 1,
                        **row,
                    }
                )
            metrics_count[symbol] = self.store.put_market_rows("metrics", symbol, normalized)
        result = RefreshResult(
            server_time_ms=server_time,
            start_time_ms=min(starts) if starts else end_time,
            end_time_ms=end_time,
            inserted_or_updated=inserted,
            optional_metrics_rows=metrics_count,
        )
        self.store.set_checkpoint("market_refresh", result.to_dict())
        return result

    def window_rows(
        self,
        symbol: str,
        *,
        stream: str,
        end_time_ms: int,
        days: int,
    ) -> list[dict[str, Any]]:
        start = end_time_ms - days * DAY_MS
        return self.store.market_rows(
            stream,
            symbol,
            start_open_time_ms=start,
            end_close_time_ms=end_time_ms,
        )

    def verify_continuity(
        self,
        symbol: str,
        *,
        stream: str,
        end_time_ms: int,
        days: int,
        maximum_missing_fraction: float = 0.002,
    ) -> dict[str, Any]:
        rows = self.window_rows(symbol, stream=stream, end_time_ms=end_time_ms, days=days)
        if not rows:
            raise RuntimeError(f"no cached {stream} rows for {symbol}")
        opens = sorted(int(row["open_time_ms"]) for row in rows)
        expected = max(1, int((opens[-1] - opens[0]) // MINUTE_MS) + 1)
        unique = len(set(opens))
        missing = max(0, expected - unique)
        fraction = missing / expected
        if stream in {"futures", "mark", "index"} and fraction > maximum_missing_fraction:
            raise RuntimeError(
                f"{stream} continuity is too weak for {symbol}: missing={missing}/{expected} ({fraction:.4%})"
            )
        return {
            "symbol": symbol,
            "stream": stream,
            "rows": len(rows),
            "unique_rows": unique,
            "expected_rows": expected,
            "missing_rows": missing,
            "missing_fraction": fraction,
            "first_open_time_ms": opens[0],
            "last_open_time_ms": opens[-1],
        }
