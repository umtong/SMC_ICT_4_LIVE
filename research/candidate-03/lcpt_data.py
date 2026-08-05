"Checksum-identifiable Binance public-data readers for LCPT."

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
from pathlib import Path
from typing import Iterable, Iterator
import zipfile

from lcpt_model import AggTrade, FiveMinuteBar, MinuteBar, NS_PER_MINUTE


FIVE_MINUTES_NS = 5 * NS_PER_MINUTE


@dataclass(frozen=True, slots=True)
class ArchiveStats:
    files: tuple[str, ...]
    sha256: tuple[str, ...]
    archive_size_bytes: tuple[int, ...]
    rows: int
    first_event_time_ns: int
    last_event_time_ns: int
    first_identifier: int
    last_identifier: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MetricsStats:
    files: tuple[str, ...]
    sha256: tuple[str, ...]
    archive_size_bytes: tuple[int, ...]
    rows: int
    invalid_rows: int
    first_time_ns: int
    last_time_ns: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_timestamp_ns(raw: int) -> int:
    """Normalize Binance historical millisecond or microsecond timestamps."""
    return raw * (1_000 if raw >= 100_000_000_000_000 else 1_000_000)


class AggTradeArchiveStream:
    """Ordered aggregate-trade stream with archive identity and continuity checks."""

    def __init__(self, paths: Iterable[str | Path]) -> None:
        self.paths = tuple(sorted((Path(path).resolve() for path in paths), key=lambda path: path.name))
        if not self.paths:
            raise ValueError("at least one aggregate-trade archive is required")
        for path in self.paths:
            if not path.is_file():
                raise FileNotFoundError(path)
        self._hashes = tuple(sha256_file(path) for path in self.paths)
        self._sizes = tuple(path.stat().st_size for path in self.paths)
        self._rows = 0
        self._first_time = -1
        self._last_time = -1
        self._first_id = -1
        self._last_id = -1

    def __iter__(self) -> Iterator[AggTrade]:
        previous_time = -1
        previous_id = -1
        rows = 0
        first_time = first_id = -1
        last_time = last_id = -1

        for path in self.paths:
            with zipfile.ZipFile(path) as archive:
                members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if len(members) != 1:
                    raise ValueError(f"expected one CSV in {path}, found {members}")
                with archive.open(members[0]) as raw_stream:
                    reader = csv.reader(io.TextIOWrapper(raw_stream, encoding="utf-8", newline=""))
                    for row in reader:
                        if not row:
                            continue
                        token = row[0]
                        if not token or not token[0].isdigit():
                            continue
                        if len(row) < 7:
                            raise ValueError(f"aggregate-trade row has {len(row)} columns in {path}")
                        aggregate_id = int(token)
                        event_time_ns = normalize_timestamp_ns(int(row[5]))
                        if event_time_ns < previous_time:
                            raise ValueError("aggregate-trade event time moved backwards")
                        if aggregate_id <= previous_id:
                            raise ValueError("aggregate-trade identifier is duplicate or non-monotonic")
                        price = float(row[1])
                        quantity = float(row[2])
                        aggressor_sign = -1 if row[6][0] in ("t", "T") else 1
                        trade = AggTrade(
                            aggregate_id=aggregate_id,
                            price=price,
                            quantity=quantity,
                            event_time_ns=event_time_ns,
                            aggressor_sign=aggressor_sign,
                        )
                        if rows == 0:
                            first_time = event_time_ns
                            first_id = aggregate_id
                        rows += 1
                        last_time = event_time_ns
                        last_id = aggregate_id
                        previous_time = event_time_ns
                        previous_id = aggregate_id
                        yield trade

        self._rows = rows
        self._first_time = first_time
        self._last_time = last_time
        self._first_id = first_id
        self._last_id = last_id

    def stats(self) -> ArchiveStats:
        if self._rows <= 0:
            raise ValueError("stream must be consumed before requesting stats")
        return ArchiveStats(
            files=tuple(path.as_posix() for path in self.paths),
            sha256=self._hashes,
            archive_size_bytes=self._sizes,
            rows=self._rows,
            first_event_time_ns=self._first_time,
            last_event_time_ns=self._last_time,
            first_identifier=self._first_id,
            last_identifier=self._last_id,
        )


def aggregate_minute_bars(stream: AggTradeArchiveStream) -> dict[int, MinuteBar]:
    bars: dict[int, MinuteBar] = {}
    current: MinuteBar | None = None
    previous_minute = -1

    for trade in stream:
        minute = (trade.event_time_ns // NS_PER_MINUTE) * NS_PER_MINUTE
        if current is None:
            current = MinuteBar.from_trade(trade)
            previous_minute = minute
            continue
        if minute == current.minute_start_ns:
            current.add(trade)
            continue
        if minute < current.minute_start_ns:
            raise ValueError("minute moved backwards")
        if minute != current.minute_start_ns + NS_PER_MINUTE:
            raise ValueError(
                f"missing aggregate-trade minute: {current.minute_start_ns} -> {minute}",
            )
        bars[current.minute_start_ns] = current
        current = MinuteBar.from_trade(trade)
        previous_minute = minute

    if current is not None:
        bars[current.minute_start_ns] = current
    if not bars:
        raise ValueError("no minute bars were produced")
    return bars


def build_five_minute_bars(minutes: dict[int, MinuteBar]) -> dict[int, FiveMinuteBar]:
    groups: dict[int, list[MinuteBar]] = {}
    for minute_ns, bar in sorted(minutes.items()):
        boundary = ((minute_ns // FIVE_MINUTES_NS) + 1) * FIVE_MINUTES_NS
        groups.setdefault(boundary, []).append(bar)

    output: dict[int, FiveMinuteBar] = {}
    for boundary, bars in sorted(groups.items()):
        expected = [boundary - FIVE_MINUTES_NS + index * NS_PER_MINUTE for index in range(5)]
        observed = [bar.minute_start_ns for bar in bars]
        if observed != expected:
            raise ValueError(f"incomplete five-minute bar at {boundary}: {observed!r}")
        output[boundary] = FiveMinuteBar(
            boundary_ns=boundary,
            open=bars[0].open,
            high=max(bar.high for bar in bars),
            low=min(bar.low for bar in bars),
            close=bars[-1].close,
            volume=sum(bar.volume for bar in bars),
            notional=sum(bar.notional for bar in bars),
            signed_notional=sum(bar.signed_notional for bar in bars),
            trade_count=sum(bar.trade_count for bar in bars),
        )
    return output


def parse_metrics_time_ns(raw: str) -> int:
    moment = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1_000_000_000)


def load_open_interest_metrics(
    paths: Iterable[str | Path],
) -> tuple[dict[int, float], MetricsStats]:
    ordered = tuple(sorted((Path(path).resolve() for path in paths), key=lambda path: path.name))
    if not ordered:
        raise ValueError("at least one metrics archive is required")
    for path in ordered:
        if not path.is_file():
            raise FileNotFoundError(path)

    hashes = tuple(sha256_file(path) for path in ordered)
    sizes = tuple(path.stat().st_size for path in ordered)
    values: dict[int, float] = {}
    previous_time = -1
    rows = 0
    invalid_rows = 0

    for path in ordered:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one CSV in {path}, found {members}")
            with archive.open(members[0]) as raw_stream:
                reader = csv.DictReader(io.TextIOWrapper(raw_stream, encoding="utf-8", newline=""))
                required = {"create_time", "sum_open_interest"}
                if not reader.fieldnames or not required.issubset(reader.fieldnames):
                    raise ValueError(f"metrics columns missing in {path}: {reader.fieldnames}")
                for row in reader:
                    timestamp_ns = parse_metrics_time_ns(row["create_time"])
                    if timestamp_ns < previous_time:
                        raise ValueError("metrics time moved backwards")
                    if timestamp_ns in values:
                        raise ValueError(f"duplicate metrics timestamp: {timestamp_ns}")
                    value = float(row["sum_open_interest"])
                    previous_time = timestamp_ns
                    rows += 1
                    # Binance's historical metrics occasionally contains explicit
                    # zero snapshots during provider outages. Zero is not an
                    # economic OI observation. Preserve it in the row count but
                    # exclude it from causal state construction rather than
                    # manufacturing a liquidation event.
                    if value <= 0:
                        invalid_rows += 1
                        continue
                    values[timestamp_ns] = value

    if not values:
        raise ValueError("metrics archives contained no rows")
    times = sorted(values)
    stats = MetricsStats(
        files=tuple(path.as_posix() for path in ordered),
        sha256=hashes,
        archive_size_bytes=sizes,
        rows=rows,
        invalid_rows=invalid_rows,
        first_time_ns=times[0],
        last_time_ns=times[-1],
    )
    return values, stats
