"""NautilusTrader adapter for live public data, sandbox paper, and futures testnet.

Execution-lifecycle provenance is the existing candidate-10 implementation at
``research/candidate-10/c10_flow_parent_execution.py`` commit
``02b8b939e88b69445ecafb8a1df90671f47b351f``: cancel the parent remainder on
first execution, protect every raced fill chunk independently, cancel a completed
exit's sibling, and emergency-flatten genuine protection failure.  This adapter
ports those semantics to the shared four-market account; it does not claim them
as a newly discovered mechanism.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterable, Mapping

from .domain import Bar as PolicyBar
from .domain import DEFAULT_CONTRACTS, SYMBOLS, TradePlan
from .live_bars import DEFAULT_WARMUP_MINUTES
from .live_bars import fetch_recent_binance_bars as _fetch_recent_binance_bars
from .live_inventory import (
    InventoryMetricConflictError,
    LiveInventoryCollector,
    LiveInventoryPollResult,
)
from .policy import LiquidityEpisodeCoordinator, PolicyConfig, SymbolEpisodePolicy
from .sizing import SizingAccepted, size_three_percent_stop_risk
from .storage import StateStore


NS_PER_MINUTE = 60_000_000_000


def canonicalize_completed_policy_bar(bar: PolicyBar) -> PolicyBar:
    """Return a completed bar on its exclusive right-edge clock.

    Binance REST encodes a kline close as the final inclusive millisecond,
    while the tick builder historically used the final inclusive nanosecond.
    Nautilus external bars use the exclusive right edge.  These are transport
    representations of one minute, not three different causal observations.
    """

    expected_close_ns = bar.open_time_ns + bar.interval_minutes * NS_PER_MINUTE
    if bar.close_time_ns not in {
        expected_close_ns,
        expected_close_ns - 1,
        expected_close_ns - 1_000_000,
    }:
        raise RuntimeError(
            "non-canonical completed bar clock: "
            f"{bar.symbol} {bar.interval_minutes}m open={bar.open_time_ns} "
            f"close={bar.close_time_ns} expected={expected_close_ns}"
        )
    if bar.close_time_ns == expected_close_ns:
        return bar
    return replace(bar, close_time_ns=expected_close_ns)


def _canonical_external_close_ns(raw_close_ns: int, *, interval_minutes: int) -> int:
    """Normalize a completed external bar to its exclusive right edge.

    Binance WebSocket klines timestamp the final inclusive millisecond, while
    replay and policy state use the exclusive minute boundary.  Legacy stored
    rows may also use the final inclusive nanosecond.  No other clock shape is
    accepted because rounding an arbitrary timestamp could admit a partial or
    incorrectly configured candle.
    """

    span_ns = interval_minutes * NS_PER_MINUTE
    for increment in (0, 1, 1_000_000):
        candidate = raw_close_ns + increment
        if candidate % span_ns == 0:
            return candidate
    raise RuntimeError(
        "non-canonical completed external bar clock: "
        f"close={raw_close_ns} interval_minutes={interval_minutes}"
    )


def policy_bar_from_native_binance_bar(
    bar: Any,
    *,
    expected_bar_type: Any | None = None,
) -> PolicyBar:
    """Map one native, exchange-completed Binance kline without reconstruction."""

    bar_type = bar.bar_type
    if expected_bar_type is not None and str(bar_type) != str(expected_bar_type):
        raise RuntimeError(
            "unexpected native Binance bar type: "
            f"actual={bar_type} expected={expected_bar_type}"
        )
    if not bar_type.is_externally_aggregated() or not bar_type.spec.is_time_aggregated():
        raise RuntimeError(f"native Binance policy bar must be external time bar: {bar_type}")
    if int(bar_type.spec.step) != 1 or not str(bar_type).endswith(
        "-1-MINUTE-LAST-EXTERNAL"
    ):
        raise RuntimeError(f"native Binance policy bar must be external 1-minute LAST: {bar_type}")

    symbol = _symbol_from_instrument(str(bar_type.instrument_id))
    close_ns = _canonical_external_close_ns(
        int(bar.ts_event),
        interval_minutes=1,
    )
    count = int(bar.count)
    if isinstance(bar.count, bool) or count != bar.count or count < 0:
        raise RuntimeError(f"invalid native Binance trade count: {bar.count!r}")
    taker_buy_base = Decimal(bar.taker_buy_base_volume)
    taker_buy_quote = Decimal(bar.taker_buy_quote_volume)
    quote_volume = Decimal(bar.quote_volume)
    base_volume = Decimal(str(bar.volume))
    if min(base_volume, quote_volume, taker_buy_base, taker_buy_quote) < 0:
        raise RuntimeError("native Binance bar volumes must be non-negative")
    if taker_buy_base > base_volume or taker_buy_quote > quote_volume:
        raise RuntimeError("native Binance taker-buy volume exceeds total volume")

    return PolicyBar(
        symbol=symbol,
        interval_minutes=1,
        open_time_ns=close_ns - NS_PER_MINUTE,
        close_time_ns=close_ns,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(base_volume),
        quote_volume=float(quote_volume),
        taker_buy_quote_volume=float(taker_buy_quote),
        # BinanceBar.count is the kline's raw `n` field.  It is intentionally
        # not the number of aggTrade websocket messages observed locally.
        trade_count=count,
    )


def _canonical_stored_bars(
    bars: Iterable[PolicyBar],
) -> tuple[list[PolicyBar], dict[str, int]]:
    """Normalize legacy clocks without rewriting hash-evidenced SQLite rows."""

    by_minute: dict[tuple[str, int, int], PolicyBar] = {}
    raw_count = 0
    normalized = 0
    collapsed = 0
    for raw in bars:
        raw_count += 1
        bar = canonicalize_completed_policy_bar(raw)
        normalized += int(bar.close_time_ns != raw.close_time_ns)
        key = (bar.symbol, bar.interval_minutes, bar.open_time_ns)
        prior = by_minute.get(key)
        if prior is None:
            by_minute[key] = bar
            continue
        if prior != bar:
            raise RuntimeError(
                "conflicting stored bars share one canonical minute: "
                f"{bar.symbol} {bar.interval_minutes}m open={bar.open_time_ns}"
            )
        collapsed += 1
    ordered = sorted(
        by_minute.values(),
        key=lambda item: (item.close_time_ns, item.symbol),
    )
    return ordered, {
        "raw_rows": raw_count,
        "canonical_rows": len(ordered),
        "legacy_clock_rows_normalized": normalized,
        "identical_clock_aliases_collapsed": collapsed,
    }


def native_restart_capabilities() -> dict[str, object]:
    """Describe the executable restart boundary of the pinned runtime.

    NautilusTrader 1.230 exposes only Redis as a cache database.  More
    importantly, cache snapshots do not restore the in-process sandbox venue's
    matching engines, balances, open orders, or positions.  SQLite therefore
    remains durable policy evidence, not a native sandbox account backup.
    """

    return {
        "nautilus_version": "1.230.0",
        "cache_database_types": ["redis"],
        "external_cache_configured": False,
        "sqlite_restores_native_account": False,
        "shadow_sandbox_restart": "FAIL_CLOSED_AFTER_NATIVE_ORDER_OR_FILL",
        "testnet_restart": "EXPERIMENTAL_PARTIAL_EXCHANGE_REPORT_RECONCILIATION",
        "funded_live_execution": False,
    }


def native_restart_block_reason(
    execution_mode: str,
    runtime: Mapping[str, object] | None,
) -> str | None:
    """Return why a process-local execution account cannot be resumed."""

    if not runtime:
        return None
    prior_mode = str(runtime.get("mode", "")).upper()
    current_mode = execution_mode.upper()
    if prior_mode and prior_mode != current_mode:
        return f"EXECUTION_MODE_CHANGED:{prior_mode}->{current_mode}"
    if current_mode not in {"SHADOW", "SANDBOX"}:
        return None
    if runtime.get("active_plan") is not None or runtime.get("active_order_ids"):
        return "PROCESS_LOCAL_SANDBOX_OPEN_EXECUTION_STATE_NOT_RESTORABLE"
    if bool(runtime.get("sandbox_native_account_mutated", False)):
        return "PROCESS_LOCAL_SANDBOX_ACCOUNT_HISTORY_NOT_RESTORABLE"
    if bool(runtime.get("emergency_flatten_pending", False)):
        return "PROCESS_LOCAL_SANDBOX_EMERGENCY_STATE_NOT_RESTORABLE"
    return None


def _symbol_from_instrument(value: str) -> str:
    symbol = value.split(".", 1)[0].replace("-PERP", "")
    if symbol not in SYMBOLS:
        raise ValueError(f"unsupported instrument: {value}")
    return symbol


def fetch_recent_binance_bars(
    symbol: str,
    *,
    limit: int = DEFAULT_WARMUP_MINUTES,
    clock_ns: Callable[[], int] = time.time_ns,
) -> list[PolicyBar]:
    """Delegate connected warm-up to the causal paginated public loader."""

    return _fetch_recent_binance_bars(symbol, limit=limit, clock_ns=clock_ns)


def bootstrap_store(
    store: StateStore,
    *,
    limit: int = DEFAULT_WARMUP_MINUTES,
) -> dict[str, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("bootstrap limit must be a positive integer")
    stored, clock_evidence = _canonical_stored_bars(
        store.load_bars(interval_minutes=1, symbols=SYMBOLS),
    )
    known = {
        (bar.symbol, bar.interval_minutes, bar.open_time_ns): bar
        for bar in stored
    }
    if (
        clock_evidence["legacy_clock_rows_normalized"]
        or clock_evidence["identical_clock_aliases_collapsed"]
    ):
        store.append_event(
            time_ns=time.time_ns(),
            event_type="BAR_CLOCK_COMPATIBILITY_NORMALIZED",
            payload={"context": "BOOTSTRAP", **clock_evidence},
        )
    # Fetch and fully validate all four windows before mutating SQLite.  A
    # provider failure for the fourth symbol must not leave a startup which
    # appears to have successfully bootstrapped only the first three.
    bootstrap_started_ns = time.time_ns()
    downloaded = {
        symbol: tuple(
            canonicalize_completed_policy_bar(raw)
            for raw in fetch_recent_binance_bars(
                symbol,
                limit=limit,
                clock_ns=lambda: bootstrap_started_ns,
            )
        )
        for symbol in SYMBOLS
    }
    expected_opens = tuple(bar.open_time_ns for bar in downloaded[SYMBOLS[0]])
    for symbol in SYMBOLS:
        symbol_opens = tuple(bar.open_time_ns for bar in downloaded[symbol])
        if symbol_opens != expected_opens:
            raise RuntimeError(
                "bootstrap symbols do not share one completed-minute window: "
                f"{symbol}",
            )
        for bar in downloaded[symbol]:
            key = (bar.symbol, bar.interval_minutes, bar.open_time_ns)
            prior = known.get(key)
            if prior is not None and prior != bar:
                raise RuntimeError(
                    "bootstrap market data mutation for canonical minute: "
                    f"{bar.symbol} open={bar.open_time_ns}"
                )

    counts: dict[str, int] = {}
    for symbol in SYMBOLS:
        inserted = 0
        for bar in downloaded[symbol]:
            key = (bar.symbol, bar.interval_minutes, bar.open_time_ns)
            prior = known.get(key)
            if prior is not None:
                continue
            inserted += int(store.append_bar(bar))
            known[key] = bar
        counts[symbol] = inserted
    store.append_event(time_ns=time.time_ns(), event_type="BOOTSTRAP_COMPLETE", payload=counts)
    return counts


def apply_live_inventory_results(
    strategy: object,
    results: Iterable[LiveInventoryPollResult],
) -> dict[str, str]:
    """Atomically hand one public metrics poll to all four live policies.

    Every symbol is assigned on every poll.  A failed, stale, invalid, or
    timestamp-unjoined result explicitly replaces any prior timeline with
    ``None``; cached metrics are never retained as if they were current.
    """

    collected = tuple(results)
    by_symbol = {item.symbol: item for item in collected}
    if len(by_symbol) != len(collected) or set(by_symbol) != set(SYMBOLS):
        raise ValueError("live inventory poll must contain exactly the four symbols")
    coordinator = getattr(strategy, "coordinator", None)
    policies = getattr(coordinator, "policies", None)
    if not isinstance(policies, Mapping) or set(policies) != set(SYMBOLS):
        raise ValueError("strategy coordinator does not expose four inventory policies")

    assignments = {
        symbol: by_symbol[symbol].timeline if by_symbol[symbol].ready else None
        for symbol in SYMBOLS
    }
    for symbol in SYMBOLS:
        policy = policies[symbol]
        if not hasattr(policy, "inventory_timeline"):
            raise ValueError(f"policy cannot accept inventory timeline: {symbol}")
    for symbol in SYMBOLS:
        policies[symbol].inventory_timeline = assignments[symbol]
    statuses = {symbol: by_symbol[symbol].status.value for symbol in SYMBOLS}
    setattr(strategy, "live_inventory_status", statuses)
    return statuses


@dataclass(slots=True)
class MinuteTradeBuilder:
    """Focused test fixture for legacy aggTrade reconstruction.

    Production does not instantiate this builder.  Binance ``aggTrade``
    messages count aggregate executions, whereas the exchange kline ``n``
    field counts underlying trades; reconstructing policy bars here therefore
    cannot preserve the historical/live ``trade_count`` contract.
    """

    symbol: str
    minute_open_ns: int | None = None
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    quote_volume: float = 0.0
    taker_buy_quote_volume: float = 0.0
    trade_count: int = 0

    def push(self, *, ts_ns: int, price: float, quantity: float, buyer_aggressor: bool) -> PolicyBar | None:
        minute = ts_ns // NS_PER_MINUTE * NS_PER_MINUTE
        completed: PolicyBar | None = None
        if self.minute_open_ns is None:
            self._start(minute, price)
        elif minute != self.minute_open_ns:
            completed = self.finish()
            self._start(minute, price)
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += quantity
        notional = price * quantity
        self.quote_volume += notional
        if buyer_aggressor:
            self.taker_buy_quote_volume += notional
        self.trade_count += 1
        return completed

    def _start(self, minute: int, price: float) -> None:
        self.minute_open_ns = minute
        self.open = self.high = self.low = self.close = price
        self.volume = 0.0
        self.quote_volume = 0.0
        self.taker_buy_quote_volume = 0.0
        self.trade_count = 0

    def finish(self) -> PolicyBar | None:
        if self.minute_open_ns is None or self.trade_count == 0:
            return None
        return PolicyBar(
            symbol=self.symbol,
            interval_minutes=1,
            open_time_ns=self.minute_open_ns,
            close_time_ns=self.minute_open_ns + NS_PER_MINUTE,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            taker_buy_quote_volume=self.taker_buy_quote_volume,
            trade_count=self.trade_count,
        )


def _load_nautilus() -> dict[str, Any]:
    from nautilus_trader.adapters.binance import BINANCE
    from nautilus_trader.adapters.binance import BinanceBar
    from nautilus_trader.adapters.binance import BinanceAccountType
    from nautilus_trader.adapters.binance import BinanceDataClientConfig
    from nautilus_trader.adapters.binance import BinanceExecClientConfig
    from nautilus_trader.adapters.binance import BinanceInstrumentProviderConfig
    from nautilus_trader.adapters.binance import BinanceLiveDataClientFactory
    from nautilus_trader.adapters.binance import BinanceLiveExecClientFactory
    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
    from nautilus_trader.adapters.binance.futures.types import BinanceFuturesMarkPriceUpdate
    from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
    from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
    from nautilus_trader.config import CacheConfig
    from nautilus_trader.config import LiveDataEngineConfig
    from nautilus_trader.config import LiveExecEngineConfig
    from nautilus_trader.config import LoggingConfig
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trader.core import nautilus_pyo3
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.data import DataType
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.enums import OrderType
    from nautilus_trader.model.enums import TimeInForce
    from nautilus_trader.model.identifiers import ClientId
    from nautilus_trader.model.identifiers import ClientOrderId
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import TraderId
    from nautilus_trader.model.objects import Money
    from nautilus_trader.trading import Strategy
    from nautilus_trader.trading.config import StrategyConfig

    BinanceBarPyo3 = nautilus_pyo3.binance.BinanceBar

    return locals()


NT = None
try:  # module remains importable for core-only historical environments
    NT = _load_nautilus()
except ImportError:
    NT = None


if NT is not None:
    StrategyConfig = NT["StrategyConfig"]
    Strategy = NT["Strategy"]
    InstrumentId = NT["InstrumentId"]
    BarType = NT["BarType"]
    OrderSide = NT["OrderSide"]
    OrderType = NT["OrderType"]
    TimeInForce = NT["TimeInForce"]

    class LiquidityEpisodeStrategyConfig(StrategyConfig, frozen=True):
        instrument_ids: tuple[InstrumentId, ...]
        state_path: str
        bar_types: tuple[BarType, ...] = ()
        execution_mode: str = "SHADOW"  # BACKTEST / SHADOW / SANDBOX / TESTNET
        initial_nav: float = 100_000.0
        bootstrap_lookback_minutes: int = DEFAULT_WARMUP_MINUTES
        live_inventory_poll_seconds: float = 15.0
        execution_start_ns: int = 0
        execution_end_ns: int | None = None


    class LiquidityEpisodeStrategy(Strategy):
        def __init__(self, config: LiquidityEpisodeStrategyConfig) -> None:
            super().__init__(config)
            if config.execution_mode not in {"BACKTEST", "SHADOW", "SANDBOX", "TESTNET"}:
                raise ValueError("invalid execution_mode")
            if config.execution_start_ns < 0:
                raise ValueError("execution_start_ns must be non-negative")
            if config.bootstrap_lookback_minutes <= 0:
                raise ValueError("bootstrap_lookback_minutes must be positive")
            if config.live_inventory_poll_seconds <= 0.0:
                raise ValueError("live_inventory_poll_seconds must be positive")
            if (
                config.execution_end_ns is not None
                and config.execution_end_ns <= config.execution_start_ns
            ):
                raise ValueError("execution_end_ns must be after execution_start_ns")
            symbols = {_symbol_from_instrument(str(item)) for item in config.instrument_ids}
            if symbols != set(SYMBOLS):
                raise ValueError("strategy requires BTC, ETH, SOL and XRP perpetuals")
            self.instrument_ids = {_symbol_from_instrument(str(item)): item for item in config.instrument_ids}
            if config.bar_types:
                bar_symbols = {_symbol_from_instrument(str(item.instrument_id)) for item in config.bar_types}
                if bar_symbols != set(SYMBOLS):
                    raise ValueError("bar_types must contain one external 1-minute bar for every instrument")
                self.bar_types = {
                    _symbol_from_instrument(str(item.instrument_id)): item for item in config.bar_types
                }
            else:
                self.bar_types = {
                    symbol: BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
                    for symbol, instrument_id in self.instrument_ids.items()
                }
            self.instruments: dict[str, Any] = {}
            policies = {
                symbol: SymbolEpisodePolicy(symbol, float(DEFAULT_CONTRACTS[symbol].tick_size), PolicyConfig())
                for symbol in SYMBOLS
            }
            self.coordinator = LiquidityEpisodeCoordinator(policies)
            self.store = StateStore(Path(config.state_path))
            runtime = self.store.load_snapshot("strategy_runtime") or {}
            self._restart_block_reason = native_restart_block_reason(
                config.execution_mode,
                runtime if isinstance(runtime, Mapping) else None,
            )
            self._restart_blocked = False
            self.active_plan: TradePlan | None = (
                TradePlan.from_dict(runtime["active_plan"])
                if isinstance(runtime, Mapping) and runtime.get("active_plan") is not None
                else None
            )
            self.active_order_ids: set[str] = set(
                runtime.get("active_order_ids", []) if isinstance(runtime, Mapping) else []
            )
            self.order_roles: dict[str, str] = dict(
                runtime.get("order_roles", {}) if isinstance(runtime, Mapping) else {}
            )
            self.order_mates: dict[str, str] = dict(
                runtime.get("order_mates", {}) if isinstance(runtime, Mapping) else {}
            )
            self.entry_filled_quantity = Decimal(
                str(runtime.get("entry_filled_quantity", "0")) if isinstance(runtime, Mapping) else "0"
            )
            self.active_sizing: dict[str, str] = (
                dict(runtime.get("active_sizing", {})) if isinstance(runtime, Mapping) else {}
            )
            self.deferred_targets: list[dict[str, str | int]] = (
                list(runtime.get("deferred_targets", [])) if isinstance(runtime, Mapping) else []
            )
            self.emergency_flatten_pending = bool(
                runtime.get("emergency_flatten_pending", False)
                if isinstance(runtime, Mapping)
                else False
            )
            self.emergency_flatten_reason = (
                str(runtime.get("emergency_flatten_reason"))
                if isinstance(runtime, Mapping) and runtime.get("emergency_flatten_reason")
                else None
            )
            self.sandbox_native_account_mutated = bool(
                runtime.get("sandbox_native_account_mutated", False)
                if isinstance(runtime, Mapping)
                else False
            )
            self._funding_state: dict[str, dict[str, float | int]] = (
                dict(runtime.get("funding_state", {})) if isinstance(runtime, Mapping) else {}
            )
            self._restored_policy_state: Mapping[str, object] | None = (
                runtime.get("policy_state")
                if isinstance(runtime, Mapping) and isinstance(runtime.get("policy_state"), Mapping)
                else None
            )
            self._replayed = False
            self._halted = bool(runtime.get("halted", False)) if isinstance(runtime, Mapping) else False
            self.policy_flow_by_key: dict[tuple[str, int], Any] = {}
            self.latest_policy_bars: dict[str, PolicyBar] = {}
            self._known_policy_minutes: dict[tuple[str, int, int], PolicyBar] = {}
            self.missing_flow_bars = 0
            # Public counters make the native execution contract directly testable.
            self.parent_orders_submitted = 0
            self.protective_pairs_submitted = 0
            self.plans_blocked_by_global_slot = 0
            self.max_active_instruments = 0
            # Backtest-only metadata used to turn an OHLC-ambiguous favorable
            # market exit into the declared adverse stop outcome.
            self._conservative_stop_exits: dict[str, str] = {}
            self._claimed_execution_plan_ids: set[str] = set()
            self._waiting_global_slot_plans: dict[str, TradePlan] = {}
            self.live_inventory_collector: LiveInventoryCollector | None = None
            self.live_inventory_status: dict[str, str] = {
                symbol: "NOT_CONFIGURED" for symbol in SYMBOLS
            }
            self._last_checkpoint_payload: dict[str, object] | None = None

        def on_start(self) -> None:
            for symbol, instrument_id in self.instrument_ids.items():
                instrument = self.cache.instrument(instrument_id)
                if instrument is None:
                    self.log.error(f"Instrument not loaded: {instrument_id}")
                    self.stop()
                    return
                self.instruments[symbol] = instrument
                # Binance's adapter emits BinanceBar only after the exchange
                # kline says x=true.  This preserves the exchange OHLCV,
                # quote/taker flow and underlying trade count in one payload.
                self.subscribe_bars(self.bar_types[symbol])
                if self.config.execution_mode != "BACKTEST":
                    self.subscribe_data(
                        data_type=NT["DataType"](
                            NT["BinanceFuturesMarkPriceUpdate"],
                            metadata={"instrument_id": instrument.id},
                        ),
                        client_id=NT["ClientId"](str(NT["BINANCE"])),
                    )
            if self._restart_block_reason is not None:
                self._restart_blocked = True
                self._halted = True
                self.store.append_event(
                    time_ns=self.clock.timestamp_ns(),
                    event_type="NATIVE_RESTART_BLOCKED",
                    payload={
                        "mode": self.config.execution_mode,
                        "reason": self._restart_block_reason,
                        "capabilities": native_restart_capabilities(),
                    },
                )
                self.stop()
                return
            self.store.append_event(
                time_ns=self.clock.timestamp_ns(),
                event_type="NATIVE_RESTART_BOUNDARY_DECLARED",
                payload={
                    "mode": self.config.execution_mode,
                    "capabilities": native_restart_capabilities(),
                },
            )
            self._replay_store()
            if self._restored_policy_state is not None:
                self.coordinator.restore_state(self._restored_policy_state)
            self._reconcile_runtime()
            self.store.append_event(
                time_ns=self.clock.timestamp_ns(),
                event_type="STRATEGY_STARTED",
                payload={
                    "mode": self.config.execution_mode,
                    "strategy_id": str(self.id),
                    "instrument_ids": [str(item) for item in self.config.instrument_ids],
                },
            )

        def _replay_store(self) -> None:
            if self._replayed:
                return
            raw_bars = self.store.load_bars(interval_minutes=1, symbols=SYMBOLS)
            bars, clock_evidence = _canonical_stored_bars(raw_bars)
            self._known_policy_minutes = {
                (bar.symbol, bar.interval_minutes, bar.open_time_ns): bar
                for bar in bars
            }
            if (
                clock_evidence["legacy_clock_rows_normalized"]
                or clock_evidence["identical_clock_aliases_collapsed"]
            ):
                self.store.append_event(
                    time_ns=self.clock.timestamp_ns(),
                    event_type="BAR_CLOCK_COMPATIBILITY_NORMALIZED",
                    payload={"context": "REPLAY", **clock_evidence},
                )
            grouped: dict[int, dict[str, PolicyBar]] = {}
            for bar in bars:
                grouped.setdefault(bar.close_time_ns, {})[bar.symbol] = bar
            complete_groups = 0
            pending_members = 0
            for close_ns in sorted(grouped):
                group = grouped[close_ns]
                # Complete stored groups are replayed exactly once.  A crash
                # can leave only part of the four-symbol minute durable; seed
                # those members into the coordinator's pending bucket now so
                # reconnect duplicates can remain known/idempotent and the
                # first missing live members complete the minute exactly once.
                for symbol in sorted(group):
                    self.coordinator.push_bar(group[symbol])
                    self._persist_policy_decisions()
                if set(group) == set(SYMBOLS):
                    complete_groups += 1
                else:
                    pending_members += len(group)
            decision_events = self.store.load_events(
                event_types=("POLICY_EPISODE_STARTED", "POLICY_EPISODE_TERMINAL"),
            )
            decision_restorer = getattr(self.coordinator, "restore_decision_events", None)
            if callable(decision_restorer):
                decision_restorer(decision_events)
            self._replayed = True
            self.store.append_event(
                time_ns=self.clock.timestamp_ns(),
                event_type="STATE_REPLAYED",
                payload={
                    "stored_bars": len(bars),
                    "complete_four_symbol_minutes": complete_groups,
                    "pending_partial_minute_members": pending_members,
                    "durable_policy_decision_events": len(decision_events),
                    **clock_evidence,
                },
            )

        def _persist_policy_decisions(self) -> None:
            """Drain policy facts before any snapshot can advance past them."""

            drainer = getattr(self.coordinator, "drain_decision_events", None)
            if not callable(drainer):
                return
            for item in drainer():
                self.store.append_event(**item)

        def _claim_policy_plan(self, plan: TradePlan, *, time_ns: int) -> None:
            """Claim through the optional semantic-ledger coordinator contract."""

            if callable(getattr(self.coordinator, "drain_decision_events", None)):
                self.coordinator.claim(plan, time_ns=time_ns)
            else:
                # Test and third-party coordinators written before the ledger
                # contract retain their one-positional-argument API.
                self.coordinator.claim(plan)
            self._persist_policy_decisions()

        def _reconcile_runtime(self) -> None:
            open_orders = []
            open_positions = []
            for instrument_id in self.config.instrument_ids:
                open_orders.extend(self.cache.orders_open(instrument_id=instrument_id))
                open_positions.extend(self.cache.positions_open(instrument_id=instrument_id))
            active_instruments = {str(item.instrument_id) for item in open_orders}
            active_instruments.update(str(item.instrument_id) for item in open_positions)
            if len(active_instruments) > 1:
                self._halted = True
                self.store.append_event(
                    time_ns=self.clock.timestamp_ns(),
                    event_type="RECONCILIATION_HALT",
                    payload={
                        "reason": "MULTIPLE_GLOBAL_ACCOUNT_SLOTS",
                        "instruments": sorted(active_instruments),
                    },
                )
                self.stop()
                return
            if (open_orders or open_positions) and self.active_plan is None:
                self._halted = True
                self.store.append_event(
                    time_ns=self.clock.timestamp_ns(),
                    event_type="RECONCILIATION_HALT",
                    payload={
                        "reason": "ORPHAN_EXCHANGE_STATE",
                        "open_order_ids": [str(item.client_order_id) for item in open_orders],
                        "open_position_ids": [str(item.id) for item in open_positions],
                    },
                )
                self.stop()
                return
            if self.active_plan is not None:
                expected = str(self.instrument_ids[self.active_plan.symbol])
                if active_instruments and active_instruments != {expected}:
                    self._halted = True
                    self.store.append_event(
                        time_ns=self.clock.timestamp_ns(),
                        event_type="RECONCILIATION_HALT",
                        payload={
                            "reason": "PLAN_EXCHANGE_INSTRUMENT_MISMATCH",
                            "expected": expected,
                            "actual": sorted(active_instruments),
                        },
                    )
                    self.stop()
                    return
                if not open_orders and not open_positions:
                    self.active_plan = None
                    self.active_order_ids.clear()
                else:
                    self.active_order_ids = {str(item.client_order_id) for item in open_orders}
                    try:
                        # An exchange-reconciled open order/position proves the
                        # entry was accepted even if the process died between
                        # acceptance and its local policy checkpoint.
                        claim_time_ns = self.clock.timestamp_ns()
                        self._claim_policy_plan(
                            self.active_plan,
                            time_ns=claim_time_ns,
                        )
                    except (KeyError, ValueError) as exc:
                        self._halted = True
                        self.store.append_event(
                            time_ns=self.clock.timestamp_ns(),
                            event_type="RECONCILIATION_HALT",
                            payload={
                                "reason": "ACTIVE_PLAN_CLAIM_MISMATCH",
                                "plan_id": self.active_plan.plan_id,
                                "details": str(exc),
                            },
                        )
                        self.stop()
                        return
            if self.emergency_flatten_pending and open_positions:
                instrument_id = open_positions[0].instrument_id
                self.cancel_all_orders(instrument_id)
                self.close_all_positions(instrument_id)
                self.store.append_event(
                    time_ns=self.clock.timestamp_ns(),
                    event_type="EMERGENCY_FLATTEN_RESUMED",
                    payload={
                        "instrument_id": str(instrument_id),
                        "reason": self.emergency_flatten_reason or "RESTORED_PROTECTION_FAILURE",
                    },
                )
            elif self.emergency_flatten_pending:
                self.emergency_flatten_pending = False
                self.emergency_flatten_reason = None
            self.store.append_event(
                time_ns=self.clock.timestamp_ns(),
                event_type="RECONCILIATION_COMPLETE",
                payload={
                    "open_orders": len(open_orders),
                    "open_positions": len(open_positions),
                    "active_plan": None if self.active_plan is None else self.active_plan.plan_id,
                },
            )

        def on_data(self, data) -> None:
            if not isinstance(data, NT["BinanceFuturesMarkPriceUpdate"]):
                return
            symbol = _symbol_from_instrument(str(data.instrument_id))
            self._funding_state[symbol] = {
                "rate": float(data.funding_rate),
                "next_funding_ns": int(data.next_funding_ns),
                "mark": float(data.mark),
                "ts_event": int(data.ts_event),
            }

        def on_bar(self, bar) -> None:
            """Consume the same completed external 1-minute bars in backtest and live."""
            symbol = _symbol_from_instrument(str(bar.bar_type.instrument_id))
            expected_bar_type = self.bar_types[symbol]
            if str(bar.bar_type) != str(expected_bar_type):
                raise RuntimeError(
                    "unexpected policy bar type: "
                    f"actual={bar.bar_type} expected={expected_bar_type}"
                )

            if isinstance(bar, (NT["BinanceBar"], NT["BinanceBarPyo3"])):
                # The live adapter keeps all raw exchange kline fields on its
                # BinanceBar subclass.  No tick reconstruction or sidecar is
                # permitted on this production path.
                self._process_completed_bar(
                    policy_bar_from_native_binance_bar(
                        bar,
                        expected_bar_type=expected_bar_type,
                    )
                )
                return

            if self.config.execution_mode != "BACKTEST":
                raise RuntimeError(
                    "production policy requires native BinanceBar payloads; "
                    f"received {type(bar).__module__}.{type(bar).__name__}"
                )

            close_ns = int(bar.ts_event)
            flow = self.policy_flow_by_key.pop((symbol, close_ns), None)
            if flow is None:
                # Historical backtests use ordinary Nautilus Bar plus an exact
                # PolicyBar flow sidecar.  A non-Binance live Bar must never
                # silently degrade to reconstructed or neutral flow.
                self.missing_flow_bars += 1
                self.store.append_event(
                    time_ns=close_ns,
                    event_type="BAR_ABSTAINED_MISSING_POLICY_FLOW",
                    payload={"symbol": symbol, "bar_type": str(bar.bar_type)},
                )
                return
            if str(flow.symbol) != symbol or int(flow.ts_event) != close_ns:
                raise RuntimeError("policy flow sidecar does not match native bar clock")
            policy_bar = PolicyBar(
                symbol=symbol,
                interval_minutes=1,
                open_time_ns=int(flow.open_time_ns),
                close_time_ns=close_ns,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
                quote_volume=float(flow.quote_volume),
                taker_buy_quote_volume=float(flow.taker_buy_quote_volume),
                trade_count=int(flow.trade_count),
            )
            self._process_completed_bar(policy_bar)

        def register_policy_flow(self, records) -> None:
            """Register timestamp-identical Binance kline fields for native bars."""
            for flow in records:
                key = (str(flow.symbol), int(flow.ts_event))
                prior = self.policy_flow_by_key.get(key)
                if prior is not None and prior != flow:
                    raise RuntimeError(f"policy flow mutation for {key}")
                self.policy_flow_by_key[key] = flow

        def _process_completed_bar(self, completed: PolicyBar) -> None:
            completed = canonicalize_completed_policy_bar(completed)
            minute_key = (
                completed.symbol,
                completed.interval_minutes,
                completed.open_time_ns,
            )
            prior = self._known_policy_minutes.get(minute_key)
            if prior is not None:
                if prior != completed:
                    raise RuntimeError(
                        "live market data mutation for canonical minute: "
                        f"{completed.symbol} open={completed.open_time_ns}"
                    )
                self.store.append_event(
                    time_ns=completed.close_time_ns,
                    event_type="BAR_CLOCK_DUPLICATE_IGNORED",
                    payload={
                        "symbol": completed.symbol,
                        "open_time_ns": completed.open_time_ns,
                        "canonical_close_time_ns": completed.close_time_ns,
                    },
                )
                return
            self.latest_policy_bars[completed.symbol] = completed
            self._activate_deferred_targets(completed)
            if not self.store.append_bar(completed):
                self._known_policy_minutes[minute_key] = completed
                return
            self._known_policy_minutes[minute_key] = completed
            plans = self.coordinator.push_bar(completed)
            self._persist_policy_decisions()
            plans.extend(self._refresh_waiting_global_slot(completed))
            # A four-market policy decision is made when the final symbol for
            # a synchronized close arrives.  Re-check the active symbol's bar
            # at that same close so a durable supersession/opposite-leg signal
            # cancels its pending entry immediately, regardless of which peer
            # happened to complete the group last.
            pending_bar = completed
            if self.active_plan is not None and completed.symbol != self.active_plan.symbol:
                active_symbol_bar = self.latest_policy_bars.get(self.active_plan.symbol)
                if (
                    active_symbol_bar is not None
                    and active_symbol_bar.close_time_ns == completed.close_time_ns
                ):
                    pending_bar = active_symbol_bar
            self._manage_pending_plan(pending_bar)
            synchronized_close = all(
                self.latest_policy_bars.get(symbol) is not None
                and self.latest_policy_bars[symbol].close_time_ns == completed.close_time_ns
                for symbol in SYMBOLS
            )
            if (
                synchronized_close
                and not self._global_slot_busy()
                and self._waiting_global_slot_plans
            ):
                plans = self._rerank_released_global_slot(plans)
            self._handle_plans(plans, completed)
            self._observe_global_slot()
            self._checkpoint(completed.close_time_ns)

        def _manage_pending_plan(self, bar: PolicyBar) -> None:
            if self.active_plan is None:
                return
            plan = self.active_plan
            if plan.symbol != bar.symbol:
                return
            instrument_id = self.instrument_ids[plan.symbol]
            if not self.portfolio.is_flat(instrument_id):
                return
            entry_id = next((key for key, value in self.order_roles.items() if value == "ENTRY"), None)
            if entry_id is None:
                return
            entry_order = self.cache.order(NT["ClientOrderId"](entry_id))
            if entry_order is None or not entry_order.is_open:
                return
            policy_valid, policy_reason = self._claimed_plan_validity(plan)
            if not policy_valid:
                reason = "POLICY_INVALIDATED"
            elif bar.close_time_ns >= plan.expires_time_ns:
                reason = "PENDING_EXPIRED"
            elif plan.side == "LONG" and bar.low <= plan.stop:
                reason = "PREFILL_INVALIDATED"
            elif plan.side == "SHORT" and bar.high >= plan.stop:
                reason = "PREFILL_INVALIDATED"
            elif plan.side == "LONG" and bar.high >= plan.target:
                reason = "TARGET_SPENT_BEFORE_FILL"
            elif plan.side == "SHORT" and bar.low <= plan.target:
                reason = "TARGET_SPENT_BEFORE_FILL"
            else:
                return
            self.cancel_order(entry_order)
            self.store.append_event(
                time_ns=bar.close_time_ns,
                event_type="PENDING_PLAN_CANCELED",
                payload={
                    "plan_id": plan.plan_id,
                    "reason": reason,
                    "policy_reason": policy_reason,
                },
            )

        def _claimed_plan_validity(self, plan: TradePlan) -> tuple[bool, str | None]:
            """Route durable policy validity without giving it position authority."""

            policies = getattr(self.coordinator, "policies", None)
            policy = policies.get(plan.symbol) if isinstance(policies, Mapping) else None
            checker = getattr(policy, "claimed_plan_validity", None)
            if checker is None:
                # Compatibility for focused execution coordinators which do
                # not implement policy selection.  The production coordinator
                # always exposes its four SymbolEpisodePolicy instances.
                return True, None
            valid, reason = checker(plan.plan_id)
            return bool(valid), None if reason is None else str(reason)

        def _handle_plans(
            self,
            plans: Iterable[TradePlan],
            decision_bar: PolicyBar,
        ) -> None:
            """Resolve execution-infeasible winners without waiting another minute.

            The coordinator returns the next ranked proposal when its current
            winner is terminally rejected.  Consume that cascade synchronously;
            otherwise an acceptance response can become a stale next-bar entry.
            """

            queue = list(plans)
            seen: set[str] = set()
            while queue:
                plan = queue.pop(0)
                if plan.plan_id in seen:
                    continue
                seen.add(plan.plan_id)
                queue.extend(self._handle_plan(plan, decision_bar))

        def _handle_plan(self, plan: TradePlan, decision_bar: PolicyBar) -> list[TradePlan]:
            if self._halted:
                return []
            if plan.decision_time_ns < self.config.execution_start_ns:
                return []
            if (
                self.config.execution_end_ns is not None
                and plan.decision_time_ns >= self.config.execution_end_ns
            ):
                return []
            if self._global_slot_busy():
                if self._is_immediate_acceptance(plan):
                    self.plans_blocked_by_global_slot += 1
                    return self._terminal_reject_plan(
                        plan,
                        reason="GLOBAL_SLOT_BUSY_IMMEDIATE_RESPONSE_MISSED",
                        event_type="IMMEDIATE_RESPONSE_MISSED_GLOBAL_SLOT",
                        time_ns=decision_bar.close_time_ns,
                    )
                if self._is_failed_future_first_return(plan):
                    first_wait = plan.plan_id not in self._waiting_global_slot_plans
                    self._waiting_global_slot_plans[plan.plan_id] = plan
                    if first_wait:
                        self.plans_blocked_by_global_slot += 1
                        self.store.append_event(
                            time_ns=decision_bar.close_time_ns,
                            event_type="PLAN_WAITING_GLOBAL_SLOT",
                            payload={
                                "plan_id": plan.plan_id,
                                "reason": "FAILED_AUCTION_FUTURE_FIRST_RETURN_REMAINS_LIVE",
                            },
                        )
                        self._checkpoint(decision_bar.close_time_ns)
                    return []
                self.plans_blocked_by_global_slot += 1
                return self._terminal_reject_plan(
                    plan,
                    reason="GLOBAL_ACCOUNT_SLOT_BUSY",
                    event_type="PLAN_REJECTED_GLOBAL_SLOT",
                    time_ns=decision_bar.close_time_ns,
                )
            if plan.plan_id in self._waiting_global_slot_plans:
                reason = self._waiting_touch_reason(plan, decision_bar)
                if reason is not None:
                    self._waiting_global_slot_plans.pop(plan.plan_id, None)
                    return self._terminal_reject_plan(
                        plan,
                        reason=reason,
                        event_type="WAITING_GLOBAL_SLOT_INVALIDATED",
                        time_ns=decision_bar.close_time_ns,
                    )
                self._waiting_global_slot_plans.pop(plan.plan_id, None)
                self.store.append_event(
                    time_ns=decision_bar.close_time_ns,
                    event_type="WAITING_GLOBAL_SLOT_RELEASED",
                    payload={"plan_id": plan.plan_id},
                )
            return self._submit_parent(plan)

        def _global_slot_busy(self) -> bool:
            if self.active_plan is not None:
                return True
            for instrument_id in self.config.instrument_ids:
                if self.cache.orders_open(instrument_id=instrument_id):
                    return True
                if self.cache.positions_open(instrument_id=instrument_id):
                    return True
            return False

        def _observe_global_slot(self) -> None:
            active = {
                str(instrument_id)
                for instrument_id in self.config.instrument_ids
                if self.cache.orders_open(instrument_id=instrument_id)
                or self.cache.positions_open(instrument_id=instrument_id)
            }
            self.max_active_instruments = max(self.max_active_instruments, len(active))
            if len(active) > 1:
                self._halted = True
                raise RuntimeError(f"global slot violated: {sorted(active)}")

        def _terminal_reject_plan(
            self,
            plan: TradePlan,
            *,
            reason: str,
            event_type: str,
            details: Mapping[str, object] | None = None,
            time_ns: int | None = None,
        ) -> list[TradePlan]:
            """Persist one execution-infeasible terminal proposal decision."""

            event_time_ns = self.clock.timestamp_ns() if time_ns is None else int(time_ns)
            rejector = getattr(self.coordinator, "reject_proposal", None)
            next_plans: list[TradePlan] = []
            if callable(rejector):
                try:
                    if callable(getattr(self.coordinator, "drain_decision_events", None)):
                        offered = rejector(plan, reason, time_ns=event_time_ns)
                    else:
                        offered = rejector(plan, reason)
                    self._persist_policy_decisions()
                    if offered is not None:
                        next_plans = [item for item in offered if isinstance(item, TradePlan)]
                except (KeyError, ValueError) as exc:
                    self._halted = True
                    self.store.append_event(
                        time_ns=event_time_ns,
                        event_type="PROPOSAL_TERMINALIZATION_FAILED",
                        payload={
                            "plan_id": plan.plan_id,
                            "reason": reason,
                            "details": str(exc),
                        },
                    )
                    self._checkpoint(event_time_ns)
                    raise RuntimeError(
                        f"cannot terminalize execution-infeasible plan {plan.plan_id}",
                    ) from exc
            self.store.append_event(
                time_ns=event_time_ns,
                event_type=event_type,
                payload={
                    "plan_id": plan.plan_id,
                    "reason": reason,
                    **dict(details or {}),
                },
            )
            self._checkpoint(event_time_ns)
            return next_plans

        @staticmethod
        def _is_immediate_acceptance(plan: TradePlan) -> bool:
            return str(plan.evidence.get("entry_event", "")) == (
                "ACCEPTANCE_FIRST_RESPONSE_CLOSE"
            )

        @staticmethod
        def _is_failed_future_first_return(plan: TradePlan) -> bool:
            return (
                plan.family == "FAILED_AUCTION_REVERSAL"
                and str(plan.evidence.get("entry_event", ""))
                == "FAILED_AUCTION_FUTURE_FIRST_RETURN"
            )

        @staticmethod
        def _waiting_touch_reason(plan: TradePlan, bar: PolicyBar) -> str | None:
            if plan.symbol != bar.symbol or bar.close_time_ns <= plan.decision_time_ns:
                return None
            if plan.side == "LONG":
                if bar.high >= plan.target:
                    return "DESTINATION_SPENT_WHILE_GLOBAL_SLOT_BUSY"
                if bar.low <= plan.stop:
                    return "STOP_INVALIDATED_WHILE_GLOBAL_SLOT_BUSY"
                if bar.low <= plan.entry:
                    return "FIRST_RETURN_PASSED_WHILE_GLOBAL_SLOT_BUSY"
            else:
                if bar.low <= plan.target:
                    return "DESTINATION_SPENT_WHILE_GLOBAL_SLOT_BUSY"
                if bar.high >= plan.stop:
                    return "STOP_INVALIDATED_WHILE_GLOBAL_SLOT_BUSY"
                if bar.high >= plan.entry:
                    return "FIRST_RETURN_PASSED_WHILE_GLOBAL_SLOT_BUSY"
            if (
                bar.high >= plan.entry_zone.lower
                and bar.low <= plan.entry_zone.upper
            ):
                return "SOURCE_RETOUCHED_WHILE_GLOBAL_SLOT_BUSY"
            return None

        def _coordinator_proposal_is_live(self, plan: TradePlan) -> bool | None:
            policies = getattr(self.coordinator, "policies", None)
            if not isinstance(policies, Mapping):
                return None
            policy = policies.get(plan.symbol)
            proposals = getattr(policy, "_proposals", None)
            if not isinstance(proposals, Mapping):
                return None
            current = proposals.get(plan.episode_id)
            return isinstance(current, TradePlan) and current.plan_id == plan.plan_id

        def _rerank_released_global_slot(
            self,
            fresh_plans: Iterable[TradePlan],
        ) -> list[TradePlan]:
            """Rerank every still-live waiting owner with the fresh winner.

            A dict's insertion order is not account opportunity priority.  Only
            the selected owner is released; other waiting proposals remain in
            the waiting ledger for the next synchronized decision.
            """

            combined = {
                plan.plan_id: plan
                for plan in (
                    *self._waiting_global_slot_plans.values(),
                    *tuple(fresh_plans),
                )
            }
            if not combined:
                return []
            arbiter = getattr(self.coordinator, "arbitrate", None)
            if callable(arbiter):
                ranked = arbiter(tuple(combined.values()))
            else:
                # Focused execution coordinators do not own policy selection.
                # Use the production semantic ordering, never insertion order.
                ranked = LiquidityEpisodeCoordinator.arbitrate(
                    tuple(combined.values()),
                )
            return [plan for plan in ranked if isinstance(plan, TradePlan)]

        def _refresh_waiting_global_slot(
            self,
            bar: PolicyBar,
        ) -> list[TradePlan]:
            """Refresh deferred failed-auction validity on every completed bar."""

            cascaded: list[TradePlan] = []
            for plan_id, plan in list(self._waiting_global_slot_plans.items()):
                reason = self._waiting_touch_reason(plan, bar)
                proposal_live = self._coordinator_proposal_is_live(plan)
                if reason is None and proposal_live is False:
                    reason = "POLICY_PROPOSAL_NO_LONGER_LIVE_WHILE_GLOBAL_SLOT_BUSY"
                if reason is None:
                    continue
                self._waiting_global_slot_plans.pop(plan_id, None)
                if proposal_live is True or proposal_live is None:
                    cascaded.extend(
                        self._terminal_reject_plan(
                            plan,
                            reason=reason,
                            event_type="WAITING_GLOBAL_SLOT_INVALIDATED",
                            time_ns=bar.close_time_ns,
                        ),
                    )
                else:
                    # Production policy already terminalized the first-return,
                    # stop, destination or source; do not reject it twice.
                    self.store.append_event(
                        time_ns=bar.close_time_ns,
                        event_type="WAITING_GLOBAL_SLOT_INVALIDATED",
                        payload={"plan_id": plan.plan_id, "reason": reason},
                    )
                    self._checkpoint(bar.close_time_ns)
            return cascaded

        def _submit_parent(self, plan: TradePlan) -> list[TradePlan]:
            instrument = self.instruments[plan.symbol]
            account = self.portfolio.account(instrument.venue)
            equity_by_currency = self.portfolio.equity(venue=instrument.venue)
            settlement = instrument.settlement_currency or instrument.quote_currency
            nav = equity_by_currency.get(settlement)
            free_margin = None if account is None else account.balance_free(settlement)
            missing_prices = self.portfolio.missing_price_instruments(instrument.venue)
            if account is None or nav is None or free_margin is None or missing_prices:
                return self._terminal_reject_plan(
                    plan,
                    reason="NATIVE_ACCOUNT_OR_MTM_UNAVAILABLE",
                    event_type="PLAN_REJECTED_SIZING",
                    details={
                        "missing_price_instruments": [str(item) for item in missing_prices],
                    },
                )
            account_leverage = account.leverage(instrument.id) or account.default_leverage
            immediate_acceptance = self._is_immediate_acceptance(plan)
            planned_entry = Decimal(str(plan.entry))
            execution_limit = planned_entry
            if immediate_acceptance:
                # A naked MARKET order can gap beyond both the target and the
                # 3% risk geometry.  The response is a one-shot IOC at the
                # worst native price which still preserves gross RR >= 1.
                tick = instrument.price_increment.as_decimal()
                raw_stop = Decimal(str(plan.stop)) / tick
                stop_rounding = ROUND_FLOOR if plan.side == "LONG" else ROUND_CEILING
                native_stop_hint = raw_stop.to_integral_value(rounding=stop_rounding) * tick
                native_target_hint = Decimal(str(instrument.make_price(plan.target)))
                midpoint = (native_stop_hint + native_target_hint) / Decimal(2)
                units = midpoint / tick
                bound_rounding = ROUND_FLOOR if plan.side == "LONG" else ROUND_CEILING
                execution_limit = units.to_integral_value(rounding=bound_rounding) * tick
            sizing = size_three_percent_stop_risk(
                instrument,
                side=plan.side,
                # Size the declared structural entry-to-stop risk.  The IOC
                # limit below is only a worst acceptable execution guard;
                # using it here silently under-risks every response entry.
                entry=planned_entry,
                stop=plan.stop,
                nav=nav,
                free_margin=free_margin,
                max_leverage=DEFAULT_CONTRACTS[plan.symbol].max_leverage,
                account_leverage=account_leverage,
                # The parent is not declared post-only, so a marketable limit
                # is risked at the taker rate rather than credited as maker.
                entry_post_only_guaranteed=False,
            )
            if not isinstance(sizing, SizingAccepted):
                return self._terminal_reject_plan(
                    plan,
                    reason=f"SIZING:{sizing.reason.value}",
                    event_type="PLAN_REJECTED_SIZING",
                    details={"details": dict(sizing.details)},
                )
            target_price = instrument.make_price(plan.target)
            execution_limit_price = instrument.make_price(execution_limit)
            native_entry = sizing.entry_price.as_decimal()
            native_stop = sizing.stop_trigger_price.as_decimal()
            native_target = target_price.as_decimal()
            if instrument.min_price is not None and target_price < instrument.min_price:
                native_geometry_reason = "NATIVE_TARGET_BELOW_PRICE_BOUND"
            elif instrument.max_price is not None and target_price > instrument.max_price:
                native_geometry_reason = "NATIVE_TARGET_ABOVE_PRICE_BOUND"
            elif plan.side == "LONG" and not (native_stop < native_entry < native_target):
                native_geometry_reason = "NATIVE_LONG_GEOMETRY_INVALID"
            elif plan.side == "SHORT" and not (native_target < native_entry < native_stop):
                native_geometry_reason = "NATIVE_SHORT_GEOMETRY_INVALID"
            else:
                native_risk = abs(native_entry - native_stop)
                native_reward = abs(native_target - native_entry)
                native_gross_rr = native_reward / native_risk
                native_geometry_reason = (
                    "NATIVE_GROSS_RR_BELOW_ONE" if native_gross_rr < Decimal(1) else None
                )
            if native_geometry_reason is not None:
                return self._terminal_reject_plan(
                    plan,
                    reason=native_geometry_reason,
                    event_type="PLAN_REJECTED_NATIVE_GEOMETRY",
                    details={
                        "native_entry": str(native_entry),
                        "native_stop": str(native_stop),
                        "native_target": str(native_target),
                    },
                )
            # A marketable IOC can execute away from the structural entry.
            # Preserve 3% structural quantity while proving the whole order is
            # still feasible at its worst accepted native price.
            execution_notional = instrument.notional_value(
                sizing.quantity,
                execution_limit_price,
            )
            execution_effective_leverage = execution_notional.as_decimal() / sizing.nav
            contract_max_leverage = DEFAULT_CONTRACTS[plan.symbol].max_leverage
            if execution_effective_leverage > contract_max_leverage:
                return self._terminal_reject_plan(
                    plan,
                    reason="EXECUTION_BOUND_MAX_LEVERAGE",
                    event_type="PLAN_REJECTED_SIZING",
                    details={
                        "effective_leverage": str(execution_effective_leverage),
                        "maximum": str(contract_max_leverage),
                        "execution_limit_price": str(execution_limit_price),
                    },
                )
            native_margin = account.calculate_margin_init(
                instrument,
                sizing.quantity,
                execution_limit_price,
            )
            if native_margin > free_margin:
                return self._terminal_reject_plan(
                    plan,
                    reason="INSUFFICIENT_NATIVE_MARGIN",
                    event_type="PLAN_REJECTED_SIZING",
                    details={
                        "required": str(native_margin),
                        "available": str(free_margin),
                    },
                )
            quantity = sizing.quantity
            side = OrderSide.BUY if plan.side == "LONG" else OrderSide.SELL
            if immediate_acceptance:
                # IOC guarantees there is no resting second-return order.  The
                # limit is the economic fail-closed bound, not an entry target.
                order = self.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=side,
                    quantity=quantity,
                    price=execution_limit_price,
                    time_in_force=TimeInForce.IOC,
                )
                entry_mode = "IMMEDIATE_RESPONSE_BOUNDED_IOC"
            else:
                order = self.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=side,
                    quantity=quantity,
                    price=sizing.entry_price,
                    # Structural policy invalidation owns the pending lifetime.
                    time_in_force=TimeInForce.GTC,
                )
                entry_mode = "FIRST_RETURN_LIMIT"
            self.active_plan = plan
            self.active_sizing = {
                "quantity": str(sizing.quantity),
                "planned_entry_price": str(sizing.entry_price),
                "execution_limit_price": str(execution_limit_price),
                "stop_trigger_price": str(sizing.stop_trigger_price),
                "adverse_stop_fill_price": str(sizing.adverse_stop_fill_price),
                "target_price": str(target_price),
                "native_gross_rr": str(native_gross_rr),
                "entry_mode": entry_mode,
                "structural_risk_budget": str(sizing.structural_risk_budget),
                "planned_structural_stop_loss": str(sizing.planned_structural_stop_loss),
                "planned_structural_risk_fraction": str(
                    sizing.planned_structural_risk_fraction
                ),
                "estimated_adverse_price_loss": str(sizing.estimated_adverse_price_loss),
                "estimated_entry_fee": str(sizing.estimated_entry_fee),
                "estimated_stop_fee": str(sizing.estimated_stop_fee),
                "estimated_all_in_stop_loss": str(sizing.estimated_all_in_stop_loss),
                "estimated_all_in_risk_fraction": str(
                    sizing.estimated_all_in_risk_fraction
                ),
                "effective_leverage": str(sizing.effective_leverage),
                "execution_bound_effective_leverage": str(execution_effective_leverage),
                "initial_margin_required": str(native_margin),
            }
            order_id = str(order.client_order_id)
            self.active_order_ids = {order_id}
            self.order_roles = {order_id: "ENTRY"}
            self.order_mates.clear()
            self.deferred_targets.clear()
            self.entry_filled_quantity = Decimal("0")
            self.emergency_flatten_pending = False
            self.emergency_flatten_reason = None
            # Store the immutable intent and client ID before crossing the
            # process/exchange boundary.  Reconciliation can then distinguish
            # an accepted exchange order from an intent which never left the
            # process.
            self.store.append_event(
                time_ns=plan.decision_time_ns,
                event_type="PARENT_SUBMISSION_INTENT",
                payload={
                    "mode": self.config.execution_mode,
                    "plan": plan.to_dict(),
                    "quantity": str(quantity),
                    "client_order_id": order_id,
                    "order_type": str(order.order_type),
                },
            )
            self._checkpoint(plan.decision_time_ns)
            try:
                self.submit_order(order)
            except Exception as exc:
                self.active_plan = None
                self.active_order_ids.clear()
                self.order_roles.clear()
                self.order_mates.clear()
                self.deferred_targets.clear()
                self.entry_filled_quantity = Decimal("0")
                self.emergency_flatten_pending = False
                self.emergency_flatten_reason = None
                self.active_sizing.clear()
                self.store.append_event(
                    time_ns=self.clock.timestamp_ns(),
                    event_type="PARENT_SUBMISSION_FAILED",
                    payload={"client_order_id": order_id, "reason": repr(exc)},
                )
                self._checkpoint(self.clock.timestamp_ns())
                raise
            self.parent_orders_submitted += 1
            self.store.append_event(
                time_ns=plan.decision_time_ns,
                event_type="PARENT_ORDER_SUBMITTED",
                payload={
                    "mode": self.config.execution_mode,
                    "plan": plan.to_dict(),
                    "quantity": str(quantity),
                    "sizing": dict(self.active_sizing),
                    "client_order_id": order_id,
                    "order_type": str(order.order_type),
                    "time_in_force": str(order.time_in_force),
                },
            )
            if not immediate_acceptance:
                # Preserve the existing evidence contract for first-return
                # resting parents without mislabeling response market orders.
                self.store.append_event(
                    time_ns=plan.decision_time_ns,
                    event_type="PARENT_LIMIT_SUBMITTED",
                    payload={
                        "mode": self.config.execution_mode,
                        "plan": plan.to_dict(),
                        "quantity": str(quantity),
                        "sizing": dict(self.active_sizing),
                        "client_order_id": order_id,
                        "time_in_force": "GTC",
                    },
                )
            return []

        def _submit_protection(self, quantity, *, fill_time_ns: int) -> None:
            if self.active_plan is None:
                return
            plan = self.active_plan
            instrument = self.instruments[plan.symbol]
            exit_side = OrderSide.SELL if plan.side == "LONG" else OrderSide.BUY
            stop_trigger = self.active_sizing.get("stop_trigger_price", str(plan.stop))
            adverse_stop_fill = self.active_sizing.get(
                "adverse_stop_fill_price",
                str(stop_trigger),
            )
            target_price = self.active_sizing.get("target_price", str(plan.target))
            fill_bar = self.latest_policy_bars.get(plan.symbol)
            pre_match_context = getattr(self, "native_bar_context", {})
            pre_match_bar = pre_match_context.get(str(instrument.id))
            # Backtest matching emits fills before Strategy.on_bar for that
            # bar, but the native cache already owns the current bar.  Read it
            # directly so fill-bar ambiguity is not mistaken for a future bar.
            native_fill_bar = (
                self.cache.bar(self.bar_types[plan.symbol], index=0)
                if self.config.execution_mode == "BACKTEST"
                else None
            )
            if pre_match_bar is not None and int(pre_match_bar.ts_event) == fill_time_ns:
                fill_bar_time_ns = int(pre_match_bar.ts_event)
                fill_bar_low = float(pre_match_bar.low)
                fill_bar_high = float(pre_match_bar.high)
            elif native_fill_bar is not None and int(native_fill_bar.ts_event) == fill_time_ns:
                fill_bar_time_ns = int(native_fill_bar.ts_event)
                fill_bar_low = float(native_fill_bar.low)
                fill_bar_high = float(native_fill_bar.high)
            elif fill_bar is not None:
                fill_bar_time_ns = fill_bar.close_time_ns
                fill_bar_low = fill_bar.low
                fill_bar_high = fill_bar.high
            else:
                fill_bar_time_ns = -1
                fill_bar_low = float("inf")
                fill_bar_high = float("-inf")
            fill_bar_stop_touched = bool(
                self.config.execution_mode == "BACKTEST"
                and fill_bar_time_ns == fill_time_ns
                and (
                    (plan.side == "LONG" and fill_bar_low <= float(stop_trigger))
                    or (plan.side == "SHORT" and fill_bar_high >= float(stop_trigger))
                )
            )
            if fill_bar_stop_touched:
                # With OHLC bars the order of entry and the bar extreme is
                # unknowable.  Candidate-10's gap protection principle is
                # applied conservatively: never credit this bar's target and
                # exit the actually filled chunk through the native matcher.
                market = self.order_factory.market(
                    instrument_id=instrument.id,
                    order_side=exit_side,
                    quantity=quantity,
                    reduce_only=True,
                )
                market_id = str(market.client_order_id)
                self.active_order_ids.add(market_id)
                self.order_roles[market_id] = "STOP"
                self._conservative_stop_exits[market_id] = str(adverse_stop_fill)
                try:
                    self.submit_order(market)
                except Exception as exc:
                    self._handle_active_order_error(
                        market_id,
                        instrument.id,
                        f"PROTECTIVE_MARKET_SUBMISSION_FAILED:{exc!r}",
                        fill_time_ns,
                    )
                    return
                if self.emergency_flatten_pending or market_id not in self.active_order_ids:
                    return
                self.protective_pairs_submitted += 1
                self.store.append_event(
                    time_ns=fill_time_ns,
                    event_type="PROTECTION_SUBMITTED",
                    payload={
                        "plan_id": plan.plan_id,
                        "quantity": str(quantity),
                        "stop": str(stop_trigger),
                        "adverse_stop_fill": str(adverse_stop_fill),
                        "target": str(target_price),
                        "reason": "FILL_BAR_STOP_OR_AMBIGUOUS_NATIVE_MARKET_EXIT",
                    },
                )
                return
            stop = self.order_factory.stop_market(
                instrument_id=instrument.id,
                order_side=exit_side,
                quantity=quantity,
                trigger_price=instrument.make_price(stop_trigger),
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
            )
            stop_id = str(stop.client_order_id)
            self.active_order_ids.add(stop_id)
            self.order_roles[stop_id] = "STOP"
            try:
                self.submit_order(stop)
            except Exception as exc:
                self._handle_active_order_error(
                    stop_id,
                    instrument.id,
                    f"PROTECTIVE_STOP_SUBMISSION_FAILED:{exc!r}",
                    fill_time_ns,
                )
                return
            if self.emergency_flatten_pending or stop_id not in self.active_order_ids:
                return
            # A target touched on the fill bar is never credited.  For bar
            # replay the target becomes eligible on the next completed bar;
            # live tick execution can safely install it immediately.
            if (
                self.config.execution_mode == "BACKTEST"
                and self.active_sizing.get("entry_mode")
                != "IMMEDIATE_RESPONSE_BOUNDED_IOC"
            ):
                # Native bar fills inherit the source bar timestamp, which may
                # still be an inclusive -1ns representation.  Persist the
                # same exclusive right edge used by PolicyBar so the current
                # fill bar cannot masquerade as the next eligible bar.
                deferred_after_ns = (
                    fill_bar_time_ns
                    if fill_bar_time_ns % NS_PER_MINUTE == 0
                    else (fill_bar_time_ns // NS_PER_MINUTE + 1) * NS_PER_MINUTE
                )
                self.deferred_targets.append(
                    {
                        "symbol": plan.symbol,
                        "stop_id": stop_id,
                        "quantity": str(quantity),
                        "target": str(target_price),
                        "fill_time_ns": deferred_after_ns,
                    },
                )
                target_id = "DEFERRED"
            else:
                target = self.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=exit_side,
                    quantity=quantity,
                    price=instrument.make_price(target_price),
                    time_in_force=TimeInForce.GTC,
                    reduce_only=True,
                )
                target_id = str(target.client_order_id)
                self.active_order_ids.add(target_id)
                self.order_roles[target_id] = "TARGET"
                self.order_mates[stop_id] = target_id
                self.order_mates[target_id] = stop_id
                try:
                    self.submit_order(target)
                except Exception as exc:
                    self._handle_active_order_error(
                        target_id,
                        instrument.id,
                        f"PROTECTIVE_TARGET_SUBMISSION_FAILED:{exc!r}",
                        fill_time_ns,
                    )
                    return
                if self.emergency_flatten_pending or target_id not in self.active_order_ids:
                    return
            # Provenance: candidate-10/c10_flow_parent_execution.py already
            # established per-fill protection and independent sibling pairs.
            # Each additional parent fill receives its own reduce-only pair.
            self.protective_pairs_submitted += 1
            self.store.append_event(
                time_ns=self.clock.timestamp_ns(),
                event_type="PROTECTION_SUBMITTED",
                payload={
                    "plan_id": plan.plan_id,
                    "quantity": str(quantity),
                    "stop": str(stop_trigger),
                    "target": str(target_price),
                    "target_client_order_id": target_id,
                },
            )

        def _activate_deferred_targets(self, bar: PolicyBar) -> None:
            if not self.deferred_targets:
                return
            remaining: list[dict[str, str | int]] = []
            for item in self.deferred_targets:
                if item["symbol"] != bar.symbol or bar.close_time_ns <= int(item["fill_time_ns"]):
                    remaining.append(item)
                    continue
                stop_id = str(item["stop_id"])
                stop_order = self.cache.order(NT["ClientOrderId"](stop_id))
                instrument_id = self.instrument_ids[bar.symbol]
                if stop_order is None or not stop_order.is_open or self.portfolio.is_flat(instrument_id):
                    continue
                instrument = self.instruments[bar.symbol]
                assert self.active_plan is not None
                exit_side = OrderSide.SELL if self.active_plan.side == "LONG" else OrderSide.BUY
                target = self.order_factory.limit(
                    instrument_id=instrument.id,
                    order_side=exit_side,
                    quantity=instrument.make_qty(Decimal(str(item["quantity"]))),
                    price=instrument.make_price(Decimal(str(item["target"]))),
                    time_in_force=TimeInForce.GTC,
                    reduce_only=True,
                )
                target_id = str(target.client_order_id)
                self.active_order_ids.add(target_id)
                self.order_roles[target_id] = "TARGET"
                self.order_mates[stop_id] = target_id
                self.order_mates[target_id] = stop_id
                try:
                    self.submit_order(target)
                except Exception as exc:
                    self._handle_active_order_error(
                        target_id,
                        instrument.id,
                        f"DEFERRED_TARGET_SUBMISSION_FAILED:{exc!r}",
                        bar.close_time_ns,
                    )
                    remaining.clear()
                    break
                if self.emergency_flatten_pending or target_id not in self.active_order_ids:
                    remaining.clear()
                    break
                self.store.append_event(
                    time_ns=bar.close_time_ns,
                    event_type="DEFERRED_TARGET_ACTIVATED",
                    payload={
                        "plan_id": self.active_plan.plan_id,
                        "stop_client_order_id": stop_id,
                        "target_client_order_id": target_id,
                    },
                )
            self.deferred_targets = remaining

        def _submit_emergency_fill_exit(self, quantity, *, fill_time_ns: int) -> None:
            """Flatten a parent fill which raced an already-failed protection."""

            if self.active_plan is None:
                return
            plan = self.active_plan
            instrument = self.instruments[plan.symbol]
            exit_side = OrderSide.SELL if plan.side == "LONG" else OrderSide.BUY
            market = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=exit_side,
                quantity=quantity,
                reduce_only=True,
            )
            market_id = str(market.client_order_id)
            self.active_order_ids.add(market_id)
            self.order_roles[market_id] = "EMERGENCY"
            try:
                self.submit_order(market)
            except Exception as exc:
                self.store.append_event(
                    time_ns=fill_time_ns,
                    event_type="EMERGENCY_FILL_EXIT_SUBMISSION_FAILED",
                    payload={
                        "plan_id": plan.plan_id,
                        "quantity": str(quantity),
                        "reason": repr(exc),
                    },
                )
                return
            self.store.append_event(
                time_ns=fill_time_ns,
                event_type="EMERGENCY_RACE_FILL_EXIT_SUBMITTED",
                payload={"plan_id": plan.plan_id, "quantity": str(quantity)},
            )

        def _claim_active_plan(self, *, time_ns: int, instrument_id) -> bool:
            plan = self.active_plan
            if plan is None:
                self._fail_closed_execution(
                    instrument_id,
                    "ENTRY_EXECUTION_WITHOUT_ACTIVE_PLAN",
                    time_ns,
                    halt_when_flat=True,
                )
                return False
            if plan.plan_id in self._claimed_execution_plan_ids:
                return True
            try:
                self._claim_policy_plan(plan, time_ns=time_ns)
            except (KeyError, ValueError) as exc:
                self._fail_closed_execution(
                    instrument_id,
                    f"EPISODE_CLAIM_FAILED:{exc}",
                    time_ns,
                    halt_when_flat=True,
                )
                return False
            self._claimed_execution_plan_ids.add(plan.plan_id)
            self.store.append_event(
                time_ns=time_ns,
                event_type="EPISODE_CLAIMED",
                payload={
                    "plan_id": plan.plan_id,
                    "episode_id": plan.episode_id,
                    "symbol": plan.symbol,
                },
            )
            if self.config.execution_mode in {"SHADOW", "SANDBOX"}:
                self.sandbox_native_account_mutated = True
            self._checkpoint(time_ns)
            return True

        def _queue_conservative_stop_adjustment(self, event, desired_raw: str) -> None:
            """Debit a favorable ambiguous exit down to the declared stop fill."""

            module = getattr(self, "native_bar_context_module", None)
            if module is None:
                raise RuntimeError("native conservative stop adjustment module is unavailable")
            plan = self.active_plan
            if plan is None:
                raise RuntimeError("conservative stop adjustment has no active plan")
            instrument = self.instruments[plan.symbol]
            actual = event.last_px.as_decimal()
            desired = Decimal(str(desired_raw))
            quantity = event.last_qty.as_decimal()
            multiplier = instrument.multiplier.as_decimal()
            direction = Decimal(1) if plan.side == "LONG" else Decimal(-1)
            price_delta = direction * (desired - actual) * quantity * multiplier
            fee_rate = Decimal(str(instrument.taker_fee))
            actual_fee = actual * quantity * multiplier * fee_rate
            desired_fee = desired * quantity * multiplier * fee_rate
            cash_delta = min(price_delta + actual_fee - desired_fee, Decimal(0))
            position_id = getattr(event, "position_id", None)
            if position_id is None:
                position = self.cache.position_for_order(event.client_order_id)
                if position is None:
                    raise RuntimeError("conservative stop fill has no native position")
                position_id = position.id
            module.queue_conservative_stop_adjustment(
                position_id=position_id,
                instrument_id=event.instrument_id,
                ts_event=int(event.ts_event),
                actual_exit_price=actual,
                conservative_exit_price=desired,
                quantity=quantity,
                cash_delta=cash_delta,
            )
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="CONSERVATIVE_STOP_ADJUSTMENT_QUEUED",
                payload={
                    "plan_id": plan.plan_id,
                    "client_order_id": str(event.client_order_id),
                    "actual_exit_price": str(actual),
                    "conservative_exit_price": str(desired),
                    "quantity": str(quantity),
                    "cash_delta": str(cash_delta),
                },
            )

        def on_order_filled(self, event) -> None:
            order_id = str(event.client_order_id)
            role = self.order_roles.get(order_id)
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="ORDER_FILLED",
                payload={
                    "client_order_id": str(event.client_order_id),
                    "instrument_id": str(event.instrument_id),
                    "last_qty": str(event.last_qty),
                    "last_px": str(event.last_px),
                },
            )
            if role == "ENTRY":
                entry_order = self.cache.order(event.client_order_id)
                immediate_response = (
                    self.active_sizing.get("entry_mode")
                    == "IMMEDIATE_RESPONSE_BOUNDED_IOC"
                )
                if immediate_response:
                    bound = Decimal(
                        str(
                            self.active_sizing.get(
                                "execution_limit_price",
                                # Restart compatibility for snapshots written
                                # before planned entry and IOC bound were split.
                                self.active_sizing.get("entry_price"),
                            ),
                        ),
                    )
                    actual = event.last_px.as_decimal()
                    plan = self.active_plan
                    outside_bound = plan is None or (
                        plan.side == "LONG" and actual > bound
                    ) or (
                        plan.side == "SHORT" and actual < bound
                    )
                    if outside_bound:
                        if plan is not None:
                            self._terminal_reject_plan(
                                plan,
                                reason="IMMEDIATE_RESPONSE_PRICE_BOUND_BREACH",
                                event_type="EXECUTION_PRICE_BOUND_BREACH",
                                details={"actual": str(actual), "bound": str(bound)},
                                time_ns=int(event.ts_event),
                            )
                        self.entry_filled_quantity += event.last_qty.as_decimal()
                        self._fail_closed_execution(
                            event.instrument_id,
                            "IMMEDIATE_RESPONSE_PRICE_BOUND_BREACH",
                            int(event.ts_event),
                            halt_when_flat=True,
                        )
                        return
                    if not self._claim_active_plan(
                        time_ns=int(event.ts_event),
                        instrument_id=event.instrument_id,
                    ):
                        return
                first_fill = self.entry_filled_quantity == 0
                self.entry_filled_quantity += event.last_qty.as_decimal()
                if first_fill and entry_order is not None and entry_order.leaves_qty.as_double() > 0.0:
                    self.cancel_order(entry_order)
                # The cancel acknowledgement can race with more executions.
                # Protect every actual chunk instead of halting or pretending
                # the later fill did not occur.
                if self.emergency_flatten_pending:
                    self._submit_emergency_fill_exit(event.last_qty, fill_time_ns=int(event.ts_event))
                else:
                    self._submit_protection(event.last_qty, fill_time_ns=int(event.ts_event))
            elif role in {"STOP", "TARGET", "EMERGENCY"}:
                conservative_stop = self._conservative_stop_exits.get(order_id)
                if conservative_stop is not None:
                    self._queue_conservative_stop_adjustment(event, conservative_stop)
                exit_order = self.cache.order(event.client_order_id)
                # Nautilus can split one protective order across several
                # executions.  Candidate-10's sibling is canceled only after
                # the protective order is complete; canceling it on the first
                # chunk leaves the remaining net position unprotected.
                if exit_order is not None and exit_order.leaves_qty.as_double() > 0.0:
                    return
                self._conservative_stop_exits.pop(order_id, None)
                self.active_order_ids.discard(order_id)
                sibling_id = self.order_mates.pop(order_id, None)
                if sibling_id is not None:
                    self.order_mates.pop(sibling_id, None)
                    sibling = self.cache.order(NT["ClientOrderId"](sibling_id))
                    if sibling is not None and sibling.is_open:
                        self.cancel_order(sibling)
                    self.order_roles.pop(sibling_id, None)
                self.order_roles.pop(order_id, None)

        def on_order_accepted(self, event) -> None:
            """Claim the causal episode only after the execution account accepts it."""

            order_id = str(event.client_order_id)
            role = self.order_roles.get(order_id)
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="ORDER_ACCEPTED",
                payload={
                    "client_order_id": order_id,
                    "instrument_id": str(event.instrument_id),
                    "role": role or "UNKNOWN",
                },
            )
            if role != "ENTRY":
                return
            if (
                self.active_sizing.get("entry_mode")
                == "IMMEDIATE_RESPONSE_BOUNDED_IOC"
            ):
                # An accepted IOC is not an executed episode.  Claim only on
                # its first bounded fill; an unfilled IOC is terminally missed.
                return
            self._claim_active_plan(
                time_ns=int(event.ts_event),
                instrument_id=event.instrument_id,
            )

        def _detach_failed_order(self, order_id: str) -> str | None:
            self.active_order_ids.discard(order_id)
            role = self.order_roles.pop(order_id, None)
            sibling_id = self.order_mates.pop(order_id, None)
            if sibling_id is not None:
                self.order_mates.pop(sibling_id, None)
                sibling = self.cache.order(NT["ClientOrderId"](sibling_id))
                if sibling is not None and sibling.is_open:
                    self.cancel_order(sibling)
                self.order_roles.pop(sibling_id, None)
            self.deferred_targets = [
                item for item in self.deferred_targets if str(item.get("stop_id")) != order_id
            ]
            return role

        def _handle_active_order_error(
            self,
            order_id: str,
            instrument_id,
            reason: str,
            time_ns: int,
        ) -> None:
            role = self.order_roles.get(order_id)
            if (
                role == "ENTRY"
                and self.active_plan is not None
                and self.entry_filled_quantity == 0
                and self.portfolio.is_flat(instrument_id)
            ):
                # A broker-side reject/deny is a terminal execution decision,
                # not a proposal which may silently reappear next minute.
                self._terminal_reject_plan(
                    self.active_plan,
                    reason=reason,
                    event_type="PARENT_ORDER_TERMINALLY_REJECTED",
                    time_ns=time_ns,
                )
                self._detach_failed_order(order_id)
                self._finalize_slot_if_flat()
                self._checkpoint(time_ns)
                return
            role = self._detach_failed_order(order_id)
            self._fail_closed_execution(
                instrument_id,
                f"{reason}:{role or 'UNKNOWN'}",
                time_ns,
            )

        def _fail_closed_execution(
            self,
            instrument_id,
            reason: str,
            time_ns: int,
            *,
            halt_when_flat: bool = False,
        ) -> None:
            self.cancel_all_orders(instrument_id)
            if self.portfolio.is_flat(instrument_id):
                if halt_when_flat:
                    self._halted = True
                    self.store.append_event(
                        time_ns=time_ns,
                        event_type="EXECUTION_HALT",
                        payload={"instrument_id": str(instrument_id), "reason": reason},
                    )
                    self._checkpoint(time_ns)
                    return
                # A late reject/deny can arrive after the paired exit already
                # flattened the account.  There is no exposure to emergency-
                # flatten and no justification for permanently halting future
                # independent episodes.
                self.store.append_event(
                    time_ns=time_ns,
                    event_type="ORDER_ERROR_FLAT_RECOVERED",
                    payload={"instrument_id": str(instrument_id), "reason": reason},
                )
                self._finalize_slot_if_flat()
                self._checkpoint(time_ns)
                return
            if self.emergency_flatten_pending:
                return
            self.emergency_flatten_pending = True
            self.emergency_flatten_reason = reason
            self._halted = True
            self.close_all_positions(instrument_id)
            self.store.append_event(
                time_ns=time_ns,
                event_type="EMERGENCY_FLATTEN_SUBMITTED",
                payload={"instrument_id": str(instrument_id), "reason": reason},
            )
            self.store.append_event(
                time_ns=time_ns,
                event_type="EXECUTION_HALT",
                payload={"instrument_id": str(instrument_id), "reason": reason},
            )
            self._checkpoint(time_ns)

        def on_order_rejected(self, event) -> None:
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="ORDER_REJECTED",
                payload={"client_order_id": str(event.client_order_id), "reason": str(event.reason)},
            )
            if str(event.client_order_id) in self.active_order_ids:
                self._handle_active_order_error(
                    str(event.client_order_id),
                    event.instrument_id,
                    f"ORDER_REJECTED:{event.reason}",
                    int(event.ts_event),
                )

        def on_order_denied(self, event) -> None:
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="ORDER_DENIED",
                payload={"client_order_id": str(event.client_order_id), "reason": str(event.reason)},
            )
            if str(event.client_order_id) in self.active_order_ids:
                self._handle_active_order_error(
                    str(event.client_order_id),
                    event.instrument_id,
                    f"ORDER_DENIED:{event.reason}",
                    int(event.ts_event),
                )

        def on_order_canceled(self, event) -> None:
            order_id = str(event.client_order_id)
            role = self.order_roles.get(order_id)
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="ORDER_CANCELED",
                payload={"client_order_id": order_id},
            )
            self.active_order_ids.discard(order_id)
            if (
                role == "ENTRY"
                and self.active_plan is not None
                and self.active_sizing.get("entry_mode")
                == "IMMEDIATE_RESPONSE_BOUNDED_IOC"
                and self.entry_filled_quantity == 0
            ):
                self._terminal_reject_plan(
                    self.active_plan,
                    reason="IMMEDIATE_RESPONSE_NOT_FILLED",
                    event_type="IMMEDIATE_RESPONSE_MISSED",
                    time_ns=int(event.ts_event),
                )
            self._finalize_slot_if_flat()

        def on_order_expired(self, event) -> None:
            self.active_order_ids.discard(str(event.client_order_id))
            self._finalize_slot_if_flat()

        def _finalize_slot_if_flat(self) -> None:
            if self.active_plan is None:
                return
            instrument_id = self.instrument_ids[self.active_plan.symbol]
            open_active = [
                order
                for order in self.cache.orders_open(instrument_id=instrument_id)
                if str(order.client_order_id) in self.active_order_ids
            ]
            if self.portfolio.is_flat(instrument_id) and not open_active:
                self.active_plan = None
                self.active_order_ids.clear()
                self.order_roles.clear()
                self.order_mates.clear()
                self.entry_filled_quantity = Decimal("0")
                self.active_sizing.clear()
                self.deferred_targets.clear()

        def on_position_closed(self, event) -> None:
            self.store.append_event(
                time_ns=int(event.ts_event),
                event_type="POSITION_CLOSED",
                payload={
                    "instrument_id": str(event.instrument_id),
                    "position_id": str(event.position_id),
                    "realized_return": str(getattr(event, "realized_return", "")),
                    "realized_pnl": str(getattr(event, "realized_pnl", "")),
                },
            )
            for order in self.cache.orders_open(instrument_id=event.instrument_id):
                if self.order_roles.get(str(order.client_order_id)) in {"STOP", "TARGET"}:
                    self.cancel_order(order)
            self.emergency_flatten_pending = False
            self.emergency_flatten_reason = None
            self._finalize_slot_if_flat()

        def _checkpoint(self, time_ns: int) -> None:
            policy_exporter = getattr(self.coordinator, "export_runtime_state", None)
            policy_state = (
                policy_exporter()
                if callable(policy_exporter)
                else self.coordinator.export_state()
            )
            payload: dict[str, object] = {
                    "runtime_state_version": 2,
                    "active_plan": None if self.active_plan is None else self.active_plan.to_dict(),
                    "active_order_ids": sorted(self.active_order_ids),
                    "order_roles": dict(self.order_roles),
                    "order_mates": dict(self.order_mates),
                    "entry_filled_quantity": str(self.entry_filled_quantity),
                    "active_sizing": dict(self.active_sizing),
                    "deferred_targets": [dict(item) for item in self.deferred_targets],
                    "emergency_flatten_pending": self.emergency_flatten_pending,
                    "emergency_flatten_reason": self.emergency_flatten_reason,
                    "sandbox_native_account_mutated": self.sandbox_native_account_mutated,
                    "mode": self.config.execution_mode,
                    "halted": self._halted,
                    "funding_state": {
                        symbol: dict(values)
                        for symbol, values in self._funding_state.items()
                    },
                    "policy_state": policy_state,
                }
            # Bars and semantic policy events are durable before this point.
            # Only a materially changed execution/identity image needs a new
            # atomic snapshot; identical per-symbol calls are no-ops.
            if payload == self._last_checkpoint_payload:
                return
            self.store.save_snapshot(
                "strategy_runtime",
                time_ns=time_ns,
                payload=payload,
            )
            self._last_checkpoint_payload = payload

        def on_stop(self) -> None:
            for instrument_id in self.config.instrument_ids:
                self.unsubscribe_bars(self.bar_types[_symbol_from_instrument(str(instrument_id))])
                if self.config.execution_mode != "BACKTEST":
                    self.unsubscribe_data(
                        data_type=NT["DataType"](
                            NT["BinanceFuturesMarkPriceUpdate"],
                            metadata={"instrument_id": instrument_id},
                        ),
                        client_id=NT["ClientId"](str(NT["BINANCE"])),
                    )
            # Do not overwrite the prior executable snapshot with the fresh,
            # empty sandbox cache which caused this restart to be rejected.
            if not self._restart_blocked:
                self._checkpoint(self.clock.timestamp_ns())
            self.store.append_event(
                time_ns=self.clock.timestamp_ns(),
                event_type="STRATEGY_STOPPED",
                payload={"mode": self.config.execution_mode},
            )
            self.store.close()

        def on_save(self) -> dict[str, bytes]:
            policy_exporter = getattr(self.coordinator, "export_runtime_state", None)
            payload = {
                "runtime_state_version": 2,
                "active_plan": None if self.active_plan is None else self.active_plan.to_dict(),
                "active_order_ids": sorted(self.active_order_ids),
                "order_roles": self.order_roles,
                "order_mates": self.order_mates,
                "entry_filled_quantity": str(self.entry_filled_quantity),
                "active_sizing": self.active_sizing,
                "deferred_targets": self.deferred_targets,
                "emergency_flatten_pending": self.emergency_flatten_pending,
                "emergency_flatten_reason": self.emergency_flatten_reason,
                "sandbox_native_account_mutated": self.sandbox_native_account_mutated,
                "halted": self._halted,
                "funding_state": self._funding_state,
                "policy_state": (
                    policy_exporter()
                    if callable(policy_exporter)
                    else self.coordinator.export_state()
                ),
            }
            return {"episode_policy_runtime": json.dumps(payload, sort_keys=True).encode("utf-8")}

        def on_load(self, state: dict[str, bytes]) -> None:
            raw = state.get("episode_policy_runtime")
            if raw is None:
                return
            payload = json.loads(raw)
            if payload.get("active_plan") is not None:
                self.active_plan = TradePlan.from_dict(payload["active_plan"])
            self.active_order_ids = set(payload.get("active_order_ids", []))
            self.order_roles = dict(payload.get("order_roles", {}))
            self.order_mates = dict(payload.get("order_mates", {}))
            self.entry_filled_quantity = Decimal(str(payload.get("entry_filled_quantity", "0")))
            self.active_sizing = dict(payload.get("active_sizing", {}))
            self.deferred_targets = list(payload.get("deferred_targets", []))
            self.emergency_flatten_pending = bool(payload.get("emergency_flatten_pending", False))
            self.emergency_flatten_reason = (
                str(payload["emergency_flatten_reason"])
                if payload.get("emergency_flatten_reason")
                else None
            )
            self.sandbox_native_account_mutated = (
                self.sandbox_native_account_mutated
                or bool(payload.get("sandbox_native_account_mutated", False))
            )
            restart_payload = dict(payload)
            restart_payload["sandbox_native_account_mutated"] = self.sandbox_native_account_mutated
            self._restart_block_reason = native_restart_block_reason(
                self.config.execution_mode,
                restart_payload,
            )
            self._halted = bool(payload.get("halted", False))
            self._funding_state = dict(payload.get("funding_state", {}))
            if isinstance(payload.get("policy_state"), Mapping):
                # Apply after completed-bar replay in on_start.  The overlay is
                # monotonic and idempotent, so an SQLite snapshot and a
                # Nautilus state snapshot can safely carry the same state.
                self._restored_policy_state = payload["policy_state"]


def build_node(
    *,
    execution_mode: str,
    state_path: Path,
    initial_nav: float = 100_000.0,
    bootstrap_lookback_minutes: int = DEFAULT_WARMUP_MINUTES,
    live_inventory_poll_seconds: float = 15.0,
):
    if NT is None:
        raise RuntimeError("nautilus_trader is not installed")
    BINANCE = NT["BINANCE"]
    instrument_ids = tuple(
        NT["InstrumentId"].from_str(f"{symbol}-PERP.{BINANCE}") for symbol in SYMBOLS
    )
    instrument_provider = NT["BinanceInstrumentProviderConfig"](
        load_ids=frozenset(instrument_ids),
        query_commission_rates=True,
    )
    data_environment = (
        NT["BinanceEnvironment"].TESTNET
        if execution_mode == "TESTNET"
        else NT["BinanceEnvironment"].LIVE
    )
    data_config = NT["BinanceDataClientConfig"](
        account_type=NT["BinanceAccountType"].USDT_FUTURES,
        environment=data_environment,
        instrument_provider=instrument_provider,
        # The policy consumes exchange-completed kline BinanceBar objects.
        # Never substitute aggregate trade ticks for this market-data stream.
        use_agg_trade_ticks=False,
        handle_revised_bars=False,
    )
    exec_clients: dict[str, Any]
    exec_factory: Any
    if execution_mode == "TESTNET":
        if not os.environ.get("BINANCE_API_KEY") or not os.environ.get("BINANCE_API_SECRET"):
            raise RuntimeError("TESTNET requires BINANCE_API_KEY and BINANCE_API_SECRET")
        exec_clients = {
            BINANCE: NT["BinanceExecClientConfig"](
                account_type=NT["BinanceAccountType"].USDT_FUTURES,
                environment=NT["BinanceEnvironment"].TESTNET,
                instrument_provider=instrument_provider,
                max_retries=3,
            )
        }
        exec_factory = NT["BinanceLiveExecClientFactory"]
    else:
        exec_clients = {
            BINANCE: NT["SandboxExecutionClientConfig"](
                venue=str(BINANCE),
                account_type="MARGIN",
                starting_balances=[f"{initial_nav:.2f} USDT"],
                default_leverage=Decimal(20),
            )
        }
        exec_factory = NT["SandboxLiveExecClientFactory"]
    config_node = NT["TradingNodeConfig"](
        trader_id=NT["TraderId"]("LEP-001"),
        logging=NT["LoggingConfig"](log_level="INFO", use_pyo3=True),
        data_engine=NT["LiveDataEngineConfig"](external_clients=[NT["ClientId"](str(BINANCE))]),
        exec_engine=NT["LiveExecEngineConfig"](
            reconciliation=True,
            reconciliation_lookback_mins=1440,
            filter_position_reports=True,
            snapshot_orders=True,
            snapshot_positions=True,
            snapshot_positions_interval_secs=5,
            graceful_shutdown_on_exception=True,
        ),
        # DatabaseConfig in pinned Nautilus 1.230 supports Redis only.  The
        # Windows one-click runtime intentionally has no external Redis, and a
        # cache snapshot would not restore SandboxExecutionClient matching
        # engines/account state in any case.
        cache=NT["CacheConfig"](
            database=None,
            timestamps_as_iso8601=True,
            flush_on_start=False,
        ),
        data_clients={BINANCE: data_config},
        exec_clients=exec_clients,
        timeout_connection=30.0,
        timeout_reconciliation=20.0,
        timeout_portfolio=20.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )
    node = NT["TradingNode"](config=config_node)
    strategy = LiquidityEpisodeStrategy(
        LiquidityEpisodeStrategyConfig(
            instrument_ids=instrument_ids,
            state_path=str(state_path),
            execution_mode=execution_mode,
            initial_nav=initial_nav,
            bootstrap_lookback_minutes=bootstrap_lookback_minutes,
            live_inventory_poll_seconds=live_inventory_poll_seconds,
            external_order_claims=list(instrument_ids),
        )
    )
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(BINANCE, NT["BinanceLiveDataClientFactory"])
    node.add_exec_client_factory(BINANCE, exec_factory)
    node.build()
    # Runtime orchestration uses this explicit reference for public inventory
    # polling.  BACKTEST construction never attaches a collector or performs
    # network I/O.
    node._episode_policy_strategy = strategy
    return node


def _prepare_runtime_node(
    *,
    execution_mode: str,
    state_path: Path,
    initial_nav: float = 100_000.0,
    bootstrap: bool = True,
    bootstrap_lookback_minutes: int = DEFAULT_WARMUP_MINUTES,
    live_inventory_poll_seconds: float = 15.0,
):
    """Run restart/bootstrap checks and build one caller-owned live node."""

    with StateStore(state_path) as store:
        runtime = store.load_snapshot("strategy_runtime")
        restart_reason = native_restart_block_reason(execution_mode, runtime)
        if restart_reason is not None:
            store.append_event(
                time_ns=time.time_ns(),
                event_type="NATIVE_RESTART_PREFLIGHT_BLOCKED",
                payload={
                    "mode": execution_mode,
                    "reason": restart_reason,
                    "capabilities": native_restart_capabilities(),
                },
            )
            raise RuntimeError(
                f"{execution_mode} restart refused: {restart_reason}. "
                "SQLite preserves policy evidence but cannot restore the "
                "process-local Nautilus sandbox account. Use a new state file "
                "or TESTNET exchange reconciliation."
            )
    if bootstrap:
        with StateStore(state_path) as store:
            bootstrap_store(store, limit=bootstrap_lookback_minutes)
    # Seed metrics before the native node starts so the first connected policy
    # decision uses exactly the replay InventoryTimeline semantics.  Endpoint
    # failures remain explicit UNKNOWN assignments rather than aborting or
    # retaining stale data.
    collector = LiveInventoryCollector()
    inventory_seed = collector.poll_all()
    node = build_node(
        execution_mode=execution_mode,
        state_path=state_path,
        initial_nav=initial_nav,
        bootstrap_lookback_minutes=bootstrap_lookback_minutes,
        live_inventory_poll_seconds=live_inventory_poll_seconds,
    )
    strategy = node._episode_policy_strategy
    strategy.live_inventory_collector = collector
    apply_live_inventory_results(strategy, inventory_seed)
    return node


async def _poll_live_inventory(strategy: object) -> None:
    """Fetch off-loop and publish immutable results back on the node loop."""

    collector = getattr(strategy, "live_inventory_collector", None)
    if collector is None:
        return
    interval = float(strategy.config.live_inventory_poll_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            results = await asyncio.to_thread(collector.poll_all)
            apply_live_inventory_results(strategy, results)
        except InventoryMetricConflictError:
            # Provider mutation is a data-integrity failure, unlike temporary
            # endpoint unavailability which is represented inside results.
            strategy._halted = True
            strategy.stop()
            raise


async def _run_prepared_node(node, duration_seconds: int | None) -> None:
    """Drive a prepared node and its non-blocking public inventory poller."""

    node_task = asyncio.create_task(node.run_async())
    strategy = getattr(node, "_episode_policy_strategy", None)
    inventory_task = (
        asyncio.create_task(_poll_live_inventory(strategy))
        if strategy is not None
        and getattr(strategy, "live_inventory_collector", None) is not None
        else None
    )
    async def connected_duration() -> None:
        # A seven-day causal replay can take materially longer than a bounded
        # smoke duration.  Count the requested duration only after Nautilus is
        # actually RUNNING; otherwise a nominal connected smoke can expire
        # entirely inside synchronous strategy startup without observing the
        # public stream at all.
        running = getattr(node, "is_running", None)
        if callable(running):
            while not running():
                await asyncio.sleep(0.05)
        await asyncio.sleep(duration_seconds)

    timer_task = (
        asyncio.create_task(connected_duration())
        if duration_seconds is not None
        else None
    )
    try:
        watched = {node_task}
        if inventory_task is not None:
            watched.add(inventory_task)
        if timer_task is not None:
            watched.add(timer_task)
        done, _pending = await asyncio.wait(
            watched,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if inventory_task is not None and inventory_task in done:
            failure = inventory_task.exception()
            if failure is not None:
                await node.stop_async()
                await node_task
                raise failure
        if timer_task is not None and timer_task in done:
            await node.stop_async()
        await node_task
    finally:
        if timer_task is not None and not timer_task.done():
            timer_task.cancel()
            with suppress(asyncio.CancelledError):
                await timer_task
        if inventory_task is not None and not inventory_task.done():
            inventory_task.cancel()
            with suppress(asyncio.CancelledError):
                await inventory_task
        if not node_task.done():
            with suppress(Exception):
                await node.stop_async()
                await node_task


async def run_node(
    *,
    execution_mode: str,
    state_path: Path,
    duration_seconds: int | None,
    initial_nav: float = 100_000.0,
    bootstrap: bool = True,
    bootstrap_lookback_minutes: int = DEFAULT_WARMUP_MINUTES,
    live_inventory_poll_seconds: float = 15.0,
):
    """Run under an existing event loop and return the stopped caller-owned node.

    The caller must dispose the returned node only after its event loop has
    stopped.  Calling ``TradingNode.dispose`` from inside ``asyncio.run`` stops
    asyncio's running loop and converts an otherwise successful bounded run
    into a non-zero lifecycle failure.
    """

    node = _prepare_runtime_node(
        execution_mode=execution_mode,
        state_path=state_path,
        initial_nav=initial_nav,
        bootstrap=bootstrap,
        bootstrap_lookback_minutes=bootstrap_lookback_minutes,
        live_inventory_poll_seconds=live_inventory_poll_seconds,
    )
    await _run_prepared_node(node, duration_seconds)
    return node


def run_node_blocking(
    *,
    execution_mode: str,
    state_path: Path,
    duration_seconds: int | None,
    initial_nav: float = 100_000.0,
    bootstrap: bool = True,
    bootstrap_lookback_minutes: int = DEFAULT_WARMUP_MINUTES,
    live_inventory_poll_seconds: float = 15.0,
) -> None:
    """Own the native loop so disposal occurs only after it is not running."""

    node = _prepare_runtime_node(
        execution_mode=execution_mode,
        state_path=state_path,
        initial_nav=initial_nav,
        bootstrap=bootstrap,
        bootstrap_lookback_minutes=bootstrap_lookback_minutes,
        live_inventory_poll_seconds=live_inventory_poll_seconds,
    )
    loop = node.kernel.loop
    completed = False
    try:
        loop.run_until_complete(_run_prepared_node(node, duration_seconds))
        completed = True
    finally:
        if not completed and not loop.is_closed():
            try:
                loop.run_until_complete(node.stop_async())
            except Exception:
                pass
        node.dispose()
