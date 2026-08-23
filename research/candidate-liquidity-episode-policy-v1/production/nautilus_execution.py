from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import socket
import uuid
from typing import Any

from .config import ProductionConfig
from .contracts import EpisodePlan, RuntimeMode
from .event_store import EventStore
from .risk import size_for_plan


def _consumer_id() -> str:
    return f"nautilus:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _symbol_from_instrument(value: str) -> str:
    return value.split("-PERP", 1)[0].split(".", 1)[0]


def _load_nautilus_types():
    # Imports are intentionally delayed so contract and Windows bootstrap tests can
    # run without constructing a live node.
    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.instruments import Instrument
    from nautilus_trader.model.orders.list import OrderList
    from nautilus_trader.trading.strategy import Strategy

    return StrategyConfig, Bar, BarType, OrderSide, OrderType, TimeInForce, InstrumentId, Instrument, OrderList, Strategy


# These classes must exist at module scope for Nautilus strategy serialization.
try:
    (
        _StrategyConfig,
        _Bar,
        _BarType,
        _OrderSide,
        _OrderType,
        _TimeInForce,
        _InstrumentId,
        _Instrument,
        _OrderList,
        _Strategy,
    ) = _load_nautilus_types()
except Exception:  # pragma: no cover - verified in the dedicated Nautilus job
    _StrategyConfig = object
    _Strategy = object
    _Bar = Any
    _BarType = Any
    _OrderSide = None
    _OrderType = None
    _TimeInForce = None
    _InstrumentId = Any
    _Instrument = Any
    _OrderList = Any


if _StrategyConfig is not object:
    class EpisodeExecutionConfig(_StrategyConfig, frozen=True):
        instrument_ids: tuple[_InstrumentId, ...]
        bar_types: tuple[_BarType, ...]
        state_database: str
        starting_balance_usdt: Decimal
        risk_fraction: Decimal
        maximum_leverage: Decimal
        minimum_notional_usdt: Decimal
        entry_expiry_minutes: int
        close_positions_on_stop: bool = False


    class EpisodeExecutionStrategy(_Strategy):
        """Consume durable plans and submit one account-wide protected bracket."""

        def __init__(self, config: EpisodeExecutionConfig) -> None:
            super().__init__(config)
            self.consumer_id = _consumer_id()
            self.store = EventStore(config.state_database)
            self.instruments: dict[str, _Instrument] = {}
            self.instrument_ids = tuple(config.instrument_ids)
            self.active_decision_id: str | None = None
            self.active_episode_id: str | None = None

        def on_start(self) -> None:
            for instrument_id, bar_type in zip(self.config.instrument_ids, self.config.bar_types, strict=True):
                instrument = self.cache.instrument(instrument_id)
                if instrument is None:
                    self.log.error(f"Could not find instrument {instrument_id}")
                    self.stop()
                    return
                self.instruments[_symbol_from_instrument(str(instrument_id))] = instrument
                self.request_bars(
                    bar_type,
                    start=self.clock.utc_now() - timedelta(hours=3),
                    callback=lambda _, bt=bar_type: self.subscribe_bars(bt),
                )
            self.store.append_event(
                "NAUTILUS_EXECUTION_STARTED",
                {
                    "consumer_id": self.consumer_id,
                    "instrument_ids": [str(item) for item in self.instrument_ids],
                    "restart_slot": self.store.account_slot(),
                },
                event_id=f"nautilus-start:{self.consumer_id}",
            )

        def _portfolio_flat(self) -> bool:
            return all(self.portfolio.is_flat(instrument_id) for instrument_id in self.instrument_ids)

        def _equity(self, instrument: _Instrument) -> float:
            try:
                from nautilus_trader.model.currencies import USDT
                account = self.portfolio.account(venue=instrument.venue)
                balance = account.balance_total(USDT) if account is not None else None
                if balance is not None:
                    if hasattr(balance, "as_double"):
                        value = float(balance.as_double())
                    else:
                        value = float(str(balance).split()[0].replace("_", ""))
                    if value > 0.0:
                        return value
            except Exception as exc:
                self.store.append_event(
                    "ACCOUNT_EQUITY_FALLBACK",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
            return float(self.config.starting_balance_usdt)

        def on_bar(self, bar: _Bar) -> None:
            if self.active_decision_id is not None:
                return
            if not self._portfolio_flat():
                return
            plan = self.store.claim_next_plan(self.consumer_id)
            if plan is None:
                return
            try:
                self._submit_plan(plan)
            except BaseException as exc:
                self.store.complete_decision(
                    plan.decision_id,
                    "FAILED",
                    "SUBMISSION_EXCEPTION",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
                raise

        def _submit_plan(self, plan: EpisodePlan) -> None:
            instrument = self.instruments.get(plan.symbol)
            if instrument is None:
                self.store.complete_decision(plan.decision_id, "FAILED", "INSTRUMENT_NOT_LOADED")
                return
            now_ns = int(self.clock.timestamp_ns())
            expiry_ns = plan.order_time_ns + int(self.config.entry_expiry_minutes * 60 * 1_000_000_000)
            if now_ns >= expiry_ns:
                self.store.complete_decision(plan.decision_id, "EXPIRED", "PLAN_EXPIRED_BEFORE_SUBMISSION")
                return
            quantity = size_for_plan(
                plan,
                equity=self._equity(instrument),
                risk_fraction=float(self.config.risk_fraction),
                maximum_leverage=float(self.config.maximum_leverage),
                minimum_notional=float(self.config.minimum_notional_usdt),
            )
            if quantity.capped_quantity <= 0.0:
                self.store.complete_decision(
                    plan.decision_id,
                    "REJECTED",
                    "QUANTITY_BELOW_MINIMUM_NOTIONAL",
                    quantity.to_dict(),
                )
                return
            side = _OrderSide.BUY if plan.side == "LONG" else _OrderSide.SELL
            order_list: _OrderList = self.order_factory.bracket(
                instrument_id=instrument.id,
                order_side=side,
                quantity=instrument.make_qty(quantity.capped_quantity),
                time_in_force=_TimeInForce.GTD,
                expire_time=self.clock.utc_now() + timedelta(
                    seconds=max(1.0, (expiry_ns - now_ns) / 1_000_000_000.0)
                ),
                entry_price=instrument.make_price(plan.entry),
                sl_trigger_price=instrument.make_price(plan.stop),
                tp_price=instrument.make_price(plan.target),
                entry_order_type=_OrderType.LIMIT,
                tags=[f"decision_id={plan.decision_id}", f"episode_id={plan.episode_id}"],
            )
            self.active_decision_id = plan.decision_id
            self.active_episode_id = plan.episode_id
            self.submit_order_list(order_list)
            self.store.mark_submitted(
                plan.decision_id,
                {
                    "consumer_id": self.consumer_id,
                    "instrument_id": str(instrument.id),
                    "quantity": quantity.to_dict(),
                    "order_list_id": str(order_list.id),
                },
            )

        def on_event(self, event: Any) -> None:
            if self.active_decision_id is None:
                return
            name = event.__class__.__name__
            payload = {"event_class": name, "event": repr(event)}
            self.store.append_event("NAUTILUS_ORDER_EVENT", payload)
            if name in {"OrderDenied", "OrderRejected"}:
                self.store.complete_decision(
                    self.active_decision_id,
                    "REJECTED",
                    name.upper(),
                    payload,
                )
                self.active_decision_id = None
                self.active_episode_id = None
                return
            if name in {"PositionClosed"}:
                self.store.complete_decision(
                    self.active_decision_id,
                    "COMPLETED",
                    "POSITION_CLOSED",
                    payload,
                )
                self.active_decision_id = None
                self.active_episode_id = None
                return
            if name in {"OrderCanceled", "OrderExpired"} and self._portfolio_flat():
                terminal = "EXPIRED" if name == "OrderExpired" else "CANCELED"
                self.store.complete_decision(
                    self.active_decision_id,
                    terminal,
                    name.upper(),
                    payload,
                )
                self.active_decision_id = None
                self.active_episode_id = None

        def on_stop(self) -> None:
            for instrument_id, bar_type in zip(self.config.instrument_ids, self.config.bar_types, strict=True):
                self.cancel_all_orders(instrument_id)
                if self.config.close_positions_on_stop:
                    self.close_all_positions(instrument_id)
                self.unsubscribe_bars(bar_type)
            self.store.append_event(
                "NAUTILUS_EXECUTION_STOPPED",
                {
                    "consumer_id": self.consumer_id,
                    "active_decision_id": self.active_decision_id,
                    "portfolio_flat": self._portfolio_flat(),
                },
                event_id=f"nautilus-stop:{self.consumer_id}",
            )
            self.store.close()
else:
    EpisodeExecutionConfig = None
    EpisodeExecutionStrategy = None


async def run_nautilus_node(config: ProductionConfig) -> None:
    if config.mode not in {RuntimeMode.PAPER, RuntimeMode.TESTNET}:
        raise ValueError("Nautilus execution node only supports paper or testnet mode")
    if EpisodeExecutionStrategy is None:
        raise RuntimeError("nautilus_trader is not installed")

    from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
    from nautilus_trader.adapters.binance.config import BinanceDataClientConfig, BinanceExecClientConfig
    from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory, BinanceLiveExecClientFactory
    from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
    from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
    from nautilus_trader.config import CacheConfig, InstrumentProviderConfig, LiveExecEngineConfig, LoggingConfig, TradingNodeConfig
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.identifiers import InstrumentId, TraderId
    from nautilus_trader.model.identifiers import Venue

    if config.mode is RuntimeMode.PAPER:
        venue_name = "BINANCE_FUTURES"
        venue = Venue(venue_name)
        instrument_ids = tuple(
            InstrumentId.from_str(f"{symbol}-PERP.{venue_name}") for symbol in config.symbols
        )
        data_clients = {
            venue_name: BinanceDataClientConfig(
                venue=venue,
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.LIVE,
                instrument_provider=InstrumentProviderConfig(load_ids=frozenset(instrument_ids)),
            )
        }
        exec_clients = {
            venue_name: SandboxExecutionClientConfig(
                venue=venue_name,
                account_type="MARGIN",
                starting_balances=[f"{config.starting_balance_usdt:g} USDT"],
                default_leverage=Decimal(str(config.maximum_leverage)),
            )
        }
        data_factories = {venue_name: BinanceLiveDataClientFactory}
        exec_factories = {venue_name: SandboxLiveExecClientFactory}
    else:
        venue_name = "BINANCE"
        instrument_ids = tuple(
            InstrumentId.from_str(f"{symbol}-PERP.{venue_name}") for symbol in config.symbols
        )
        provider = InstrumentProviderConfig(load_ids=frozenset(instrument_ids))
        data_clients = {
            venue_name: BinanceDataClientConfig(
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.TESTNET,
                instrument_provider=provider,
            )
        }
        exec_clients = {
            venue_name: BinanceExecClientConfig(
                account_type=BinanceAccountType.USDT_FUTURES,
                environment=BinanceEnvironment.TESTNET,
                instrument_provider=provider,
                max_retries=3,
            )
        }
        data_factories = {venue_name: BinanceLiveDataClientFactory}
        exec_factories = {venue_name: BinanceLiveExecClientFactory}

    bar_types = tuple(
        BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
        for instrument_id in instrument_ids
    )
    node_config = TradingNodeConfig(
        trader_id=TraderId("EPISODE-POLICY-001"),
        logging=LoggingConfig(log_level=config.log_level, log_colors=True, use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
            filter_position_reports=True,
            snapshot_orders=True,
            snapshot_positions=True,
            snapshot_positions_interval_secs=5.0,
            graceful_shutdown_on_exception=True,
        ),
        cache=CacheConfig(timestamps_as_iso8601=True, flush_on_start=False),
        data_clients=data_clients,
        exec_clients=exec_clients,
        timeout_connection=30.0,
        timeout_reconciliation=15.0,
        timeout_portfolio=15.0,
        timeout_disconnection=15.0,
        timeout_post_stop=10.0,
    )
    node = TradingNode(config=node_config)
    strategy = EpisodeExecutionStrategy(
        EpisodeExecutionConfig(
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            state_database=str(config.database_path),
            starting_balance_usdt=Decimal(str(config.starting_balance_usdt)),
            risk_fraction=Decimal(str(config.risk_fraction)),
            maximum_leverage=Decimal(str(config.maximum_leverage)),
            minimum_notional_usdt=Decimal(str(config.minimum_notional_usdt)),
            entry_expiry_minutes=config.entry_expiry_minutes,
            close_positions_on_stop=config.close_positions_on_stop,
            external_order_claims=list(instrument_ids) if config.external_order_claims else None,
        )
    )
    node.trader.add_strategy(strategy)
    for key, factory in data_factories.items():
        node.add_data_client_factory(key, factory)
    for key, factory in exec_factories.items():
        node.add_exec_client_factory(key, factory)
    node.build()
    try:
        await node.run_async()
    finally:
        await node.stop_async()
        await asyncio.sleep(1.0)
        node.dispose()
