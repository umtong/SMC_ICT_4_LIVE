"""Causal sparse-archive adapter for optional Binance five-minute metrics.

Binance's public metrics history is not uniformly dense across old dates.  The
underlying checksum-verified day loader remains unchanged; this adapter only
regularizes what was actually published onto a five-minute clock.  Missing bins
remain NaN so merge-asof cannot silently carry an old positioning observation
through a long archive gap.  Price, volume, index and mark data therefore remain
usable when an optional derivatives metric is absent.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

import metrics_state as native


def load_range_metrics_sparse(
    symbol: str,
    start: date,
    end: date,
    cache: Path,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    day = start
    while day <= end:
        frames.append(native.load_day_metrics(symbol, day, cache))
        day += timedelta(days=1)
    if not frames:
        return pd.DataFrame(columns=["metric_time", *native.COLUMNS[2:]])

    observed = pd.concat(frames, ignore_index=True).sort_values("metric_time")
    observed = observed.drop_duplicates("metric_time", keep="last")
    observed["metric_time"] = pd.to_datetime(observed["metric_time"], utc=True)
    observed = observed.set_index("metric_time").sort_index()

    start_ts = pd.Timestamp(start, tz="UTC")
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    # Resampling both preserves real observations and inserts an explicit NaN row
    # into every unpublished five-minute bin.  The downstream one-sample shift
    # then keeps event availability causal.
    regular = observed.resample("5min").last()
    complete_index = pd.date_range(
        start=start_ts,
        end=end_exclusive,
        freq="5min",
        inclusive="left",
        tz="UTC",
    )
    # The repository's one-minute market state uses microsecond datetime storage;
    # pandas merge_asof now requires the physical datetime units to match exactly.
    complete_index = complete_index.as_unit("us")
    regular.index = pd.DatetimeIndex(regular.index).as_unit("us")
    regular = regular.reindex(complete_index)
    regular.index.name = "metric_time"
    return regular.reset_index()


__all__ = ["load_range_metrics_sparse"]
