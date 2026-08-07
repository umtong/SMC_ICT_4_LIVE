#!/usr/bin/env python3
"""Prepare only bookTicker dates causally required by V18 pre-candidates.

This is a data-acquisition optimization only. It reuses the frozen V18 detector,
config, source URLs, checksum verifier, and signal schedule. Futures/spot
klines and open interest are prepared first; expansion pre-candidates then
identify the UTC dates touched by each ten-minute baseline and 30-second
post-event observation. Only those official daily bookTicker archives are
fetched, concurrently. No orders, fills, PnL, or NAV are calculated here.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from derive_nt_lvcfr_v18_signals import (
    BASELINE_MINUTES,
    OBSERVATION_SECONDS,
    derive_expansion_pre_candidates,
)
from nt_lvcfr_data import (
    BINANCE_VISION,
    CandidateConfig,
    NS_PER_MINUTE,
    NS_PER_SECOND,
    date_to_ns,
    download_verified,
    prepare_signal_schedule,
)


def utc_dates_touched(start_ns: int, end_ns: int) -> set[date]:
    if end_ns <= start_ns:
        raise ValueError("end_ns must be after start_ns")
    first = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).date()
    last = datetime.fromtimestamp((end_ns - 1) / 1e9, tz=timezone.utc).date()
    output: set[date] = set()
    current = first
    while current <= last:
        output.add(current)
        current += timedelta(days=1)
    return output


def required_book_ticker_dates(
    candidates: list[dict[str, object]],
) -> list[date]:
    required: set[date] = set()
    for signal in candidates:
        start_ns = int(signal["first_start_time_ns"]) - BASELINE_MINUTES * NS_PER_MINUTE
        end_ns = int(signal["confirm_time_ns"]) + OBSERVATION_SECONDS * NS_PER_SECOND
        required.update(utc_dates_touched(start_ns, end_ns))
    return sorted(required)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("--workers must be positive")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = CandidateConfig.load(args.config.resolve())
    if args.week_start.isoformat() not in config.validation_weeks:
        parser.error(f"week is not frozen: {config.validation_weeks}")

    signal_manifest = prepare_signal_schedule(
        week_start=args.week_start,
        output_root=output,
        config=config,
    )
    raw_root = output / "raw"
    evaluation_start_ns = date_to_ns(args.week_start)
    evaluation_end_ns = date_to_ns(args.week_start + timedelta(days=7))
    pre_candidates = derive_expansion_pre_candidates(
        raw_root=raw_root,
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
    )
    dates = required_book_ticker_dates(pre_candidates)
    # The frozen route function requires at least one official archive even if
    # no expansion pre-candidate survives. A single evaluation-day archive is
    # sufficient because no L1 context will be requested in that case.
    if not dates:
        dates = [args.week_start]

    def fetch_book_ticker(day: date):
        stamp = day.isoformat()
        url = (
            f"{BINANCE_VISION}/futures/um/daily/bookTicker/BTCUSDT/"
            f"BTCUSDT-bookTicker-{stamp}.zip"
        )
        return download_verified(url, raw_root / "book_ticker", "book_ticker")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        book_records = list(executor.map(fetch_book_ticker, dates))

    months = sorted(
        {
            (args.week_start + timedelta(days=offset)).strftime("%Y-%m")
            for offset in range(8)
        }
    )
    funding_records = []
    for month in months:
        url = (
            f"{BINANCE_VISION}/futures/um/monthly/fundingRate/BTCUSDT/"
            f"BTCUSDT-fundingRate-{month}.zip"
        )
        funding_records.append(
            download_verified(url, raw_root / "funding", "funding")
        )

    sources = list(signal_manifest.get("sources", []))
    sources.extend(asdict(item) for item in sorted(book_records, key=lambda row: row.local_path))
    sources.extend(asdict(item) for item in funding_records)
    manifest = {
        "candidate": config.candidate,
        "engine_status": "sparse_official_data_preparation_only_no_backtest",
        "week_start": args.week_start.isoformat(),
        "week_end": (args.week_start + timedelta(days=7)).isoformat(),
        "evaluation_start_ns": evaluation_start_ns,
        "evaluation_end_ns": evaluation_end_ns,
        "signals": int(signal_manifest["signals"]),
        "signal_path": (output / "signals.json").as_posix(),
        "catalog_path": (output / "catalog").as_posix(),
        "catalog": {},
        "sources": sources,
        "detection_data": [
            "futures_1m_klines",
            "spot_1m_klines",
            "open_interest_metrics",
            "futures_bookTicker_L1",
        ],
        "data_acquisition": {
            "policy": "pre-candidate required UTC dates only",
            "expansion_pre_candidate_count": len(pre_candidates),
            "book_ticker_dates": [day.isoformat() for day in dates],
            "parallel_workers": args.workers,
            "detector_logic_changed": False,
        },
    }
    (output / "data_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate": config.candidate,
                "week_start": args.week_start.isoformat(),
                "source_contraction_signals": manifest["signals"],
                "expansion_pre_candidates": len(pre_candidates),
                "book_ticker_dates": manifest["data_acquisition"]["book_ticker_dates"],
                "book_ticker_archives": len(book_records),
                "funding_archives": len(funding_records),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
