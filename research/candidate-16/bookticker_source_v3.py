"""Pinned minimal Candidate 03 Binance Vision source contract.

Copied without semantic changes from:

- branch: ``research/candidate-03``
- file: ``research/candidate-03/nt_lvcfr_data.py``
- source blob: ``f096b6dfd3944f559983010c03cd61622ee8c977``

Only the reusable archive/checksum/timestamp/CSV functions needed by Candidate
16 v3 are retained.  This module prepares observations only; it cannot create
orders, fills, positions, PnL, or NAV.
"""
from __future__ import annotations

import csv
import io
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

CANDIDATE03_SOURCE_BLOB = "f096b6dfd3944f559983010c03cd61622ee8c977"


@dataclass(frozen=True, slots=True)
class SourceFile:
    kind: str
    source_url: str
    local_path: str
    sha256: str
    size_bytes: int


def sha256_file(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_timestamp_ns(raw: int) -> int:
    """Normalize Binance millisecond or microsecond epochs to nanoseconds."""
    return raw * (1_000 if raw >= 100_000_000_000_000 else 1_000_000)


def _download(url: str, destination: Path, attempts: int = 5) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-research"},
            )
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                destination.open("wb") as target,
            ):
                shutil.copyfileobj(response, target, length=1024 * 1024)
            return
        except (
            OSError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
        ) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt + 1 == attempts:
                break
            import time

            time.sleep(2**attempt)
    raise RuntimeError(
        f"download failed after {attempts} attempts: {url}",
    ) from last_error


def download_verified(url: str, output: Path, kind: str) -> SourceFile:
    output.mkdir(parents=True, exist_ok=True)
    destination = output / url.rsplit("/", 1)[-1]
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    if not destination.exists():
        _download(url, destination)
    _download(url + ".CHECKSUM", checksum_path)
    expected = (
        checksum_path.read_text(encoding="utf-8")
        .strip()
        .split()[0]
        .lower()
    )
    actual = sha256_file(destination).lower()
    if actual != expected:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"checksum mismatch for {destination.name}: {actual} != {expected}",
        )
    return SourceFile(
        kind,
        url,
        destination.as_posix(),
        actual,
        destination.stat().st_size,
    )


def one_csv_reader(path: Path) -> tuple[zipfile.ZipFile, csv.reader]:
    archive = zipfile.ZipFile(path)
    names = [
        name
        for name in archive.namelist()
        if name.lower().endswith(".csv")
    ]
    if len(names) != 1:
        archive.close()
        raise ValueError(f"expected one CSV in {path}, found {names}")
    raw = archive.open(names[0])
    text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
    return archive, csv.reader(text)


__all__ = [
    "CANDIDATE03_SOURCE_BLOB",
    "SourceFile",
    "download_verified",
    "normalize_timestamp_ns",
    "one_csv_reader",
    "sha256_file",
]
