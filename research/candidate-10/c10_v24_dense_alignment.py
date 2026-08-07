"""Causal dense-grid repair for v24 cross-market no-trade intervals.

A missing five-second aggregate-trade bucket is not missing market data: it
means that venue printed no trade in that interval.  The original v24 aligner
intersected non-empty spot/perpetual buckets and mislabeled the resulting 20
natural holes as data gaps.  This module creates the intended completed grid.

For a venue with no trade in [t-5s, t):

* OHLC equals the last price observed strictly before t;
* quote volume, taker-buy quote and trade count are zero;
* the source timestamp remains the last actual trade timestamp;
* no future trade is used.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Any

from c10_v24_model import NS_PER_SECOND


def _latest_at_or_before(
    keys: list[int],
    buckets: dict[int, Any],
    ts_ns: int,
) -> Any:
    index = bisect_right(keys, ts_ns) - 1
    if index < 0:
        raise RuntimeError(f"no causal price exists at or before {ts_ns}")
    return buckets[keys[index]]


def _empty_interval(previous: Any) -> dict[str, Any]:
    close = float(previous.close)
    return {
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "quote_volume": 0.0,
        "taker_buy_quote": 0.0,
        "trade_count": 0,
        "first_trade_ts_ns": int(previous.last_trade_ts_ns),
        "last_trade_ts_ns": int(previous.last_trade_ts_ns),
        "empty_interval": True,
    }


def _observed_interval(bucket: Any) -> dict[str, Any]:
    return {
        "open": float(bucket.open),
        "high": float(bucket.high),
        "low": float(bucket.low),
        "close": float(bucket.close),
        "quote_volume": float(bucket.quote_volume),
        "taker_buy_quote": float(bucket.taker_buy_quote),
        "trade_count": int(bucket.trade_count),
        "first_trade_ts_ns": int(bucket.first_trade_ts_ns),
        "last_trade_ts_ns": int(bucket.last_trade_ts_ns),
        "empty_interval": False,
    }


def align_cross_market_rows_dense(
    spot: dict[int, Any],
    perp: dict[int, Any],
    *,
    bucket_seconds: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Align both venues on every completed interval without future fill."""

    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    if not spot or not perp:
        raise RuntimeError("spot and perpetual buckets must be nonempty")
    interval_ns = bucket_seconds * NS_PER_SECOND
    spot_keys = sorted(spot)
    perp_keys = sorted(perp)
    start_ns = max(spot_keys[0], perp_keys[0])
    end_ns = min(spot_keys[-1], perp_keys[-1])
    if start_ns > end_ns:
        raise RuntimeError("spot and perpetual bucket ranges do not overlap")

    last_spot = _latest_at_or_before(spot_keys, spot, start_ns)
    last_perp = _latest_at_or_before(perp_keys, perp, start_ns)
    rows: list[dict[str, Any]] = []
    spot_empty = 0
    perp_empty = 0
    both_empty = 0

    for ts_ns in range(start_ns, end_ns + 1, interval_ns):
        spot_bucket = spot.get(ts_ns)
        perp_bucket = perp.get(ts_ns)
        if spot_bucket is not None:
            last_spot = spot_bucket
            s = _observed_interval(spot_bucket)
        else:
            spot_empty += 1
            s = _empty_interval(last_spot)
        if perp_bucket is not None:
            last_perp = perp_bucket
            p = _observed_interval(perp_bucket)
        else:
            perp_empty += 1
            p = _empty_interval(last_perp)
        if s["empty_interval"] and p["empty_interval"]:
            both_empty += 1
        if int(s["last_trade_ts_ns"]) >= ts_ns:
            raise RuntimeError(f"spot dense row uses noncausal trade at {ts_ns}")
        if int(p["last_trade_ts_ns"]) >= ts_ns:
            raise RuntimeError(f"perpetual dense row uses noncausal trade at {ts_ns}")
        rows.append(
            {
                "ts_ns": ts_ns,
                "spot_open": s["open"],
                "spot_high": s["high"],
                "spot_low": s["low"],
                "spot_close": s["close"],
                "spot_quote_volume": s["quote_volume"],
                "spot_taker_buy_quote": s["taker_buy_quote"],
                "spot_trade_count": s["trade_count"],
                "spot_last_trade_ts_ns": s["last_trade_ts_ns"],
                "spot_empty_interval": s["empty_interval"],
                "perp_open": p["open"],
                "perp_high": p["high"],
                "perp_low": p["low"],
                "perp_close": p["close"],
                "perp_quote_volume": p["quote_volume"],
                "perp_taker_buy_quote": p["taker_buy_quote"],
                "perp_trade_count": p["trade_count"],
                "perp_last_trade_ts_ns": p["last_trade_ts_ns"],
                "perp_empty_interval": p["empty_interval"],
            },
        )

    return rows, {
        "aligned_row_count": len(rows),
        "expected_grid_row_count": (end_ns - start_ns) // interval_ns + 1,
        "spot_source_bucket_count": len(spot),
        "perp_source_bucket_count": len(perp),
        "spot_empty_interval_count": spot_empty,
        "perp_empty_interval_count": perp_empty,
        "both_empty_interval_count": both_empty,
        "spot_only_bucket_count": perp_empty,
        "perp_only_bucket_count": spot_empty,
        "gap_count": 0,
        "gaps": [],
        "first_ts_ns": start_ns,
        "last_ts_ns": end_ns,
        "bucket_seconds": bucket_seconds,
        "empty_interval_policy": (
            "carry the last strictly prior completed trade price as OHLC; "
            "set volume, taker-buy quote and trade count to zero"
        ),
        "causality": (
            "dense row timestamp is interval end; observed or carried source "
            "trade timestamp is strictly earlier; no future interpolation"
        ),
    }


__all__ = ["align_cross_market_rows_dense"]
