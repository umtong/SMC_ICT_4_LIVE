"""Native multi-instrument NautilusTrader runner for the episode policy.

The runner deliberately contains no execution/account simulator of its own.
Orders, margin, fees, fills, positions and account balances all come from the
pinned NautilusTrader 1.230.0 ``BacktestEngine``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, MakerTakerFeeModel
from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import Bar as NautilusBar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, PositionAdjustmentType
from nautilus_trader.model.events import PositionAdjusted
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Currency, Money, Price, Quantity

from .domain import Bar as PolicyBar
from .domain import DEFAULT_CONTRACTS, SYMBOLS
from .live import LiquidityEpisodeStrategy, LiquidityEpisodeStrategyConfig
from .nautilus_data import PolicyFlowRecord, SynchronizedMinute
from .nautilus_funding import HistoricalFundingPayment, PerpetualFundingModule, funding_config


VENUE = Venue("BINANCE")
USDT = Currency.from_str("USDT")


class CurrentBarContextConfig(SimulationModuleConfig, frozen=True):
    """Configuration for native pre-match bar context."""


@dataclass(frozen=True, slots=True)
class AppliedConservativeStopAdjustment:
    """Native account debit enforcing an adverse fill-bar stop convention."""

    position_id: str
    instrument_id: str
    ts_event: int
    actual_exit_price: Decimal
    conservative_exit_price: Decimal
    quantity: Decimal
    cash_delta: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class _PendingConservativeStopAdjustment:
    position_id: Any
    instrument_id: InstrumentId
    ts_event: int
    actual_exit_price: Decimal
    conservative_exit_price: Decimal
    quantity: Decimal
    cash_delta: Decimal


class CurrentBarContextModule(SimulationModule):
    """Expose pre-match bars and enforce the explicit adverse-first convention.

    Strategy callbacks receive order fills before ``Strategy.on_bar`` for the
    same bar.  The exchange simulation-module hook is the native place to make
    that already-arrived OHLC visible for conservative fill-bar protection.
    It also cancels favorable targets before matching a later bar which touches
    both exits, and settles any favorable fill-bar market-exit difference as a
    native ``PositionAdjusted`` account debit.
    """

    def __init__(self) -> None:
        super().__init__(CurrentBarContextConfig())
        self.current: dict[str, NautilusBar] = {}
        self.strategy: Any | None = None
        self._pending_adjustments: list[_PendingConservativeStopAdjustment] = []
        self._applied_adjustments: list[AppliedConservativeStopAdjustment] = []
        self.adverse_first_target_cancels = 0

    @property
    def applied_adjustments(self) -> tuple[AppliedConservativeStopAdjustment, ...]:
        return tuple(self._applied_adjustments)

    def queue_conservative_stop_adjustment(
        self,
        *,
        position_id: Any,
        instrument_id: InstrumentId,
        ts_event: int,
        actual_exit_price: Decimal,
        conservative_exit_price: Decimal,
        quantity: Decimal,
        cash_delta: Decimal,
    ) -> None:
        """Queue only a debit; a worse native fill is never credited back."""

        if cash_delta >= 0:
            return
        self._pending_adjustments.append(
            _PendingConservativeStopAdjustment(
                position_id=position_id,
                instrument_id=instrument_id,
                ts_event=ts_event,
                actual_exit_price=actual_exit_price,
                conservative_exit_price=conservative_exit_price,
                quantity=quantity,
                cash_delta=cash_delta,
            ),
        )

    def pre_process(self, data) -> None:
        if isinstance(data, NautilusBar):
            self.current[str(data.bar_type.instrument_id)] = data
            self._cancel_favorable_targets_on_ambiguous_bar(data)

    def _cancel_favorable_targets_on_ambiguous_bar(self, bar: NautilusBar) -> None:
        strategy = self.strategy
        plan = None if strategy is None else getattr(strategy, "active_plan", None)
        if plan is None or str(bar.bar_type.instrument_id) != str(
            strategy.instrument_ids[plan.symbol],
        ):
            return
        if not self.exchange.cache.positions_open(instrument_id=bar.bar_type.instrument_id):
            return
        sizing = getattr(strategy, "active_sizing", {})
        stop_raw = sizing.get("stop_trigger_price")
        target_raw = sizing.get("target_price")
        if stop_raw is None or target_raw is None:
            return
        stop = Decimal(str(stop_raw))
        target = Decimal(str(target_raw))
        low = Decimal(str(bar.low))
        high = Decimal(str(bar.high))
        if not (low <= stop <= high and low <= target <= high):
            return
        matching = self.exchange.get_matching_engine(bar.bar_type.instrument_id)
        for order in tuple(matching.get_open_orders()):
            order_id = str(order.client_order_id)
            if strategy.order_roles.get(order_id) != "TARGET" or not order.is_open:
                continue
            matching.cancel_order(order)
            self.adverse_first_target_cancels += 1

    def process(self, ts_now: int) -> None:
        pending = self._pending_adjustments
        self._pending_adjustments = []
        for item in pending:
            position = self.exchange.cache.position(item.position_id)
            if position is None:
                raise RuntimeError(
                    f"conservative stop position is unavailable: {item.position_id}",
                )
            pnl_change = Money.from_decimal(item.cash_delta, position.settlement_currency)
            adjustment = PositionAdjusted(
                trader_id=position.trader_id,
                strategy_id=position.strategy_id,
                instrument_id=position.instrument_id,
                position_id=position.id,
                account_id=position.account_id,
                # Nautilus 1.230 exposes COMMISSION and FUNDING only.  This is
                # an execution-cost debit, so COMMISSION is the honest native
                # category; the reason preserves its exact ambiguity origin.
                adjustment_type=PositionAdjustmentType.COMMISSION,
                quantity_change=None,
                pnl_change=pnl_change,
                reason=(
                    "CONSERVATIVE_FILL_BAR_STOP "
                    f"actual={item.actual_exit_price} "
                    f"adverse={item.conservative_exit_price}"
                ),
                event_id=UUID4(),
                ts_event=item.ts_event,
                ts_init=item.ts_event,
            )
            position.apply_adjustment(adjustment)
            self.exchange.cache.update_position(position)
            self.exchange.adjust_account(pnl_change)
            self._applied_adjustments.append(
                AppliedConservativeStopAdjustment(
                    position_id=str(position.id),
                    instrument_id=str(item.instrument_id),
                    ts_event=item.ts_event,
                    actual_exit_price=item.actual_exit_price,
                    conservative_exit_price=item.conservative_exit_price,
                    quantity=item.quantity,
                    cash_delta=item.cash_delta,
                    currency=str(position.settlement_currency),
                ),
            )

    def log_diagnostics(self, logger) -> None:
        return None

    def reset(self) -> None:
        self.current.clear()
        self._pending_adjustments.clear()
        self._applied_adjustments.clear()
        self.adverse_first_target_cancels = 0


def _precision(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def make_binance_perpetuals() -> dict[str, CryptoPerpetual]:
    """Build the four USD-M contracts used by both policy and native engine."""
    instruments: dict[str, CryptoPerpetual] = {}
    for symbol in SYMBOLS:
        contract = DEFAULT_CONTRACTS[symbol]
        base = Currency.from_str(symbol.removesuffix("USDT"))
        instruments[symbol] = CryptoPerpetual(
            instrument_id=InstrumentId(Symbol(f"{symbol}-PERP"), VENUE),
            raw_symbol=Symbol(symbol),
            base_currency=base,
            quote_currency=USDT,
            settlement_currency=USDT,
            is_inverse=False,
            price_precision=_precision(contract.tick_size),
            size_precision=_precision(contract.quantity_step),
            price_increment=Price.from_str(str(contract.tick_size)),
            size_increment=Quantity.from_str(str(contract.quantity_step)),
            min_quantity=Quantity.from_str(str(contract.min_quantity)),
            min_notional=Money(contract.min_notional, USDT),
            margin_init=Decimal("0.05"),
            margin_maint=Decimal("0.025"),
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0005"),
            ts_event=0,
            ts_init=0,
        )
    return instruments


def external_bar_types(
    instruments: Mapping[str, CryptoPerpetual],
) -> dict[str, BarType]:
    return {
        symbol: BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        for symbol, instrument in instruments.items()
    }


def _instrument_map(
    instruments: Mapping[str, CryptoPerpetual] | Iterable[CryptoPerpetual] | None,
) -> dict[str, CryptoPerpetual]:
    if instruments is None:
        return make_binance_perpetuals()
    if isinstance(instruments, Mapping):
        return dict(instruments)
    return {
        str(instrument.id).split(".", 1)[0].removesuffix("-PERP"): instrument
        for instrument in instruments
    }


def to_nautilus_bar(
    bar: PolicyBar,
    instrument: CryptoPerpetual,
    bar_type: BarType,
) -> NautilusBar:
    if bar.interval_minutes != 1:
        raise ValueError("native episode-policy runner accepts completed 1-minute bars only")
    return NautilusBar(
        bar_type=bar_type,
        open=instrument.make_price(bar.open),
        high=instrument.make_price(bar.high),
        low=instrument.make_price(bar.low),
        close=instrument.make_price(bar.close),
        volume=instrument.make_qty(bar.volume),
        ts_event=bar.close_time_ns,
        ts_init=bar.close_time_ns,
    )


@dataclass(slots=True)
class NativeBacktestSession:
    engine: BacktestEngine
    strategy: LiquidityEpisodeStrategy
    instruments: dict[str, CryptoPerpetual]
    bar_types: dict[str, BarType]
    funding_module: PerpetualFundingModule | None = None
    bar_context_module: CurrentBarContextModule | None = None
    streaming: bool = False

    def run(self) -> None:
        self.engine.run(streaming=self.streaming)
        if self.streaming:
            self.engine.end()

    def dispose(self) -> None:
        self.engine.dispose()


@dataclass(frozen=True, slots=True)
class NativeBacktestResult:
    fills: Any
    positions: Any
    account: Any
    final_balance: float
    final_nav: float
    parent_orders_submitted: int
    protective_pairs_submitted: int
    plans_blocked_by_global_slot: int
    max_active_instruments: int
    missing_flow_bars: int
    funding_payments_applied: int
    funding_totals: Mapping[str, Decimal]
    conservative_stop_adjustments: tuple[AppliedConservativeStopAdjustment, ...]
    adverse_first_target_cancels: int


def _flow_from_policy_bar(bar: PolicyBar, instrument_id: InstrumentId) -> PolicyFlowRecord:
    return PolicyFlowRecord(
        symbol=bar.symbol,
        instrument_id=str(instrument_id),
        open_time_ns=bar.open_time_ns,
        source_close_time_ns=bar.close_time_ns,
        ts_event=bar.close_time_ns,
        quote_volume=bar.quote_volume,
        taker_buy_volume=bar.taker_buy_quote_volume / max(bar.close, 1e-12),
        taker_buy_quote_volume=bar.taker_buy_quote_volume,
        trade_count=bar.trade_count,
    )


def build_native_backtest(
    bars: Iterable[PolicyBar | NautilusBar],
    *,
    state_path: str | Path,
    instruments: Mapping[str, CryptoPerpetual] | Iterable[CryptoPerpetual] | None = None,
    initial_nav: float = 100_000.0,
    log_level: str = "ERROR",
    flow_records: Iterable[PolicyFlowRecord] | None = None,
    funding_payments: Iterable[HistoricalFundingPayment] | None = None,
    execution_start_ns: int = 0,
    execution_end_ns: int | None = None,
    reject_stop_orders: bool = False,
) -> NativeBacktestSession:
    """Create, wire and load one MARGIN/NETTING venue without running it."""
    instrument_map = _instrument_map(instruments)
    if set(instrument_map) != set(SYMBOLS):
        raise ValueError("exactly the four policy instruments are required")
    bar_types = external_bar_types(instrument_map)
    native_bars: list[NautilusBar] = []
    flows: list[PolicyFlowRecord] = list(flow_records or ())
    for item in bars:
        if isinstance(item, PolicyBar):
            native_bars.append(to_nautilus_bar(item, instrument_map[item.symbol], bar_types[item.symbol]))
            flows.append(_flow_from_policy_bar(item, instrument_map[item.symbol].id))
        elif isinstance(item, NautilusBar):
            native_bars.append(item)
        else:
            raise TypeError(f"unsupported bar type: {type(item)!r}")
    if not native_bars:
        raise ValueError("at least one bar is required")

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("LIQUIDITY-EPISODE-001"),
            logging=LoggingConfig(log_level=log_level, use_pyo3=False, bypass_logging=True),
            risk_engine=RiskEngineConfig(bypass=False),
        )
    )
    funding_module = (
        PerpetualFundingModule(funding_config(funding_payments))
        if funding_payments is not None
        else None
    )
    bar_context = CurrentBarContextModule()
    modules = [bar_context]
    if funding_module is not None:
        modules.append(funding_module)
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(initial_nav, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("20"),
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=1.0, random_seed=73),
        fee_model=MakerTakerFeeModel(),
        reject_stop_orders=reject_stop_orders,
        support_gtd_orders=True,
        # Children are separate reduce-only orders managed by the strategy, not
        # an unsupported Binance Futures submitted bracket/contingency list.
        support_contingent_orders=False,
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
        use_reduce_only=True,
        modules=modules,
    )
    for instrument in instrument_map.values():
        engine.add_instrument(instrument)
    engine.add_data(native_bars, sort=True)
    strategy = LiquidityEpisodeStrategy(
        LiquidityEpisodeStrategyConfig(
            instrument_ids=tuple(item.id for item in instrument_map.values()),
            bar_types=tuple(bar_types.values()),
            state_path=str(state_path),
            execution_mode="BACKTEST",
            initial_nav=initial_nav,
            execution_start_ns=execution_start_ns,
            execution_end_ns=execution_end_ns,
        )
    )
    strategy.register_policy_flow(flows)
    strategy.native_bar_context = bar_context.current
    strategy.native_bar_context_module = bar_context
    bar_context.strategy = strategy
    engine.add_strategy(strategy)
    return NativeBacktestSession(
        engine,
        strategy,
        instrument_map,
        bar_types,
        funding_module=funding_module,
        bar_context_module=bar_context,
    )


def build_streaming_native_backtest(
    minutes: Iterable[SynchronizedMinute],
    *,
    state_path: str | Path,
    instruments: Mapping[str, CryptoPerpetual] | Iterable[CryptoPerpetual] | None = None,
    initial_nav: float = 100_000.0,
    chunk_minutes: int = 10_000,
    log_level: str = "ERROR",
    funding_payments: Iterable[HistoricalFundingPayment] | None = None,
    execution_start_ns: int = 0,
    execution_end_ns: int | None = None,
    reject_stop_orders: bool = False,
) -> NativeBacktestSession:
    """Wire a bounded-memory native run directly from the synchronized loader."""
    if chunk_minutes <= 0:
        raise ValueError("chunk_minutes must be positive")
    instrument_map = _instrument_map(instruments)
    if set(instrument_map) != set(SYMBOLS):
        raise ValueError("exactly the four policy instruments are required")
    bar_types = external_bar_types(instrument_map)
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("LIQUIDITY-EPISODE-STREAM-001"),
            logging=LoggingConfig(log_level=log_level, use_pyo3=False, bypass_logging=True),
            risk_engine=RiskEngineConfig(bypass=False),
        )
    )
    funding_module = (
        PerpetualFundingModule(funding_config(funding_payments))
        if funding_payments is not None
        else None
    )
    bar_context = CurrentBarContextModule()
    modules = [bar_context]
    if funding_module is not None:
        modules.append(funding_module)
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(initial_nav, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("20"),
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=1.0, random_seed=73),
        fee_model=MakerTakerFeeModel(),
        reject_stop_orders=reject_stop_orders,
        support_gtd_orders=True,
        support_contingent_orders=False,
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
        use_reduce_only=True,
        modules=modules,
    )
    for instrument in instrument_map.values():
        engine.add_instrument(instrument)
    strategy = LiquidityEpisodeStrategy(
        LiquidityEpisodeStrategyConfig(
            instrument_ids=tuple(item.id for item in instrument_map.values()),
            bar_types=tuple(bar_types.values()),
            state_path=str(state_path),
            execution_mode="BACKTEST",
            initial_nav=initial_nav,
            execution_start_ns=execution_start_ns,
            execution_end_ns=execution_end_ns,
        )
    )
    strategy.native_bar_context = bar_context.current
    strategy.native_bar_context_module = bar_context
    bar_context.strategy = strategy

    def chunks():
        pending: list[NautilusBar] = []
        for minute in minutes:
            if set(minute.bars) != set(SYMBOLS) or set(minute.flows) != set(SYMBOLS):
                raise ValueError("stream contains a partial four-market minute")
            strategy.register_policy_flow(minute.flows.values())
            pending.extend(minute.bars[symbol] for symbol in SYMBOLS)
            if len(pending) >= chunk_minutes * len(SYMBOLS):
                yield pending
                pending = []
        if pending:
            yield pending

    engine.add_data_iterator("BINANCE-USDM-1M", chunks())
    engine.add_strategy(strategy)
    return NativeBacktestSession(
        engine,
        strategy,
        instrument_map,
        bar_types,
        funding_module=funding_module,
        bar_context_module=bar_context,
        streaming=True,
    )


def run_native_backtest(
    bars: Iterable[PolicyBar | NautilusBar],
    *,
    state_path: str | Path,
    instruments: Mapping[str, CryptoPerpetual] | Iterable[CryptoPerpetual] | None = None,
    initial_nav: float = 100_000.0,
    configure_strategy: Callable[[LiquidityEpisodeStrategy], None] | None = None,
    flow_records: Iterable[PolicyFlowRecord] | None = None,
    funding_payments: Iterable[HistoricalFundingPayment] | None = None,
    execution_start_ns: int = 0,
    execution_end_ns: int | None = None,
    reject_stop_orders: bool = False,
) -> NativeBacktestResult:
    """Run the native engine and return detached reports/trade-ledger evidence."""
    session = build_native_backtest(
        bars,
        state_path=state_path,
        instruments=instruments,
        initial_nav=initial_nav,
        flow_records=flow_records,
        funding_payments=funding_payments,
        execution_start_ns=execution_start_ns,
        execution_end_ns=execution_end_ns,
        reject_stop_orders=reject_stop_orders,
    )
    try:
        if configure_strategy is not None:
            configure_strategy(session.strategy)
        session.run()
        account = session.engine.portfolio.account(VENUE)
        if account is None:
            raise RuntimeError("native MARGIN account was not initialized")
        balance = account.balance_total(USDT)
        if balance is None:
            raise RuntimeError("native USDT account balance is unavailable")
        nav = session.engine.portfolio.equity(venue=VENUE).get(USDT)
        if nav is None:
            raise RuntimeError("native USDT portfolio equity is unavailable")
        return NativeBacktestResult(
            fills=session.engine.trader.generate_order_fills_report(),
            positions=session.engine.trader.generate_positions_report(),
            account=session.engine.trader.generate_account_report(VENUE),
            final_balance=float(balance.as_double()),
            final_nav=float(nav.as_double()),
            parent_orders_submitted=session.strategy.parent_orders_submitted,
            protective_pairs_submitted=session.strategy.protective_pairs_submitted,
            plans_blocked_by_global_slot=session.strategy.plans_blocked_by_global_slot,
            max_active_instruments=session.strategy.max_active_instruments,
            missing_flow_bars=session.strategy.missing_flow_bars,
            funding_payments_applied=(
                0 if session.funding_module is None else len(session.funding_module.applied)
            ),
            funding_totals=(
                {} if session.funding_module is None else session.funding_module.totals
            ),
            conservative_stop_adjustments=(
                ()
                if session.bar_context_module is None
                else session.bar_context_module.applied_adjustments
            ),
            adverse_first_target_cancels=(
                0
                if session.bar_context_module is None
                else session.bar_context_module.adverse_first_target_cancels
            ),
        )
    finally:
        session.dispose()


def run_streaming_native_backtest(
    minutes: Iterable[SynchronizedMinute],
    *,
    state_path: str | Path,
    instruments: Mapping[str, CryptoPerpetual] | Iterable[CryptoPerpetual] | None = None,
    initial_nav: float = 100_000.0,
    chunk_minutes: int = 10_000,
    configure_strategy: Callable[[LiquidityEpisodeStrategy], None] | None = None,
    funding_payments: Iterable[HistoricalFundingPayment] | None = None,
    execution_start_ns: int = 0,
    execution_end_ns: int | None = None,
    reject_stop_orders: bool = False,
) -> NativeBacktestResult:
    """Run a multi-year loader without materializing all bars or flow records."""
    session = build_streaming_native_backtest(
        minutes,
        state_path=state_path,
        instruments=instruments,
        initial_nav=initial_nav,
        chunk_minutes=chunk_minutes,
        funding_payments=funding_payments,
        execution_start_ns=execution_start_ns,
        execution_end_ns=execution_end_ns,
        reject_stop_orders=reject_stop_orders,
    )
    try:
        if configure_strategy is not None:
            configure_strategy(session.strategy)
        session.run()
        account = session.engine.portfolio.account(VENUE)
        if account is None:
            raise RuntimeError("native MARGIN account was not initialized")
        balance = account.balance_total(USDT)
        if balance is None:
            raise RuntimeError("native USDT account balance is unavailable")
        nav = session.engine.portfolio.equity(venue=VENUE).get(USDT)
        if nav is None:
            raise RuntimeError("native USDT portfolio equity is unavailable")
        return NativeBacktestResult(
            fills=session.engine.trader.generate_order_fills_report(),
            positions=session.engine.trader.generate_positions_report(),
            account=session.engine.trader.generate_account_report(VENUE),
            final_balance=float(balance.as_double()),
            final_nav=float(nav.as_double()),
            parent_orders_submitted=session.strategy.parent_orders_submitted,
            protective_pairs_submitted=session.strategy.protective_pairs_submitted,
            plans_blocked_by_global_slot=session.strategy.plans_blocked_by_global_slot,
            max_active_instruments=session.strategy.max_active_instruments,
            missing_flow_bars=session.strategy.missing_flow_bars,
            funding_payments_applied=(
                0 if session.funding_module is None else len(session.funding_module.applied)
            ),
            funding_totals=(
                {} if session.funding_module is None else session.funding_module.totals
            ),
            conservative_stop_adjustments=(
                ()
                if session.bar_context_module is None
                else session.bar_context_module.applied_adjustments
            ),
            adverse_first_target_cancels=(
                0
                if session.bar_context_module is None
                else session.bar_context_module.adverse_first_target_cancels
            ),
        )
    finally:
        session.dispose()
