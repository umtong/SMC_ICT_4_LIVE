"""Causal 10-second event data built from verified Binance aggTrades.

The module creates external 10-second bars, lagged same-phase event features and
one real execution tick per 10-second bucket.  It does not match orders, maintain
positions, compute PnL or expose future trades to the strategy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler

from features import _maker_mask
from features import _agg_reader
from smc_ict_4.manifest import write_json_atomic


BUCKET_NS = 10_000_000_000
MODELED_ORDER_LATENCY_NS = 250_000_000
EXECUTION_OFFSET_NS = 300_000_000
SAFETY_MARGIN_NS = EXECUTION_OFFSET_NS - MODELED_ORDER_LATENCY_NS


class EventTimeDataError(RuntimeError):
    """Raised when 10-second event data cannot be trusted."""


@dataclass(frozen=True, slots=True)
class EventTimeCatalogResult:
    feature_path: Path
    bar_count: int
    execution_tick_count: int
    first_bar_ts_event: int
    last_bar_ts_event: int
    first_tick_ts_event: int
    last_tick_ts_event: int
    boundary_rows: int
    ready_boundary_rows: int
    latency_ready_ticks: int
    fallback_ticks: int


def _timestamp_ns(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    if numeric.empty:
        return numeric
    factor = 1_000 if int(numeric.iloc[0]) > 10**14 else 1_000_000
    return numeric * factor


def _trade_rows(chunk: pd.DataFrame) -> pd.DataFrame:
    ts_ns = _timestamp_ns(chunk["transact_time"])
    price = pd.to_numeric(chunk["price"], errors="raise").astype(float)
    quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
    maker = _maker_mask(chunk["is_buyer_maker"])
    notional = price * quantity
    signed = np.where(
        maker.to_numpy(),
        -notional.to_numpy(),
        notional.to_numpy(),
    )
    rows = pd.DataFrame(
        {
            "ts_ns": ts_ns.to_numpy(dtype="int64"),
            "bucket_ns": ((ts_ns // BUCKET_NS) * BUCKET_NS).to_numpy(
                dtype="int64",
            ),
            "price": price.to_numpy(dtype=float),
            "quantity": quantity.to_numpy(dtype=float),
            "notional": notional.to_numpy(dtype=float),
            "signed_notional": signed,
            "trade_id": pd.to_numeric(
                chunk["agg_trade_id"],
                errors="raise",
            ).astype("int64").astype(str).to_numpy(),
            "buyer_maker": maker.to_numpy(dtype=bool),
        },
    )
    rows = rows[
        np.isfinite(rows["price"])
        & np.isfinite(rows["quantity"])
        & rows["price"].gt(0.0)
        & rows["quantity"].gt(0.0)
    ]
    return rows.sort_values("ts_ns").reset_index(drop=True)


def _aggregate_trade_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Aggregate ordered trade rows without crossing 10-second buckets."""
    if rows.empty:
        return pd.DataFrame()
    grouped = rows.groupby("bucket_ns", sort=True).agg(
        first_ts=("ts_ns", "first"),
        last_ts=("ts_ns", "last"),
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("quantity", "sum"),
        notional=("notional", "sum"),
        signed_notional=("signed_notional", "sum"),
        trade_count=("price", "size"),
    )
    return grouped.reset_index()


def _merge_bar_pieces(pieces: list[pd.DataFrame]) -> pd.DataFrame:
    if not pieces:
        raise EventTimeDataError("aggTrades produced no 10-second bars")
    combined = pd.concat(pieces, ignore_index=True)
    combined = combined.sort_values(["bucket_ns", "first_ts"])
    merged = combined.groupby("bucket_ns", sort=True).agg(
        first_ts=("first_ts", "first"),
        last_ts=("last_ts", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        notional=("notional", "sum"),
        signed_notional=("signed_notional", "sum"),
        trade_count=("trade_count", "sum"),
    )
    merged = merged.reset_index().sort_values("bucket_ns").reset_index(
        drop=True,
    )
    if merged["bucket_ns"].duplicated().any():
        raise EventTimeDataError("duplicate 10-second buckets after merge")
    if not merged["bucket_ns"].is_monotonic_increasing:
        raise EventTimeDataError("10-second buckets are not ordered")
    if (merged["first_ts"] > merged["last_ts"]).any():
        raise EventTimeDataError("bar trade timestamps are reversed")
    if (
        (merged["low"] > merged["high"])
        | (merged["open"] < merged["low"])
        | (merged["open"] > merged["high"])
        | (merged["close"] < merged["low"])
        | (merged["close"] > merged["high"])
    ).any():
        raise EventTimeDataError("aggregated 10-second OHLC is inconsistent")
    return merged


def _select_bucket_ticks(rows: pd.DataFrame) -> pd.DataFrame:
    """Select the first real trade after modeled latency in each 10s bucket."""
    if rows.empty:
        return pd.DataFrame()
    required = {
        "ts_ns",
        "bucket_ns",
        "price",
        "quantity",
        "trade_id",
        "buyer_maker",
    }
    missing = required.difference(rows.columns)
    if missing:
        raise EventTimeDataError(
            f"execution rows missing columns: {sorted(missing)}",
        )
    work = rows.copy()
    work["offset_ns"] = work["ts_ns"] - work["bucket_ns"]
    work["latency_ready"] = work["offset_ns"] >= EXECUTION_OFFSET_NS
    fallback = (
        work.sort_values("ts_ns")
        .drop_duplicates("bucket_ns", keep="first")
    )
    eligible = (
        work[work["latency_ready"]]
        .sort_values("ts_ns")
        .drop_duplicates("bucket_ns", keep="first")
    )
    selected = pd.concat([fallback, eligible], ignore_index=True)
    selected = (
        selected.sort_values(
            ["bucket_ns", "latency_ready", "ts_ns"],
            ascending=[True, False, True],
        )
        .drop_duplicates("bucket_ns", keep="first")
        .sort_values("ts_ns")
        .reset_index(drop=True)
    )
    if selected["bucket_ns"].duplicated().any():
        raise EventTimeDataError("duplicate execution tick buckets")
    if selected["ts_ns"].duplicated().any():
        raise EventTimeDataError("duplicate execution tick timestamps")
    if not selected["ts_ns"].is_monotonic_increasing:
        raise EventTimeDataError("execution ticks are not ordered")
    return selected


def _merge_tick_pieces(pieces: list[pd.DataFrame]) -> pd.DataFrame:
    if not pieces:
        raise EventTimeDataError("aggTrades produced no execution ticks")
    return _select_bucket_ticks(pd.concat(pieces, ignore_index=True))


def _aggregate_archives(
    paths: list[Path],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    bar_pieces: list[pd.DataFrame] = []
    tick_pieces: list[pd.DataFrame] = []
    agg_paths = sorted(
        path
        for path in paths
        if path.suffix == ".zip" and "-aggTrades-" in path.name
    )
    if not agg_paths:
        raise EventTimeDataError("no Binance aggTrades archives supplied")

    for path in agg_paths:
        observed = False
        for chunk in _agg_reader(path):
            rows = _trade_rows(chunk)
            if rows.empty:
                continue
            observed = True
            bar_pieces.append(_aggregate_trade_rows(rows))
            tick_pieces.append(_select_bucket_ticks(rows))
        if not observed:
            raise EventTimeDataError(f"empty aggregate-trade archive {path}")

    bars = _merge_bar_pieces(bar_pieces)
    ticks = _merge_tick_pieces(tick_pieces)
    return bars, ticks


def _clock_boundary(bucket_ns: pd.Series, period_minutes: int) -> pd.Series:
    if period_minutes < 1 or 60 % period_minutes != 0:
        raise ValueError("period_minutes must be a positive divisor of 60")
    starts = pd.to_datetime(bucket_ns, unit="ns", utc=True)
    return starts.dt.second.eq(0) & starts.dt.minute.mod(period_minutes).eq(0)


def build_feature_frame(
    bars: pd.DataFrame,
    *,
    period_minutes: int,
    baseline_periods: int,
    min_baseline_samples: int,
) -> pd.DataFrame:
    """Build causal per-bar features and a lagged same-phase baseline."""
    if baseline_periods < 1:
        raise ValueError("baseline_periods must be positive")
    if not 1 <= min_baseline_samples <= baseline_periods:
        raise ValueError("min_baseline_samples must be within baseline_periods")
    frame = bars.copy()
    if frame.empty:
        raise EventTimeDataError("cannot build features from empty bars")
    frame["observed_time_ns"] = (
        frame["bucket_ns"].astype("int64") + BUCKET_NS - 1
    )
    finite_matrix = np.isfinite(
        frame[["open", "high", "low", "close", "notional"]]
        .to_numpy(dtype=float)
    ).all(axis=1)
    frame["feature_ready"] = (
        finite_matrix
        & frame["notional"].gt(0.0).to_numpy(dtype=bool)
    )
    frame["flow_10s"] = (
        frame["signed_notional"]
        / frame["notional"].replace(0.0, np.nan)
    )
    frame["return_10s_bps"] = (
        np.log(frame["close"] / frame["open"]) * 10_000.0
    )
    frame["range_10s_bps"] = (
        np.log(frame["high"] / frame["low"]) * 10_000.0
    )
    frame["efficiency_10s"] = (
        frame["return_10s_bps"].abs()
        / frame["range_10s_bps"].replace(0.0, np.nan)
    ).clip(0.0, 1.0)
    boundary = _clock_boundary(
        frame["bucket_ns"],
        period_minutes,
    )
    frame["event_boundary"] = boundary
    frame["event_phase_sample_count"] = 0.0
    frame["event_notional_baseline"] = np.nan
    frame["event_phase_burst"] = np.nan

    boundary_rows = frame.loc[boundary, ["notional"]].copy()
    current = pd.to_numeric(
        boundary_rows["notional"],
        errors="coerce",
    )
    lagged = current.shift(1)
    baseline = lagged.rolling(
        baseline_periods,
        min_periods=1,
    ).median()
    sample_count = lagged.rolling(
        baseline_periods,
        min_periods=1,
    ).count()
    burst = current / baseline.replace(0.0, np.nan)
    frame.loc[boundary, "event_phase_sample_count"] = (
        sample_count.to_numpy(dtype=float)
    )
    frame.loc[boundary, "event_notional_baseline"] = (
        baseline.to_numpy(dtype=float)
    )
    frame.loc[boundary, "event_phase_burst"] = burst.to_numpy(dtype=float)
    frame["event_feature_ready"] = (
        boundary
        & frame["feature_ready"]
        & frame["event_phase_sample_count"].ge(
            float(min_baseline_samples),
        )
        & np.isfinite(frame["event_phase_burst"])
        & np.isfinite(frame["flow_10s"])
    )

    columns = [
        "observed_time_ns",
        "feature_ready",
        "event_boundary",
        "event_feature_ready",
        "bucket_ns",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "notional",
        "signed_notional",
        "trade_count",
        "flow_10s",
        "return_10s_bps",
        "range_10s_bps",
        "efficiency_10s",
        "event_phase_sample_count",
        "event_notional_baseline",
        "event_phase_burst",
    ]
    result = frame[columns].copy()
    if result["observed_time_ns"].duplicated().any():
        raise EventTimeDataError("duplicate feature observation timestamps")
    if not result["observed_time_ns"].is_monotonic_increasing:
        raise EventTimeDataError("feature observations are not ordered")
    return result


def _bar_frame(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars[["open", "high", "low", "close", "volume"]].astype(float)
    close_ns = bars["bucket_ns"].astype("int64") + BUCKET_NS - 1
    frame.index = pd.to_datetime(close_ns, unit="ns", utc=True)
    frame.index.name = "timestamp"
    return frame


def _tick_frame(ticks: pd.DataFrame) -> pd.DataFrame:
    frame = ticks[
        ["price", "quantity", "trade_id", "buyer_maker"]
    ].copy()
    frame.index = pd.to_datetime(
        ticks["ts_ns"].astype("int64"),
        unit="ns",
        utc=True,
    )
    frame.index.name = "timestamp"
    return frame


def write_event_time_catalog(
    *,
    raw_files: list[Path],
    catalog_path: Path,
    instrument: Any,
    bar_type: BarType,
    output: Path,
    period_minutes: int,
    baseline_periods: int,
    min_baseline_samples: int,
) -> EventTimeCatalogResult:
    """Write causal 10-second bars, real execution ticks and feature evidence."""
    bars_frame, selected_ticks = _aggregate_archives(raw_files)
    features = build_feature_frame(
        bars_frame,
        period_minutes=period_minutes,
        baseline_periods=baseline_periods,
        min_baseline_samples=min_baseline_samples,
    )
    feature_path = output / "features_10s.csv.gz"
    features.to_csv(
        feature_path,
        index=False,
        compression="gzip",
    )

    bars = BarDataWrangler(bar_type, instrument).process(
        _bar_frame(bars_frame),
    )
    ticks = TradeTickDataWrangler(instrument).process(
        _tick_frame(selected_ticks),
        ts_init_delta=0,
    )
    if not bars:
        raise EventTimeDataError("BarDataWrangler produced no 10-second bars")
    if not ticks:
        raise EventTimeDataError("TradeTickDataWrangler produced no ticks")
    if any(
        int(left.ts_event) >= int(right.ts_event)
        for left, right in zip(bars, bars[1:])
    ):
        raise EventTimeDataError("10-second bar events are not strictly ordered")
    if any(
        int(left.ts_event) >= int(right.ts_event)
        for left, right in zip(ticks, ticks[1:])
    ):
        raise EventTimeDataError("execution tick events are not strictly ordered")

    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data([instrument])
    catalog.write_data(bars)
    catalog.write_data(ticks)

    ready = features["event_feature_ready"]
    boundary = features["event_boundary"]
    result = EventTimeCatalogResult(
        feature_path=feature_path,
        bar_count=len(bars),
        execution_tick_count=len(ticks),
        first_bar_ts_event=int(bars[0].ts_event),
        last_bar_ts_event=int(bars[-1].ts_event),
        first_tick_ts_event=int(ticks[0].ts_event),
        last_tick_ts_event=int(ticks[-1].ts_event),
        boundary_rows=int(boundary.sum()),
        ready_boundary_rows=int(ready.sum()),
        latency_ready_ticks=int(selected_ticks["latency_ready"].sum()),
        fallback_ticks=int((~selected_ticks["latency_ready"]).sum()),
    )
    evidence = {
        "schema": "candidate-21-event-time-data-v1",
        "bar_type": str(bar_type),
        "bar_interval_seconds": 10,
        "bar_timestamp_semantics": (
            "last nanosecond of completed [bucket_start, bucket_start+10s)"
        ),
        "feature_observation_semantics": (
            "observed_time_ns equals completed 10-second bar ts_event"
        ),
        "baseline": (
            "lagged rolling median of prior quarter-hour first-10-second "
            "notional; current event excluded"
        ),
        "baseline_periods": baseline_periods,
        "minimum_baseline_samples": min_baseline_samples,
        "execution_tick_selection": (
            "first actual aggTrade at or after 300 ms in every 10-second "
            "bucket; fallback first actual trade if none"
        ),
        "modeled_order_latency_ns": MODELED_ORDER_LATENCY_NS,
        "execution_selection_offset_ns": EXECUTION_OFFSET_NS,
        "safety_margin_ns": SAFETY_MARGIN_NS,
        "actual_prices_only": True,
        "strategy_alpha_visibility_of_execution_ticks": False,
        "strictly_increasing_bars": True,
        "strictly_increasing_ticks": True,
        **{
            name: (
                str(value)
                if isinstance(value, Path)
                else value
            )
            for name, value in asdict(result).items()
        },
    }
    write_json_atomic(output / "event_time_data_contract.json", evidence)
    return result


__all__ = [
    "BUCKET_NS",
    "EXECUTION_OFFSET_NS",
    "MODELED_ORDER_LATENCY_NS",
    "SAFETY_MARGIN_NS",
    "EventTimeCatalogResult",
    "EventTimeDataError",
    "_aggregate_archives",
    "_aggregate_trade_rows",
    "_merge_bar_pieces",
    "_select_bucket_ticks",
    "build_feature_frame",
    "write_event_time_catalog",
]
