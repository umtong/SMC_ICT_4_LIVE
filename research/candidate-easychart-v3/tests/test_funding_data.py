from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig, StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, TraderId, Venue
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.trading.strategy import Strategy

from funding_data import _read_funding_month, _read_mark_month, load_funding_history
from funding_module import HistoricalFundingBoundary, HistoricalPerpetualFundingModule
from instruments import make_instrument

NS_PER_MINUTE = 60_000_000_000
USDT = Currency.from_str("USDT")
BINANCE = Venue("BINANCE")


def make_probe_engine(module: HistoricalPerpetualFundingModule) -> BacktestEngine:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("FUNDING-PROBE-001"),
            logging=LoggingConfig(log_level="ERROR"),
            risk_engine=RiskEngineConfig(bypass=False),
        ),
    )
    engine.add_venue(
        venue=BINANCE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(100_000.0, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("100"),
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=0.0, random_seed=42),
        modules=[module],
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    return engine


class FundingProbeConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[InstrumentId, ...]
    bar_types: tuple[BarType, ...]
    sides: tuple[OrderSide, ...]
    clock_instrument_id: InstrumentId
    capture_times_ns: tuple[int, ...]


class FundingProbeStrategy(Strategy):
    def __init__(self, config: FundingProbeConfig) -> None:
        super().__init__(config)
        if not len(config.instrument_ids) == len(config.bar_types) == len(config.sides):
            raise ValueError("probe routes must have equal lengths")
        self.route = {
            bar_type.id_spec_key(): (instrument_id, side)
            for instrument_id, bar_type, side in zip(
                config.instrument_ids,
                config.bar_types,
                config.sides,
                strict=True,
            )
        }
        self.instruments = {}
        self.submitted: set[InstrumentId] = set()
        self.snapshots: dict[int, dict[str, object]] = {}

    def on_start(self) -> None:
        for instrument_id, bar_type in zip(
            self.config.instrument_ids,
            self.config.bar_types,
            strict=True,
        ):
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise RuntimeError("probe instrument unavailable")
            self.instruments[instrument_id] = instrument
            self.subscribe_bars(bar_type)

    @staticmethod
    def _account_balance(account: object) -> float:
        value = account.balance_total(USDT)
        if value is None:
            raise RuntimeError("probe USDT balance unavailable")
        return float(value.as_double())

    def _snapshot(self) -> dict[str, object]:
        portfolio_account = self.portfolio.account(BINANCE)
        cache_account = self.cache.account_for_venue(BINANCE)
        if portfolio_account is None or cache_account is None:
            raise RuntimeError("probe account unavailable")
        positions: dict[str, dict[str, object]] = {}
        for instrument_id in self.config.instrument_ids:
            open_positions = self.cache.positions_open(instrument_id=instrument_id)
            if len(open_positions) != 1:
                raise RuntimeError(
                    f"expected one open probe position for {instrument_id}, got {len(open_positions)}",
                )
            position = open_positions[0]
            realized = None if position.realized_pnl is None else float(position.realized_pnl.as_double())
            positions[str(instrument_id)] = {
                "realized_pnl": realized,
                "signed_qty": float(position.signed_qty),
                "account_id": str(position.account_id),
            }
        return {
            "portfolio_balance": self._account_balance(portfolio_account),
            "cache_balance": self._account_balance(cache_account),
            "portfolio_event_count": int(portfolio_account.event_count),
            "cache_event_count": int(cache_account.event_count),
            "positions": positions,
        }

    def on_bar(self, bar: Bar) -> None:
        route = self.route.get(bar.bar_type.id_spec_key())
        if route is None:
            return
        instrument_id, side = route
        if instrument_id not in self.submitted:
            self.submitted.add(instrument_id)
            self.submit_order(
                self.order_factory.market(
                    instrument_id=instrument_id,
                    order_side=side,
                    quantity=self.instruments[instrument_id].make_qty(1),
                ),
            )
        if (
            instrument_id == self.config.clock_instrument_id
            and bar.ts_event in self.config.capture_times_ns
        ):
            if any(self.portfolio.is_flat(item) for item in self.config.instrument_ids):
                raise RuntimeError("probe funding capture occurred before both entries filled")
            self.snapshots[bar.ts_event] = self._snapshot()

    def on_stop(self) -> None:
        # Leave constant-price positions open so snapshot differences are
        # funding only, not exit commissions.
        for bar_type in self.config.bar_types:
            self.unsubscribe_bars(bar_type)


class FundingHistoryTests(unittest.TestCase):
    def test_archive_parsers_use_interval_rate_and_boundary_mark_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            funding_archive = root / "funding.zip"
            mark_archive = root / "mark.zip"
            with ZipFile(funding_archive, "w") as archive:
                archive.writestr(
                    "BTCUSDT-fundingRate-2024-02.csv",
                    "calc_time,funding_interval_hours,last_funding_rate\n"
                    "1706745600000,8,0.00050000\n",
                )
            mark_row = [
                "1706745600000", "101.25", "102", "100", "101.5", "0",
                "1706745659999", "0", "0", "0", "0", "0",
            ]
            with ZipFile(mark_archive, "w") as archive:
                archive.writestr(
                    "BTCUSDT-1m-2024-02.csv",
                    ",".join(mark_row) + "\n",
                )
            with patch("funding_data._verified_archive", return_value=funding_archive):
                funding = _read_funding_month("BTCUSDT", "2024-02", root)
            with patch("funding_data._verified_archive", return_value=mark_archive):
                marks = _read_mark_month("BTCUSDT", "2024-02", root)
        self.assertEqual(funding[0]["fundingRate"], Decimal("0.00050000"))
        self.assertEqual(funding[0]["intervalMinutes"], 480)
        self.assertEqual(marks[1706745600000], Decimal("101.25"))

    def test_history_joins_same_boundary_mark_and_includes_end_flatten_boundary(self) -> None:
        funding_rows = [
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1706745600000,
                "fundingRate": Decimal("0.0005"),
                "intervalMinutes": 480,
            },
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1706832000000,
                "fundingRate": Decimal("-0.0001"),
                "intervalMinutes": 480,
            },
        ]
        marks = {
            1706745600000: Decimal("101.0"),
            1706832000000: Decimal("102.0"),
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "funding_data._read_funding_month",
            return_value=funding_rows,
        ), patch(
            "funding_data._read_mark_month",
            return_value=marks,
        ):
            rows = load_funding_history(
                "BTCUSDT",
                date(2024, 2, 1),
                date(2024, 2, 1),
                Path(directory),
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["markPrice"], Decimal("101.0"))
        self.assertEqual(rows[-1]["fundingTime"], 1706832000000)

    def test_simulation_module_debits_long_and_credits_short_in_one_engine(self) -> None:
        btc = make_instrument("BTCUSDT")
        eth = make_instrument("ETHUSDT")
        long_boundary = 4 * NS_PER_MINUTE
        short_boundary = 6 * NS_PER_MINUTE
        module = HistoricalPerpetualFundingModule(
            [
                HistoricalFundingBoundary(
                    symbol="BTCUSDT",
                    instrument_id=btc.id,
                    funding_time_ns=long_boundary,
                    interval_minutes=1,
                    rate=Decimal("0.01"),
                    mark_price=Decimal("100"),
                ),
                HistoricalFundingBoundary(
                    symbol="ETHUSDT",
                    instrument_id=eth.id,
                    funding_time_ns=short_boundary,
                    interval_minutes=1,
                    rate=Decimal("0.02"),
                    mark_price=Decimal("100"),
                ),
            ],
        )
        engine = make_probe_engine(module)
        engine.add_instrument(btc)
        engine.add_instrument(eth)
        btc_type = BarType.from_str(f"{btc.id}-1-MINUTE-LAST-EXTERNAL")
        eth_type = BarType.from_str(f"{eth.id}-1-MINUTE-LAST-EXTERNAL")
        # Simulation modules run after strategy callbacks for a timestamp.  The
        # first later bar therefore exposes the completed settlement.
        bar_times = (1, 2, 3, 5, 7, 9)
        for instrument, bar_type in ((btc, btc_type), (eth, eth_type)):
            engine.add_data(
                [
                    Bar(
                        bar_type=bar_type,
                        open=instrument.make_price(100.0),
                        high=instrument.make_price(100.0),
                        low=instrument.make_price(100.0),
                        close=instrument.make_price(100.0),
                        volume=instrument.make_qty(100),
                        ts_event=index * NS_PER_MINUTE,
                        ts_init=index * NS_PER_MINUTE,
                    )
                    for index in bar_times
                ],
                sort=False,
            )
        engine.sort_data()
        strategy = FundingProbeStrategy(
            FundingProbeConfig(
                instrument_ids=(btc.id, eth.id),
                bar_types=(btc_type, eth_type),
                sides=(OrderSide.BUY, OrderSide.SELL),
                clock_instrument_id=btc.id,
                capture_times_ns=(
                    3 * NS_PER_MINUTE,
                    7 * NS_PER_MINUTE,
                    9 * NS_PER_MINUTE,
                ),
            ),
        )
        engine.add_strategy(strategy)
        try:
            engine.run()
        finally:
            engine.dispose()

        before = strategy.snapshots[3 * NS_PER_MINUTE]
        after_long = strategy.snapshots[7 * NS_PER_MINUTE]
        after_short = strategy.snapshots[9 * NS_PER_MINUTE]
        self.assertAlmostEqual(
            after_long["cache_balance"] - before["cache_balance"],
            -1.0,
            places=6,
            msg=str(strategy.snapshots),
        )
        self.assertAlmostEqual(
            after_short["cache_balance"] - after_long["cache_balance"],
            2.0,
            places=6,
            msg=str(strategy.snapshots),
        )
        self.assertEqual(module.processed_boundaries, 2)
        self.assertEqual(module.settled_positions, 2)
        self.assertEqual(
            [Decimal(item["amount"]) for item in module.ledger],
            [Decimal("-1.00"), Decimal("2.00")],
        )
        # Legacy Nautilus modules book financing as account cash flows, exactly
        # like FXRolloverInterestModule. Position realized PnL therefore remains
        # execution-only and the funding ledger is joined separately for audit.
        btc_key = str(btc.id)
        eth_key = str(eth.id)
        self.assertEqual(
            before["positions"][btc_key]["realized_pnl"],
            after_long["positions"][btc_key]["realized_pnl"],
        )
        self.assertEqual(
            after_long["positions"][eth_key]["realized_pnl"],
            after_short["positions"][eth_key]["realized_pnl"],
        )


if __name__ == "__main__":
    unittest.main()
