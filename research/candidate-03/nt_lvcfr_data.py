"""Checksum-verified data preparation for the NautilusTrader LVCFR candidate.

This module does not simulate orders, fills, positions, PnL, or NAV. It only:
- downloads and identifies official Binance Vision source archives;
- converts causally observable market facts into a frozen signal schedule;
- writes execution quotes, funding updates, and instrument metadata into a
  NautilusTrader ``ParquetDataCatalog``.
"""
from __future__ import annotations

import csv
import io
import json
import math
import shutil
import urllib.error
import urllib.request
import zipfile
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Iterable, Iterator, Mapping, Sequence

NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND
NS_PER_DAY = 24 * 60 * NS_PER_MINUTE
FIVE_MINUTES_NS = 5 * NS_PER_MINUTE
BINANCE_VISION = "https://data.binance.vision/data"


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    candidate: str
    instrument_id: str
    initial_nav: float
    first_displacement_bp: float
    second_activity_min: float
    second_futures_flow_max: float
    second_spot_flow_min: float
    total_oi_drop_bp: float
    atr_minutes: int
    activity_baseline_5m: int
    activity_min_periods: int
    initial_stop_buffer_atr: float
    entry_buffer_minutes: int
    continuation_target_net_r: float
    continuation_protection_activate_r: float
    continuation_protection_lock_r: float
    continuation_trail_minutes: int
    continuation_trail_buffer_atr: float
    continuation_max_holding_minutes: int
    rapid_failure_minutes: int
    reversal_entry_delay_minutes: int
    reversal_stop_buffer_atr: float
    reversal_target_net_r: float
    reversal_max_holding_minutes: int
    risk_fraction: float
    taker_fee_bps: float
    slippage_impact_bps: float
    maintenance_margin_fraction: float
    minimum_episodes: int
    minimum_win_rate: float
    minimum_daily_geometric_growth: float
    maximum_mark_to_market_drawdown: float
    validation_weeks: tuple[str, ...]

    @classmethod
    def load(cls, path: str | Path) -> "CandidateConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["validation_weeks"] = tuple(payload["validation_weeks"])
        config = cls(**payload)
        config.validate()
        return config

    def validate(self) -> None:
        if not 0 < self.risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if self.initial_nav <= 0:
            raise ValueError("initial_nav must be positive")
        if not 0 <= self.minimum_win_rate <= 1:
            raise ValueError("minimum_win_rate must be in [0, 1]")
        if not 0 < self.maximum_mark_to_market_drawdown < 1:
            raise ValueError("maximum drawdown must be in (0, 1)")
        if self.entry_buffer_minutes <= 0 or self.atr_minutes <= 0:
            raise ValueError("time windows must be positive")


@dataclass(frozen=True, slots=True)
class MinuteFact:
    minute_index: int
    open: float
    high: float
    low: float
    close: float
    notional: float
    signed_notional: float

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FiveMinuteFact:
    end_time_ns: int
    open: float
    high: float
    low: float
    close: float
    notional: float
    signed_notional: float

    @property
    def flow(self) -> float:
        return self.signed_notional / self.notional if self.notional > 0 else 0.0

    @property
    def return_bp(self) -> float:
        return (self.close / self.open - 1.0) * 10_000.0


@dataclass(frozen=True, slots=True)
class Signal:
    scenario_id: str
    confirm_time_ns: int
    eligible_time_ns: int
    direction: int
    initial_stop: float
    atr: float
    first_start_time_ns: int
    first_end_time_ns: int
    details: Mapping[str, float]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["details"] = dict(self.details)
        return payload


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
    return raw * (1_000 if raw >= 100_000_000_000_000 else 1_000_000)


def date_to_ns(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1e9)


def _download(url: str, destination: Path, attempts: int = 5) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "SMC-ICT-4-research"})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
                shutil.copyfileobj(response, target, length=1024 * 1024)
            return
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt + 1 == attempts:
                break
            import time
            time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {attempts} attempts: {url}") from last_error


def download_verified(url: str, output: Path, kind: str) -> SourceFile:
    output.mkdir(parents=True, exist_ok=True)
    destination = output / url.rsplit("/", 1)[-1]
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    if not destination.exists():
        _download(url, destination)
    _download(url + ".CHECKSUM", checksum_path)
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(destination).lower()
    if actual != expected:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {destination.name}: {actual} != {expected}")
    return SourceFile(kind, url, destination.as_posix(), actual, destination.stat().st_size)


def _daily_dates(start: date, end_inclusive: date) -> Iterator[date]:
    current = start
    while current <= end_inclusive:
        yield current
        current += timedelta(days=1)


def _one_csv_reader(path: Path) -> tuple[zipfile.ZipFile, csv.reader]:
    archive = zipfile.ZipFile(path)
    names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(names) != 1:
        archive.close()
        raise ValueError(f"expected one CSV in {path}, found {names}")
    raw = archive.open(names[0])
    text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
    return archive, csv.reader(text)


def load_kline_minutes(paths: Sequence[Path]) -> list[MinuteFact]:
    result: list[MinuteFact] = []
    previous = -1
    for path in sorted(paths, key=lambda item: item.name):
        archive, reader = _one_csv_reader(path)
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                if len(row) < 11:
                    raise ValueError(f"kline row too short in {path}: {row}")
                open_time_ns = normalize_timestamp_ns(int(row[0]))
                minute_index = open_time_ns // NS_PER_MINUTE
                if minute_index <= previous:
                    raise ValueError("kline minute is duplicate or non-monotonic")
                quote_volume = float(row[7])
                taker_buy_quote = float(row[10])
                result.append(
                    MinuteFact(
                        minute_index=minute_index,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        notional=quote_volume,
                        signed_notional=2.0 * taker_buy_quote - quote_volume,
                    )
                )
                previous = minute_index
        finally:
            archive.close()
    if not result:
        raise ValueError("no kline minutes loaded")
    return result


def load_open_interest(paths: Sequence[Path]) -> dict[int, float]:
    output: dict[int, float] = {}
    previous = -1
    for path in sorted(paths, key=lambda item: item.name):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one CSV in {path}")
            with archive.open(names[0]) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
                if not reader.fieldnames or not {"create_time", "sum_open_interest"}.issubset(reader.fieldnames):
                    raise ValueError(f"unexpected metrics columns: {reader.fieldnames}")
                for row in reader:
                    moment = datetime.strptime(row["create_time"].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    timestamp_ns = int(moment.timestamp() * 1e9)
                    if timestamp_ns <= previous:
                        raise ValueError("OI time is duplicate or non-monotonic")
                    previous = timestamp_ns
                    value = float(row["sum_open_interest"])
                    if value > 0:
                        output[timestamp_ns] = value
    if not output:
        raise ValueError("no positive open-interest observations")
    return output


def _atr(minutes: Sequence[MinuteFact], window: int) -> dict[int, float]:
    history: deque[float] = deque(maxlen=window)
    previous_close: float | None = None
    previous_minute: int | None = None
    output: dict[int, float] = {}
    for bar in minutes:
        if previous_minute is not None and bar.minute_index != previous_minute + 1:
            raise ValueError(f"missing minute: {previous_minute} -> {bar.minute_index}")
        true_range = bar.high - bar.low
        if previous_close is not None:
            true_range = max(true_range, abs(bar.high - previous_close), abs(bar.low - previous_close))
        history.append(true_range)
        if len(history) == window:
            output[bar.minute_index] = sum(history) / window
        previous_close = bar.close
        previous_minute = bar.minute_index
    return output


def _five(minutes: Sequence[MinuteFact]) -> dict[int, FiveMinuteFact]:
    grouped: dict[int, list[MinuteFact]] = {}
    for bar in minutes:
        end_minute = (bar.minute_index // 5 + 1) * 5
        grouped.setdefault(end_minute, []).append(bar)
    output: dict[int, FiveMinuteFact] = {}
    for end_minute, members in sorted(grouped.items()):
        expected = list(range(end_minute - 5, end_minute))
        if [item.minute_index for item in members] != expected:
            continue
        output[end_minute * NS_PER_MINUTE] = FiveMinuteFact(
            end_time_ns=end_minute * NS_PER_MINUTE,
            open=members[0].open,
            high=max(item.high for item in members),
            low=min(item.low for item in members),
            close=members[-1].close,
            notional=sum(item.notional for item in members),
            signed_notional=sum(item.signed_notional for item in members),
        )
    return output


def detect_signals(
    futures_minutes: Sequence[MinuteFact],
    spot_minutes: Sequence[MinuteFact],
    open_interest: Mapping[int, float],
    *,
    start_ns: int,
    end_ns: int,
    config: CandidateConfig,
) -> tuple[list[Signal], dict[int, float]]:
    futures_by_minute = {bar.minute_index: bar for bar in futures_minutes}
    atr = _atr(futures_minutes, config.atr_minutes)
    futures_five = _five(futures_minutes)
    spot_five = _five(spot_minutes)
    aligned = sorted(set(futures_five) & set(spot_five) & set(open_interest))
    signals: list[Signal] = []
    for index in range(2, len(aligned)):
        second_end = aligned[index]
        if not start_ns <= second_end < end_ns:
            continue
        first_end = aligned[index - 1]
        before_end = aligned[index - 2]
        if second_end - first_end != FIVE_MINUTES_NS or first_end - before_end != FIVE_MINUTES_NS:
            continue
        baseline_times = aligned[max(0, index - config.activity_baseline_5m) : index]
        baseline = [futures_five[timestamp].notional for timestamp in baseline_times]
        if len(baseline) < config.activity_min_periods:
            continue
        baseline_notional = median(baseline)
        if baseline_notional <= 0:
            continue
        first = futures_five[first_end]
        second = futures_five[second_end]
        second_spot = spot_five[second_end]
        direction = 1 if first.return_bp > 0 else (-1 if first.return_bp < 0 else 0)
        if direction == 0:
            continue
        first_displacement = direction * first.return_bp
        second_progress = direction * (second.close / first.close - 1.0) * 10_000.0
        oi_before = open_interest[before_end]
        oi_first = open_interest[first_end]
        oi_second = open_interest[second_end]
        first_oi_drop = -(oi_first / oi_before - 1.0) * 10_000.0
        second_oi_drop = -(oi_second / oi_first - 1.0) * 10_000.0
        total_oi_drop = -(oi_second / oi_before - 1.0) * 10_000.0
        activity_ratio = second.notional / baseline_notional
        futures_flow = direction * second.flow
        spot_flow = direction * second_spot.flow
        at = atr.get(second_end // NS_PER_MINUTE - 1)
        values = (first_displacement, second_progress, first_oi_drop, second_oi_drop, total_oi_drop, activity_ratio, futures_flow, spot_flow, at or float("nan"))
        if not all(math.isfinite(value) for value in values) or at is None or at <= 0:
            continue
        if not (
            first_displacement >= config.first_displacement_bp
            and second_progress > 0
            and first_oi_drop > 0
            and second_oi_drop > 0
            and total_oi_drop >= config.total_oi_drop_bp
            and activity_ratio >= config.second_activity_min
            and futures_flow <= config.second_futures_flow_max
            and spot_flow >= config.second_spot_flow_min
        ):
            continue
        first_start_minute = first_end // NS_PER_MINUTE - 5
        event = [futures_by_minute.get(value) for value in range(first_start_minute, second_end // NS_PER_MINUTE)]
        if len(event) != 10 or any(value is None for value in event):
            continue
        completed = [value for value in event if value is not None]
        event_low = min(value.low for value in completed)
        event_high = max(value.high for value in completed)
        stop = event_low - config.initial_stop_buffer_atr * at if direction > 0 else event_high + config.initial_stop_buffer_atr * at
        scenario_id = "NT-LVCFR-" + sha256(f"{second_end}|{direction}|{first.close:.12g}|{second.close:.12g}".encode()).hexdigest()[:16]
        signals.append(
            Signal(
                scenario_id=scenario_id,
                confirm_time_ns=second_end,
                eligible_time_ns=second_end + config.entry_buffer_minutes * NS_PER_MINUTE,
                direction=direction,
                initial_stop=stop,
                atr=at,
                first_start_time_ns=first_start_minute * NS_PER_MINUTE,
                first_end_time_ns=first_end,
                details={
                    "first_displacement_bp": first_displacement,
                    "second_progress_bp": second_progress,
                    "first_oi_drop_bp": first_oi_drop,
                    "second_oi_drop_bp": second_oi_drop,
                    "total_oi_drop_bp": total_oi_drop,
                    "second_activity_ratio": activity_ratio,
                    "second_futures_flow": futures_flow,
                    "second_spot_flow": spot_flow,
                    "two_bar_low": event_low,
                    "two_bar_high": event_high,
                    "activity_baseline_notional": baseline_notional,
                },
            )
        )
    return signals, atr


def merge_windows(signals: Sequence[Signal], config: CandidateConfig, end_ns: int) -> list[tuple[int, int]]:
    horizon_minutes = max(
        config.continuation_max_holding_minutes,
        config.rapid_failure_minutes + config.reversal_entry_delay_minutes + config.reversal_max_holding_minutes,
    ) + 5
    windows = sorted((signal.confirm_time_ns, min(end_ns + 2 * NS_PER_MINUTE, signal.confirm_time_ns + horizon_minutes * NS_PER_MINUTE)) for signal in signals)
    merged: list[list[int]] = []
    for start, end in windows:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _in_windows(timestamp_ns: int, windows: Sequence[tuple[int, int]], pointer: int) -> tuple[bool, int]:
    while pointer < len(windows) and timestamp_ns > windows[pointer][1]:
        pointer += 1
    if pointer >= len(windows):
        return False, pointer
    start, end = windows[pointer]
    return start <= timestamp_ns <= end, pointer


def _quantized_price(value: float, *, ask: bool) -> str:
    rounding = ROUND_CEILING if ask else ROUND_FLOOR
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=rounding))


def build_catalog(
    *,
    catalog_path: Path,
    book_ticker_paths: Sequence[Path],
    funding_paths: Sequence[Path],
    signals: Sequence[Signal],
    config: CandidateConfig,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
) -> dict[str, object]:
    from nautilus_trader.model.data import FundingRateUpdate, QuoteTick
    from nautilus_trader.model.identifiers import InstrumentId, Symbol
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    shutil.rmtree(catalog_path, ignore_errors=True)
    catalog_path.mkdir(parents=True, exist_ok=True)
    usdt = Currency.from_str("USDT")
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId.from_str(config.instrument_id),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=evaluation_start_ns,
        ts_init=evaluation_start_ns,
        multiplier=Quantity.from_int(1),
        lot_size=Quantity.from_str("0.001"),
        min_quantity=Quantity.from_str("0.001"),
        min_notional=Money.from_str("5 USDT"),
        margin_init=Decimal("0"),
        margin_maint=Decimal(str(config.maintenance_margin_fraction)),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal(str(config.taker_fee_bps / 10_000.0)),
    )
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument])

    impact = config.slippage_impact_bps / 10_000.0
    windows = merge_windows(signals, config, evaluation_end_ns)
    rows = 0
    retained = 0
    previous_init = -1
    pointer = 0
    batch: list[QuoteTick] = []
    outside_last: tuple[int, float, float, float, float, int, int] | None = None
    outside_minute = -1

    def append_quote(values: tuple[int, float, float, float, float, int, int]) -> None:
        nonlocal retained, previous_init
        _, bid, bid_qty, ask, ask_qty, ts_event, ts_init = values
        if ts_init < previous_init:
            raise ValueError("bookTicker observed time moved backwards")
        previous_init = ts_init
        adjusted_bid = bid * (1.0 - impact)
        adjusted_ask = ask * (1.0 + impact)
        bid_string = _quantized_price(adjusted_bid, ask=False)
        ask_string = _quantized_price(adjusted_ask, ask=True)
        bid_size = instrument.make_qty(bid_qty)
        ask_size = instrument.make_qty(ask_qty)
        if float(bid_size) <= 0 or float(ask_size) <= 0:
            return
        batch.append(
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=Price.from_str(bid_string),
                ask_price=Price.from_str(ask_string),
                bid_size=bid_size,
                ask_size=ask_size,
                ts_event=ts_event,
                ts_init=ts_init,
            )
        )
        retained += 1
        if len(batch) >= 100_000:
            catalog.write_data(batch)
            batch.clear()

    for path in sorted(book_ticker_paths, key=lambda item: item.name):
        archive, reader = _one_csv_reader(path)
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                rows += 1
                if len(row) < 7:
                    raise ValueError(f"bookTicker row too short in {path}")
                update_id = int(row[0])
                bid = float(row[1]); bid_qty = float(row[2]); ask = float(row[3]); ask_qty = float(row[4])
                transaction_ns = normalize_timestamp_ns(int(row[5]))
                observed_ns = normalize_timestamp_ns(int(row[6]))
                observed_ns = max(observed_ns, transaction_ns)
                if observed_ns < evaluation_start_ns or observed_ns > evaluation_end_ns + 2 * NS_PER_MINUTE:
                    continue
                values = (update_id, bid, bid_qty, ask, ask_qty, transaction_ns, observed_ns)
                inside, pointer = _in_windows(observed_ns, windows, pointer)
                if inside:
                    if outside_last is not None:
                        append_quote(outside_last)
                        outside_last = None
                    append_quote(values)
                else:
                    minute = observed_ns // NS_PER_MINUTE
                    if outside_last is not None and minute != outside_minute:
                        append_quote(outside_last)
                    outside_last = values
                    outside_minute = minute
        finally:
            archive.close()
    if outside_last is not None:
        append_quote(outside_last)
    if batch:
        catalog.write_data(batch)
        batch.clear()
    if retained == 0:
        raise ValueError("no execution quotes retained")

    funding_updates: list[FundingRateUpdate] = []
    for path in sorted(funding_paths, key=lambda item: item.name):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one funding CSV in {path}")
            with archive.open(names[0]) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
                if not reader.fieldnames:
                    raise ValueError("funding archive has no header")
                for row in reader:
                    timestamp_token = row.get("calc_time") or row.get("funding_time") or row.get("fundingTime")
                    rate_token = row.get("last_funding_rate") or row.get("funding_rate") or row.get("fundingRate")
                    if timestamp_token is None or rate_token is None:
                        raise ValueError(f"unsupported funding columns: {reader.fieldnames}")
                    raw_timestamp = int(timestamp_token)
                    timestamp_ns = normalize_timestamp_ns(raw_timestamp)
                    if not evaluation_start_ns - NS_PER_DAY <= timestamp_ns <= evaluation_end_ns + 2 * NS_PER_MINUTE:
                        continue
                    interval_hours = row.get("funding_interval_hours") or row.get("fundingIntervalHours")
                    interval = int(float(interval_hours) * 60) if interval_hours else 480
                    funding_updates.append(
                        FundingRateUpdate(
                            instrument_id=instrument.id,
                            rate=Decimal(rate_token),
                            ts_event=timestamp_ns,
                            ts_init=timestamp_ns,
                            interval=interval,
                            next_funding_ns=None,
                        )
                    )
    if funding_updates:
        funding_updates.sort(key=lambda item: item.ts_init)
        catalog.write_data(funding_updates)

    return {
        "instrument_id": str(instrument.id),
        "book_ticker_source_rows": rows,
        "quote_ticks_retained": retained,
        "funding_updates": len(funding_updates),
        "full_resolution_windows": windows,
        "impact_bps_each_fill": config.slippage_impact_bps,
    }


def prepare_week(
    *,
    week_start: date,
    output_root: Path,
    config: CandidateConfig,
) -> dict[str, object]:
    if week_start.isoformat() not in config.validation_weeks:
        raise ValueError(f"week is not precommitted: {week_start}")
    evaluation_start_ns = date_to_ns(week_start)
    evaluation_end_ns = date_to_ns(week_start + timedelta(days=7))
    raw_root = output_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    warmup_start = week_start - timedelta(days=1)
    source_files: list[SourceFile] = []
    futures_kline: list[Path] = []
    spot_kline: list[Path] = []
    metrics: list[Path] = []
    book_ticker: list[Path] = []

    for day in _daily_dates(warmup_start, week_start + timedelta(days=6)):
        stamp = day.isoformat()
        entries = [
            ("futures_kline", f"{BINANCE_VISION}/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{stamp}.zip", futures_kline),
            ("spot_kline", f"{BINANCE_VISION}/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{stamp}.zip", spot_kline),
            ("open_interest", f"{BINANCE_VISION}/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{stamp}.zip", metrics),
        ]
        for kind, url, collection in entries:
            record = download_verified(url, raw_root / kind, kind)
            source_files.append(record)
            collection.append(Path(record.local_path))

    futures_minutes = load_kline_minutes(futures_kline)
    spot_minutes = load_kline_minutes(spot_kline)
    oi = load_open_interest(metrics)
    signals, _ = detect_signals(
        futures_minutes,
        spot_minutes,
        oi,
        start_ns=evaluation_start_ns,
        end_ns=evaluation_end_ns,
        config=config,
    )
    signal_path = output_root / "signals.json"
    signal_path.write_text(json.dumps([signal.to_dict() for signal in signals], indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for day in _daily_dates(week_start, week_start + timedelta(days=7)):
        stamp = day.isoformat()
        url = f"{BINANCE_VISION}/futures/um/daily/bookTicker/BTCUSDT/BTCUSDT-bookTicker-{stamp}.zip"
        record = download_verified(url, raw_root / "book_ticker", "book_ticker")
        source_files.append(record)
        book_ticker.append(Path(record.local_path))

    months = sorted({(week_start + timedelta(days=offset)).strftime("%Y-%m") for offset in range(8)})
    funding_paths: list[Path] = []
    for month in months:
        url = f"{BINANCE_VISION}/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{month}.zip"
        record = download_verified(url, raw_root / "funding", "funding")
        source_files.append(record)
        funding_paths.append(Path(record.local_path))

    catalog_stats = build_catalog(
        catalog_path=output_root / "catalog",
        book_ticker_paths=book_ticker,
        funding_paths=funding_paths,
        signals=signals,
        config=config,
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
    )
    manifest = {
        "candidate": config.candidate,
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=7)).isoformat(),
        "evaluation_start_ns": evaluation_start_ns,
        "evaluation_end_ns": evaluation_end_ns,
        "signals": len(signals),
        "signal_path": signal_path.as_posix(),
        "catalog_path": (output_root / "catalog").as_posix(),
        "catalog": catalog_stats,
        "sources": [asdict(item) for item in source_files],
    }
    (output_root / "data_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def prepare_signal_schedule(
    *,
    week_start: date,
    output_root: Path,
    config: CandidateConfig,
) -> dict[str, object]:
    """Prepare only causal detector inputs and the frozen signal schedule.

    This is a fail-fast opportunity preflight, not a backtest. It performs no
    order, fill, position, PnL, or NAV calculation. The formal result always
    comes from ``BacktestNode`` after this count proves the minimum episode
    requirement is reachable.
    """
    if week_start.isoformat() not in config.validation_weeks:
        raise ValueError(f"week is not precommitted: {week_start}")
    evaluation_start_ns = date_to_ns(week_start)
    evaluation_end_ns = date_to_ns(week_start + timedelta(days=7))
    raw_root = output_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    warmup_start = week_start - timedelta(days=1)
    source_files: list[SourceFile] = []
    futures_kline: list[Path] = []
    spot_kline: list[Path] = []
    metrics: list[Path] = []
    for day in _daily_dates(warmup_start, week_start + timedelta(days=6)):
        stamp = day.isoformat()
        entries = [
            ("futures_kline", f"{BINANCE_VISION}/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{stamp}.zip", futures_kline),
            ("spot_kline", f"{BINANCE_VISION}/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{stamp}.zip", spot_kline),
            ("open_interest", f"{BINANCE_VISION}/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{stamp}.zip", metrics),
        ]
        for kind, url, collection in entries:
            record = download_verified(url, raw_root / kind, kind)
            source_files.append(record)
            collection.append(Path(record.local_path))
    futures_minutes = load_kline_minutes(futures_kline)
    spot_minutes = load_kline_minutes(spot_kline)
    oi = load_open_interest(metrics)
    signals, _ = detect_signals(
        futures_minutes,
        spot_minutes,
        oi,
        start_ns=evaluation_start_ns,
        end_ns=evaluation_end_ns,
        config=config,
    )
    signal_path = output_root / "signals.json"
    signal_path.write_text(
        json.dumps([signal.to_dict() for signal in signals], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "candidate": config.candidate,
        "engine_status": "opportunity_preflight_only_no_backtest",
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=7)).isoformat(),
        "signals": len(signals),
        "minimum_possible_episodes": min(len(signals), config.minimum_episodes),
        "minimum_required_episodes": config.minimum_episodes,
        "sources": [asdict(item) for item in source_files],
    }
    (output_root / "signal_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
