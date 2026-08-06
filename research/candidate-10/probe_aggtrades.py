"""One-shot v3 feasibility probe for Binance USD-M aggregate trades.

The probe verifies one official archive against its checksum, inspects the
actual schema, converts a bounded sample to NautilusTrader TradeTick objects,
and proves the pinned engine dispatches those ticks through a strategy callback.
It does not implement trading logic or an alternative execution engine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.request import urlopen
import zipfile

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
try:
    from nautilus_trader.model import TradeTick
except ImportError:
    from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from c10_strategy import make_cost_loaded_btc_perpetual


DATE = "2023-10-16"
SYMBOL = "BTCUSDT"
ROOT = "https://data.binance.vision/data/futures/um/daily/aggTrades"
FILE_NAME = f"{SYMBOL}-aggTrades-{DATE}.zip"
URL = f"{ROOT}/{SYMBOL}/{FILE_NAME}"
CHECKSUM_URL = URL + ".CHECKSUM"
SAMPLE_LIMIT = 150_000
EXPECTED_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)


def download(url: str, destination: Path) -> None:
    if destination.exists():
        return
    with urlopen(url, timeout=90) as response:
        destination.write_bytes(response.read())


def timestamp_to_ns(raw: str) -> int:
    value = int(raw)
    # Historical futures archives are milliseconds. Keep an explicit magnitude
    # guard so a future microsecond schema cannot be silently misinterpreted.
    if value < 10_000_000_000_000:
        return value * 1_000_000
    if value < 10_000_000_000_000_000:
        return value * 1_000
    return value


def bool_field(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"unexpected boolean field: {raw!r}")


@dataclass(frozen=True)
class ProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId


class TickProbeStrategy(Strategy):
    def __init__(self, config: ProbeConfig):
        super().__init__(config)
        self.count = 0
        self.buyer_count = 0
        self.seller_count = 0
        self.buyer_notional = 0.0
        self.seller_notional = 0.0
        self.first_ts_ns: int | None = None
        self.last_ts_ns: int | None = None

    def on_start(self) -> None:
        self.subscribe_trade_ticks(self.config.instrument_id)

    def on_trade_tick(self, tick: TradeTick) -> None:
        self.count += 1
        self.first_ts_ns = tick.ts_event if self.first_ts_ns is None else self.first_ts_ns
        self.last_ts_ns = tick.ts_event
        notional = tick.price.as_double() * tick.size.as_double()
        if tick.aggressor_side == AggressorSide.BUYER:
            self.buyer_count += 1
            self.buyer_notional += notional
        elif tick.aggressor_side == AggressorSide.SELLER:
            self.seller_count += 1
            self.seller_notional += notional


def parse_archive(
    archive_path: Path,
    instrument_id: InstrumentId,
) -> tuple[list[TradeTick], dict[str, Any]]:
    sample: list[TradeTick] = []
    row_count = 0
    buyer_rows = 0
    seller_rows = 0
    buyer_notional = 0.0
    seller_notional = 0.0
    duplicate_ids = 0
    nonmonotonic_timestamps = 0
    first_id: int | None = None
    last_id: int | None = None
    first_ts_ns: int | None = None
    last_ts_ns: int | None = None
    previous_id: int | None = None
    previous_ts: int | None = None
    header: list[str] | None = None
    first_data_row: list[str] | None = None

    with zipfile.ZipFile(archive_path) as archive:
        members = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(members) != 1:
            raise RuntimeError(f"expected one CSV member, found {members}")
        info = archive.getinfo(members[0])
        stream = io.TextIOWrapper(archive.open(members[0]), encoding="utf-8")
        reader = csv.reader(stream)
        for raw_row in reader:
            if not raw_row:
                continue
            if not raw_row[0].lstrip("-").isdigit():
                header = [item.strip() for item in raw_row]
                continue
            if len(raw_row) != len(EXPECTED_COLUMNS):
                raise RuntimeError(
                    f"unexpected aggregate-trade row width {len(raw_row)}: {raw_row[:12]}",
                )
            row = [item.strip() for item in raw_row]
            if first_data_row is None:
                first_data_row = row
            agg_id = int(row[0])
            price_text = row[1]
            quantity_text = row[2]
            ts_ns = timestamp_to_ns(row[5])
            buyer_maker = bool_field(row[6])
            aggressor = (
                AggressorSide.SELLER if buyer_maker else AggressorSide.BUYER
            )
            price = float(price_text)
            quantity = float(quantity_text)
            notional = price * quantity

            row_count += 1
            if buyer_maker:
                seller_rows += 1
                seller_notional += notional
            else:
                buyer_rows += 1
                buyer_notional += notional
            if previous_id is not None and agg_id <= previous_id:
                duplicate_ids += int(agg_id == previous_id)
            if previous_ts is not None and ts_ns < previous_ts:
                nonmonotonic_timestamps += 1
            previous_id = agg_id
            previous_ts = ts_ns
            first_id = agg_id if first_id is None else first_id
            last_id = agg_id
            first_ts_ns = ts_ns if first_ts_ns is None else first_ts_ns
            last_ts_ns = ts_ns

            if len(sample) < SAMPLE_LIMIT:
                sample.append(
                    TradeTick(
                        instrument_id=instrument_id,
                        price=Price.from_str(price_text),
                        size=Quantity.from_str(quantity_text),
                        aggressor_side=aggressor,
                        trade_id=TradeId(str(agg_id)),
                        ts_event=ts_ns,
                        ts_init=ts_ns,
                    ),
                )

    if not row_count or not sample:
        raise RuntimeError("aggregate-trade archive contained no data")
    return sample, {
        "csv_member": members[0],
        "csv_uncompressed_bytes": info.file_size,
        "csv_compressed_bytes": info.compress_size,
        "header": header,
        "effective_columns": list(EXPECTED_COLUMNS),
        "first_data_row": first_data_row,
        "row_count": row_count,
        "sample_tick_count": len(sample),
        "first_agg_trade_id": first_id,
        "last_agg_trade_id": last_id,
        "first_ts_ns": first_ts_ns,
        "last_ts_ns": last_ts_ns,
        "buyer_aggressor_rows": buyer_rows,
        "seller_aggressor_rows": seller_rows,
        "buyer_aggressor_notional": buyer_notional,
        "seller_aggressor_notional": seller_notional,
        "signed_aggressor_notional": buyer_notional - seller_notional,
        "duplicate_id_count": duplicate_ids,
        "nonmonotonic_timestamp_count": nonmonotonic_timestamps,
    }


def run_engine_probe(ticks: list[TradeTick]) -> dict[str, Any]:
    instrument = make_cost_loaded_btc_perpetual()
    strategy = TickProbeStrategy(ProbeConfig(instrument_id=instrument.id))
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    try:
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            book_type=BookType.L1_MBP,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(Decimal("100000"), usdt)],
            base_currency=usdt,
            default_leverage=Decimal("20"),
            trade_execution=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(ticks)
        engine.add_strategy(strategy)
        started = time.perf_counter()
        engine.run()
        elapsed = time.perf_counter() - started
        return {
            "callback_count": strategy.count,
            "buyer_callback_count": strategy.buyer_count,
            "seller_callback_count": strategy.seller_count,
            "buyer_callback_notional": strategy.buyer_notional,
            "seller_callback_notional": strategy.seller_notional,
            "first_callback_ts_ns": strategy.first_ts_ns,
            "last_callback_ts_ns": strategy.last_ts_ns,
            "engine_elapsed_seconds": elapsed,
            "all_sample_ticks_dispatched": strategy.count == len(ticks),
        }
    finally:
        engine.dispose()


def main() -> int:
    output = Path("artifacts/candidate-10-tick-probe")
    output.mkdir(parents=True, exist_ok=True)
    data = Path("/tmp/candidate-10-tick-probe")
    data.mkdir(parents=True, exist_ok=True)
    archive_path = data / FILE_NAME
    checksum_path = data / (FILE_NAME + ".CHECKSUM")

    started = time.perf_counter()
    download(CHECKSUM_URL, checksum_path)
    download(URL, archive_path)
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
    actual = sha256(archive_path.read_bytes()).hexdigest()
    if expected.lower() != actual.lower():
        raise RuntimeError(f"checksum mismatch: {actual} != {expected}")

    instrument = make_cost_loaded_btc_perpetual()
    ticks, archive = parse_archive(archive_path, instrument.id)
    engine = run_engine_probe(ticks)
    report = {
        "date": DATE,
        "symbol": SYMBOL,
        "source_url": URL,
        "checksum_url": CHECKSUM_URL,
        "sha256": actual,
        "archive_bytes": archive_path.stat().st_size,
        "download_and_parse_elapsed_seconds": time.perf_counter() - started,
        "aggressor_mapping": {
            "is_buyer_maker_true": "SELLER",
            "is_buyer_maker_false": "BUYER",
        },
        "archive": archive,
        "nautilus_trade_tick_probe": engine,
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
