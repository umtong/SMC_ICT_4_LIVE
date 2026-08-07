"""Checksum-verified Binance USD-M aggTrades compressed into causal auction profiles.

The raw trade archive is used only to build completed volume-at-price profiles
and completed one-minute aggressive-flow summaries.  NautilusTrader still owns
all event replay, orders, fills, positions, fees and portfolio accounting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
import time
from typing import Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


BASE_URL = "https://data.binance.vision/data/futures/um/daily/aggTrades"
ONE_MINUTE_MS = 60_000


@dataclass(frozen=True, slots=True)
class AggMinuteStat:
    end_ts_ns: int
    total_volume: float
    signed_aggressive_volume: float
    trades: int
    high: float
    low: float
    close: float

    @property
    def flow_ratio(self) -> float:
        return self.signed_aggressive_volume / self.total_volume if self.total_volume > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class AuctionProfile:
    start_ts_ns: int
    end_ts_ns: int
    open: float
    high: float
    low: float
    close: float
    total_volume: float
    signed_aggressive_volume: float
    trades: int
    poc: float
    val: float
    vah: float
    value_volume_fraction: float
    poc_concentration: float
    lower_tail_share: float
    upper_tail_share: float

    @property
    def width(self) -> float:
        return max(self.vah - self.val, 0.0)

    @property
    def range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def directional_efficiency(self) -> float:
        return abs(self.close - self.open) / self.range if self.range > 0.0 else 0.0

    @property
    def delta_ratio(self) -> float:
        return self.signed_aggressive_volume / self.total_volume if self.total_volume > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class LoadedAggTradeProfiles:
    profiles: Mapping[int, AuctionProfile]
    minute_stats: Mapping[int, AggMinuteStat]
    source_files: tuple[Path, ...]
    quality: Mapping[str, object]


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _request_bytes(url: str, *, attempts: int = 4) -> bytes:
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-06-BAVR/1.0"})
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=120) as response:  # noqa: S310 - fixed HTTPS host
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def _expected_checksum(text: str, filename: str) -> str:
    for line in text.splitlines():
        fields = line.strip().replace("*", " ").split()
        if fields and len(fields[0]) == 64 and (len(fields) == 1 or fields[-1].endswith(filename)):
            return fields[0].lower()
    raise ValueError(f"could not parse SHA-256 checksum for {filename}")


def download_daily_archive(symbol: str, day: date, destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}-aggTrades-{day.isoformat()}.zip"
    archive = destination / filename
    checksum = destination / f"{filename}.CHECKSUM"
    url = f"{BASE_URL}/{symbol}/{filename}"
    checksum_bytes = _request_bytes(f"{url}.CHECKSUM")
    expected = _expected_checksum(checksum_bytes.decode("utf-8"), filename)
    if not archive.exists() or _sha256_file(archive) != expected:
        payload = _request_bytes(url)
        temporary = archive.with_suffix(".zip.tmp")
        temporary.write_bytes(payload)
        actual = _sha256_file(temporary)
        if actual != expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"checksum mismatch for {filename}: expected={expected}, actual={actual}")
        temporary.replace(archive)
    checksum.write_bytes(checksum_bytes)
    return archive, checksum


def _timestamp_ms(value: str) -> int:
    raw = int(value)
    # Public archives after 2025 may use microseconds.  Candidate weeks are 2024,
    # but the parser keeps the contract explicit for later validation.
    return raw // 1000 if raw >= 100_000_000_000_000 else raw


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"invalid buyer-maker flag: {value!r}")


def _value_area(price_volume: Mapping[float, float], fraction: float) -> tuple[float, float, float, float, float, float, float]:
    if not price_volume:
        raise ValueError("cannot build value area without trades")
    if not 0.5 <= fraction < 1.0:
        raise ValueError("value-area fraction must be in [0.5, 1.0)")
    levels = sorted((float(price), float(volume)) for price, volume in price_volume.items())
    total = sum(volume for _, volume in levels)
    if total <= 0.0:
        raise ValueError("profile total volume must be positive")
    vwap = sum(price * volume for price, volume in levels) / total
    maximum = max(volume for _, volume in levels)
    candidates = [index for index, (price, volume) in enumerate(levels) if volume == maximum]
    poc_index = min(candidates, key=lambda index: (abs(levels[index][0] - vwap), levels[index][0]))
    left = right = poc_index
    included = levels[poc_index][1]
    target = fraction * total
    while included < target and (left > 0 or right + 1 < len(levels)):
        left_volume = levels[left - 1][1] if left > 0 else -1.0
        right_volume = levels[right + 1][1] if right + 1 < len(levels) else -1.0
        if right_volume > left_volume:
            right += 1
            included += levels[right][1]
        elif left_volume > right_volume:
            left -= 1
            included += levels[left][1]
        else:
            # Equal adjacent volume: expand toward the side closer to VWAP,
            # then downward for deterministic symmetry.
            left_distance = abs(levels[left - 1][0] - vwap) if left > 0 else float("inf")
            right_distance = abs(levels[right + 1][0] - vwap) if right + 1 < len(levels) else float("inf")
            if left_distance <= right_distance:
                left -= 1
                included += levels[left][1]
            else:
                right += 1
                included += levels[right][1]
    val = levels[left][0]
    vah = levels[right][0]
    lower_tail = sum(volume for price, volume in levels if price < val) / total
    upper_tail = sum(volume for price, volume in levels if price > vah) / total
    return levels[poc_index][0], val, vah, included / total, maximum / total, lower_tail, upper_tail


@dataclass(slots=True)
class _ProfileAccumulator:
    start_ms: int
    end_ms: int
    open: float
    high: float
    low: float
    close: float
    total_volume: float
    signed_volume: float
    trades: int
    price_volume: dict[float, float]

    def add(self, price: float, qty: float, signed: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.total_volume += qty
        self.signed_volume += signed
        self.trades += 1
        self.price_volume[price] = self.price_volume.get(price, 0.0) + qty


@dataclass(slots=True)
class _MinuteAccumulator:
    end_ms: int
    total_volume: float
    signed_volume: float
    trades: int
    high: float
    low: float
    close: float

    def add(self, price: float, qty: float, signed: float) -> None:
        self.total_volume += qty
        self.signed_volume += signed
        self.trades += 1
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price


def _profile_from(acc: _ProfileAccumulator, value_fraction: float) -> AuctionProfile:
    poc, val, vah, actual, concentration, lower_tail, upper_tail = _value_area(
        acc.price_volume,
        value_fraction,
    )
    return AuctionProfile(
        start_ts_ns=acc.start_ms * 1_000_000,
        end_ts_ns=acc.end_ms * 1_000_000,
        open=acc.open,
        high=acc.high,
        low=acc.low,
        close=acc.close,
        total_volume=acc.total_volume,
        signed_aggressive_volume=acc.signed_volume,
        trades=acc.trades,
        poc=poc,
        val=val,
        vah=vah,
        value_volume_fraction=actual,
        poc_concentration=concentration,
        lower_tail_share=lower_tail,
        upper_tail_share=upper_tail,
    )


def load_week_profiles(
    symbol: str,
    week_start: date,
    cache_root: str | Path,
    *,
    period_minutes: int = 15,
    value_area_fraction: float = 0.70,
) -> LoadedAggTradeProfiles:
    if period_minutes < 5 or 1440 % period_minutes != 0:
        raise ValueError("profile period must be at least five minutes and divide one UTC day")
    root = Path(cache_root).resolve() / symbol / "aggTrades"
    sources: list[Path] = []
    profiles: dict[int, AuctionProfile] = {}
    minute_stats: dict[int, AggMinuteStat] = {}
    profile_acc: _ProfileAccumulator | None = None
    minute_acc: _MinuteAccumulator | None = None
    raw_rows = 0
    last_timestamp_ms = -1
    period_ms = period_minutes * ONE_MINUTE_MS

    def flush_profile() -> None:
        nonlocal profile_acc
        if profile_acc is None:
            return
        profile = _profile_from(profile_acc, value_area_fraction)
        profiles[profile.end_ts_ns] = profile
        profile_acc = None

    def flush_minute() -> None:
        nonlocal minute_acc
        if minute_acc is None:
            return
        stat = AggMinuteStat(
            end_ts_ns=minute_acc.end_ms * 1_000_000,
            total_volume=minute_acc.total_volume,
            signed_aggressive_volume=minute_acc.signed_volume,
            trades=minute_acc.trades,
            high=minute_acc.high,
            low=minute_acc.low,
            close=minute_acc.close,
        )
        minute_stats[stat.end_ts_ns] = stat
        minute_acc = None

    for offset in range(7):
        day = week_start + timedelta(days=offset)
        archive, checksum = download_daily_archive(symbol, day, root)
        sources.extend((archive, checksum))
        with zipfile.ZipFile(archive) as bundle:
            members = [name for name in bundle.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one CSV in {archive}, found {members}")
            with bundle.open(members[0]) as raw:
                reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
                for row in reader:
                    if not row:
                        continue
                    if not row[0].strip().lstrip("-").isdigit():
                        continue
                    if len(row) < 7:
                        raise ValueError(f"short aggTrade row in {archive}: {row!r}")
                    price = float(row[1])
                    qty = float(row[2])
                    timestamp_ms = _timestamp_ms(row[5])
                    buyer_maker = _bool(row[6])
                    if timestamp_ms < last_timestamp_ms:
                        raise ValueError(
                            f"non-monotonic aggTrade timestamp: {timestamp_ms} < {last_timestamp_ms}",
                        )
                    last_timestamp_ms = timestamp_ms
                    signed = -qty if buyer_maker else qty
                    raw_rows += 1

                    minute_end = (timestamp_ms // ONE_MINUTE_MS + 1) * ONE_MINUTE_MS
                    if minute_acc is None or minute_acc.end_ms != minute_end:
                        flush_minute()
                        minute_acc = _MinuteAccumulator(
                            end_ms=minute_end,
                            total_volume=qty,
                            signed_volume=signed,
                            trades=1,
                            high=price,
                            low=price,
                            close=price,
                        )
                    else:
                        minute_acc.add(price, qty, signed)

                    bucket_start = (timestamp_ms // period_ms) * period_ms
                    bucket_end = bucket_start + period_ms
                    if profile_acc is None or profile_acc.start_ms != bucket_start:
                        flush_profile()
                        profile_acc = _ProfileAccumulator(
                            start_ms=bucket_start,
                            end_ms=bucket_end,
                            open=price,
                            high=price,
                            low=price,
                            close=price,
                            total_volume=qty,
                            signed_volume=signed,
                            trades=1,
                            price_volume={price: qty},
                        )
                    else:
                        profile_acc.add(price, qty, signed)
    flush_minute()
    flush_profile()

    expected_profiles = 7 * 24 * 60 // period_minutes
    if len(profiles) != expected_profiles:
        raise ValueError(f"expected {expected_profiles} completed profiles, found {len(profiles)}")
    if not minute_stats:
        raise ValueError("aggTrades produced no minute statistics")
    quality: dict[str, object] = {
        "symbol": symbol,
        "provider": "Binance public data / USD-M futures aggTrades",
        "week_start_utc": week_start.isoformat(),
        "raw_aggregate_trade_rows": raw_rows,
        "minute_stat_count": len(minute_stats),
        "profile_count": len(profiles),
        "expected_profile_count": expected_profiles,
        "profile_period_minutes": period_minutes,
        "value_area_fraction": value_area_fraction,
        "timestamp_contract": "trade timestamp bucketed causally; completed minute/profile visible only at interval end",
        "signed_flow_contract": "buyer-maker=true is seller-aggressive negative volume; false is buyer-aggressive positive volume",
        "archives": [path.name for path in sources if path.suffix == ".zip"],
    }
    return LoadedAggTradeProfiles(
        profiles=profiles,
        minute_stats=minute_stats,
        source_files=tuple(sources),
        quality=quality,
    )


def write_profile_quality(path: str | Path, quality: Mapping[str, object]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(quality), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
