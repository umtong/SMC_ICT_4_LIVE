#!/usr/bin/env python3
"""Prepare official detector and L1 data for V18 without running a backtest."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from nt_lvcfr_data import (
    BINANCE_VISION,
    CandidateConfig,
    _daily_dates,
    date_to_ns,
    download_verified,
    prepare_signal_schedule,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

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
    sources = list(signal_manifest.get("sources", []))
    # One warm-up day is required because V18 compares post-event resilience
    # with the ten minutes immediately preceding the expansion event.
    for day in _daily_dates(
        args.week_start - timedelta(days=1),
        args.week_start + timedelta(days=7),
    ):
        stamp = day.isoformat()
        url = (
            f"{BINANCE_VISION}/futures/um/daily/bookTicker/BTCUSDT/"
            f"BTCUSDT-bookTicker-{stamp}.zip"
        )
        record = download_verified(url, raw_root / "book_ticker", "book_ticker")
        sources.append(asdict(record))
    months = sorted(
        {
            (args.week_start + timedelta(days=offset)).strftime("%Y-%m")
            for offset in range(8)
        }
    )
    for month in months:
        url = (
            f"{BINANCE_VISION}/futures/um/monthly/fundingRate/BTCUSDT/"
            f"BTCUSDT-fundingRate-{month}.zip"
        )
        record = download_verified(url, raw_root / "funding", "funding")
        sources.append(asdict(record))

    evaluation_start_ns = date_to_ns(args.week_start)
    evaluation_end_ns = date_to_ns(args.week_start + timedelta(days=7))
    manifest = {
        "candidate": config.candidate,
        "engine_status": "official_data_preparation_only_no_backtest",
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
                "book_ticker_archives": len(
                    list((raw_root / "book_ticker").glob("*.zip"))
                ),
                "funding_archives": len(
                    list((raw_root / "funding").glob("*.zip"))
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
