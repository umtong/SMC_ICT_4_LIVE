#!/usr/bin/env python3
"""Rebuild only the NautilusTrader execution catalog for a derived schedule.

The raw official Binance aggTrades and funding archives must already exist in
``prepared/raw``. This command neither downloads data nor runs a backtest. It
re-materializes the ParquetDataCatalog windows required by a causally delayed
signal schedule; all execution and accounting remain native NautilusTrader.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from nt_lvcfr_data import CandidateConfig
from nt_lvcfr_trade_proxy import build_trade_proxy_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    prepared = args.prepared_root.resolve()
    config = CandidateConfig.load(args.config.resolve())
    manifest_path = prepared / "data_manifest.json"
    signals_path = prepared / "signals.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    if not signals_path.exists():
        raise FileNotFoundError(signals_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    aggtrade_paths = sorted((prepared / "raw" / "aggTrades").glob("*.zip"))
    funding_paths = sorted((prepared / "raw" / "funding").glob("*.zip"))
    if not aggtrade_paths:
        raise RuntimeError("cached prepared data contains no aggTrades archives")
    if not funding_paths:
        raise RuntimeError("cached prepared data contains no funding archives")

    catalog_stats = build_trade_proxy_catalog(
        catalog_path=prepared / "catalog",
        aggtrade_paths=aggtrade_paths,
        funding_paths=funding_paths,
        signals=signals,
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
        "reason": "causally delayed derived signal schedule",
        "raw_aggtrade_archives": len(aggtrade_paths),
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
                "aggtrade_archives": len(aggtrade_paths),
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
