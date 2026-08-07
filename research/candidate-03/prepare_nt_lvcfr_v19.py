#!/usr/bin/env python3
"""Prepare uniform official futures/spot aggTrades for the V19 detector.

The module prepares only source data and the frozen V1 contraction-event
schedule. It does not build fills, positions, PnL, or NAV. Futures aggregate
trades are later reused by the native NautilusTrader execution catalog; spot
aggregate trades are detector-only cross-market evidence.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from nt_lvcfr_data import BINANCE_VISION, CandidateConfig, date_to_ns, download_verified, prepare_signal_schedule


def daily_dates(start: date, end_inclusive: date):
    current = start
    while current <= end_inclusive:
        yield current
        current += timedelta(days=1)


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

    base = prepare_signal_schedule(
        week_start=args.week_start,
        output_root=output,
        config=config,
    )
    raw = output / "raw"
    sources = list(base.get("sources", []))
    futures_paths: list[str] = []
    spot_paths: list[str] = []
    for day in daily_dates(
        args.week_start - timedelta(days=1),
        args.week_start + timedelta(days=7),
    ):
        stamp = day.isoformat()
        futures = download_verified(
            f"{BINANCE_VISION}/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{stamp}.zip",
            raw / "aggTrades",
            "aggTrades",
        )
        spot = download_verified(
            f"{BINANCE_VISION}/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-{stamp}.zip",
            raw / "spot_aggTrades",
            "spot_aggTrades",
        )
        sources.extend((asdict(futures), asdict(spot)))
        futures_paths.append(futures.local_path)
        spot_paths.append(spot.local_path)

    funding_paths: list[str] = []
    months = sorted(
        {
            (args.week_start + timedelta(days=offset)).strftime("%Y-%m")
            for offset in range(8)
        }
    )
    for month in months:
        record = download_verified(
            f"{BINANCE_VISION}/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{month}.zip",
            raw / "funding",
            "funding",
        )
        sources.append(asdict(record))
        funding_paths.append(record.local_path)

    manifest = {
        "candidate": config.candidate,
        "engine_status": "uniform_official_data_preparation_only_no_backtest",
        "week_start": args.week_start.isoformat(),
        "week_end": (args.week_start + timedelta(days=7)).isoformat(),
        "evaluation_start_ns": date_to_ns(args.week_start),
        "evaluation_end_ns": date_to_ns(args.week_start + timedelta(days=7)),
        "signals": int(base["signals"]),
        "signal_path": (output / "signals.json").as_posix(),
        "catalog_path": (output / "catalog").as_posix(),
        "catalog": {},
        "sources": sources,
        "data_contract": {
            "detector": [
                "futures_1m_klines",
                "spot_1m_klines",
                "open_interest_metrics",
                "futures_aggTrades",
                "spot_aggTrades",
            ],
            "execution": ["futures_aggTrades", "funding_rates"],
            "futures_aggtrade_archives": futures_paths,
            "spot_aggtrade_archives": spot_paths,
            "funding_archives": funding_paths,
            "historical_contract_identical_across_frozen_weeks": True,
            "detector_warmup_days": 1,
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
                "source_contraction_events": manifest["signals"],
                "futures_aggtrade_archives": len(futures_paths),
                "spot_aggtrade_archives": len(spot_paths),
                "funding_archives": len(funding_paths),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
