from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import zipfile

import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.trading.strategy import Strategy

from smc_ict_4.episode_policy_live.nautilus_data import make_binance_usdm_instruments
from smc_ict_4.episode_policy_live.nautilus_funding import HistoricalFundingPayment
from smc_ict_4.episode_policy_live.nautilus_funding import BinanceFundingDataError
from smc_ict_4.episode_policy_live.nautilus_funding import BinanceFundingPaymentSource
from smc_ict_4.episode_policy_live.nautilus_funding import BinanceMarkPrice1mLoader
from smc_ict_4.episode_policy_live.nautilus_funding import PerpetualFundingModule
from smc_ict_4.episode_policy_live.nautilus_funding import funding_config


MINUTE_NS = 60_000_000_000
VENUE = Venue("BINANCE")
USDT = Currency.from_str("USDT")
FUNDING_HEADER = "calc_time,funding_interval_hours,last_funding_rate\n"
MARK_HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
)


def _zip_csv(path: Path, text: str) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path.with_suffix(".csv").name, text)
    return path


def _funding_row(ts_ns: int, rate: str = "0.0001") -> str:
    return f"{ts_ns // 1_000_000},8,{rate}\n"


def _mark_row(ts_ns: int, open_price: Decimal) -> str:
    open_ms = ts_ns // 1_000_000
    close_ms = open_ms + 59_999
    close = open_price + Decimal("0.5")
    return (
        f"{open_ms},{open_price},{close},{open_price},{close},0,{close_ms},"
        "0,60,0,0,0\n"
    )


class _RoundTripAcrossFunding(Strategy):
    def __init__(self, instrument_id, bar_type) -> None:
        super().__init__(StrategyConfig())
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.submitted = False
        self.bar_count = 0

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self.bar_count += 1
        instrument = self.cache.instrument(self.instrument_id)
        if not self.submitted:
            self.submitted = True
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=instrument.make_qty(1),
            )
            self.submit_order(order)
        elif self.bar_count == 4:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(1),
                reduce_only=True,
            )
            self.submit_order(order)


class _RestingTargetsAcrossFunding(Strategy):
    """Open first, rest targets second, let the funding-time bar close them."""

    def __init__(self, bar_types: tuple[BarType, ...], target: Decimal) -> None:
        super().__init__(StrategyConfig())
        self.bar_types = bar_types
        self.target = target
        self.counts: dict[str, int] = {}

    def on_start(self) -> None:
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        instrument_id = bar.bar_type.instrument_id
        key = str(instrument_id)
        count = self.counts.get(key, 0) + 1
        self.counts[key] = count
        instrument = self.cache.instrument(instrument_id)
        if count == 1:
            order = self.order_factory.market(
                instrument_id=instrument_id,
                order_side=OrderSide.BUY,
                quantity=instrument.make_qty(1),
            )
            self.submit_order(order)
        elif count == 2:
            order = self.order_factory.limit(
                instrument_id=instrument_id,
                order_side=OrderSide.SELL,
                quantity=instrument.make_qty(1),
                price=instrument.make_price(self.target),
                reduce_only=True,
            )
            self.submit_order(order)


def _bar(
    instrument,
    bar_type: BarType,
    minute: int,
    *,
    open_price: Decimal = Decimal("100"),
    high_price: Decimal | None = None,
    low_price: Decimal | None = None,
    close_price: Decimal | None = None,
) -> Bar:
    high_price = high_price if high_price is not None else open_price
    low_price = low_price if low_price is not None else open_price
    close_price = close_price if close_price is not None else open_price
    return Bar(
        bar_type=bar_type,
        open=instrument.make_price(open_price),
        high=instrument.make_price(high_price),
        low=instrument.make_price(low_price),
        close=instrument.make_price(close_price),
        volume=instrument.make_qty(10_000),
        ts_event=minute * MINUTE_NS,
        ts_init=minute * MINUTE_NS,
    )


def _engine_with_funding(
    *,
    trader_id: str,
    instruments,
    module: PerpetualFundingModule,
) -> BacktestEngine:
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId(trader_id),
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(100_000, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("20"),
        modules=[module],
        fill_model=FillModel(),
        fee_model=MakerTakerFeeModel(),
        bar_execution=True,
    )
    for instrument in instruments:
        engine.add_instrument(instrument)
    return engine


def test_native_module_debits_account_and_records_position_funding() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    payment = HistoricalFundingPayment(
        instrument_id=str(instrument.id),
        ts_event=3 * MINUTE_NS,
        rate=Decimal("0.01"),
        mark_price=Decimal("100"),
    )
    module = PerpetualFundingModule(funding_config([payment]))
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("FUNDING-TEST-001"),
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(10_000, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("20"),
        modules=[module],
        fill_model=FillModel(),
        fee_model=MakerTakerFeeModel(),
        bar_execution=True,
    )
    engine.add_instrument(instrument)
    engine.add_data([_bar(instrument, bar_type, minute) for minute in range(1, 5)])
    engine.add_strategy(_RoundTripAcrossFunding(instrument.id, bar_type))

    try:
        engine.run()
        account = engine.portfolio.account(VENUE)
        positions = engine.cache.positions_closed(instrument_id=instrument.id)
        assert len(positions) == 1
        position = positions[0]

        # 1 BTC * 100 USDT * 1%: a positive rate debits a long by 1 USDT.
        assert len(module.applied) == 1
        assert module.applied[0].cash_delta == Decimal("-1.000000000")
        assert module.totals == {"USDT": Decimal("-1.000000000")}
        # The same-price round trip has two 5 bp commissions (-0.10) plus
        # funding (-1.00).  Closing does not apply the funding a second time.
        assert account.balance_total(USDT).as_double() == pytest.approx(9_998.90)

        funding_adjustments = [
            item for item in position.adjustments if item.adjustment_type.name == "FUNDING"
        ]
        assert len(funding_adjustments) == 1
        assert funding_adjustments[0].pnl_change.as_double() == pytest.approx(-1.0)
        # Native closed-position PnL contains both commissions and funding.
        assert position.realized_pnl.as_double() == pytest.approx(-1.10)
    finally:
        engine.dispose()


def test_funding_precedes_target_fill_on_first_bar_at_settlement() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    payment = HistoricalFundingPayment(
        instrument_id=str(instrument.id),
        ts_event=3 * MINUTE_NS,
        rate=Decimal("0.01"),
        mark_price=Decimal("100"),
    )
    module = PerpetualFundingModule(funding_config([payment]))
    engine = _engine_with_funding(
        trader_id="FUNDING-ORDER-001",
        instruments=[instrument],
        module=module,
    )
    bars = [
        _bar(instrument, bar_type, 1),
        _bar(instrument, bar_type, 2),
        _bar(
            instrument,
            bar_type,
            3,
            high_price=Decimal("110"),
            close_price=Decimal("110"),
        ),
    ]
    engine.add_data(bars)
    engine.add_strategy(_RestingTargetsAcrossFunding((bar_type,), Decimal("105")))

    try:
        engine.run()
        positions = engine.cache.positions_closed(instrument_id=instrument.id)
        assert len(positions) == 1  # Resting target filled on the funding-time bar.
        assert len(module.applied) == 1
        assert module.applied[0].ts_event == 3 * MINUTE_NS
        assert module.applied[0].cash_delta == Decimal("-1.000000000")
        funding_adjustments = [
            item for item in positions[0].adjustments if item.adjustment_type.name == "FUNDING"
        ]
        # This is the regression assertion: process()-time settlement would
        # see the already-closed position and leave this list empty.
        assert len(funding_adjustments) == 1
    finally:
        engine.dispose()


def test_position_opened_on_funding_bar_is_not_charged_retroactively() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    payment = HistoricalFundingPayment(
        instrument_id=str(instrument.id),
        ts_event=MINUTE_NS,
        rate=Decimal("0.01"),
        mark_price=Decimal("100"),
    )
    module = PerpetualFundingModule(funding_config([payment]))
    engine = _engine_with_funding(
        trader_id="FUNDING-ORDER-002",
        instruments=[instrument],
        module=module,
    )
    engine.add_data([_bar(instrument, bar_type, 1), _bar(instrument, bar_type, 2)])
    engine.add_strategy(_RoundTripAcrossFunding(instrument.id, bar_type))

    try:
        engine.run()
        positions = engine.cache.positions_open(instrument_id=instrument.id)
        assert len(positions) == 1
        assert module.pending_count == 0
        assert module.applied == ()
        assert all(item.adjustment_type.name != "FUNDING" for item in positions[0].adjustments)
    finally:
        engine.dispose()


def test_millisecond_calc_time_settles_on_native_timer_before_next_bar() -> None:
    instrument = make_binance_usdm_instruments()["BTCUSDT"]
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    exact_funding_time = 3 * MINUTE_NS + 7_000_000
    payment = HistoricalFundingPayment(
        instrument_id=str(instrument.id),
        ts_event=exact_funding_time,
        rate=Decimal("0.01"),
        mark_price=Decimal("100"),
        mark_time_ns=3 * MINUTE_NS,
    )
    module = PerpetualFundingModule(funding_config([payment]))
    engine = _engine_with_funding(
        trader_id="FUNDING-ORDER-003",
        instruments=[instrument],
        module=module,
    )
    engine.add_data(
        [
            _bar(instrument, bar_type, 1),
            _bar(instrument, bar_type, 2),
            _bar(
                instrument,
                bar_type,
                4,
                high_price=Decimal("110"),
                close_price=Decimal("110"),
            ),
        ]
    )
    engine.add_strategy(_RestingTargetsAcrossFunding((bar_type,), Decimal("105")))

    try:
        engine.run()
        assert len(module.applied) == 1
        assert module.applied[0].ts_event == exact_funding_time
        # The native clock alert fires at raw calc_time, not lazily at minute 4.
        assert module.applied[0].settled_time_ns == exact_funding_time
        assert len(engine.cache.positions_closed(instrument_id=instrument.id)) == 1
    finally:
        engine.dispose()


def test_same_timestamp_funding_precedes_matching_for_all_four_symbols() -> None:
    instrument_map = make_binance_usdm_instruments()
    instruments = tuple(instrument_map.values())
    bar_types = tuple(
        BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        for instrument in instruments
    )
    payments = tuple(
        HistoricalFundingPayment(
            instrument_id=str(instrument.id),
            ts_event=3 * MINUTE_NS,
            rate=Decimal("0.01"),
            mark_price=Decimal("100"),
        )
        for instrument in instruments
    )
    module = PerpetualFundingModule(funding_config(payments))
    engine = _engine_with_funding(
        trader_id="FUNDING-ORDER-004",
        instruments=instruments,
        module=module,
    )
    bars: list[Bar] = []
    for minute in (1, 2, 3):
        for instrument, bar_type in zip(instruments, bar_types, strict=True):
            bars.append(
                _bar(
                    instrument,
                    bar_type,
                    minute,
                    high_price=Decimal("110") if minute == 3 else Decimal("100"),
                    close_price=Decimal("110") if minute == 3 else Decimal("100"),
                )
            )
    engine.add_data(bars, sort=True)
    engine.add_strategy(_RestingTargetsAcrossFunding(bar_types, Decimal("105")))

    try:
        engine.run()
        expected_ids = {str(instrument.id) for instrument in instruments}
        assert len(module.applied) == 4
        assert {item.instrument_id for item in module.applied} == expected_ids
        assert {item.ts_event for item in module.applied} == {3 * MINUTE_NS}
        assert {item.settled_time_ns for item in module.applied} == {3 * MINUTE_NS}
        closed = engine.cache.positions_closed()
        assert {str(position.instrument_id) for position in closed} == expected_ids
        for position in closed:
            adjustments = [
                item for item in position.adjustments if item.adjustment_type.name == "FUNDING"
            ]
            assert len(adjustments) == 1
            assert adjustments[0].ts_event == 3 * MINUTE_NS
    finally:
        engine.dispose()


def test_payment_validation_and_duplicate_schedule() -> None:
    with pytest.raises(ValueError, match="mark_price"):
        HistoricalFundingPayment(
            instrument_id="BTCUSDT-PERP.BINANCE",
            ts_event=MINUTE_NS,
            rate=Decimal("0.0001"),
            mark_price=Decimal("0"),
        )

    payment = HistoricalFundingPayment(
        instrument_id="BTCUSDT-PERP.BINANCE",
        ts_event=MINUTE_NS,
        rate=Decimal("0.0001"),
        mark_price=Decimal("100"),
    )
    with pytest.raises(ValueError, match="duplicate funding payment"):
        PerpetualFundingModule(funding_config([payment, payment]))


def test_official_month_archives_stream_and_join_causally(tmp_path: Path) -> None:
    base = 1_800_000_000_000_000_000
    funding_a = _zip_csv(
        tmp_path / "BTCUSDT-fundingRate-2027-01.zip",
        FUNDING_HEADER + _funding_row(base, "0.0001"),
    )
    # Real Binance funding calc_time can drift a few milliseconds after the
    # scheduled boundary.  Preserve that exact timestamp.
    second_funding = base + 8 * 60 * MINUTE_NS + 7_000_000
    funding_b = _zip_csv(
        tmp_path / "BTCUSDT-fundingRate-2027-02.zip",
        FUNDING_HEADER + _funding_row(second_funding, "-0.0002"),
    )
    first_rows = "".join(
        _mark_row(base + minute * MINUTE_NS, Decimal(100 + minute))
        for minute in range(240)
    )
    second_rows = "".join(
        _mark_row(base + minute * MINUTE_NS, Decimal(100 + minute))
        for minute in range(240, 481)
    )
    mark_a = _zip_csv(tmp_path / "BTCUSDT-1m-2027-01.zip", MARK_HEADER + first_rows)
    mark_b = _zip_csv(tmp_path / "BTCUSDT-1m-2027-02.zip", MARK_HEADER + second_rows)

    payments = list(
        BinanceFundingPaymentSource(
            funding_archives={"BTCUSDT": [funding_a, funding_b]},
            mark_price_archives={"BTCUSDT": [mark_a, mark_b]},
        )
    )

    assert len(payments) == 2
    assert payments[0].mark_price == Decimal("100")
    assert payments[1].ts_event == second_funding
    assert payments[1].mark_time_ns == base + 8 * 60 * MINUTE_NS
    assert payments[1].mark_price == Decimal("580")
    # The close is deliberately 0.5 higher; using it would be look-ahead.
    assert payments[1].mark_price != Decimal("580.5")


def test_source_rejects_missing_mark_minute(tmp_path: Path) -> None:
    base = 1_800_000_000_000_000_000
    funding = _zip_csv(
        tmp_path / "BTCUSDT-fundingRate-2027-01.zip",
        FUNDING_HEADER + _funding_row(base + 2 * MINUTE_NS),
    )
    marks = _zip_csv(
        tmp_path / "BTCUSDT-1m-2027-01.zip",
        MARK_HEADER
        + _mark_row(base, Decimal("100"))
        + _mark_row(base + 2 * MINUTE_NS, Decimal("102")),
    )
    source = BinanceFundingPaymentSource(
        funding_archives={"BTCUSDT": [funding]},
        mark_price_archives={"BTCUSDT": [marks]},
    )
    with pytest.raises(BinanceFundingDataError, match="missing mark timestamp"):
        list(source)


def test_source_rejects_missing_funding_interval(tmp_path: Path) -> None:
    base = 1_800_000_000_000_000_000
    funding = _zip_csv(
        tmp_path / "BTCUSDT-fundingRate-2027-01.zip",
        FUNDING_HEADER
        + _funding_row(base)
        + _funding_row(base + 16 * 60 * MINUTE_NS),
    )
    marks = _zip_csv(
        tmp_path / "BTCUSDT-1m-2027-01.zip",
        MARK_HEADER
        + "".join(
            _mark_row(base + minute * MINUTE_NS, Decimal(100 + minute))
            for minute in range(961)
        ),
    )
    source = BinanceFundingPaymentSource(
        funding_archives={"BTCUSDT": [funding]},
        mark_price_archives={"BTCUSDT": [marks]},
    )
    with pytest.raises(BinanceFundingDataError, match="missing funding timestamp"):
        list(source)


@pytest.mark.parametrize(
    ("changed", "message"),
    [(False, "duplicate mark timestamp"), (True, "mutated mark timestamp")],
)
def test_source_rejects_duplicate_or_mutated_mark_timestamp(
    tmp_path: Path,
    changed: bool,
    message: str,
) -> None:
    base = 1_800_000_000_000_000_000
    funding = _zip_csv(
        tmp_path / "BTCUSDT-fundingRate-2027-01.zip",
        FUNDING_HEADER + _funding_row(base + 2 * MINUTE_NS),
    )
    mark_a = _zip_csv(
        tmp_path / "BTCUSDT-1m-a.zip",
        MARK_HEADER
        + _mark_row(base, Decimal("100"))
        + _mark_row(base + MINUTE_NS, Decimal("101")),
    )
    repeated_price = Decimal("999") if changed else Decimal("101")
    mark_b = _zip_csv(
        tmp_path / "BTCUSDT-1m-b.zip",
        MARK_HEADER
        + _mark_row(base + MINUTE_NS, repeated_price)
        + _mark_row(base + 2 * MINUTE_NS, Decimal("102")),
    )
    source = BinanceFundingPaymentSource(
        funding_archives={"BTCUSDT": [funding]},
        mark_price_archives={"BTCUSDT": [mark_a, mark_b]},
    )
    with pytest.raises(BinanceFundingDataError, match=message):
        list(source)


def test_mark_loader_uses_completed_close_on_exact_right_edge(tmp_path: Path) -> None:
    base = 1_800_000_000_000_000_000
    archives: dict[str, list[Path]] = {}
    for offset, symbol in enumerate(("BTCUSDT", "ETHUSDT")):
        path = _zip_csv(
            tmp_path / f"{symbol}-1m-2027-01.zip",
            MARK_HEADER
            + _mark_row(base, Decimal(100 + offset))
            + _mark_row(base + MINUTE_NS, Decimal(101 + offset)),
        )
        archives[symbol] = [path]

    minutes = list(BinanceMarkPrice1mLoader(archives))

    assert [item.ts_event for item in minutes] == [base + MINUTE_NS, base + 2 * MINUTE_NS]
    assert minutes[0].bars["BTCUSDT"].close == Decimal("100.5")
    assert minutes[0].bars["ETHUSDT"].close == Decimal("101.5")
