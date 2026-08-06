#!/usr/bin/env python3
"""Probe official Binance Vision microstructure archives without assumptions.

The official public-data README documents trades/aggTrades/klines but does not
fully enumerate every object present in the backing archive. This probe uses
S3-compatible list requests, records exact keys and coverage, sends HEAD only
to representative objects, and downloads only a bounded small sample when the
server-reported size is below the declared limit.

It is a data-availability probe only. It does not create signals, fills, PnL or
NAV and is never part of an authoritative performance run.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


BUCKET_LIST_ENDPOINTS = (
    "https://data.binance.vision/",
    "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
)
PREFIXES = (
    "data/futures/um/monthly/bookTicker/BTCUSDT/",
    "data/futures/um/monthly/bookDepth/BTCUSDT/",
    "data/futures/um/daily/bookTicker/BTCUSDT/",
    "data/futures/um/daily/bookDepth/BTCUSDT/",
    "data/futures/um/monthly/metrics/BTCUSDT/",
    "data/futures/um/daily/metrics/BTCUSDT/",
)
BASE_OBJECT_URL = "https://data.binance.vision/"
USER_AGENT = "SMC-ICT-4-LIVE-candidate-01-data-probe/2.0"
REPRESENTATIVE_DATE_TOKENS = (
    "2020-09",
    "2023-01",
    "2023-05",
    "2024-03",
    "2024-04",
    "2025-01",
    "2026-04",
)


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    key: str
    size: int
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True, slots=True)
class HttpRecord:
    url: str
    method: str
    status: int | None
    content_length: int | None
    content_type: str | None
    etag: str | None
    last_modified: str | None
    error: str | None


def request(
    url: str,
    *,
    method: str = "GET",
    timeout: int = 30,
) -> urllib.response.addinfourl:
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": USER_AGENT},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def list_prefix(prefix: str) -> tuple[str, list[ObjectRecord]]:
    errors: list[str] = []
    for endpoint in BUCKET_LIST_ENDPOINTS:
        token: str | None = None
        records: list[ObjectRecord] = []
        try:
            while True:
                params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
                if token:
                    params["continuation-token"] = token
                url = endpoint + "?" + urllib.parse.urlencode(params)
                with request(url) as response:
                    payload = response.read()
                root = ET.fromstring(payload)
                namespace = ""
                if root.tag.startswith("{"):
                    namespace = root.tag.split("}", 1)[0] + "}"
                for node in root.findall(f"{namespace}Contents"):
                    key = node.findtext(f"{namespace}Key")
                    size = node.findtext(f"{namespace}Size")
                    if not key or size is None:
                        continue
                    etag = node.findtext(f"{namespace}ETag")
                    records.append(
                        ObjectRecord(
                            key=key,
                            size=int(size),
                            etag=etag.strip('"') if etag else None,
                            last_modified=node.findtext(
                                f"{namespace}LastModified",
                            ),
                        ),
                    )
                truncated = (
                    root.findtext(f"{namespace}IsTruncated", "false").lower()
                    == "true"
                )
                if not truncated:
                    return endpoint, records
                token = root.findtext(f"{namespace}NextContinuationToken")
                if not token:
                    raise RuntimeError("truncated listing without continuation token")
        except Exception as exc:  # noqa: BLE001 - evidence records exact failure
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def representative_records(records: list[ObjectRecord]) -> list[ObjectRecord]:
    """Bound HEAD requests while preserving coverage and anomaly checks."""

    if not records:
        return []
    by_key = {record.key: record for record in records}
    zip_records = sorted(
        (record for record in records if record.key.endswith(".zip")),
        key=lambda row: row.key,
    )
    selected: dict[str, ObjectRecord] = {}
    if zip_records:
        for record in (
            zip_records[0],
            zip_records[-1],
            min(zip_records, key=lambda row: row.size),
            max(zip_records, key=lambda row: row.size),
        ):
            selected[record.key] = record
        for record in zip_records:
            if any(token in record.key for token in REPRESENTATIVE_DATE_TOKENS):
                selected[record.key] = record
    for key in list(selected):
        checksum_key = key + ".CHECKSUM"
        if checksum_key in by_key:
            selected[checksum_key] = by_key[checksum_key]
    return [selected[key] for key in sorted(selected)]


def head(url: str) -> HttpRecord:
    try:
        with request(url, method="HEAD") as response:
            headers = response.headers
            length = headers.get("Content-Length")
            return HttpRecord(
                url=url,
                method="HEAD",
                status=int(response.status),
                content_length=int(length) if length else None,
                content_type=headers.get("Content-Type"),
                etag=headers.get("ETag"),
                last_modified=headers.get("Last-Modified"),
                error=None,
            )
    except urllib.error.HTTPError as exc:
        return HttpRecord(
            url=url,
            method="HEAD",
            status=int(exc.code),
            content_length=None,
            content_type=None,
            etag=None,
            last_modified=None,
            error=f"HTTPError: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return HttpRecord(
            url=url,
            method="HEAD",
            status=None,
            content_length=None,
            content_type=None,
            etag=None,
            last_modified=None,
            error=f"{type(exc).__name__}: {exc}",
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sample_zip(
    record: ObjectRecord,
    *,
    output_dir: Path,
    max_download_bytes: int,
) -> dict[str, object]:
    result: dict[str, object] = {
        "key": record.key,
        "declared_size": record.size,
        "downloaded": False,
    }
    if not record.key.endswith(".zip"):
        result["skip_reason"] = "not_zip"
        return result
    if record.size <= 0 or record.size > max_download_bytes:
        result["skip_reason"] = "outside_download_size_limit"
        return result

    url = urllib.parse.urljoin(BASE_OBJECT_URL, record.key)
    with tempfile.TemporaryDirectory(prefix="binance-vision-probe-") as directory:
        archive = Path(directory) / Path(record.key).name
        try:
            with request(url, timeout=120) as response, archive.open("wb") as target:
                while True:
                    block = response.read(1 << 20)
                    if not block:
                        break
                    target.write(block)
            actual_size = archive.stat().st_size
            result.update(
                {
                    "downloaded": True,
                    "actual_size": actual_size,
                    "sha256": sha256(archive),
                },
            )
            if actual_size != record.size:
                result["size_mismatch"] = True
            with zipfile.ZipFile(archive) as zipped:
                names = zipped.namelist()
                result["members"] = names
                if not names:
                    result["sample_error"] = "empty_zip"
                    return result
                with zipped.open(names[0]) as stream:
                    raw_lines = []
                    for _ in range(8):
                        line = stream.readline()
                        if not line:
                            break
                        raw_lines.append(line.decode("utf-8", errors="replace").rstrip())
                result["first_member"] = names[0]
                result["first_lines"] = raw_lines
                sample_name = Path(record.key).name + ".sample.txt"
                (output_dir / sample_name).write_text(
                    "\n".join(raw_lines) + ("\n" if raw_lines else ""),
                    encoding="utf-8",
                )
                result["sample_file"] = sample_name
        except Exception as exc:  # noqa: BLE001
            result["download_error"] = f"{type(exc).__name__}: {exc}"
    return result


def run(args: argparse.Namespace) -> int:
    args.output.mkdir(parents=True, exist_ok=True)
    listings: dict[str, object] = {}
    all_records: list[ObjectRecord] = []
    head_candidates: dict[str, ObjectRecord] = {}
    sample_candidates: list[ObjectRecord] = []
    for prefix in PREFIXES:
        try:
            endpoint, records = list_prefix(prefix)
            zip_records = [row for row in records if row.key.endswith(".zip")]
            checksum_records = [
                row for row in records if row.key.endswith(".CHECKSUM")
            ]
            representatives = representative_records(records)
            listings[prefix] = {
                "endpoint": endpoint,
                "object_count": len(records),
                "zip_count": len(zip_records),
                "checksum_count": len(checksum_records),
                "first_key": records[0].key if records else None,
                "last_key": records[-1].key if records else None,
                "minimum_zip_size": min(
                    (row.size for row in zip_records),
                    default=None,
                ),
                "maximum_zip_size": max(
                    (row.size for row in zip_records),
                    default=None,
                ),
                "representative_keys": [row.key for row in representatives],
                "objects": [asdict(row) for row in records],
            }
            all_records.extend(records)
            head_candidates.update({row.key: row for row in representatives})
            if zip_records:
                sample_candidates.append(min(zip_records, key=lambda row: row.size))
        except Exception as exc:  # noqa: BLE001
            listings[prefix] = {
                "error": f"{type(exc).__name__}: {exc}",
                "objects": [],
            }

    http_records = [
        asdict(head(urllib.parse.urljoin(BASE_OBJECT_URL, key)))
        for key in sorted(head_candidates)
    ]
    samples = [
        sample_zip(
            record,
            output_dir=args.output,
            max_download_bytes=args.max_download_bytes,
        )
        for record in sample_candidates
    ]

    payload = {
        "probe": "official Binance Vision microstructure archive availability",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "performance_research": False,
        "custom_market_data_source": False,
        "base_object_url": BASE_OBJECT_URL,
        "prefixes": listings,
        "listed_object_count": len({row.key for row in all_records}),
        "representative_head_count": len(http_records),
        "head_records": http_records,
        "bounded_samples": samples,
        "max_download_bytes": args.max_download_bytes,
    }
    output = args.output / "binance_vision_microstructure_probe.json"
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/candidate-01-binance-vision-probe"),
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=50_000_000,
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
