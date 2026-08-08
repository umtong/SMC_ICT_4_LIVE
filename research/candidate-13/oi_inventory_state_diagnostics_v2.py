#!/usr/bin/env python3
"""Run V13 while preserving explicit Binance metrics-archive gaps.

The earliest Candidate 13 development events predate some symbol/day files in
Binance Vision's daily metrics collection. A missing official archive is not
synthetic zero OI and is not filled from future data. It is recorded as
unavailable, while events without a causal post-sweep observation remain
UNRESOLVED in the base classifier.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from typing import Any

import pandas as pd

import oi_inventory_state_diagnostics as diagnostic


MISSING_ARCHIVES: list[dict[str, Any]] = []


def _load_symbol_metrics(
    *,
    symbol: str,
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]],
    cache: Path,
) -> tuple[pd.DataFrame, list[diagnostic.RawEvidence]]:
    days: set = set()
    for sweep, confirmation in intervals:
        start = sweep.date() - diagnostic.timedelta(days=1)
        end = confirmation.date()
        days.update(diagnostic.daterange(start, end))

    frames: list[pd.DataFrame] = []
    evidence: list[diagnostic.RawEvidence] = []
    for day in sorted(days):
        try:
            archive, item = diagnostic.download_metrics(symbol, day, cache)
        except HTTPError as error:
            if error.code != 404:
                raise
            missing = {
                "symbol": symbol,
                "day": day.isoformat(),
                "http_status": int(error.code),
                "classification": "OFFICIAL_ARCHIVE_UNAVAILABLE",
            }
            MISSING_ARCHIVES.append(missing)
            print(json.dumps(missing, sort_keys=True))
            continue
        frames.append(diagnostic.read_metrics(archive))
        evidence.append(item)

    if not frames:
        columns = [
            "metrics_create_time",
            "metrics_observed_time",
            "_source_day",
            *diagnostic.METRICS_COLUMNS,
        ]
        empty = pd.DataFrame(columns=columns)
        for column in ("metrics_create_time", "metrics_observed_time"):
            empty[column] = pd.to_datetime(empty[column], utc=True)
        return diagnostic.add_positioning_features(empty), evidence
    return diagnostic.add_positioning_features(
        pd.concat(frames, ignore_index=True),
    ), evidence


def _argument_path(flag: str) -> Path | None:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(sys.argv):
        return None
    return Path(sys.argv[index + 1])


def _augment_outputs() -> None:
    output_json = _argument_path("--output-json")
    output_md = _argument_path("--output-md")
    evidence_json = _argument_path("--evidence-json")
    if output_json is not None and output_json.exists():
        result = json.loads(output_json.read_text(encoding="utf-8"))
        result["archive_availability"] = {
            "missing_count": len(MISSING_ARCHIVES),
            "missing": MISSING_ARCHIVES,
            "policy": "no synthetic fill; affected events remain unresolved",
        }
        output_json.write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=True) + "\n",
            encoding="utf-8",
        )
    if evidence_json is not None and evidence_json.exists():
        evidence = json.loads(evidence_json.read_text(encoding="utf-8"))
        evidence_json.write_text(
            json.dumps(
                {
                    "verified_archives": evidence,
                    "unavailable_archives": MISSING_ARCHIVES,
                },
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    if output_md is not None and output_md.exists():
        with output_md.open("a", encoding="utf-8") as stream:
            stream.write("\n## Official archive availability\n\n")
            stream.write(f"- missing archives: {len(MISSING_ARCHIVES)}\n")
            stream.write("- policy: no synthetic fill; affected observations remain UNRESOLVED\n")


diagnostic.load_symbol_metrics = _load_symbol_metrics


if __name__ == "__main__":
    status = diagnostic.main()
    _augment_outputs()
    raise SystemExit(status)
