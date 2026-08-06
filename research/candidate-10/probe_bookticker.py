"""Feasibility probe for Binance USD-M historical best bid/ask data.

The probe verifies one official archive/checksum, records the observed schema,
normalizes quote precision to the instrument contract, and proves that the pinned
NautilusTrader engine dispatches QuoteTick objects. It contains no strategy or
alternative execution simulator.
"""

from __future__ import annotations

import csv
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
    from nautilus_trader.model import QuoteTick
except ImportError:  # pragma: no cover
    from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from c10_strategy import make_cost_loaded_btc_perpetual

DATE = "2023-10-16"
SYMBOL = "BTCUSDT"
ROOT = "https://data.binance.vision/data/futures/um/daily/bookTicker"
FILE_NAME = f"{SYMBOL}-bookTicker-{DATE}.zip"
URL = f"{ROOT}/{SYMBOL}/{FILE_NAME}"
CHECKSUM_URL = URL + ".CHECKSUM"
SAMPLE_LIMIT = 250_000
EXPECTED_COLUMNS = (
    "update_id",
    "best_bid_price",
    "best_bid_quantity",
    "best_ask_price",
    "best_ask_quantity",
    "transaction_time",
    "event_time",
)


def download(url: str, destination: Path) -> None:
    if destination.exists():
        return
    with urlopen(url, timeout=120) as response:
        destination.write_bytes(response.read())


def timestamp_to_ns(raw: str) -> int:
    value = int(raw)
    if value < 10_000_000_000_000:
        return value * 1_000_000
    if value < 10_000_000_000_000_000:
        return value * 1_000
    return value


class QuoteProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId


class QuoteProbeStrategy(Strategy):
    def __init__(self, config: QuoteProbeConfig):
        super().__init__(config)
        self.count = 0
        self.first_ts_ns: int | None = None
        self.last_ts_ns: int | None = None
        self.minimum_spread: float | None = None
        self.maximum_spread: float | None = None

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.config.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.count += 1
        self.first_ts_ns = tick.ts_event if self.first_ts_ns is None else self.first_ts_ns
        self.last_ts_ns = tick.ts_event
        spread = tick.ask_price.as_double() - tick.bid_price.as_double()
        self.minimum_spread = spread if self.minimum_spread is None else min(self.minimum_spread, spread)
        self.maximum_spread = spread if self.maximum_spread is None else max(self.maximum_spread, spread)


def parse_archive(
    archive_path: Path,
    instrument_id: InstrumentId,
) -> tuple[list[QuoteTick], dict[str, Any]]:
    instrument = make_cost_loaded_btc_perpetual()
    sample: list[QuoteTick] = []
    row_count = 0
    header: list[str] | None = None
    first_data_row: list[str] | None = None
    first_update_id: int | None = None
    last_update_id: int | None = None
    first_event_ns: int | None = None
    last_event_ns: int | None = None
    previous_update_id: int | None = None
    previous_event_ns: int | None = None
    duplicate_update_ids = 0
    nonmonotonic_event_times = 0
    locked_quotes = 0
    crossed_quotes = 0
    minimum_spread: float | None = None
    maximum_spread: float | None = None
    spread_sum = 0.0
    transaction_event_lag_ns_sum = 0
    transaction_event_lag_ns_max = 0

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
                    f"unexpected bookTicker width {len(raw_row)}: {raw_row[:12]}",
                )
            row = [item.strip() for item in raw_row]
            if first_data_row is None:
                first_data_row = row
            update_id = int(row[0])
            bid_price_text = row[1]
            bid_quantity_text = row[2]
            ask_price_text = row[3]
            ask_quantity_text = row[4]
            transaction_ns = timestamp_to_ns(row[5])
            event_ns = timestamp_to_ns(row[6])
            bid = float(bid_price_text)
            ask = float(ask_price_text)
            spread = ask - bid

            row_count += 1
            first_update_id = update_id if first_update_id is None else first_update_id
            last_update_id = update_id
            first_event_ns = event_ns if first_event_ns is None else first_event_ns
            last_event_ns = event_ns
            if previous_update_id is not None and update_id == previous_update_id:
                duplicate_update_ids += 1
            if previous_event_ns is not None and event_ns < previous_event_ns:
                nonmonotonic_event_times += 1
            previous_update_id = update_id
            previous_event_ns = event_ns
            if spread == 0.0:
                locked_quotes += 1
            elif spread < 0.0:
                crossed_quotes += 1
            minimum_spread = spread if minimum_spread is None else min(minimum_spread, spread)
            maximum_spread = spread if maximum_spread is None else max(maximum_spread, spread)
            spread_sum += spread
            lag = abs(event_ns - transaction_ns)
            transaction_event_lag_ns_sum += lag
            transaction_event_lag_ns_max = max(transaction_event_lag_ns_max, lag)

            if len(sample) < SAMPLE_LIMIT:
                sample.append(
                    QuoteTick(
                        instrument_id=instrument_id,
                        bid_price=Price(bid, precision=instrument.price_precision),
                        ask_price=Price(ask, precision=instrument.price_precision),
                        bid_size=Quantity(
                            float(bid_quantity_text),
                            precision=instrument.size_precision,
                        ),
                        ask_size=Quantity(
                            float(ask_quantity_text),
                            precision=instrument.size_precision,
                        ),
                        ts_event=event_ns,
                        ts_init=event_ns,
                    ),
                )

    if not row_count or not sample:
        raise RuntimeError("bookTicker archive contained no data")
    return sample, {
        "csv_member": members[0],
        "csv_uncompressed_bytes": info.file_size,
        "csv_compressed_bytes": info.compress_size,
        "header": header,
        "effective_columns": list(EXPECTED_COLUMNS),
        "first_data_row": first_data_row,
        "row_count": row_count,
        "sample_quote_count": len(sample),
        "first_update_id": first_update_id,
        "last_update_id": last_update_id,
        "first_event_ts_ns": first_event_ns,
        "last_event_ts_ns": last_event_ns,
        "duplicate_update_id_count": duplicate_update_ids,
        "nonmonotonic_event_time_count": nonmonotonic_event_times,
        "locked_quote_count": locked_quotes,
        "crossed_quote_count": crossed_quotes,
        "minimum_spread": minimum_spread,
        "maximum_spread": maximum_spread,
        "mean_spread": spread_sum / row_count,
        "mean_abs_transaction_event_lag_ns": transaction_event_lag_ns_sum / row_count,
        "max_abs_transaction_event_lag_ns": transaction_event_lag_ns_max,
        "price_precision": instrument.price_precision,
        "size_precision": instrument.size_precision,
    }


def run_engine_probe(quotes: list[QuoteTick]) -> dict[str, Any]:
    instrument = make_cost_loaded_btc_perpetual()
    strategy = QuoteProbeStrategy(QuoteProbeConfig(instrument_id=instrument.id))
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
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
        engine.add_data(quotes)
        engine.add_strategy(strategy)
        started = time.perf_counter()
        engine.run()
        elapsed = time.perf_counter() - started
        return {
            "callback_count": strategy.count,
            "first_callback_ts_ns": strategy.first_ts_ns,
            "last_callback_ts_ns": strategy.last_ts_ns,
            "minimum_callback_spread": strategy.minimum_spread,
            "maximum_callback_spread": strategy.maximum_spread,
            "engine_elapsed_seconds": elapsed,
            "all_sample_quotes_dispatched": strategy.count == len(quotes),
        }
    finally:
        engine.dispose()


def main() -> int:
    output = Path("artifacts/candidate-10-bookticker-probe")
    output.mkdir(parents=True, exist_ok=True)
    data = Path("/tmp/candidate-10-bookticker-probe")
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
    quotes, archive = parse_archive(archive_path, instrument.id)
    engine = run_engine_probe(quotes)
    report = {
        "date": DATE,
        "symbol": SYMBOL,
        "source_url": URL,
        "checksum_url": CHECKSUM_URL,
        "sha256": actual,
        "archive_bytes": archive_path.stat().st_size,
        "total_elapsed_seconds": time.perf_counter() - started,
        "archive": archive,
        "nautilus_quote_tick_probe": engine,
    }
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
