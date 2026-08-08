"""Candidate 16 v3 observational feature extension.

Candidate 05 continues to prepare completed-minute klines, aggregate trades, and
its frozen feature contract.  Candidate 03 supplies official checksum-verified
Binance bookTicker archives and timestamp normalization.  This module appends
actual best-bid/best-ask recovery features without touching orders, fills, PnL,
or NAV.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd

import features as candidate05_features
import nt_lvcfr_data as candidate03_data
from topbook_features import NS_PER_MINUTE
from topbook_features import aggregate_book_ticker_paths


BOOK_TICKER_BASE = (
    "https://data.binance.vision/data/futures/um/daily/bookTicker"
)


def _book_ticker_url(symbol: str, day: date) -> str:
    filename = f"{symbol}-bookTicker-{day.isoformat()}.zip"
    return f"{BOOK_TICKER_BASE}/{symbol}/{filename}"


def _bool_series(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"},
    )


def load_range(
    *,
    symbol: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> tuple[pd.DataFrame, Path, list[Path], list[Any]]:
    """Append best-quote dynamics to the frozen Candidate 05 feature file."""
    klines, feature_path, raw_files, evidence = candidate05_features.load_range(
        symbol=symbol,
        start=start,
        end=end,
        cache=cache,
        output=output,
    )

    book_paths: list[Path] = []
    book_sources: list[Any] = []
    cursor = start
    while cursor <= end:
        source = candidate03_data.download_verified(
            _book_ticker_url(symbol, cursor),
            cache / "bookTicker",
            "bookTicker",
        )
        archive = Path(source.local_path)
        checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
        if not checksum.exists():
            raise RuntimeError(f"missing verified checksum: {checksum}")
        book_paths.append(archive)
        raw_files.extend([archive, checksum])
        book_sources.append(source)
        cursor += timedelta(days=1)

    topbook = aggregate_book_ticker_paths(book_paths)
    frame = pd.read_csv(feature_path, compression="infer")
    if "observed_time_ns" not in frame or "feature_ready" not in frame:
        raise RuntimeError("Candidate 05 feature contract drifted")
    observed = pd.to_numeric(
        frame["observed_time_ns"],
        errors="raise",
    ).astype("int64")
    frame["minute_start_ns"] = (
        observed // NS_PER_MINUTE * NS_PER_MINUTE
    )
    merged = frame.merge(
        topbook,
        on="minute_start_ns",
        how="left",
        validate="one_to_one",
        sort=False,
    )

    top_ready = _bool_series(
        merged["topbook_feature_ready"].fillna(False),
    )
    required = [
        "topbook_quote_imbalance_end",
        "topbook_bid_queue_response",
        "topbook_ask_queue_response",
        "topbook_mid_ret_60s_bps",
        "topbook_spread_start_bps",
        "topbook_spread_end_bps",
        "topbook_last_quote_age_seconds",
    ]
    merged["feature_ready"] = (
        _bool_series(merged["feature_ready"])
        & top_ready
        & merged[required].notna().all(axis=1)
    )
    if not merged.loc[merged["feature_ready"], required].notna().all().all():
        raise RuntimeError("ready top-of-book observations contain missing data")
    if merged["observed_time_ns"].duplicated().any():
        raise RuntimeError("top-of-book join duplicated feature timestamps")

    merged.to_csv(feature_path, index=False, compression="gzip")

    raw_evidence_path = output / "raw_evidence.json"
    raw_payload = json.loads(raw_evidence_path.read_text(encoding="utf-8"))
    raw_payload.extend(
        {
            "endpoint": "bookTicker",
            "day": Path(source.local_path).stem.rsplit("-", 3)[-3:],
            "source_url": source.source_url,
            "archive": source.local_path,
            "checksum": str(
                Path(source.local_path).with_suffix(
                    Path(source.local_path).suffix + ".CHECKSUM",
                )
            ),
            "size_bytes": source.size_bytes,
            "sha256": source.sha256,
            "candidate03_source_contract": (
                "research/candidate-03/nt_lvcfr_data.py"
            ),
        }
        for source in book_sources
    )
    # Normalize the derived day field to a stable string without relying on a
    # filename parser for evidence validity.
    for item in raw_payload:
        if isinstance(item.get("day"), list):
            item["day"] = "-".join(item["day"]).replace(".zip", "")
    raw_evidence_path.write_text(
        json.dumps(raw_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return klines, feature_path, raw_files, [*evidence, *book_sources]


__all__ = ["load_range"]
