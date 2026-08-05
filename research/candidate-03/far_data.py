"""Checksum-identifiable, ordered Binance USD-M aggregate-trade reader."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
import io
from pathlib import Path
from typing import Iterable, Iterator
import zipfile


@dataclass(frozen=True, slots=True)
class StreamStats:
    files: tuple[str, ...]
    sha256: tuple[str, ...]
    archive_size_bytes: tuple[int, ...]
    rows: int
    first_event_time_ns: int
    last_event_time_ns: int
    first_aggregate_id: int
    last_aggregate_id: int


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_timestamp_ns(raw: int) -> int:
    """Accept historical millisecond and newer microsecond public archives."""
    return raw * (1_000 if raw >= 100_000_000_000_000 else 1_000_000)


class AggTradeArchiveStream:
    """Streams primitive tuples to keep multi-million-event replay efficient.

    Each yielded item is ``(aggregate_id, price, quantity, event_time_ns,
    aggressor_sign)`` where aggressor_sign is +1 for buyer-initiated and -1 for
    seller-initiated activity.
    """

    def __init__(self, paths: Iterable[str | Path]) -> None:
        self.paths = tuple(sorted((Path(path).resolve() for path in paths), key=lambda path: path.name))
        if not self.paths:
            raise ValueError("at least one aggregate-trade archive is required")
        for path in self.paths:
            if not path.is_file():
                raise FileNotFoundError(path)
        self._hashes = tuple(sha256_file(path) for path in self.paths)
        self._sizes = tuple(path.stat().st_size for path in self.paths)
        self.rows = 0
        self.first_event_time_ns = -1
        self.last_event_time_ns = -1
        self.first_aggregate_id = -1
        self.last_aggregate_id = -1

    def __iter__(self) -> Iterator[tuple[int, float, float, int, int]]:
        previous_time = -1
        previous_id = -1
        rows = 0
        first_time = first_id = -1
        last_time = last_id = -1
        for path in self.paths:
            with zipfile.ZipFile(path) as archive:
                csv_members = [member for member in archive.namelist() if member.lower().endswith(".csv")]
                if len(csv_members) != 1:
                    raise ValueError(f"expected exactly one CSV in {path}, found {csv_members}")
                with archive.open(csv_members[0]) as raw_stream:
                    reader = csv.reader(io.TextIOWrapper(raw_stream, encoding="utf-8", newline=""))
                    for row in reader:
                        if not row:
                            continue
                        token = row[0]
                        if not token[0].isdigit():
                            if rows == 0 or token.lower().startswith("agg"):
                                continue
                            raise ValueError(f"invalid aggregate id {token!r}")
                        aggregate_id = int(token)
                        if len(row) < 7:
                            raise ValueError(f"aggregate-trade row has {len(row)} columns in {path}")
                        raw_timestamp = int(row[5])
                        event_time_ns = raw_timestamp * (
                            1_000 if raw_timestamp >= 100_000_000_000_000 else 1_000_000
                        )
                        if event_time_ns < previous_time:
                            raise ValueError("aggregate-trade event time moved backwards")
                        if aggregate_id <= previous_id:
                            raise ValueError("aggregate-trade identifier is duplicate or non-monotonic")
                        price = float(row[1])
                        quantity = float(row[2])
                        if price <= 0 or quantity <= 0:
                            raise ValueError("aggregate-trade price and quantity must be positive")
                        aggressor_sign = -1 if row[6][0] in ("t", "T") else 1
                        previous_time = last_time = event_time_ns
                        previous_id = last_id = aggregate_id
                        if rows == 0:
                            first_time = event_time_ns
                            first_id = aggregate_id
                        rows += 1
                        yield aggregate_id, price, quantity, event_time_ns, aggressor_sign
        self.rows = rows
        self.first_event_time_ns = first_time
        self.last_event_time_ns = last_time
        self.first_aggregate_id = first_id
        self.last_aggregate_id = last_id

    def stats(self) -> StreamStats:
        if self.rows == 0:
            raise ValueError("aggregate-trade stream was not consumed")
        return StreamStats(
            files=tuple(path.as_posix() for path in self.paths),
            sha256=self._hashes,
            archive_size_bytes=self._sizes,
            rows=self.rows,
            first_event_time_ns=self.first_event_time_ns,
            last_event_time_ns=self.last_event_time_ns,
            first_aggregate_id=self.first_aggregate_id,
            last_aggregate_id=self.last_aggregate_id,
        )
