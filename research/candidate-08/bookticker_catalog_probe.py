"""List the official Binance Vision USD-M bookTicker catalog and freeze random BTC weeks.

This module performs no trading, signal generation or backtesting.  It queries the public S3
ListObjectsV2 endpoint, pairs every ZIP with its CHECKSUM object, identifies genuinely contiguous
seven-day BTC windows, and uses a predeclared seed to freeze up to three candidate weeks before any
market outcome is inspected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from data import BinanceDataError
from smc_ict_4.manifest import write_json_atomic


CATALOG_REVISION = "BINANCE_VISION_USDM_BOOKTICKER_CATALOG_V1"
S3_LIST_ENDPOINT = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DEFAULT_SEED = 8811
_DATE_PATTERN = re.compile(r"^(?P<symbol>[A-Z0-9_]+)-bookTicker-(?P<day>\d{4}-\d{2}-\d{2})\.zip$")


@dataclass(frozen=True, slots=True)
class CatalogObject:
    key: str
    size: int
    last_modified: str
    etag: str


def _download_xml(url: str, *, timeout: int = 180) -> bytes:
    request = Request(url, headers={"User-Agent": "smc-ict-4-bookticker-catalog/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _list_page(
    *,
    prefix: str,
    continuation_token: str | None,
    downloader=_download_xml,
) -> tuple[list[CatalogObject], bool, str | None]:
    params = {
        "list-type": "2",
        "prefix": prefix,
        "max-keys": "1000",
    }
    if continuation_token is not None:
        params["continuation-token"] = continuation_token
    payload = downloader(f"{S3_LIST_ENDPOINT}?{urlencode(params)}")
    root = ET.fromstring(payload)
    namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    objects: list[CatalogObject] = []
    for node in root.findall("s3:Contents", namespace):
        key = node.findtext("s3:Key", default="", namespaces=namespace)
        size_text = node.findtext("s3:Size", default="0", namespaces=namespace)
        modified = node.findtext("s3:LastModified", default="", namespaces=namespace)
        etag = node.findtext("s3:ETag", default="", namespaces=namespace).strip('"')
        if not key:
            raise BinanceDataError("S3 catalog returned an object without a key")
        objects.append(
            CatalogObject(
                key=key,
                size=int(size_text),
                last_modified=modified,
                etag=etag,
            )
        )
    truncated_text = root.findtext("s3:IsTruncated", default="false", namespaces=namespace)
    truncated = truncated_text.lower() == "true"
    next_token = root.findtext("s3:NextContinuationToken", default=None, namespaces=namespace)
    if truncated and not next_token:
        raise BinanceDataError("truncated S3 catalog page omitted continuation token")
    return objects, truncated, next_token


def list_catalog_objects(
    *,
    symbol: str,
    downloader=_download_xml,
) -> list[CatalogObject]:
    prefix = f"data/futures/um/daily/bookTicker/{symbol}/"
    token: str | None = None
    objects: list[CatalogObject] = []
    seen_tokens: set[str] = set()
    while True:
        page, truncated, next_token = _list_page(
            prefix=prefix,
            continuation_token=token,
            downloader=downloader,
        )
        objects.extend(page)
        if not truncated:
            break
        assert next_token is not None
        if next_token in seen_tokens:
            raise BinanceDataError("S3 catalog continuation token repeated")
        seen_tokens.add(next_token)
        token = next_token
    keys = [item.key for item in objects]
    if len(keys) != len(set(keys)):
        raise BinanceDataError("S3 catalog returned duplicate object keys")
    return sorted(objects, key=lambda item: item.key)


def _archive_days(
    objects: list[CatalogObject],
    *,
    symbol: str,
) -> tuple[dict[date, CatalogObject], list[str], list[str]]:
    by_key = {item.key: item for item in objects}
    archives: dict[date, CatalogObject] = {}
    missing_checksums: list[str] = []
    malformed_zip_names: list[str] = []
    prefix = f"data/futures/um/daily/bookTicker/{symbol}/"
    for item in objects:
        if not item.key.endswith(".zip"):
            continue
        filename = item.key.removeprefix(prefix)
        match = _DATE_PATTERN.fullmatch(filename)
        if match is None or match.group("symbol") != symbol:
            malformed_zip_names.append(item.key)
            continue
        day = date.fromisoformat(match.group("day"))
        if day in archives:
            raise BinanceDataError(f"duplicate bookTicker archive day: {day}")
        archives[day] = item
        if f"{item.key}.CHECKSUM" not in by_key:
            missing_checksums.append(item.key)
    return archives, sorted(missing_checksums), sorted(malformed_zip_names)


def _contiguous_week_starts(days: set[date]) -> list[date]:
    starts: list[date] = []
    for start in sorted(days):
        if all(start + timedelta(days=offset) in days for offset in range(7)):
            starts.append(start)
    return starts


def build_catalog_evidence(
    *,
    symbol: str = "BTCUSDT",
    seed: int = DEFAULT_SEED,
    objects: list[CatalogObject] | None = None,
) -> dict[str, Any]:
    raw_objects = objects if objects is not None else list_catalog_objects(symbol=symbol)
    catalog_objects = sorted(raw_objects, key=lambda item: item.key)
    keys = [item.key for item in catalog_objects]
    if len(keys) != len(set(keys)):
        raise BinanceDataError("catalog evidence input contained duplicate object keys")
    archives, missing_checksums, malformed = _archive_days(catalog_objects, symbol=symbol)
    if not archives:
        raise BinanceDataError(f"no official daily bookTicker ZIP archives found for {symbol}")
    if missing_checksums:
        raise BinanceDataError(
            f"bookTicker archives without CHECKSUM objects: {missing_checksums[:5]}"
        )
    if malformed:
        raise BinanceDataError(f"malformed bookTicker archive names: {malformed[:5]}")

    starts = _contiguous_week_starts(set(archives))
    if not starts:
        raise BinanceDataError(f"no contiguous seven-day bookTicker window found for {symbol}")
    rng = random.Random(seed)
    selected = rng.sample(starts, k=min(3, len(starts)))
    selected_windows = [
        {
            "start": start.isoformat(),
            "end_exclusive": (start + timedelta(days=7)).isoformat(),
            "days": [
                (start + timedelta(days=offset)).isoformat() for offset in range(7)
            ],
        }
        for start in selected
    ]
    canonical_key_text = "\n".join(item.key for item in catalog_objects) + "\n"
    archive_sizes = sorted(item.size for item in archives.values())
    return {
        "catalog_revision": CATALOG_REVISION,
        "source": {
            "endpoint": S3_LIST_ENDPOINT,
            "prefix": f"data/futures/um/daily/bookTicker/{symbol}/",
            "listing_protocol": "S3_LIST_OBJECTS_V2_PAGINATED",
            "catalog_key_sha256": sha256(canonical_key_text.encode("utf-8")).hexdigest(),
        },
        "symbol": symbol,
        "selection": {
            "seed": seed,
            "rule": "UNIFORM_SAMPLE_WITHOUT_REPLACEMENT_FROM_ALL_CONTIGUOUS_SEVEN_DAY_STARTS",
            "outcome_blind": True,
            "selected_windows": selected_windows,
        },
        "availability": {
            "objects": len(catalog_objects),
            "daily_archives": len(archives),
            "earliest_day": min(archives).isoformat(),
            "latest_day": max(archives).isoformat(),
            "contiguous_week_starts": len(starts),
            "minimum_archive_size_bytes": archive_sizes[0],
            "median_archive_size_bytes": archive_sizes[len(archive_sizes) // 2],
            "maximum_archive_size_bytes": archive_sizes[-1],
            "missing_checksum_archives": 0,
            "malformed_archive_names": 0,
        },
        "research_use": {
            "selected_first_day_probe": selected_windows[0]["start"],
            "candidate_scope": "BTC_FIRST_QUOTE_RESILIENCY",
            "not_a_performance_selection": True,
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_catalog_evidence(symbol=args.symbol, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(args.output.resolve(), result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
