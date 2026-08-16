"""Flow-preserving warm start and fail-closed paper runtime for RE1.

The historical and live decision path must expose the same Binance kline fields:
quote volume, trade count, taker-buy base volume and taker-buy quote volume.
This module keeps those fields through REST catch-up, multi-timeframe warmup and
live ``BinanceBar`` conversion.

Restart policy is deliberately conservative.  Scenario state is reconstructed
from closed candles, but an old venue position or open order cannot be mapped
back to an immutable plan after process loss.  After Nautilus reconciliation,
any such exposure on the fixed four instruments is canceled and flattened; the
strategy remains halted until the operator verifies flat state and restarts.
No new trade is allowed from a partially reconstructed account.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
import urllib.parse

import pandas as pd

from nautilus_trader.model.identifiers import InstrumentId

from data_re1_flow import FLOW_COLUMNS, load_range_flow
from easychart_re1_flow import FlowCandle
from execution_re1_flow import EasyChartRE1FlowStrategy
from paper_re1 import (
    PUBLIC_FUTURES_KLINES,
    WarmupMap,
    _request_json,
)
from paper_re1_fixed import EasyChartRE1CoherentPaperStrategy


FLOW_PAPER_DATA_RULE = (
    "EXTERNAL_METHOD:BINANCE_ARCHIVE_REST_AND_LIVE_KLINES_PRESERVE_IDENTICAL_"
    "QUOTE_VOLUME_TRADE_COUNT_AND_TAKER_BUY_FIELDS"
)
RESTART_RECONCILIATION_RULE = (
    "EXTERNAL_METHOD:AFTER_VENUE_RECONCILIATION_UNKNOWN_OPEN_ORDERS_OR_"
    "POSITIONS_ARE_CANCELLED_AND_FLATTENED_BEFORE_ANY_NEW_DECISION"
)


def _empty_flow_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[*FLOW_COLUMNS, "close_time_ms"])


def _recent_one_minute_flow_klines(
    symbol: str,
    start_open_ms: int,
    end_open_ms: int,
) -> pd.DataFrame:
    """Fetch completed recent Binance USD-M klines with exact flow fields."""
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
            raise RuntimeError(
                f"unexpected Binance kline response for {symbol}: {batch!r}",
            )
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
        return _empty_flow_frame()
    raw = pd.DataFrame(rows)
    output = pd.DataFrame(
        {
            "open_time_dt": pd.to_datetime(
                raw[0].astype("int64"),
                unit="ms",
                utc=True,
            ),
            "open": pd.to_numeric(raw[1], errors="raise"),
            "high": pd.to_numeric(raw[2], errors="raise"),
            "low": pd.to_numeric(raw[3], errors="raise"),
            "close": pd.to_numeric(raw[4], errors="raise"),
            "volume": pd.to_numeric(raw[5], errors="raise"),
            "close_time_ms": raw[6].astype("int64"),
            "quote_volume": pd.to_numeric(raw[7], errors="raise"),
            "count": pd.to_numeric(raw[8], errors="raise").astype("int64"),
            "taker_buy_volume": pd.to_numeric(raw[9], errors="raise"),
            "taker_buy_quote_volume": pd.to_numeric(raw[10], errors="raise"),
        },
    )
    return output.sort_values("open_time_dt").reset_index(drop=True)


def load_flow_warmup_frame(
    symbol: str,
    warmup_days: int,
    cache: Path,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Join checksum-verified archive data with completed REST catch-up bars."""
    if warmup_days < 7:
        raise ValueError("warmup_days must be at least 7")
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    current_open = pd.Timestamp(now_utc).floor("min")
    last_complete_open = current_open - pd.Timedelta(minutes=1)
    archive_end = now_utc.date() - timedelta(days=1)
    archive_start = archive_end - timedelta(days=warmup_days + 1)
    archived = load_range_flow(symbol, archive_start, archive_end, cache)

    last_archived_open = pd.Timestamp(archived["open_time_dt"].iloc[-1])
    recent_start = int(
        (last_archived_open + pd.Timedelta(minutes=1)).timestamp() * 1000,
    )
    recent_end = int(last_complete_open.timestamp() * 1000)
    recent = (
        _recent_one_minute_flow_klines(symbol, recent_start, recent_end)
        if recent_start <= recent_end
        else _empty_flow_frame()
    )
    if not recent.empty:
        # Pagination may cross into the currently forming minute.  Completion
        # time, not local arrival order, decides availability.
        now_ms = int(pd.Timestamp(datetime.now(UTC)).timestamp() * 1000)
        recent = recent.loc[recent["close_time_ms"] < now_ms].drop(
            columns=["close_time_ms"],
        )
    elif "close_time_ms" in recent:
        recent = recent.drop(columns=["close_time_ms"])

    combined = pd.concat([archived, recent], ignore_index=True)
    combined = (
        combined.drop_duplicates("open_time_dt", keep="last")
        .sort_values("open_time_dt")
        .reset_index(drop=True)
    )
    cutoff = pd.Timestamp(now_utc) - pd.Timedelta(days=warmup_days)
    combined = combined.loc[combined["open_time_dt"] >= cutoff].reset_index(drop=True)
    if combined.empty:
        raise RuntimeError(f"empty flow warmup data for {symbol}")
    if combined["open_time_dt"].duplicated().any():
        raise RuntimeError(f"duplicate flow warmup bars for {symbol}")
    expected_latest = last_complete_open
    actual_latest = pd.Timestamp(combined["open_time_dt"].iloc[-1])
    if actual_latest < expected_latest:
        raise RuntimeError(
            f"flow warmup data for {symbol} is stale: {actual_latest} < {expected_latest}",
        )
    return combined[FLOW_COLUMNS]


def resample_flow(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate the same closed one-minute flow facts used by live Binance bars."""
    if minutes == 1:
        return frame[FLOW_COLUMNS].copy()
    indexed = frame.set_index("open_time_dt")
    output = indexed.resample(
        f"{minutes}min",
        label="left",
        closed="left",
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        count=("count", "sum"),
        taker_buy_volume=("taker_buy_volume", "sum"),
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
    ).dropna()
    output["count"] = output["count"].astype("int64")
    return output.reset_index()


def _flow_candles(
    frame: pd.DataFrame,
    timeframe: int,
    now: datetime,
) -> list[FlowCandle]:
    sampled = resample_flow(frame, timeframe)
    sampled = sampled.copy()
    sampled.index = (
        pd.DatetimeIndex(sampled.pop("open_time_dt"))
        + pd.Timedelta(minutes=timeframe)
    )
    latest_complete_close = pd.Timestamp(now).floor(f"{timeframe}min")
    sampled = sampled.loc[sampled.index <= latest_complete_close]
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
        for row in sampled.itertuples()
    ]


def build_flow_warmup_map(
    symbols_by_instrument: dict[InstrumentId, str],
    warmup_days: int,
    cache: Path,
) -> WarmupMap:
    """Build 1m/5m/15m/60m warmup with flow fields intact."""
    now = datetime.now(UTC)
    output: WarmupMap = {}
    for instrument_id, symbol in symbols_by_instrument.items():
        frame = load_flow_warmup_frame(symbol, warmup_days, cache, now=now)
        output[str(instrument_id)] = {
            timeframe: _flow_candles(frame, timeframe, now)
            for timeframe in (60, 15, 5, 1)
        }
    return output


class EasyChartRE1FlowCoherentPaperStrategy(
    EasyChartRE1CoherentPaperStrategy,
):
    """Coherent paper execution with exact live and warmup aggressor flow."""

    @staticmethod
    def _candle(bar: Any) -> FlowCandle:
        return EasyChartRE1FlowStrategy._candle(bar)

    def on_start(self) -> None:
        super().on_start()
        open_orders: list[Any] = []
        open_positions: list[Any] = []
        for instrument_id in self.config.instrument_ids:
            open_orders.extend(
                self.cache.orders_open(instrument_id=instrument_id),
            )
            open_positions.extend(
                self.cache.positions_open(instrument_id=instrument_id),
            )

        if not open_orders and not open_positions:
            self._record(
                "startup_reconciliation_flat",
                instruments=len(self.config.instrument_ids),
                data_rule=FLOW_PAPER_DATA_RULE,
                restart_rule=RESTART_RECONCILIATION_RULE,
            )
            return

        self._live_data_halted = True
        self._record(
            "startup_reconciliation_unknown_exposure",
            open_order_ids=[str(order.client_order_id) for order in open_orders],
            open_position_ids=[str(position.id) for position in open_positions],
            restart_rule=RESTART_RECONCILIATION_RULE,
        )
        for instrument_id in self.config.instrument_ids:
            self.cancel_all_orders(instrument_id)
            if not self.portfolio.is_flat(instrument_id):
                self.close_all_positions(instrument_id)
        self._record(
            "startup_reconciliation_flatten_requested_and_strategy_halted",
            restart_required_after_flat_verification=True,
            restart_rule=RESTART_RECONCILIATION_RULE,
        )


__all__ = [
    "EasyChartRE1FlowCoherentPaperStrategy",
    "FLOW_PAPER_DATA_RULE",
    "RESTART_RECONCILIATION_RULE",
    "build_flow_warmup_map",
    "load_flow_warmup_frame",
    "resample_flow",
]
