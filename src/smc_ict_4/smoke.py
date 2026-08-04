"""Deterministic end-to-end smoke test through the real NautilusTrader engine."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .manifest import create_run_manifest, write_json_atomic


def run_smoke(output: str | Path) -> dict[str, Any]:
    """Run a tiny buy/close cycle and persist reports."""

    import pandas as pd

    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType
    from nautilus_trader.model.enums import OmsType
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Currency
    from nautilus_trader.model.objects import Money
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy

    class SmokeConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        trade_size: Decimal

    class SmokeStrategy(Strategy):
        def __init__(self, config: SmokeConfig):
            super().__init__(config)
            self.bars_seen = 0
            self.entry_sent = False
            self.exit_sent = False

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def on_bar(self, bar: Bar) -> None:
            self.bars_seen += 1
            if self.bars_seen == 1 and self.portfolio.is_flat(self.config.instrument_id):
                instrument = self.cache.instrument(self.config.instrument_id)
                order = self.order_factory.market(
                    self.config.instrument_id,
                    OrderSide.BUY,
                    instrument.make_qty(self.config.trade_size),
                )
                self.submit_order(order)
                self.entry_sent = True
            elif self.bars_seen == 4 and self.portfolio.is_net_long(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
                self.exit_sent = True

        def on_stop(self) -> None:
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    run_id = f"smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    instrument = TestInstrumentProvider.btcusdt_binance()
    venue = Venue("BINANCE")
    bar_type = BarType.from_str("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL")
    index = pd.date_range("2024-01-01", periods=8, freq="1min", tz="UTC")
    opens = [40_000, 40_010, 40_020, 40_030, 40_040, 40_050, 40_060, 40_070]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": [value + 20 for value in opens],
            "low": [value - 20 for value in opens],
            "close": [value + 5 for value in opens],
            "volume": [10.0] * len(opens),
        },
        index=index,
    )
    bars = BarDataWrangler(bar_type, instrument).process(frame)

    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    strategy = SmokeStrategy(
        SmokeConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_size=Decimal("0.01"),
        ),
    )

    try:
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(100_000, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(1),
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)

        fills.to_csv(destination / "orders.csv", index=False)
        positions.to_csv(destination / "positions.csv", index=False)
        account.to_csv(destination / "account.csv", index=False)

        metrics = {
            "run_id": run_id,
            "bars": len(bars),
            "entry_sent": strategy.entry_sent,
            "exit_sent": strategy.exit_sent,
            "fills": int(len(fills.index)),
            "positions": int(len(positions.index)),
        }
        if not strategy.entry_sent:
            raise RuntimeError("smoke strategy did not submit its entry")
        if len(fills.index) < 2:
            raise RuntimeError(f"expected at least two fills, got {len(fills.index)}")
        if len(positions.index) < 1:
            raise RuntimeError("expected at least one closed position")

        write_json_atomic(destination / "metrics.json", metrics)
        write_json_atomic(
            destination / "run.json",
            create_run_manifest(
                run_id=run_id,
                candidate="foundation-smoke",
                extra={"bar_type": str(bar_type), "instrument_id": str(instrument.id)},
            ),
        )
        return metrics
    finally:
        engine.dispose()
