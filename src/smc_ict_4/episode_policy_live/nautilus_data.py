"""Streaming Binance USD-M one-minute data for NautilusTrader.

Binance kline rows contain policy-relevant fields which Nautilus ``Bar`` does
not: quote volume, trade count, and taker-buy base/quote volume.  This module
therefore emits a native ``Bar`` and a timestamp-identical
``PolicyFlowRecord`` for every row.  Consumers must keep the pair together;
silently manufacturing those fields from OHLCV would change the policy.

The loader is deliberately streaming.  It keeps one CSV row per market (and a
bounded catalog write chunk) in memory, so multi-year monthly files do not
need to be concatenated into a DataFrame.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json
import math
from pathlib import Path
import re
from typing import Iterable, Iterator, Mapping, Sequence, TextIO
import zipfile

from nautilus_trader.model.data import Bar as NautilusBar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from .domain import DEFAULT_CONTRACTS, SYMBOLS


MINUTE_NS = 60_000_000_000
MILLISECOND_NS = 1_000_000
SUPPORTED_SUFFIXES = (".csv", ".csv.gz", ".zip")


class BinanceKlineDataError(ValueError):
    """The source cannot form a complete causal four-market clock."""


@dataclass(frozen=True, slots=True)
class PolicyFlowRecord:
    """The Binance fields which a Nautilus ``Bar`` cannot carry."""

    symbol: str
    instrument_id: str
    open_time_ns: int
    source_close_time_ns: int
    ts_event: int
    quote_volume: float
    taker_buy_volume: float
    taker_buy_quote_volume: float
    trade_count: int

    @property
    def signed_quote_flow(self) -> float:
        return 2.0 * self.taker_buy_quote_volume - self.quote_volume


@dataclass(frozen=True, slots=True)
class SynchronizedMinute:
    """Exactly one completed minute from every configured market."""

    ts_event: int
    bars: Mapping[str, NautilusBar]
    flows: Mapping[str, PolicyFlowRecord]


@dataclass(frozen=True, slots=True)
class CatalogWriteResult:
    catalog_path: Path
    flow_sidecar: Path
    synchronized_minutes: int
    bars_written: int
    first_ts_event: int | None
    last_ts_event: int | None


@dataclass(frozen=True, slots=True)
class _ParsedKline:
    bar: NautilusBar
    flow: PolicyFlowRecord


_POSITIONAL_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)

_ALIASES = {
    "open_time": {"open_time", "opentime", "start_time", "starttime"},
    "open": {"open"},
    "high": {"high"},
    "low": {"low"},
    "close": {"close"},
    "volume": {"volume", "base_volume", "base_asset_volume"},
    "close_time": {"close_time", "closetime", "end_time", "endtime"},
    "quote_volume": {"quote_volume", "quote_asset_volume", "quoteassetvolume"},
    "count": {"count", "trade_count", "number_of_trades", "trades"},
    "taker_buy_volume": {
        "taker_buy_volume",
        "taker_buy_base_volume",
        "taker_buy_base_asset_volume",
        "takerbuybaseassetvolume",
    },
    "taker_buy_quote_volume": {
        "taker_buy_quote_volume",
        "taker_buy_quote_asset_volume",
        "takerbuyquoteassetvolume",
    },
}
_REQUIRED = tuple(_ALIASES)


def _precision(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def make_binance_usdm_instruments(
    *,
    maker_fee: Decimal = Decimal("0.0002"),
    taker_fee: Decimal = Decimal("0.0005"),
) -> dict[str, CryptoPerpetual]:
    """Return the fixed four-market ``CryptoPerpetual`` universe.

    Price and size increments come from the production policy's contract
    specs.  Fees are parameters because the authenticated account tier, not a
    historical CSV, is authoritative for execution costs.
    """

    usdt = Currency.from_str("USDT")
    venue = Venue("BINANCE")
    result: dict[str, CryptoPerpetual] = {}
    for symbol in SYMBOLS:
        contract = DEFAULT_CONTRACTS[symbol]
        base = Currency.from_str(symbol.removesuffix("USDT"))
        result[symbol] = CryptoPerpetual(
            instrument_id=InstrumentId(Symbol(f"{symbol}-PERP"), venue),
            raw_symbol=Symbol(symbol),
            base_currency=base,
            quote_currency=usdt,
            settlement_currency=usdt,
            is_inverse=False,
            price_precision=_precision(contract.tick_size),
            size_precision=_precision(contract.quantity_step),
            price_increment=Price.from_str(str(contract.tick_size)),
            size_increment=Quantity.from_str(str(contract.quantity_step)),
            min_quantity=Quantity.from_str(str(contract.min_quantity)),
            min_notional=Money(contract.min_notional, usdt),
            min_price=Price.from_str(str(contract.tick_size)),
            margin_init=Decimal(1) / contract.max_leverage,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            ts_event=0,
            ts_init=0,
            info={"source": "SMC_ICT_4 DEFAULT_CONTRACTS", "market": "BINANCE_USD_M"},
        )
    return result


def one_minute_bar_types(
    instruments: Mapping[str, CryptoPerpetual] | None = None,
) -> dict[str, BarType]:
    instruments = instruments or make_binance_usdm_instruments()
    return {
        symbol: BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        for symbol, instrument in instruments.items()
    }


def _canonical_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _header_positions(row: Sequence[str], source: str) -> dict[str, int]:
    normalized = [_canonical_header(item) for item in row]
    positions: dict[str, int] = {}
    for canonical, aliases in _ALIASES.items():
        for index, item in enumerate(normalized):
            if item in aliases:
                positions[canonical] = index
                break
    missing = [name for name in _REQUIRED if name not in positions]
    if missing:
        raise BinanceKlineDataError(f"{source}: missing kline columns {missing}")
    return positions


def _looks_like_header(row: Sequence[str]) -> bool:
    if not row:
        return False
    try:
        Decimal(row[0].strip())
    except Exception:
        return True
    return False


def _timestamp_to_ns(raw: str) -> int:
    text = raw.strip()
    try:
        value = int(Decimal(text))
    except Exception:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)
    magnitude = abs(value)
    if magnitude >= 10**17:
        return value
    if magnitude >= 10**14:
        return value * 1_000
    if magnitude >= 10**11:
        return value * 1_000_000
    return value * 1_000_000_000


def _decimal(raw: str, name: str, source: str, line: int) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except Exception as exc:
        raise BinanceKlineDataError(f"{source}:{line}: invalid {name}: {raw!r}") from exc
    if not value.is_finite():
        raise BinanceKlineDataError(f"{source}:{line}: non-finite {name}")
    return value


def _parse_row(
    row: Sequence[str],
    positions: Mapping[str, int],
    *,
    symbol: str,
    instrument: CryptoPerpetual,
    bar_type: BarType,
    source: str,
    line: int,
) -> _ParsedKline:
    try:
        values = {name: row[index] for name, index in positions.items()}
    except IndexError as exc:
        raise BinanceKlineDataError(f"{source}:{line}: truncated kline row") from exc

    open_time_ns = _timestamp_to_ns(values["open_time"])
    source_close_ns = _timestamp_to_ns(values["close_time"])
    nominal_close_ns = open_time_ns + MINUTE_NS
    # Binance raw files use an inclusive end (usually end - 1 ms; newer files
    # may have microsecond precision).  A completed candle becomes observable
    # at the exact right edge, never at its open or during the candle.
    if not nominal_close_ns - MILLISECOND_NS <= source_close_ns <= nominal_close_ns:
        raise BinanceKlineDataError(
            f"{source}:{line}: not a completed 1m kline "
            f"(open={open_time_ns}, close={source_close_ns})",
        )
    ts_event = nominal_close_ns

    open_price = _decimal(values["open"], "open", source, line)
    high = _decimal(values["high"], "high", source, line)
    low = _decimal(values["low"], "low", source, line)
    close = _decimal(values["close"], "close", source, line)
    volume = _decimal(values["volume"], "volume", source, line)
    quote_volume = _decimal(values["quote_volume"], "quote_volume", source, line)
    taker_buy_volume = _decimal(values["taker_buy_volume"], "taker_buy_volume", source, line)
    taker_buy_quote = _decimal(
        values["taker_buy_quote_volume"], "taker_buy_quote_volume", source, line,
    )
    try:
        count_decimal = Decimal(values["count"].strip())
        trade_count = int(count_decimal)
    except Exception as exc:
        raise BinanceKlineDataError(f"{source}:{line}: invalid trade count") from exc
    if count_decimal != trade_count:
        raise BinanceKlineDataError(f"{source}:{line}: trade count must be an integer")

    if min(open_price, high, low, close) <= 0:
        raise BinanceKlineDataError(f"{source}:{line}: prices must be positive")
    if high < max(open_price, close, low) or low > min(open_price, close, high):
        raise BinanceKlineDataError(f"{source}:{line}: inconsistent OHLC")
    if min(volume, quote_volume, taker_buy_volume, taker_buy_quote) < 0 or trade_count < 0:
        raise BinanceKlineDataError(f"{source}:{line}: volumes/count must be non-negative")
    tolerance = Decimal("1e-10")
    if taker_buy_volume > volume * (Decimal(1) + tolerance):
        raise BinanceKlineDataError(f"{source}:{line}: taker-buy base volume exceeds volume")
    if taker_buy_quote > quote_volume * (Decimal(1) + tolerance):
        raise BinanceKlineDataError(f"{source}:{line}: taker-buy quote volume exceeds quote volume")

    bar = NautilusBar(
        bar_type=bar_type,
        open=instrument.make_price(open_price),
        high=instrument.make_price(high),
        low=instrument.make_price(low),
        close=instrument.make_price(close),
        volume=instrument.make_qty(volume),
        ts_event=ts_event,
        ts_init=ts_event,
    )
    flow = PolicyFlowRecord(
        symbol=symbol,
        instrument_id=str(bar_type.instrument_id),
        open_time_ns=open_time_ns,
        source_close_time_ns=source_close_ns,
        ts_event=ts_event,
        quote_volume=float(quote_volume),
        taker_buy_volume=float(taker_buy_volume),
        taker_buy_quote_volume=float(taker_buy_quote),
        trade_count=trade_count,
    )
    return _ParsedKline(bar=bar, flow=flow)


def _is_supported(path: Path) -> bool:
    lower = path.name.lower()
    return any(lower.endswith(suffix) for suffix in SUPPORTED_SUFFIXES)


def _source_paths(source: str | Path | Iterable[str | Path]) -> Iterator[Path]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            found = sorted(item for item in path.rglob("*") if item.is_file() and _is_supported(item))
            if not found:
                raise FileNotFoundError(f"no kline CSV sources under {path}")
            yield from found
        else:
            if not _is_supported(path):
                raise BinanceKlineDataError(f"unsupported kline source: {path}")
            yield path
        return
    for item in source:
        path = Path(item)
        if not _is_supported(path):
            raise BinanceKlineDataError(f"unsupported kline source: {path}")
        yield path


@contextmanager
def _plain_text(path: Path) -> Iterator[TextIO]:
    if path.name.lower().endswith(".csv.gz"):
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            yield handle
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield handle


def _csv_rows(path: Path) -> Iterator[tuple[str, int, list[str]]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".zip":
        with _plain_text(path) as handle:
            for line, row in enumerate(csv.reader(handle), start=1):
                if row and any(cell.strip() for cell in row):
                    yield str(path), line, row
        return
    with zipfile.ZipFile(path) as archive:
        members = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if not members:
            raise BinanceKlineDataError(f"{path}: zip contains no CSV")
        for member in members:
            with archive.open(member) as raw:
                # TextIOWrapper closes only the member stream; the archive is
                # retained for the next member.
                import io

                with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as handle:
                    for line, row in enumerate(csv.reader(handle), start=1):
                        if row and any(cell.strip() for cell in row):
                            yield f"{path}!{member}", line, row


def _iter_symbol(
    symbol: str,
    source: str | Path | Iterable[str | Path],
    instrument: CryptoPerpetual,
    bar_type: BarType,
) -> Iterator[_ParsedKline]:
    last_ts: int | None = None
    seen_any = False
    for path in _source_paths(source):
        positions: dict[str, int] | None = None
        for source_name, line, row in _csv_rows(path):
            if positions is None:
                if _looks_like_header(row):
                    positions = _header_positions(row, source_name)
                    continue
                positions = {name: index for index, name in enumerate(_POSITIONAL_COLUMNS)}
            elif _looks_like_header(row):
                # Concatenated normalized extracts sometimes repeat headers.
                positions = _header_positions(row, source_name)
                continue
            parsed = _parse_row(
                row,
                positions,
                symbol=symbol,
                instrument=instrument,
                bar_type=bar_type,
                source=source_name,
                line=line,
            )
            current = parsed.bar.ts_event
            if last_ts is not None:
                if current == last_ts:
                    raise BinanceKlineDataError(f"{symbol}: duplicate minute at {current}")
                if current < last_ts:
                    raise BinanceKlineDataError(
                        f"{symbol}: out-of-order minute {current} after {last_ts}",
                    )
                if current != last_ts + MINUTE_NS:
                    raise BinanceKlineDataError(
                        f"{symbol}: gap between {last_ts} and {current}",
                    )
            last_ts = current
            seen_any = True
            yield parsed
    if not seen_any:
        raise BinanceKlineDataError(f"{symbol}: source contains no kline rows")


class BinanceKline1mLoader:
    """Re-iterable, synchronized streaming loader for the four markets."""

    def __init__(
        self,
        sources: Mapping[str, str | Path | Iterable[str | Path]],
        *,
        instruments: Mapping[str, CryptoPerpetual] | None = None,
    ) -> None:
        expected = set(SYMBOLS)
        actual = set(sources)
        if actual != expected:
            raise BinanceKlineDataError(
                f"sources must contain exactly {list(SYMBOLS)}; "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}",
            )
        # Materialize only file names, never rows.  This makes the loader
        # safely re-iterable even when a caller supplied a generator of
        # monthly archives.
        self.sources = {
            symbol: tuple(_source_paths(sources[symbol]))
            for symbol in SYMBOLS
        }
        self.instruments = dict(instruments or make_binance_usdm_instruments())
        if set(self.instruments) != expected:
            raise BinanceKlineDataError("instruments must match the four-market universe")
        for symbol, instrument in self.instruments.items():
            if not isinstance(instrument, CryptoPerpetual):
                raise TypeError(f"{symbol}: expected CryptoPerpetual, got {type(instrument).__name__}")
        self.bar_types = one_minute_bar_types(self.instruments)

    def __iter__(self) -> Iterator[SynchronizedMinute]:
        streams = {
            symbol: iter(
                _iter_symbol(
                    symbol,
                    self.sources[symbol],
                    self.instruments[symbol],
                    self.bar_types[symbol],
                ),
            )
            for symbol in SYMBOLS
        }
        while True:
            current: dict[str, _ParsedKline] = {}
            ended: list[str] = []
            for symbol in SYMBOLS:
                try:
                    current[symbol] = next(streams[symbol])
                except StopIteration:
                    ended.append(symbol)
            if ended:
                if len(ended) != len(SYMBOLS):
                    remaining = [symbol for symbol in SYMBOLS if symbol not in ended]
                    raise BinanceKlineDataError(
                        f"partial synchronized clock: ended={ended}, still_has_data={remaining}",
                    )
                return
            clocks = {symbol: parsed.bar.ts_event for symbol, parsed in current.items()}
            if len(set(clocks.values())) != 1:
                raise BinanceKlineDataError(f"partial synchronized clock: {clocks}")
            ts_event = next(iter(clocks.values()))
            bars = {symbol: current[symbol].bar for symbol in SYMBOLS}
            flows = {symbol: current[symbol].flow for symbol in SYMBOLS}
            yield SynchronizedMinute(ts_event=ts_event, bars=bars, flows=flows)

    def iter_bars(self) -> Iterator[NautilusBar]:
        """Yield native bars in clock order, then universe order."""

        for minute in self:
            yield from (minute.bars[symbol] for symbol in SYMBOLS)


def stream_synchronized_1m(
    sources: Mapping[str, str | Path | Iterable[str | Path]],
    *,
    instruments: Mapping[str, CryptoPerpetual] | None = None,
) -> Iterator[SynchronizedMinute]:
    """Convenience wrapper around :class:`BinanceKline1mLoader`."""

    return iter(BinanceKline1mLoader(sources, instruments=instruments))


def write_parquet_catalog(
    loader: BinanceKline1mLoader,
    catalog_path: str | Path,
    *,
    chunk_size: int = 20_000,
    flow_sidecar: str | Path | None = None,
) -> CatalogWriteResult:
    """Stream native bars into ``ParquetDataCatalog`` and flow into NDJSON.

    ``chunk_size`` is a hard upper bound on pending bars.  The sidecar is
    replaced atomically only after the complete synchronized input succeeds.
    """

    if chunk_size < len(SYMBOLS):
        raise ValueError(f"chunk_size must be at least {len(SYMBOLS)}")
    root = Path(catalog_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sidecar = Path(flow_sidecar).resolve() if flow_sidecar else root / "policy_flow.ndjson"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    temporary = sidecar.with_suffix(sidecar.suffix + ".part")
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data(list(loader.instruments.values()))

    pending: list[NautilusBar] = []
    synchronized_minutes = 0
    bars_written = 0
    first_ts: int | None = None
    last_ts: int | None = None

    def flush() -> None:
        nonlocal bars_written
        if pending:
            catalog.write_data(pending)
            bars_written += len(pending)
            pending.clear()

    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for minute in loader:
                first_ts = minute.ts_event if first_ts is None else first_ts
                last_ts = minute.ts_event
                synchronized_minutes += 1
                for symbol in SYMBOLS:
                    pending.append(minute.bars[symbol])
                    handle.write(json.dumps(asdict(minute.flows[symbol]), sort_keys=True) + "\n")
                if len(pending) >= chunk_size:
                    flush()
            flush()
        temporary.replace(sidecar)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return CatalogWriteResult(
        catalog_path=root,
        flow_sidecar=sidecar,
        synchronized_minutes=synchronized_minutes,
        bars_written=bars_written,
        first_ts_event=first_ts,
        last_ts_event=last_ts,
    )


def query_catalog_bars(
    catalog_path: str | Path,
    *,
    symbols: Iterable[str] = SYMBOLS,
    start: object | None = None,
    end: object | None = None,
) -> dict[str, list[NautilusBar]]:
    """Query catalog bars grouped by policy symbol."""

    selected = tuple(symbols)
    unknown = set(selected) - set(SYMBOLS)
    if unknown:
        raise ValueError(f"unsupported symbols: {sorted(unknown)}")
    bar_types = one_minute_bar_types()
    catalog = ParquetDataCatalog(str(Path(catalog_path).resolve()))
    result: dict[str, list[NautilusBar]] = {}
    for symbol in selected:
        kwargs: dict[str, object] = {}
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
        result[symbol] = catalog.bars(bar_types=[str(bar_types[symbol])], **kwargs)
    return result


def iter_policy_flow_sidecar(path: str | Path) -> Iterator[PolicyFlowRecord]:
    """Stream records written by :func:`write_parquet_catalog`."""

    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = PolicyFlowRecord(**payload)
            except Exception as exc:
                raise BinanceKlineDataError(f"{path}:{line_number}: invalid policy-flow record") from exc
            if not all(
                math.isfinite(value)
                for value in (
                    record.quote_volume,
                    record.taker_buy_volume,
                    record.taker_buy_quote_volume,
                )
            ):
                raise BinanceKlineDataError(f"{path}:{line_number}: non-finite policy flow")
            yield record


__all__ = [
    "BinanceKline1mLoader",
    "BinanceKlineDataError",
    "CatalogWriteResult",
    "PolicyFlowRecord",
    "SynchronizedMinute",
    "iter_policy_flow_sidecar",
    "make_binance_usdm_instruments",
    "one_minute_bar_types",
    "query_catalog_bars",
    "stream_synchronized_1m",
    "write_parquet_catalog",
]
