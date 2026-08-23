"""Native historical funding settlement for NautilusTrader backtests.

NautilusTrader 1.230.0 publishes :class:`FundingRateUpdate` market data, but
its simulated exchange does not settle perpetual funding from those updates.
This module follows Nautilus' own ``FXRolloverInterestModule`` pattern: it is
registered on the ``SimulatedExchange`` and adjusts the native margin account.

Every scheduled payment must include a point-in-time historical mark price
and its observation timestamp.  Supplying a trade-bar close as a silent
substitute would make the cost model look more exact than its data, so the
module deliberately has no price fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
from dataclasses import dataclass
from decimal import Decimal
import heapq
from io import TextIOWrapper
from itertools import chain
import math
from pathlib import Path
import re
from typing import Iterable
from typing import Iterator
from typing import Mapping
from typing import Sequence
import zipfile

from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import PositionAdjustmentType
from nautilus_trader.model.events import PositionAdjusted
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money


MINUTE_NS = 60_000_000_000
HOUR_NS = 60 * MINUTE_NS


class BinanceFundingDataError(ValueError):
    """Official funding/mark archives cannot form one causal source."""


@dataclass(frozen=True, slots=True)
class _FundingRecord:
    ts_event: int
    interval_hours: int
    rate: Decimal
    signature: tuple[str, ...]
    source: str
    line: int


@dataclass(frozen=True, slots=True)
class _MarkRecord:
    ts_event: int
    open_price: Decimal
    close_price: Decimal
    signature: tuple[str, ...]
    source: str
    line: int


@dataclass(frozen=True, slots=True)
class HistoricalMarkPriceBar:
    """One completed official mark-price minute on its observable clock."""

    symbol: str
    ts_event: int
    close: Decimal


@dataclass(frozen=True, slots=True)
class SynchronizedMarkPriceMinute:
    """Timestamp-identical completed mark-price bars for all requested markets."""

    ts_event: int
    bars: Mapping[str, HistoricalMarkPriceBar]


@dataclass(frozen=True, slots=True)
class HistoricalFundingPayment:
    """One exchange funding settlement with a causal point-in-time mark."""

    instrument_id: str
    ts_event: int
    rate: Decimal
    mark_price: Decimal
    mark_time_ns: int | None = None

    def __post_init__(self) -> None:
        try:
            instrument_id = InstrumentId.from_str(self.instrument_id)
        except Exception as exc:
            raise ValueError(f"invalid instrument_id: {self.instrument_id!r}") from exc
        if str(instrument_id) != self.instrument_id:
            raise ValueError(f"instrument_id must be canonical: {self.instrument_id!r}")
        if self.ts_event <= 0:
            raise ValueError("funding ts_event must be positive")
        if not self.rate.is_finite():
            raise ValueError("funding rate must be finite")
        if not self.mark_price.is_finite() or self.mark_price <= 0:
            raise ValueError("funding mark_price must be finite and positive")
        if self.mark_time_ns is None:
            object.__setattr__(self, "mark_time_ns", self.ts_event)
        if self.mark_time_ns > self.ts_event:
            raise ValueError("funding mark cannot be observed after settlement")
        if self.ts_event - self.mark_time_ns >= MINUTE_NS:
            raise ValueError("funding mark must come from the settlement minute")


def _timestamp_to_ns(raw: str, *, source: str, line: int) -> int:
    try:
        value = int(Decimal(raw.strip()))
    except Exception as exc:
        raise BinanceFundingDataError(f"{source}:{line}: invalid timestamp {raw!r}") from exc
    magnitude = abs(value)
    if magnitude >= 10**17:
        return value
    if magnitude >= 10**14:
        return value * 1_000
    if magnitude >= 10**11:
        return value * 1_000_000
    return value * 1_000_000_000


def _decimal(raw: str, *, name: str, source: str, line: int) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except Exception as exc:
        raise BinanceFundingDataError(f"{source}:{line}: invalid {name} {raw!r}") from exc
    if not value.is_finite():
        raise BinanceFundingDataError(f"{source}:{line}: non-finite {name}")
    return value


def _canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _is_header(row: Sequence[str]) -> bool:
    if not row:
        return False
    try:
        Decimal(row[0].strip())
    except Exception:
        return True
    return False


@contextmanager
def _official_zip_rows(path: Path) -> Iterator[Iterator[list[str]]]:
    if not path.is_file():
        raise BinanceFundingDataError(f"archive does not exist: {path}")
    try:
        archive = zipfile.ZipFile(path)
    except Exception as exc:
        raise BinanceFundingDataError(f"invalid ZIP archive: {path}") from exc
    with archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise BinanceFundingDataError(
                f"{path}: expected exactly one CSV member, found {len(members)}",
            )
        with archive.open(members[0], "r") as binary:
            with TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
                yield csv.reader(text)


def _funding_positions(header: Sequence[str], source: str) -> tuple[int, int, int]:
    names = {_canonical(value): index for index, value in enumerate(header)}
    time_index = names.get("calc_time", names.get("funding_time"))
    interval_index = names.get("funding_interval_hours", names.get("interval_hours"))
    rate_index = names.get("last_funding_rate", names.get("funding_rate"))
    if time_index is None or interval_index is None or rate_index is None:
        raise BinanceFundingDataError(f"{source}: unrecognized fundingRate header")
    return time_index, interval_index, rate_index


def _mark_positions(header: Sequence[str], source: str) -> tuple[int, int, int, int]:
    names = {_canonical(value): index for index, value in enumerate(header)}
    time_index = names.get("open_time")
    open_index = names.get("open")
    close_index = names.get("close")
    close_time_index = names.get("close_time")
    if (
        time_index is None
        or open_index is None
        or close_index is None
        or close_time_index is None
    ):
        raise BinanceFundingDataError(f"{source}: unrecognized markPriceKlines header")
    return time_index, open_index, close_index, close_time_index


def _iter_funding_records(paths: Sequence[Path]) -> Iterator[_FundingRecord]:
    previous: _FundingRecord | None = None
    for path in paths:
        source = str(path)
        with _official_zip_rows(path) as rows:
            try:
                first = next(rows)
            except StopIteration as exc:
                raise BinanceFundingDataError(f"{source}: empty fundingRate CSV") from exc
            if _is_header(first):
                positions = _funding_positions(first, source)
                line_offset = 2
                data_rows: Iterable[list[str]] = rows
            else:
                positions = (0, 1, 2)
                line_offset = 1
                data_rows = chain((first,), rows)
            for line, row in enumerate(data_rows, start=line_offset):
                if not row or all(not value.strip() for value in row):
                    continue
                try:
                    time_raw, interval_raw, rate_raw = (row[index] for index in positions)
                except IndexError as exc:
                    raise BinanceFundingDataError(f"{source}:{line}: truncated funding row") from exc
                interval_decimal = _decimal(
                    interval_raw,
                    name="funding interval",
                    source=source,
                    line=line,
                )
                interval_hours = int(interval_decimal)
                if interval_decimal != interval_hours or interval_hours <= 0:
                    raise BinanceFundingDataError(
                        f"{source}:{line}: funding interval must be positive whole hours",
                    )
                current = _FundingRecord(
                    ts_event=_timestamp_to_ns(time_raw, source=source, line=line),
                    interval_hours=interval_hours,
                    rate=_decimal(rate_raw, name="funding rate", source=source, line=line),
                    signature=tuple(value.strip() for value in row),
                    source=source,
                    line=line,
                )
                if previous is not None:
                    if current.ts_event < previous.ts_event:
                        raise BinanceFundingDataError(
                            f"{source}:{line}: out-of-order funding timestamp {current.ts_event}",
                        )
                    if current.ts_event == previous.ts_event:
                        kind = "duplicate" if current.signature == previous.signature else "mutated"
                        raise BinanceFundingDataError(
                            f"{source}:{line}: {kind} funding timestamp {current.ts_event}",
                        )
                    previous_minute = previous.ts_event // MINUTE_NS * MINUTE_NS
                    current_minute = current.ts_event // MINUTE_NS * MINUTE_NS
                    allowed = {
                        previous_minute + previous.interval_hours * HOUR_NS,
                        previous_minute + current.interval_hours * HOUR_NS,
                    }
                    if current_minute not in allowed:
                        raise BinanceFundingDataError(
                            f"{source}:{line}: missing funding timestamp between "
                            f"{previous.ts_event} and {current.ts_event}",
                        )
                previous = current
                yield current


def _iter_mark_records(paths: Sequence[Path]) -> Iterator[_MarkRecord]:
    previous: _MarkRecord | None = None
    for path in paths:
        source = str(path)
        with _official_zip_rows(path) as rows:
            try:
                first = next(rows)
            except StopIteration as exc:
                raise BinanceFundingDataError(f"{source}: empty markPriceKlines CSV") from exc
            if _is_header(first):
                positions = _mark_positions(first, source)
                line_offset = 2
                data_rows: Iterable[list[str]] = rows
            else:
                positions = (0, 1, 4, 6)
                line_offset = 1
                data_rows = chain((first,), rows)
            for line, row in enumerate(data_rows, start=line_offset):
                if not row or all(not value.strip() for value in row):
                    continue
                try:
                    time_raw, open_raw, close_raw, close_time_raw = (
                        row[index] for index in positions
                    )
                except IndexError as exc:
                    raise BinanceFundingDataError(f"{source}:{line}: truncated mark row") from exc
                ts_event = _timestamp_to_ns(time_raw, source=source, line=line)
                close_time_ns = _timestamp_to_ns(close_time_raw, source=source, line=line)
                # Binance uses an inclusive right edge.  Millisecond and
                # microsecond archives both normalize inside this one-minute window.
                if not ts_event + MINUTE_NS - 1_000_000 <= close_time_ns < ts_event + MINUTE_NS:
                    raise BinanceFundingDataError(
                        f"{source}:{line}: mark kline is not an exact completed minute",
                    )
                current = _MarkRecord(
                    ts_event=ts_event,
                    open_price=_decimal(open_raw, name="mark open", source=source, line=line),
                    close_price=_decimal(close_raw, name="mark close", source=source, line=line),
                    signature=tuple(value.strip() for value in row),
                    source=source,
                    line=line,
                )
                if current.open_price <= 0 or current.close_price <= 0:
                    raise BinanceFundingDataError(
                        f"{source}:{line}: mark open/close must be positive",
                    )
                if previous is not None:
                    if current.ts_event < previous.ts_event:
                        raise BinanceFundingDataError(
                            f"{source}:{line}: out-of-order mark timestamp {current.ts_event}",
                        )
                    if current.ts_event == previous.ts_event:
                        kind = "duplicate" if current.signature == previous.signature else "mutated"
                        raise BinanceFundingDataError(
                            f"{source}:{line}: {kind} mark timestamp {current.ts_event}",
                        )
                    if current.ts_event != previous.ts_event + MINUTE_NS:
                        raise BinanceFundingDataError(
                            f"{source}:{line}: missing mark timestamp between "
                            f"{previous.ts_event} and {current.ts_event}",
                        )
                previous = current
                yield current


class BinanceMarkPrice1mLoader:
    """Re-iterable synchronized official Binance mark-price minute loader.

    A source row stamped at its open becomes a valuation observation only at
    the exact right edge, using that completed row's close.  The loader shares
    the same duplicate, mutation, ordering and gap checks as funding joins.
    """

    def __init__(self, sources: Mapping[str, Sequence[str | Path]]) -> None:
        if not sources:
            raise BinanceFundingDataError("mark-price sources cannot be empty")
        self._sources = {
            str(symbol): tuple(Path(path) for path in paths)
            for symbol, paths in sorted(sources.items())
        }
        if any(not paths for paths in self._sources.values()):
            raise BinanceFundingDataError("mark-price archive sequences cannot be empty")

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._sources)

    def __iter__(self) -> Iterator[SynchronizedMarkPriceMinute]:
        streams = {
            symbol: iter(_iter_mark_records(self._sources[symbol]))
            for symbol in self.symbols
        }
        while True:
            current: dict[str, _MarkRecord] = {}
            ended: list[str] = []
            for symbol in self.symbols:
                try:
                    current[symbol] = next(streams[symbol])
                except StopIteration:
                    ended.append(symbol)
            if ended:
                if len(ended) != len(self.symbols):
                    remaining = [symbol for symbol in self.symbols if symbol not in ended]
                    raise BinanceFundingDataError(
                        f"partial synchronized mark clock: ended={ended}, "
                        f"still_has_data={remaining}",
                    )
                return
            open_clocks = {symbol: item.ts_event for symbol, item in current.items()}
            if len(set(open_clocks.values())) != 1:
                raise BinanceFundingDataError(
                    f"partial synchronized mark clock: {open_clocks}",
                )
            ts_event = next(iter(open_clocks.values())) + MINUTE_NS
            yield SynchronizedMarkPriceMinute(
                ts_event=ts_event,
                bars={
                    symbol: HistoricalMarkPriceBar(
                        symbol=symbol,
                        ts_event=ts_event,
                        close=current[symbol].close_price,
                    )
                    for symbol in self.symbols
                },
            )


class BinanceFundingPaymentSource:
    """Causally join official monthly funding and one-minute mark archives.

    ``funding_archives`` and ``mark_price_archives`` map each raw Binance
    symbol to a chronological sequence of monthly ZIP paths.  Sequences may
    cross storage roots, but must be contiguous and non-overlapping.  Fully
    exhaust the iterator so trailing source-integrity checks also run.

    Binance ``calc_time`` can lag its scheduled boundary by a few milliseconds.
    The payment therefore preserves the exact raw ``calc_time`` and uses the
    mark kline *open* whose ``open_time`` equals the start of that containing
    minute.  ``mark_time_ns`` carries that provenance.  The kline close belongs
    to the following minute and would be look-ahead at settlement time.
    """

    def __init__(
        self,
        *,
        funding_archives: Mapping[str, Sequence[str | Path]],
        mark_price_archives: Mapping[str, Sequence[str | Path]],
        instrument_ids: Mapping[str, str] | None = None,
    ) -> None:
        funding_symbols = set(funding_archives)
        mark_symbols = set(mark_price_archives)
        if not funding_symbols or funding_symbols != mark_symbols:
            raise BinanceFundingDataError(
                "funding and mark archive mappings must contain the same non-empty symbols",
            )
        self._funding = {
            symbol: tuple(Path(path) for path in funding_archives[symbol])
            for symbol in sorted(funding_symbols)
        }
        self._marks = {
            symbol: tuple(Path(path) for path in mark_price_archives[symbol])
            for symbol in sorted(mark_symbols)
        }
        for symbol in self._funding:
            if not self._funding[symbol] or not self._marks[symbol]:
                raise BinanceFundingDataError(f"{symbol}: archive sequences cannot be empty")
        supplied_ids = dict(instrument_ids or {})
        unknown = set(supplied_ids) - funding_symbols
        if unknown:
            raise BinanceFundingDataError(f"instrument IDs supplied for unknown symbols: {unknown}")
        self._instrument_ids = {
            symbol: supplied_ids.get(symbol, f"{symbol}-PERP.BINANCE")
            for symbol in self._funding
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._funding)

    def iter_symbol(self, symbol: str) -> Iterator[HistoricalFundingPayment]:
        if symbol not in self._funding:
            raise KeyError(symbol)
        funding_records = _iter_funding_records(self._funding[symbol])
        mark_records = _iter_mark_records(self._marks[symbol])
        try:
            mark = next(mark_records)
        except StopIteration as exc:
            raise BinanceFundingDataError(f"{symbol}: no mark-price rows") from exc

        for funding in funding_records:
            funding_minute = funding.ts_event // MINUTE_NS * MINUTE_NS
            while mark.ts_event < funding_minute:
                try:
                    mark = next(mark_records)
                except StopIteration as exc:
                    raise BinanceFundingDataError(
                        f"{symbol}: missing exact mark at funding timestamp {funding.ts_event}",
                    ) from exc
            if mark.ts_event != funding_minute:
                raise BinanceFundingDataError(
                    f"{symbol}: missing exact mark at funding timestamp {funding.ts_event}",
                )
            yield HistoricalFundingPayment(
                instrument_id=self._instrument_ids[symbol],
                ts_event=funding.ts_event,
                rate=funding.rate,
                mark_price=mark.open_price,
                mark_time_ns=mark.ts_event,
            )

        # Validate every supplied mark row, including the tail after the final
        # funding event.  This executes when the public iterator is exhausted.
        for _ in mark_records:
            pass

    def __iter__(self) -> Iterator[HistoricalFundingPayment]:
        iterators = {symbol: self.iter_symbol(symbol) for symbol in self.symbols}
        queue: list[tuple[int, str, HistoricalFundingPayment]] = []
        for symbol, iterator in iterators.items():
            try:
                payment = next(iterator)
            except StopIteration:
                continue
            heapq.heappush(queue, (payment.ts_event, symbol, payment))
        while queue:
            _, symbol, payment = heapq.heappop(queue)
            yield payment
            try:
                following = next(iterators[symbol])
            except StopIteration:
                continue
            heapq.heappush(queue, (following.ts_event, symbol, following))


def monthly_funding_archive_paths(
    root: str | Path,
    symbol: str,
    months: Iterable[str],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return conventional Binance Vision funding/mark paths under ``root``.

    ``root`` is the ``.../futures_um/monthly`` directory.  For months spread
    across different roots, call this helper per root and concatenate the
    resulting tuples in chronological order before constructing the source.
    """

    base = Path(root)
    funding: list[Path] = []
    marks: list[Path] = []
    for month in months:
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) is None:
            raise ValueError(f"invalid month {month!r}; expected YYYY-MM")
        funding.append(base / "fundingRate" / symbol / f"{symbol}-fundingRate-{month}.zip")
        marks.append(
            base / "markPriceKlines" / symbol / "1m" / f"{symbol}-1m-{month}.zip",
        )
    return tuple(funding), tuple(marks)


class PerpetualFundingConfig(SimulationModuleConfig, frozen=True):
    """Configuration containing the complete causal funding schedule."""

    payments: tuple[HistoricalFundingPayment, ...]


@dataclass(frozen=True, slots=True)
class AppliedFunding:
    """Detached audit record for one position/account adjustment."""

    instrument_id: str
    position_id: str
    ts_event: int
    settled_time_ns: int
    rate: Decimal
    mark_price: Decimal
    mark_time_ns: int
    notional: Decimal
    cash_delta: Decimal
    currency: str


class PerpetualFundingModule(SimulationModule):
    """Settle linear perpetual funding through Nautilus native state.

    Positive rates debit longs and credit shorts.  The module supports linear,
    quote-settled contracts (the Binance USD-M instruments used by this
    project).  It updates both the native ``Position`` adjustment history and
    the simulated exchange account; there is no parallel/custom cash ledger.
    """

    def __init__(self, config: PerpetualFundingConfig) -> None:
        super().__init__(config)
        payments = sorted(config.payments, key=lambda item: (item.ts_event, item.instrument_id))
        keys = [(item.ts_event, item.instrument_id) for item in payments]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate funding payment for instrument/timestamp")
        self._payments = tuple(payments)
        self._offset = 0
        self._applied: list[AppliedFunding] = []
        self._totals: dict[str, Decimal] = {}
        self._timer_names: tuple[str, ...] = ()

    def register_venue(self, exchange) -> None:
        super().register_venue(exchange)
        self._schedule_exact_time_alerts()

    def _schedule_exact_time_alerts(self) -> None:
        # A raw Binance calc_time may be a few milliseconds after the UTC
        # minute boundary.  Time alerts make those events settle at calc_time,
        # before the next one-minute bar.  pre_process below remains essential
        # for alerts exactly equal to a data timestamp because BacktestEngine
        # deliberately dispatches equal-time timers after that data batch.
        timestamps = sorted({payment.ts_event for payment in self._payments})
        names: list[str] = []
        venue = str(self.exchange.id)
        existing = set(self.clock.timer_names)
        for ts_event in timestamps:
            name = f"PERPETUAL-FUNDING:{venue}:{ts_event}"
            if name in existing:
                raise RuntimeError(f"funding time alert already registered: {name}")
            self.clock.set_time_alert_ns(
                name=name,
                alert_time_ns=ts_event,
                callback=self._on_funding_time,
                allow_past=True,
            )
            names.append(name)
            existing.add(name)
        self._timer_names = tuple(names)

    def _on_funding_time(self, event) -> None:
        self._apply_due(int(event.ts_event))

    @property
    def applied(self) -> tuple[AppliedFunding, ...]:
        return tuple(self._applied)

    @property
    def totals(self) -> dict[str, Decimal]:
        return dict(self._totals)

    @property
    def pending_count(self) -> int:
        return len(self._payments) - self._offset

    def pre_process(self, data) -> None:
        # SimulatedExchange invokes this hook before it sends the current bar,
        # quote, or trade into its matching engine.  Funding at this timestamp
        # must therefore settle here: doing it in process() would run after bar
        # matching and let a stop/target close the position before settlement.
        # Prices remain explicit on HistoricalFundingPayment and are never
        # inferred from the current market datum.
        self._apply_due(int(data.ts_init))

    def process(self, ts_now: int) -> None:
        # BacktestEngine calls SimulationModule.process only after current-data
        # order matching has settled.  Applying funding here would invert
        # exchange chronology.  Exact-time clock alerts handle events between
        # data timestamps; pre_process handles events equal to a data timestamp.
        return None

    def _apply_due(self, ts_now: int) -> None:
        while self._offset < len(self._payments):
            payment = self._payments[self._offset]
            if payment.ts_event > ts_now:
                break
            self._settle(payment, settled_time_ns=ts_now)
            self._offset += 1

    def _settle(self, payment: HistoricalFundingPayment, *, settled_time_ns: int) -> None:
        instrument_id = InstrumentId.from_str(payment.instrument_id)
        instrument = self.exchange.instruments.get(instrument_id)
        if instrument is None:
            raise RuntimeError(f"funding instrument is not registered: {instrument_id}")
        if instrument.is_inverse:
            raise RuntimeError(f"inverse perpetual funding is not supported: {instrument_id}")
        if instrument.settlement_currency != instrument.quote_currency:
            raise RuntimeError(f"funding contract is not quote-settled: {instrument_id}")

        mark_price = instrument.make_price(payment.mark_price)
        for position in tuple(self.exchange.cache.positions_open(instrument_id=instrument_id)):
            # If sparse input advances over a funding timestamp, never charge a
            # position which did not yet exist at the actual settlement time.
            if position.ts_opened > payment.ts_event:
                continue
            notional_money = position.notional_value(mark_price)
            if notional_money.currency != position.settlement_currency:
                raise RuntimeError(
                    f"funding notional currency mismatch for {instrument_id}: "
                    f"{notional_money.currency} != {position.settlement_currency}",
                )
            notional = notional_money.as_decimal()
            direction = Decimal("-1") if position.is_long else Decimal("1")
            cash_delta = direction * notional * payment.rate
            if not math.isfinite(float(cash_delta)):
                raise RuntimeError(f"non-finite funding amount for {instrument_id}")
            pnl_change = Money.from_decimal(cash_delta, position.settlement_currency)
            adjustment = PositionAdjusted(
                trader_id=position.trader_id,
                strategy_id=position.strategy_id,
                instrument_id=position.instrument_id,
                position_id=position.id,
                account_id=position.account_id,
                adjustment_type=PositionAdjustmentType.FUNDING,
                quantity_change=None,
                pnl_change=pnl_change,
                reason=(
                    f"historical funding rate={payment.rate} "
                    f"mark={payment.mark_price} mark_ts={payment.mark_time_ns} "
                    f"ts={payment.ts_event}"
                ),
                event_id=UUID4(),
                ts_event=payment.ts_event,
                ts_init=payment.ts_event,
            )
            position.apply_adjustment(adjustment)
            self.exchange.cache.update_position(position)
            self.exchange.adjust_account(pnl_change)

            currency = str(position.settlement_currency)
            self._totals[currency] = self._totals.get(currency, Decimal(0)) + cash_delta
            self._applied.append(
                AppliedFunding(
                    instrument_id=str(position.instrument_id),
                    position_id=str(position.id),
                    ts_event=payment.ts_event,
                    settled_time_ns=settled_time_ns,
                    rate=payment.rate,
                    mark_price=payment.mark_price,
                    mark_time_ns=int(payment.mark_time_ns),
                    notional=notional,
                    cash_delta=cash_delta,
                    currency=currency,
                )
            )

    def log_diagnostics(self, logger) -> None:
        totals = ", ".join(f"{currency}={amount}" for currency, amount in self._totals.items())
        logger.info(f"Perpetual funding totals: {totals or 'none'}")

    def reset(self) -> None:
        if self.clock is not None:
            active = set(self.clock.timer_names)
            for name in self._timer_names:
                if name in active:
                    self.clock.cancel_timer(name)
        self._offset = 0
        self._applied.clear()
        self._totals.clear()
        if self.exchange is not None and self.clock is not None:
            self._schedule_exact_time_alerts()


def funding_config(
    payments: Iterable[HistoricalFundingPayment],
) -> PerpetualFundingConfig:
    """Build an immutable module config from a streaming/parser iterable."""

    return PerpetualFundingConfig(payments=tuple(payments))
