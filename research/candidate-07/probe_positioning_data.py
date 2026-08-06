#!/usr/bin/env python3
"""Probe checksum-verifiable Binance positioning and basis archives.

This utility does not calculate a strategy. It records which official public
archives exist for the frozen BTC week and captures their CSV schemas so that
subsequent research can use only reproducible data sources.
"""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from smc_ict_4.manifest import write_json_atomic


URLS = {
    "usdm_daily_metrics": (
        "https://data.binance.vision/data/futures/um/daily/metrics/"
        "BTCUSDT/BTCUSDT-metrics-2025-12-22.zip"
    ),
    "usdm_monthly_metrics": (
        "https://data.binance.vision/data/futures/um/monthly/metrics/"
        "BTCUSDT/BTCUSDT-metrics-2025-12.zip"
    ),
    "coinm_daily_metrics": (
        "https://data.binance.vision/data/futures/cm/daily/metrics/"
        "BTCUSD_PERP/BTCUSD_PERP-metrics-2025-12-22.zip"
    ),
    "coinm_monthly_metrics": (
        "https://data.binance.vision/data/futures/cm/monthly/metrics/"
        "BTCUSD_PERP/BTCUSD_PERP-metrics-2025-12.zip"
    ),
    "usdm_premium_1m": (
        "https://data.binance.vision/data/futures/um/daily/"
        "premiumIndexKlines/BTCUSDT/1m/BTCUSDT-1m-2025-12-22.zip"
    ),
    "usdm_mark_1m": (
        "https://data.binance.vision/data/futures/um/daily/"
        "markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2025-12-22.zip"
    ),
    "usdm_index_1m": (
        "https://data.binance.vision/data/futures/um/daily/"
        "indexPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2025-12-22.zip"
    ),
}


def fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "SMC-ICT-4-candidate-07-positioning-probe/1.0"},
    )
    with urlopen(request, timeout=120) as response:
        return response.read()


def probe(url: str) -> dict:
    result = {"url": url, "available": False}
    try:
        checksum_text = fetch(url + ".CHECKSUM").decode("utf-8").strip()
        expected = checksum_text.split()[0].lower()
        payload = fetch(url)
        actual = sha256(payload).hexdigest().lower()
        result.update(
            {
                "checksum_available": True,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "checksum_valid": actual == expected,
                "archive_bytes": len(payload),
            }
        )
        if actual != expected:
            return result
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [
                name for name in archive.namelist()
                if name.lower().endswith(".csv")
            ]
            result["csv_names"] = csv_names
            if not csv_names:
                return result
            with archive.open(csv_names[0]) as raw:
                stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                reader = csv.reader(stream)
                rows = []
                for _, row in zip(range(5), reader):
                    rows.append(row)
            result.update(
                {
                    "available": True,
                    "sample_rows": rows,
                    "sample_widths": [len(row) for row in rows],
                }
            )
            return result
    except HTTPError as exc:
        result.update(
            {
                "error_type": "HTTPError",
                "http_status": exc.code,
                "error": str(exc),
            }
        )
    except (URLError, TimeoutError, zipfile.BadZipFile, UnicodeError) as exc:
        result.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
    return result


def run(args: argparse.Namespace) -> int:
    payload = {
        "purpose": (
            "official Binance public archive availability and schema probe; "
            "no strategy or performance calculation"
        ),
        "archives": {
            name: probe(url)
            for name, url in URLS.items()
        },
    }
    write_json_atomic(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True)
    return result


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
