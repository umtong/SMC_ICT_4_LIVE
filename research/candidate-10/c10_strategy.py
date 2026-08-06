"""NautilusTrader execution wrapper for candidate 10."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from nautilus_trader.config import StrategyConfig
try:
    from nautilus_trader.model import CryptoPerpetual
except ImportError:  # Pinned 1.230 exports it from model; fallback aids static tooling.
    from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from smc_ict_4.contracts import ResearchEvent

from c10_model import BarView
from c10_model import MachineParams
from c10_model import NS_PER_MINUTE
from c10_model import TradePlan
from c10_model import Transition
from c10_state import AuctionStateMachine


class Candidate10Config(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    eval_start_ns: int
    eval_end_ns: int
    risk_fraction: Decimal
    params: dict[str, Any]
    starting_balance: Decimal
    no_entry_minutes_before_end: int = 30
    funding_guard_minutes: int = 6


class Candidate10Strategy(Strategy):
    """Nautilus strategy wrapper; fills, positions and accounting stay in Nautilus."""

    def __init__(self, config: Candidate10Config):
        super().__init__(config)
        self.venue = config.instrument_id.venue
        self.currency = Currency.from_str("USDT")
        self.instrument: CryptoPerpetual | None = None
        self.machine: AuctionStateMachine | None = None
        self.events: list[ResearchEvent] = []
        self.entry_pending = False
        self.active_trade: dict[str, Any] | None = None
        self.trade_records: list[dict[str, Any]] = []
        self.equity_curve: list[dict[str, Any]] = []
        self.daily_nav: dict[str, float] = {}
        self.current_day: str | None = None
        self.last_equity = float(config.starting_balance)
        self.max_equity = self.last_equity
        self.max_drawdown = 0.0
        self.order_errors: list[dict[str, Any]] = []
        self.signals_seen = 0
        self.orders_submitted = 0
        self.forced_exits = 0
        self.signals_outside_evaluation = 0
        self.eval_start_date = datetime.fromtimestamp(
            config.eval_start_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date()
        self.eval_end_date = datetime.fromtimestamp(
            config.eval_end_ns / 1_000_000_000,
            tz=timezone.utc,
        ).date()

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not in cache: {self.config.instrument_id}")
        params = MachineParams(**dict(self.config.params))
        self.machine = AuctionStateMachine(
            params,
            tick_size=self.instrument.price_increment.as_double(),
            instrument_id=str(self.config.instrument_id),
        )
        self.subscribe_bars(self.config.bar_type)

    def _equity(self) -> float:
        values = self.portfolio.equity(self.venue)
        if values and self.currency in values:
            return values[self.currency].as_double()
        account = self.cache.account_for_venue(self.venue)
        if account is None:
            return self.last_equity
        total = account.balance_total(self.currency)
        return total.as_double() if total is not None else self.last_equity

    def _record_equity(self, ts_ns: int) -> None:
        equity = self._equity()
        self.last_equity = equity
        self.max_equity = max(self.max_equity, equity)
        if self.max_equity > 0:
            self.max_drawdown = max(self.max_drawdown, 1.0 - equity / self.max_equity)
        self.equity_curve.append({"ts_ns": ts_ns, "equity": equity})

    def _record_transition(self, transition: Transition) -> None:
        self.events.append(
            ResearchEvent(
                scenario_id=transition.scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type=transition.event_type,
                event_time_ns=transition.event_time_ns,
                observed_time_ns=transition.observed_time_ns,
                previous_state=transition.previous_state,
                next_state=transition.next_state,
                reason_code=transition.reason_code,
                reference_price=(
                    None
                    if transition.reference_price is None
                    else str(transition.reference_price)
                ),
                details=transition.details,
            ),
        )

    @staticmethod
    def _minute_of_day(ts_ns: int) -> int:
        dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        return dt.hour * 60 + dt.minute

    def _is_evaluation_day(self, day_text: str) -> bool:
        value = date.fromisoformat(day_text)
        return self.eval_start_date <= value < self.eval_end_date

    def _inside_funding_guard(self, ts_ns: int) -> bool:
        minute = self._minute_of_day(ts_ns)
        guard = self.config.funding_guard_minutes
        return any(abs(minute - hour * 60) <= guard for hour in (0, 8, 16))

    def _must_flatten(self, ts_ns: int) -> bool:
        minute = self._minute_of_day(ts_ns)
        guard = self.config.funding_guard_minutes
        funding_close = any(0 <= hour * 60 - minute <= guard for hour in (8, 16))
        day_close = minute >= 24 * 60 - self.config.no_entry_minutes_before_end
        eval_close = (
            ts_ns
            >= self.config.eval_end_ns
            - self.config.no_entry_minutes_before_end * NS_PER_MINUTE
        )
        return funding_close or day_close or eval_close

    def _round_price(self, value: float, *, upward: bool) -> Price:
        assert self.instrument is not None
        tick = Decimal(str(self.instrument.price_increment))
        raw = Decimal(str(value)) / tick
        units = raw.to_integral_value(
            rounding=ROUND_CEILING if upward else ROUND_FLOOR,
        )
        return self.instrument.make_price(units * tick)

    def _risk_quantity(self, plan: TradePlan) -> Quantity | None:
        assert self.instrument is not None
        equity = Decimal(str(self._equity()))
        risk_budget = equity * self.config.risk_fraction
        entry = Decimal(str(plan.entry_estimate))
        stop = Decimal(str(plan.stop_price))
        fee = Decimal(str(self.instrument.taker_fee))
        tick = Decimal(str(self.instrument.price_increment))
        # Entry and stop are aggressive. The cost-loaded fee includes ordinary
        # fees plus a two-basis-point execution reserve.
        per_unit_loss = abs(entry - stop) + (entry + stop) * fee + tick * Decimal(2)
        if per_unit_loss <= 0:
            return None
        raw_qty = risk_budget / per_unit_loss
        increment = Decimal(str(self.instrument.size_increment))
        units = (raw_qty / increment).to_integral_value(rounding=ROUND_FLOOR)
        qty_value = units * increment
        if qty_value < Decimal(str(self.instrument.min_quantity)):
            return None
        return self.instrument.make_qty(qty_value)

    def _submit_plan(self, plan: TradePlan, bar: BarView) -> None:
        assert self.instrument is not None
        quantity = self._risk_quantity(plan)
        if quantity is None:
            return
        entry = plan.entry_estimate
        if plan.direction > 0:
            stop = self._round_price(plan.stop_price, upward=False)
            target = self._round_price(plan.target_price, upward=False)
            valid = stop.as_double() < entry < target.as_double()
            side = OrderSide.BUY
        else:
            stop = self._round_price(plan.stop_price, upward=True)
            target = self._round_price(plan.target_price, upward=True)
            valid = target.as_double() < entry < stop.as_double()
            side = OrderSide.SELL
        if not valid:
            return

        bracket = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            tp_price=target,
            tp_post_only=False,
            sl_trigger_price=stop,
        )
        start_equity = self._equity()
        # Register state before dispatch because the backtest command queue can
        # emit fills at the same market timestamp.
        self.entry_pending = True
        self.orders_submitted += 1
        self.active_trade = {
            "scenario_id": plan.scenario_id,
            "scenario": plan.scenario,
            "direction": plan.direction,
            "signal_ts_ns": bar.ts_ns,
            "entry_estimate": entry,
            "stop": stop.as_double(),
            "target": target.as_double(),
            "quantity": quantity.as_double(),
            "start_equity": start_equity,
            "structural_target": plan.structural_target,
            "event_state": "ORDER_PENDING",
        }
        self.events.append(
            ResearchEvent(
                scenario_id=plan.scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="ORDER_SUBMITTED",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                previous_state="ENTRY_READY",
                next_state="ORDER_PENDING",
                reason_code="NAUTILUS_BRACKET_SUBMITTED",
                reference_price=str(entry),
                details={
                    "quantity": quantity.as_double(),
                    "stop": stop.as_double(),
                    "target": target.as_double(),
                    "risk_fraction": str(self.config.risk_fraction),
                    "structural_target": plan.structural_target,
                },
            ),
        )
        self.submit_order_list(bracket)

    def _force_flat(self) -> None:
        if self.entry_pending:
            self.cancel_all_orders(self.config.instrument_id)
            self.entry_pending = False
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            self.forced_exits += 1

    def on_bar(self, bar: Bar) -> None:
        view = BarView(
            ts_ns=bar.ts_event,
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=bar.volume.as_double(),
        )
        bar_open_ns = view.ts_ns - NS_PER_MINUTE
        dt = datetime.fromtimestamp(bar_open_ns / 1_000_000_000, tz=timezone.utc)
        day = dt.date().isoformat()
        if self.current_day is None:
            self.current_day = day
        elif day != self.current_day:
            if self._is_evaluation_day(self.current_day):
                self.daily_nav[self.current_day] = self._equity()
            self.current_day = day

        self._record_equity(view.ts_ns)
        if self._must_flatten(view.ts_ns):
            self._force_flat()

        if self.machine is None:
            return
        transitions, plan = self.machine.on_bar(view)
        for transition in transitions:
            self._record_transition(transition)

        inside_eval = self.config.eval_start_ns <= bar_open_ns < self.config.eval_end_ns
        can_enter = (
            inside_eval
            and not self._inside_funding_guard(view.ts_ns)
            and not self._must_flatten(view.ts_ns)
            and self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and self.active_trade is None
        )
        if plan is not None:
            if inside_eval:
                self.signals_seen += 1
                if can_enter:
                    self._submit_plan(plan, view)
            else:
                self.signals_outside_evaluation += 1

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        if self.active_trade is not None:
            self.active_trade["opened_ts_ns"] = int(
                getattr(event, "ts_event", self.clock.timestamp_ns()),
            )

    def on_position_closed(self, event: Any) -> None:
        if self.active_trade is None:
            return
        end_equity = self._equity()
        record = dict(self.active_trade)
        record["closed_ts_ns"] = int(
            getattr(event, "ts_event", self.clock.timestamp_ns()),
        )
        record["end_equity"] = end_equity
        record["net_pnl"] = end_equity - float(record["start_equity"])
        record["net_return"] = end_equity / float(record["start_equity"]) - 1.0
        self.trade_records.append(record)
        scenario_id = str(record["scenario_id"])
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="POSITION_CLOSED",
                event_time_ns=record["closed_ts_ns"],
                observed_time_ns=record["closed_ts_ns"],
                previous_state=str(record.get("event_state", "ORDER_PENDING")),
                next_state="CLOSED",
                reason_code="NAUTILUS_POSITION_CLOSED",
                reference_price=None,
                details={"net_pnl": record["net_pnl"], "end_equity": end_equity},
            ),
        )
        self.active_trade = None
        self.entry_pending = False

    def _order_error(self, event: Any, kind: str) -> None:
        ts_ns = int(getattr(event, "ts_event", self.clock.timestamp_ns()))
        payload = {"kind": kind, "ts_ns": ts_ns, "event": str(event)}
        self.order_errors.append(payload)
        self.entry_pending = False
        if self.active_trade is None:
            return
        scenario_id = str(self.active_trade["scenario_id"])
        previous_state = str(self.active_trade.get("event_state", "ORDER_PENDING"))
        self.events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="ORDER_ERROR",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                previous_state=previous_state,
                next_state="ORDER_ERROR",
                reason_code=kind,
                reference_price=None,
                details={"event": str(event)},
            ),
        )
        self.active_trade["event_state"] = "ORDER_ERROR"
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            self.forced_exits += 1
        else:
            self.active_trade = None

    def on_order_denied(self, event: Any) -> None:
        self._order_error(event, "DENIED")

    def on_order_rejected(self, event: Any) -> None:
        self._order_error(event, "REJECTED")

    def on_stop(self) -> None:
        self._force_flat()
        if self.current_day is not None and self._is_evaluation_day(self.current_day):
            self.daily_nav[self.current_day] = self._equity()


def make_cost_loaded_btc_perpetual() -> CryptoPerpetual:
    """BTCUSDT perpetual metadata with explicit retail fee and impact reserve.

    maker_fee = 2 bp venue fee + 2 bp adverse-selection/impact reserve.
    taker_fee = 5 bp venue fee + 2 bp spread/slippage/impact reserve.
    """

    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("BTCUSDT-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        min_notional=Money(10.00, usdt),
        max_price=Price.from_str("809484.0"),
        min_price=Price.from_str("261.1"),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=Decimal("0.000400"),
        taker_fee=Decimal("0.000700"),
    )
