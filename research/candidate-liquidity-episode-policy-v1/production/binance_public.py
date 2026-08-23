from __future__ import annotations

from dataclasses import dataclass
import json
import random
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


class PublicDataError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PublicClientConfig:
    base_url: str = "https://fapi.binance.com"
    timeout_seconds: float = 12.0
    retries: int = 4
    user_agent: str = "smc-ict-episode-policy-production/1"


class PublicBinanceClient:
    """Small fail-closed USD-M Futures REST client for closed public data only."""

    def __init__(self, config: PublicClientConfig | None = None) -> None:
        self.config = config or PublicClientConfig()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
        url = self.config.base_url.rstrip("/") + path + (f"?{query}" if query else "")
        last_error: BaseException | None = None
        for attempt in range(self.config.retries):
            request = Request(url, headers={"User-Agent": self.config.user_agent, "Accept": "application/json"})
            try:
                with urlopen(request, timeout=self.config.timeout_seconds) as response:
                    if response.status != 200:
                        raise PublicDataError(f"HTTP {response.status} for {path}")
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, PublicDataError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError) and exc.code in {400, 401, 403, 404}:
                    break
                if attempt + 1 < self.config.retries:
                    time.sleep(min(8.0, (2.0 ** attempt) + random.random()))
        raise PublicDataError(f"public Binance request failed: {path}: {last_error}")

    def server_time_ms(self) -> int:
        payload = self._get("/fapi/v1/time")
        value = int(payload["serverTime"])
        if value <= 0:
            raise PublicDataError("invalid Binance server time")
        return value

    def exchange_info(self) -> dict[str, Any]:
        payload = self._get("/fapi/v1/exchangeInfo")
        if not isinstance(payload, dict) or "symbols" not in payload:
            raise PublicDataError("invalid exchangeInfo response")
        return payload

    @staticmethod
    def _endpoint(stream: str) -> str:
        endpoints = {
            "futures": "/fapi/v1/klines",
            "mark": "/fapi/v1/markPriceKlines",
            "index": "/fapi/v1/indexPriceKlines",
        }
        try:
            return endpoints[stream]
        except KeyError as exc:
            raise ValueError(f"unsupported kline stream: {stream}") from exc

    def klines(
        self,
        symbol: str,
        *,
        stream: str = "futures",
        interval: str = "1m",
        start_time_ms: int,
        end_time_ms: int,
        server_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if end_time_ms <= start_time_ms:
            return []
        endpoint = self._endpoint(stream)
        cursor = int(start_time_ms)
        hard_end = int(end_time_ms)
        server_now = int(server_time_ms or self.server_time_ms())
        output: dict[int, dict[str, Any]] = {}
        while cursor < hard_end:
            payload = self._get(
                endpoint,
                {
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": cursor,
                    "endTime": hard_end,
                    "limit": 1500,
                },
            )
            if not isinstance(payload, list):
                raise PublicDataError(f"invalid {stream} kline response for {symbol}")
            if not payload:
                break
            for raw in payload:
                row = self._normalize_kline(symbol, stream, raw)
                # A live/open candle is never admitted to the causal state.
                if row["close_time_ms"] >= server_now:
                    continue
                output[int(row["open_time_ms"])] = row
            last_open = int(payload[-1][0])
            next_cursor = last_open + 60_000
            if next_cursor <= cursor:
                raise PublicDataError(f"non-advancing {stream} pagination for {symbol}")
            cursor = next_cursor
            if len(payload) < 1500:
                break
        return [output[key] for key in sorted(output)]

    @staticmethod
    def _normalize_kline(symbol: str, stream: str, raw: list[Any]) -> dict[str, Any]:
        if len(raw) < 7:
            raise PublicDataError(f"short {stream} kline row for {symbol}")
        # Futures klines carry quote volume, trade count and taker volume. Mark/index
        # klines do not; unavailable fields remain null rather than fabricated zeros.
        futures = stream == "futures"
        return {
            "symbol": symbol,
            "stream": stream,
            "open_time_ms": int(raw[0]),
            "open": float(raw[1]),
            "high": float(raw[2]),
            "low": float(raw[3]),
            "close": float(raw[4]),
            "volume": float(raw[5]) if raw[5] not in (None, "") else None,
            "close_time_ms": int(raw[6]),
            "quote_volume": float(raw[7]) if futures and len(raw) > 7 else None,
            "count": int(raw[8]) if futures and len(raw) > 8 else None,
            "taker_buy_volume": float(raw[9]) if futures and len(raw) > 9 else None,
            "taker_buy_quote_volume": float(raw[10]) if futures and len(raw) > 10 else None,
        }

    def metric_series(
        self,
        symbol: str,
        *,
        start_time_ms: int,
        end_time_ms: int,
        period: str = "5m",
    ) -> list[dict[str, Any]]:
        """Fetch public OI and positioning series; absence is recorded explicitly."""
        endpoints = {
            "oi": "/futures/data/openInterestHist",
            "taker": "/futures/data/takerlongshortRatio",
            "top_position": "/futures/data/topLongShortPositionRatio",
            "all_account": "/futures/data/globalLongShortAccountRatio",
        }
        series: dict[int, dict[str, Any]] = {}
        for name, endpoint in endpoints.items():
            cursor = int(start_time_ms)
            while cursor < end_time_ms:
                try:
                    payload = self._get(
                        endpoint,
                        {
                            "symbol": symbol,
                            "period": period,
                            "startTime": cursor,
                            "endTime": int(end_time_ms),
                            "limit": 500,
                        },
                    )
                except PublicDataError:
                    # The policy remains causal when an optional metric endpoint is
                    # unavailable. It is surfaced in evidence and never forward-filled
                    # from the future.
                    break
                if not isinstance(payload, list) or not payload:
                    break
                for raw in payload:
                    timestamp = int(raw["timestamp"])
                    row = series.setdefault(timestamp, {"timestamp_ms": timestamp})
                    if name == "oi":
                        row["sum_open_interest"] = float(raw.get("sumOpenInterest", "nan"))
                        row["sum_open_interest_value"] = float(raw.get("sumOpenInterestValue", "nan"))
                    else:
                        row[f"{name}_long_short_ratio"] = float(raw.get("longShortRatio", "nan"))
                        if "buySellRatio" in raw:
                            row[f"{name}_buy_sell_ratio"] = float(raw["buySellRatio"])
                next_cursor = int(payload[-1]["timestamp"]) + 1
                if next_cursor <= cursor:
                    break
                cursor = next_cursor
                if len(payload) < 500:
                    break
        return [series[key] for key in sorted(series)]


def kline_rows_to_frame(rows: Iterable[dict[str, Any]], *, require_flow: bool) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return frame
    frame = frame.sort_values("open_time_ms").drop_duplicates("open_time_ms", keep="last")
    frame["open_time_dt"] = pd.to_datetime(frame["open_time_ms"], unit="ms", utc=True)
    if require_flow:
        required = [
            "open", "high", "low", "close", "volume", "quote_volume", "count",
            "taker_buy_volume", "taker_buy_quote_volume",
        ]
        if frame[required].isna().any().any():
            missing = frame[required].columns[frame[required].isna().any()].tolist()
            raise PublicDataError(f"futures flow fields missing: {missing}")
        frame["count"] = frame["count"].astype("int64")
    return frame


def metric_rows_to_frame(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return frame
    frame = frame.sort_values("timestamp_ms").drop_duplicates("timestamp_ms", keep="last")
    frame.index = pd.to_datetime(frame.pop("timestamp_ms"), unit="ms", utc=True)
    return frame
