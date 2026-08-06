#!/usr/bin/env python3
"""Rebuild the native NautilusTrader catalog for a derived bookTicker schedule."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from nt_lvcfr_data import CandidateConfig, build_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    config = CandidateConfig.load(args.config.resolve())
    manifest_path = prepared / "data_manifest.json"
    signals_path = prepared / "signals.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    book_ticker_paths = sorted((prepared / "raw" / "book_ticker").glob("*.zip"))
    funding_paths = sorted((prepared / "raw" / "funding").glob("*.zip"))
    if not book_ticker_paths:
        raise RuntimeError("prepared data contains no official bookTicker archives")
    if not funding_paths:
        raise RuntimeError("prepared data contains no official funding archives")
    signal_times = [
        SimpleNamespace(confirm_time_ns=int(signal["confirm_time_ns"]))
        for signal in signals
    ]
    catalog_stats = build_catalog(
        catalog_path=prepared / "catalog",
        book_ticker_paths=book_ticker_paths,
        funding_paths=funding_paths,
        signals=signal_times,
        config=config,
        evaluation_start_ns=int(manifest["evaluation_start_ns"]),
        evaluation_end_ns=int(manifest["evaluation_end_ns"]),
    )
    manifest["candidate"] = config.candidate
    manifest["signals"] = len(signals)
    manifest["signal_path"] = signals_path.as_posix()
    manifest["catalog_path"] = (prepared / "catalog").as_posix()
    manifest["catalog"] = catalog_stats
    manifest["catalog_rebuild"] = {
        "reason": "V18 causally delayed L1 resilience schedule",
        "raw_book_ticker_archives": len(book_ticker_paths),
        "raw_funding_archives": len(funding_paths),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "candidate": config.candidate,
                "signals": len(signals),
                "book_ticker_archives": len(book_ticker_paths),
                "funding_archives": len(funding_paths),
                "quote_ticks_retained": catalog_stats["quote_ticks_retained"],
                "execution_windows": catalog_stats["execution_windows"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
