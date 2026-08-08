#!/usr/bin/env python3
"""Integrity repair for liquidation archives lacking provider CHECKSUM files.

Binance Vision publishes normal market archives with SHA-256 CHECKSUM sidecars,
but some historical liquidationSnapshot ZIPs have no sidecar.  This launcher
preserves the v9 study unchanged and changes only that data-integrity boundary:

* every non-liquidation archive still requires the official SHA-256 sidecar;
* a liquidation archive without a sidecar must pass HTTPS retrieval, ZIP CRC,
  one-CSV structure, optional single-part S3 ETag/MD5 verification, and a locally
  recorded SHA-256 used on every subsequent read.
"""
from __future__ import annotations

import argparse
from hashlib import md5, sha256
import io
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import zipfile

import v9_liquidation_event_study as base


FALLBACK_ARCHIVES: list[dict[str, object]] = []
_ORIGINAL_DOWNLOAD = base.download_verified


def _payload_with_headers(
    url: str,
    *,
    timeout: int = 180,
    attempts: int = 4,
) -> tuple[bytes, dict[str, str]]:
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-external-mechanism-study"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                return payload, headers
        except (
            OSError,
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise base.StudyError(f"failed to download {url}") from last


def _verify_liquidation_zip(payload: bytes, url: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise base.StudyError(
                    f"expected one liquidation CSV in {url}, got {names}",
                )
            bad = archive.testzip()
            if bad is not None:
                raise base.StudyError(f"ZIP CRC failed for {url}: {bad}")
    except zipfile.BadZipFile as exc:
        raise base.StudyError(f"invalid liquidation ZIP: {url}") from exc
    return sha256(payload).hexdigest()


def download_verified(archive: base.Archive, cache: Path) -> Path:
    try:
        return _ORIGINAL_DOWNLOAD(archive, cache)
    except urllib.error.HTTPError as exc:
        if exc.code != 404 or archive.data_type != "liquidationSnapshot":
            raise

    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / archive.cache_name
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    if destination.exists() and checksum_path.exists():
        expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
        if re.fullmatch(r"[0-9a-f]{64}", expected) and base._sha256_file(destination) == expected:
            return destination
        destination.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)

    payload, headers = _payload_with_headers(archive.url)
    local_sha256 = _verify_liquidation_zip(payload, archive.url)
    etag = headers.get("etag", "").strip().strip('"').lower()
    etag_verified = False
    if re.fullmatch(r"[0-9a-f]{32}", etag):
        actual_md5 = md5(payload, usedforsecurity=False).hexdigest()
        if actual_md5 != etag:
            raise base.StudyError(
                f"S3 ETag mismatch for {archive.url}: {actual_md5} != {etag}",
            )
        etag_verified = True

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    checksum_path.write_text(
        f"{local_sha256} LOCAL_SHA256_PROVIDER_SIDECAR_ABSENT\n",
        encoding="utf-8",
    )
    integrity = {
        "url": archive.url,
        "size_bytes": len(payload),
        "local_sha256": local_sha256,
        "provider_checksum_sidecar": False,
        "zip_crc_verified": True,
        "s3_etag": etag or None,
        "s3_etag_md5_verified": etag_verified,
    }
    destination.with_suffix(destination.suffix + ".integrity.json").write_text(
        json.dumps(integrity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    FALLBACK_ARCHIVES.append(integrity)
    return destination


base.download_verified = download_verified


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        raise SystemExit("--month must be YYYY-MM")
    args.output.mkdir(parents=True, exist_ok=True)
    result = base.run(args.month, args.cache.resolve(), args.output.resolve())
    result["data_integrity"] = {
        "normal_archives": "official Binance SHA-256 CHECKSUM required",
        "liquidation_snapshot_without_sidecar": (
            "HTTPS + ZIP CRC + optional S3 ETag MD5 + recorded local SHA-256"
        ),
        "fallback_archive_count": len(FALLBACK_ARCHIVES),
        "fallback_archives": FALLBACK_ARCHIVES,
    }
    result["data_source"] = (
        "Binance Vision archives; official checksums where published, "
        "audited ZIP/ETag/local-SHA contract for liquidation snapshots"
    )
    base.write_json(args.output / "study.json", result)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
