#!/usr/bin/env python3
"""Plan sparse V18 bookTicker acquisition without downloading L1 archives.

The plan uses the frozen V18 expansion pre-candidate detector on checksum-
verified futures/spot klines and open-interest metrics. It records only the UTC
dates whose baseline/observation windows require official bookTicker data and
probes the corresponding archive metadata. No performance or execution result
is calculated.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from derive_nt_lvcfr_v18_signals import derive_expansion_pre_candidates
from nt_lvcfr_data import BINANCE_VISION, CandidateConfig, date_to_ns, prepare_signal_schedule
from prepare_nt_lvcfr_v18_sparse import required_book_ticker_dates


def probe(url: str) -> dict[str, object]:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return {
                "url": url,
                "status": int(response.status),
                "content_length": int(response.headers.get("Content-Length", "0") or 0),
                "last_modified": response.headers.get("Last-Modified"),
                "etag": response.headers.get("ETag"),
            }
    except urllib.error.HTTPError as exc:
        return {"url": url, "status": int(exc.code), "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = CandidateConfig.load(args.config.resolve())
    prepare_signal_schedule(
        week_start=args.week_start,
        output_root=output,
        config=config,
    )
    candidates = derive_expansion_pre_candidates(
        raw_root=output / "raw",
        evaluation_start_ns=date_to_ns(args.week_start),
        evaluation_end_ns=date_to_ns(args.week_start + timedelta(days=7)),
    )
    dates = required_book_ticker_dates(candidates)
    probes = []
    for day in dates:
        stamp = day.isoformat()
        probes.append(
            probe(
                f"{BINANCE_VISION}/futures/um/daily/bookTicker/BTCUSDT/"
                f"BTCUSDT-bookTicker-{stamp}.zip"
            )
        )
    plan = {
        "candidate": config.candidate,
        "week_start": args.week_start.isoformat(),
        "engine_status": "data_acquisition_plan_only_no_backtest",
        "expansion_pre_candidate_count": len(candidates),
        "required_book_ticker_dates": [day.isoformat() for day in dates],
        "book_ticker_archive_probes": probes,
        "total_declared_bytes": sum(int(item.get("content_length", 0)) for item in probes),
        "all_archives_available": all(item.get("status") == 200 for item in probes),
        "performance_metrics_calculated": False,
    }
    (output / "v18_sparse_plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
