"""Uniform public execution-data preparation for LVCFR research.

Binance Vision stopped publishing USD-M ``bookTicker`` archives after
2024-03-31. This module keeps the BacktestNode/native execution path intact
while replacing the unavailable quote archive with a causal, conservative
quote proxy built only from official Binance USD-M ``aggTrades`` archives.

The proxy is data preparation, not a backtest engine:
- every retained observation uses an actual aggregate trade price, quantity,
  and exchange timestamp;
- bid/ask are placed symmetrically around that trade by the already frozen
  per-fill impact assumption;
- aggregate trade quantity is used as displayed L1 size, so native
  ``liquidity_consumption`` remains active;
- order lifecycle, fills, fees, funding, margin, liquidation, positions, and
  NAV remain NautilusTrader responsibilities.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import zipfile
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Iterator, Sequence

from nt_lvcfr_data import (
    BINANCE_VISION,
    NS_PER_DAY,
    NS_PER_MINUTE,
    NS_PER_SECOND,
    CandidateConfig,
    date_to_ns,
    download_verified,
    normalize_timestamp_ns,
    prepare_signal_schedule,
)

# id, price, quantity, buyer_was_maker, ts_event, ts_init
AggTradeRecord = tuple[int, float, float, bool, int, int]


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


def parse_aggtrade_row(row: Sequence[str]) -> AggTradeRecord:
    """Parse one official USD-M aggTrades CSV row."""
    if len(row) < 7:
        raise ValueError(f"aggTrades row too short: {row}")
    timestamp_ns = normalize_timestamp_ns(int(row[5]))
    return (
        int(row[0]),
        float(row[1]),
        float(row[2]),
        row[6].strip().lower() == "true",
        timestamp_ns,
        timestamp_ns,
    )


def select_trade_second_extrema(
    rows: Sequence[AggTradeRecord],
) -> list[AggTradeRecord]:
    """Preserve first/last and price extrema in original order for one second."""
    if not rows:
        return []
    indices = {0, len(rows) - 1}
    indices.add(min(range(len(rows)), key=lambda index: (rows[index][1], index)))
    indices.add(max(range(len(rows)), key=lambda index: (rows[index][1], -index)))
    return [rows[index] for index in sorted(indices)]


def merge_signal_windows(
    signals: Sequence[dict[str, Any]],
    config: CandidateConfig,
    end_ns: int,
) -> list[tuple[int, int]]:
    horizon_minutes = max(
        config.continuation_max_holding_minutes,
        config.rapid_failure_minutes
        + config.reversal_entry_delay_minutes
        + config.reversal_max_holding_minutes,
    ) + 5
    windows = sorted(
        (
            int(signal["confirm_time_ns"]),
            min(
                end_ns + 2 * NS_PER_MINUTE,
                int(signal["confirm_time_ns"])
                + horizon_minutes * NS_PER_MINUTE,
            ),
        )
        for signal in signals
    )
    merged: list[list[int]] = []
    for start, end in windows:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _in_windows(
    timestamp_ns: int,
    windows: Sequence[tuple[int, int]],
    pointer: int,
) -> tuple[bool, int]:
    while pointer < len(windows) and timestamp_ns > windows[pointer][1]:
        pointer += 1
    if pointer >= len(windows):
        return False, pointer
    start, end = windows[pointer]
    return start <= timestamp_ns <= end, pointer


def _quantized_price(value: float, *, ask: bool) -> str:
    rounding = ROUND_CEILING if ask else ROUND_FLOOR
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=rounding))


def build_trade_proxy_catalog(
    *,
    catalog_path: Path,
    aggtrade_paths: Sequence[Path],
    funding_paths: Sequence[Path],
    signals: Sequence[dict[str, Any]],
    config: CandidateConfig,
    evaluation_start_ns: int,
    evaluation_end_ns: int,
) -> dict[str, object]:
    """Write official-trade-derived QuoteTicks and funding into a NT catalog."""
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
    windows = merge_signal_windows(signals, config, evaluation_end_ns)
    source_rows = 0
    retained = 0
    skipped_sub_lot = 0
    buyer_aggressor_rows = 0
    seller_aggressor_rows = 0
    previous_init = -1
    pointer = 0
    batch: list[QuoteTick] = []
    outside_last: AggTradeRecord | None = None
    outside_minute = -1
    inside_second = -1
    inside_bucket: list[AggTradeRecord] = []

    def append_quote(values: AggTradeRecord) -> None:
        nonlocal retained, skipped_sub_lot, previous_init
        _, price, quantity, _, ts_event, ts_init = values
        if ts_init < previous_init:
            raise ValueError("aggTrades observed time moved backwards")
        previous_init = ts_init
        adjusted_bid = price * (1.0 - impact)
        adjusted_ask = price * (1.0 + impact)
        bid = Price.from_str(_quantized_price(adjusted_bid, ask=False))
        ask = Price.from_str(_quantized_price(adjusted_ask, ask=True))
        size = instrument.make_qty(quantity)
        if float(size) <= 0:
            skipped_sub_lot += 1
            return
        if len(batch) >= 100_000 and ts_init > batch[-1].ts_init:
            catalog.write_data(batch)
            batch.clear()
        batch.append(
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=bid,
                ask_price=ask,
                bid_size=size,
                ask_size=size,
                ts_event=ts_event,
                ts_init=ts_init,
            )
        )
        retained += 1

    def flush_inside_bucket() -> None:
        nonlocal inside_second
        if not inside_bucket:
            return
        for retained_row in select_trade_second_extrema(inside_bucket):
            append_quote(retained_row)
        inside_bucket.clear()
        inside_second = -1

    for path in sorted(aggtrade_paths, key=lambda item: item.name):
        archive, reader = _one_csv_reader(path)
        try:
            for row in reader:
                if not row or not row[0] or not row[0][0].isdigit():
                    continue
                source_rows += 1
                values = parse_aggtrade_row(row)
                _, _, _, buyer_was_maker, _, observed_ns = values
                if buyer_was_maker:
                    seller_aggressor_rows += 1
                else:
                    buyer_aggressor_rows += 1
                if (
                    observed_ns < evaluation_start_ns
                    or observed_ns > evaluation_end_ns + 2 * NS_PER_MINUTE
                ):
                    continue
                inside, pointer = _in_windows(observed_ns, windows, pointer)
                if inside:
                    if outside_last is not None:
                        append_quote(outside_last)
                        outside_last = None
                    second = observed_ns // NS_PER_SECOND
                    if inside_bucket and second != inside_second:
                        flush_inside_bucket()
                    if not inside_bucket:
                        inside_second = second
                    inside_bucket.append(values)
                else:
                    flush_inside_bucket()
                    minute = observed_ns // NS_PER_MINUTE
                    if outside_last is not None and minute != outside_minute:
                        append_quote(outside_last)
                    outside_last = values
                    outside_minute = minute
        finally:
            archive.close()

    flush_inside_bucket()
    if outside_last is not None:
        append_quote(outside_last)
    if batch:
        catalog.write_data(batch)
        batch.clear()
    if retained == 0:
        raise ValueError("no aggTrades-derived execution quotes retained")

    funding_updates: list[FundingRateUpdate] = []
    for path in sorted(funding_paths, key=lambda item: item.name):
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(names) != 1:
                raise ValueError(f"expected one funding CSV in {path}")
            with archive.open(names[0]) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="utf-8", newline="")
                )
                if not reader.fieldnames:
                    raise ValueError("funding archive has no header")
                for row in reader:
                    timestamp_token = (
                        row.get("calc_time")
                        or row.get("funding_time")
                        or row.get("fundingTime")
                    )
                    rate_token = (
                        row.get("last_funding_rate")
                        or row.get("funding_rate")
                        or row.get("fundingRate")
                    )
                    if timestamp_token is None or rate_token is None:
                        raise ValueError(
                            f"unsupported funding columns: {reader.fieldnames}"
                        )
                    timestamp_ns = normalize_timestamp_ns(int(timestamp_token))
                    if not (
                        evaluation_start_ns - NS_PER_DAY
                        <= timestamp_ns
                        <= evaluation_end_ns + 2 * NS_PER_MINUTE
                    ):
                        continue
                    interval_hours = (
                        row.get("funding_interval_hours")
                        or row.get("fundingIntervalHours")
                    )
                    interval = (
                        int(float(interval_hours) * 60)
                        if interval_hours
                        else 480
                    )
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
        "execution_source": (
            "official_binance_usdm_aggTrades_conservative_quote_proxy"
        ),
        "proxy_quote_definition": (
            "bid=actual_aggTrade_price*(1-impact); "
            "ask=actual_aggTrade_price*(1+impact); "
            "both L1 sizes=actual aggregate trade quantity"
        ),
        "aggtrade_source_rows": source_rows,
        "buyer_aggressor_rows": buyer_aggressor_rows,
        "seller_aggressor_rows": seller_aggressor_rows,
        "quote_ticks_retained": retained,
        "sub_lot_rows_skipped": skipped_sub_lot,
        "funding_updates": len(funding_updates),
        "execution_windows": windows,
        "inside_window_retention": (
            "actual-time first/last and price extrema per observed second"
        ),
        "outside_window_retention": (
            "last actual aggregate trade per observed minute"
        ),
        "impact_bps_each_side": config.slippage_impact_bps,
        "native_liquidity_consumption": True,
    }


def prepare_trade_proxy_week(
    *,
    week_start: date,
    output_root: Path,
    config: CandidateConfig,
) -> dict[str, object]:
    """Prepare one frozen week using a uniform official aggTrades proxy."""
    if week_start.isoformat() not in config.validation_weeks:
        raise ValueError(f"week is not precommitted: {week_start}")
    evaluation_start_ns = date_to_ns(week_start)
    evaluation_end_ns = date_to_ns(week_start + timedelta(days=7))
    output_root.mkdir(parents=True, exist_ok=True)

    preflight = prepare_signal_schedule(
        week_start=week_start,
        output_root=output_root,
        config=config,
    )
    if int(preflight["signals"]) < config.minimum_episodes:
        raise RuntimeError(
            f"opportunity preflight below minimum: {preflight['signals']} "
            f"< {config.minimum_episodes}"
        )
    signals = json.loads(
        (output_root / "signals.json").read_text(encoding="utf-8")
    )
    raw_root = output_root / "raw"
    source_files: list[dict[str, object]] = list(preflight["sources"])
    aggtrade_paths: list[Path] = []

    for day in _daily_dates(week_start, week_start + timedelta(days=7)):
        stamp = day.isoformat()
        url = (
            f"{BINANCE_VISION}/futures/um/daily/aggTrades/BTCUSDT/"
            f"BTCUSDT-aggTrades-{stamp}.zip"
        )
        record = download_verified(url, raw_root / "aggTrades", "aggTrades")
        source_files.append(asdict(record))
        aggtrade_paths.append(Path(record.local_path))

    months = sorted(
        {
            (week_start + timedelta(days=offset)).strftime("%Y-%m")
            for offset in range(8)
        }
    )
    funding_paths: list[Path] = []
    for month in months:
        url = (
            f"{BINANCE_VISION}/futures/um/monthly/fundingRate/BTCUSDT/"
            f"BTCUSDT-fundingRate-{month}.zip"
        )
        record = download_verified(url, raw_root / "funding", "funding")
        source_files.append(asdict(record))
        funding_paths.append(Path(record.local_path))

    catalog_stats = build_trade_proxy_catalog(
        catalog_path=output_root / "catalog",
        aggtrade_paths=aggtrade_paths,
        funding_paths=funding_paths,
        signals=signals,
        config=config,
        evaluation_start_ns=evaluation_start_ns,
        evaluation_end_ns=evaluation_end_ns,
    )
    manifest: dict[str, object] = {
        "candidate": config.candidate,
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=7)).isoformat(),
        "evaluation_start_ns": evaluation_start_ns,
        "evaluation_end_ns": evaluation_end_ns,
        "signals": len(signals),
        "signal_path": (output_root / "signals.json").as_posix(),
        "catalog_path": (output_root / "catalog").as_posix(),
        "catalog": catalog_stats,
        "sources": source_files,
    }
    (output_root / "data_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
