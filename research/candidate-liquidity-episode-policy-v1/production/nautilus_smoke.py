from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from .contracts import EpisodePlan
from .event_store import EventStore


def run_smoke(output: str | Path) -> dict[str, Any]:
    import pandas as pd
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, OrderType, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId, Venue
    from nautilus_trader.model.objects import Currency, Money
    from nautilus_trader.model.orders.list import OrderList
    from nautilus_trader.persistence.wranglers import BarDataWrangler
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.trading.strategy import Strategy

    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=True)
    database = destination / "runtime.sqlite3"
    store = EventStore(database)
    plan = EpisodePlan(
        episode_id="nautilus-smoke-episode",
        action_id="nautilus-smoke-action",
        symbol="BTCUSDT",
        side="LONG",
        family="FAILED_AUCTION_REVERSAL",
        order_time_ns=int(pd.Timestamp("2024-01-01T00:00:00Z").value),
        entry=40_000.0,
        stop=39_800.0,
        target=40_400.0,
        gross_rr=2.0,
        planned_target_net_r=1.95,
        entry_geometry="OB_FVG_OVERLAP",
        route_kind="OPPOSING_LIQUIDITY",
        expected_log_growth=0.001,
        probability_edge=0.1,
        p_fill=0.5,
        p_target_if_filled=0.6,
        model_bundle_id="smoke",
    )
    if not store.enqueue_plan(plan, ready_for_execution=True):
        raise RuntimeError("smoke plan was not enqueued")
    store.close()

    class SmokeConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        database: str

    class SmokeStrategy(Strategy):
        def __init__(self, config: SmokeConfig) -> None:
            super().__init__(config)
            self.instrument = None
            self.store = EventStore(config.database)
            self.submitted = False
            self.decision_id: str | None = None

        def on_start(self) -> None:
            self.instrument = self.cache.instrument(self.config.instrument_id)
            if self.instrument is None:
                raise RuntimeError("smoke instrument missing")
            self.subscribe_bars(self.config.bar_type)

        def on_bar(self, bar: Bar) -> None:
            if self.submitted:
                return
            plan = self.store.claim_next_plan("nautilus-backtest-smoke")
            if plan is None:
                return
            order_list: OrderList = self.order_factory.bracket(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(Decimal("0.01")),
                time_in_force=TimeInForce.GTD,
                expire_time=self.clock.utc_now() + timedelta(minutes=10),
                entry_price=self.instrument.make_price(plan.entry),
                sl_trigger_price=self.instrument.make_price(plan.stop),
                tp_price=self.instrument.make_price(plan.target),
                entry_order_type=OrderType.LIMIT,
                tags=[f"decision_id={plan.decision_id}"],
            )
            self.submit_order_list(order_list)
            self.store.mark_submitted(plan.decision_id, {"order_list_id": str(order_list.id)})
            self.decision_id = plan.decision_id
            self.submitted = True

        def on_event(self, event: Any) -> None:
            if self.decision_id and event.__class__.__name__ == "PositionClosed":
                self.store.complete_decision(
                    self.decision_id,
                    "COMPLETED",
                    "POSITION_CLOSED",
                    {"event": repr(event)},
                )
                self.decision_id = None

        def on_stop(self) -> None:
            self.cancel_all_orders(self.config.instrument_id)
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)
            self.unsubscribe_bars(self.config.bar_type)
            self.store.close()

    instrument = TestInstrumentProvider.btcusdt_binance()
    venue = Venue("BINANCE")
    bar_type = BarType.from_str("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL")
    index = pd.date_range("2024-01-01", periods=8, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [40_100, 40_050, 40_000, 40_050, 40_250, 40_420, 40_430, 40_440],
            "high": [40_150, 40_080, 40_060, 40_120, 40_300, 40_450, 40_460, 40_470],
            "low": [40_050, 39_990, 39_950, 40_000, 40_200, 40_350, 40_400, 40_410],
            "close": [40_080, 40_010, 40_040, 40_100, 40_280, 40_430, 40_450, 40_460],
            "volume": [10.0] * 8,
        },
        index=index,
    )
    bars = BarDataWrangler(bar_type, instrument).process(frame)
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    strategy = SmokeStrategy(SmokeConfig(instrument_id=instrument.id, bar_type=bar_type, database=str(database)))
    try:
        usdt = Currency.from_str("USDT")
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(100_000, usdt)],
            base_currency=usdt,
            default_leverage=Decimal(3),
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        fills.to_csv(destination / "fills.csv", index=False)
        positions.to_csv(destination / "positions.csv", index=False)
    finally:
        engine.dispose()
    reopened = EventStore(database)
    status = reopened.status()
    reopened.close()
    metrics = {
        "submitted": strategy.submitted,
        "fills": int(len(fills)),
        "positions": int(len(positions)),
        "decision_statuses": status["decisions"],
        "account_slot": status["account_slot"],
        "event_chain": status["event_chain"],
        "nautilus_version": __import__("importlib.metadata").metadata.version("nautilus_trader"),
    }
    if not strategy.submitted or len(fills) < 2 or len(positions) < 1:
        raise RuntimeError(f"Nautilus bracket smoke failed: {metrics}")
    if status["account_slot"].get("state") != "FREE":
        raise RuntimeError(f"Nautilus bracket did not release global slot: {metrics}")
    (destination / "metrics.json").write_text(
        json.dumps(metrics, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return metrics
