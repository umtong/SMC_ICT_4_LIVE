"""Attach Binance aggressor-flow fields to the verified candidate data bundle."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from data import LoadedBundle, _read_kline_archive, _timestamp_ns, load_bundle


def _is_kline_archive(path: Path) -> bool:
    return path.parent.name == "klines-1m" and "fundingRate" not in path.name


def load_flow_bundle(
    *,
    symbol: str,
    trade_start: date,
    trade_end: date,
    warmup_days: int,
    cache_root: Path,
    manifest_destination: Path,
) -> LoadedBundle:
    """Load the exact verified OHLCV interval plus taker-buy base volume.

    ``load_bundle`` remains the only archive downloader and checksum verifier.
    This function re-reads those same verified kline archives and performs an
    exact timestamp join; it neither synthesizes flow nor changes replay time.
    """
    bundle = load_bundle(
        symbol=symbol,
        trade_start=trade_start,
        trade_end=trade_end,
        warmup_days=warmup_days,
        cache_root=cache_root,
        manifest_destination=manifest_destination,
    )
    rows: list[dict[str, str]] = []
    for archive in bundle.archives:
        if _is_kline_archive(archive):
            rows.extend(_read_kline_archive(archive))
    if not rows:
        raise RuntimeError("no verified kline rows available for aggressor flow")

    frame = pd.DataFrame.from_records(rows)
    frame["close_time_ns"] = frame["close_time"].map(_timestamp_ns)
    frame["volume"] = pd.to_numeric(frame["volume"], errors="raise")
    frame["taker_buy_base"] = pd.to_numeric(
        frame["taker_buy_base"],
        errors="raise",
    )
    frame = frame.sort_values("close_time_ns", kind="stable")
    frame = frame.drop_duplicates(subset=["close_time_ns"], keep="last")

    load_start = trade_start.fromordinal(trade_start.toordinal() - warmup_days)
    load_start_ns = int(
        datetime.combine(
            load_start,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    trade_end_ns = int(
        datetime.combine(
            trade_end,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).timestamp()
        * 1e9
    )
    frame = frame[
        (frame["close_time_ns"] >= load_start_ns)
        & (frame["close_time_ns"] < trade_end_ns)
    ]
    frame.index = pd.to_datetime(frame["close_time_ns"], unit="ns", utc=True)
    frame.index.name = "timestamp"

    if not frame.index.equals(bundle.frame.index):
        missing = bundle.frame.index.difference(frame.index)
        extra = frame.index.difference(bundle.frame.index)
        raise RuntimeError(
            "aggressor-flow timestamps do not match verified OHLCV interval: "
            f"missing={len(missing)}, extra={len(extra)}"
        )
    volume_difference = (
        frame["volume"].to_numpy() - bundle.frame["volume"].to_numpy()
    )
    if abs(volume_difference).max(initial=0.0) > 1e-9:
        raise RuntimeError("aggressor-flow volume does not match verified OHLCV")
    invalid = (
        (frame["taker_buy_base"] < -1e-12)
        | (frame["taker_buy_base"] > frame["volume"] + 1e-9)
    )
    if bool(invalid.any()):
        raise RuntimeError("taker-buy base volume lies outside total volume")

    enriched = bundle.frame.copy()
    enriched["taker_buy_base"] = frame["taker_buy_base"].to_numpy()
    return LoadedBundle(
        frame=enriched,
        funding=bundle.funding,
        data_manifest_path=bundle.data_manifest_path,
        archives=bundle.archives,
    )


__all__ = ["load_flow_bundle"]
