"""NautilusTrader TradeTick execution wrapper for candidate 10 v3."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
try:
    from nautilus_trader.model import TradeTick
except ImportError:  # pragma: no cover - compatibility for static tooling
    from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import InstrumentId

from smc_ict_4.contracts import ResearchEvent

from c10_flow_model import FlowParams
from c10_flow_model import FlowTickView
from c10_flow_model import FlowTradePlan
from c10_flow_state import FlowAuctionStateMachine
from c10_model import BarView
from c10_model import NS_PER_MINUTE
from c10_strategy import Candidate10Strategy


# The flow runner contains TradeTick and one-minute NAV bars but no historical
# bid/ask quotes. Protective stops therefore must be evaluated on LAST_PRICE,
# not the OrderFactory bracket default (TriggerType.DEFAULT), which can consult a
# synthetic bid/ask and reject a valid child as already marketable at parent fill.
FLOW_STOP_TRIGGER_TYPE = TriggerType.LAST_PRICE


class FlowCandidate10Config(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    eval_start_ns: int
    eval_end_ns: int
    risk_fraction: Decimal
    params: dict[str, Any]
    starting_balance: Decimal
    no_entry_minutes_before_end: int = 30
    funding_guard_minutes: int = 6


class FlowCandidate10Strategy(Candidate10Strategy):
    """Use raw executed trades for signals and Nautilus for all execution/accounting."""

    def __init__(self, config: FlowCandidate10Config):
        super().__init__(config)  # type: ignore[arg-type]
        self.flow_machine: FlowAuctionStateMachine | None = None
        self.pending_expiry_sequence: int | None = None
        self.last_schedule_flat_minute: int | None = None
        self.signals_blocked_by_open_risk = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            raise RuntimeError(f"instrument not in cache: {self.config.instrument_id}")
        params = FlowParams(**dict(self.config.params))
        self.flow_machine = FlowAuctionStateMachine(
            params,
            tick_size=self.instrument.price_increment.as_double(),
            instrument_id=str(self.config.instrument_id),
        )
        # Trade ticks own signal creation and order matching. One-minute bars are
        # retained only for NAV sampling, day boundaries and scheduled flattening.
        self.subscribe_trade_ticks(self.config.instrument_id)
        self.subscribe_bars(self.config.bar_type)

    def _clear_pending_fields(self) -> None:
        super()._clear_pending_fields()
        self.pending_expiry_sequence = None

    def _record_flow_transition(self, transition: Any) -> None:
        # FlowTransition intentionally shares the immutable research event schema.
        self._record_transition(transition)

    def _check_pending_tick(self, view: FlowTickView) -> None:
        if not self.entry_pending:
            return
        if (
            self.pending_expiry_sequence is not None
            and self.flow_machine is not None
            and self.flow_machine.completed_sequence > self.pending_expiry_sequence
        ):
            self._cancel_pending(
                view.ts_ns,
                "FLOW_RETRACE_ENTRY_EXPIRED",
                view.price,
            )
            return
        if self.pending_direction is None or self.pending_invalidation_price is None:
            return
        invalidated = (
            view.price <= self.pending_invalidation_price
            if self.pending_direction > 0
            else view.price >= self.pending_invalidation_price
        )
        if invalidated:
            self._cancel_pending(
                view.ts_ns,
                "FLOW_STRUCTURE_INVALIDATED_BEFORE_FILL",
                view.price,
            )

    def _submit_flow_plan(self, plan: FlowTradePlan, view: FlowTickView) -> None:
        assert self.instrument is not None
        if plan.direction > 0:
            entry = self._round_price(plan.entry_price, upward=False)
            stop = self._round_price(plan.stop_price, upward=False)
            target = self._round_price(plan.target_price, upward=False)
            valid = stop.as_double() < entry.as_double() < target.as_double()
            side = OrderSide.BUY
        else:
            entry = self._round_price(plan.entry_price, upward=True)
            stop = self._round_price(plan.stop_price, upward=True)
            target = self._round_price(plan.target_price, upward=True)
            valid = target.as_double() < entry.as_double() < stop.as_double()
            side = OrderSide.SELL
        if not valid:
            return

        quantity = self._risk_quantity(  # type: ignore[arg-type]
            plan,
            entry.as_double(),
            stop.as_double(),
        )
        if quantity is None:
            return

        bracket = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            entry_order_type=OrderType.LIMIT,
            entry_price=entry,
            entry_post_only=True,
            tp_price=target,
            tp_post_only=True,
            sl_trigger_price=stop,
            sl_trigger_type=FLOW_STOP_TRIGGER_TYPE,
        )
        start_equity = self._equity()
        current_sequence = (
            self.flow_machine.completed_sequence
            if self.flow_machine is not None
            else 0
        )
        self.entry_pending = True
        self.pending_expiry_sequence = current_sequence + plan.entry_expiry_bars
        self.pending_expiry_ns = None
        self.pending_invalidation_price = stop.as_double()
        self.pending_direction = plan.direction
        self.orders_submitted += 1
        self.active_trade = {
            "scenario_id": plan.scenario_id,
            "scenario": plan.scenario,
            "direction": plan.direction,
            "signal_ts_ns": view.ts_ns,
            "entry_estimate": entry.as_double(),
            "stop": stop.as_double(),
            "target": target.as_double(),
            "quantity": quantity.as_double(),
            "start_equity": start_equity,
            "structural_target": "OPPOSITE_EVENT_RANGE_BOUNDARY",
            "entry_order_type": "LIMIT_POST_ONLY_TRADE_TICK_EXECUTION",
            "planned_expiry_sequence": self.pending_expiry_sequence,
            "event_atr": plan.event_atr,
            "source_boundary": plan.source_boundary,
            "opposite_boundary": plan.opposite_boundary,
            "flow_details": dict(plan.details),
            "event_state": "ORDER_PENDING",
        }
        self.events.append(
            ResearchEvent(
                scenario_id=plan.scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="ORDER_SUBMITTED",
                event_time_ns=view.ts_ns,
                observed_time_ns=view.ts_ns,
                previous_state="ENTRY_READY",
                next_state="ORDER_PENDING",
                reason_code="NAUTILUS_POST_ONLY_FLOW_RETRACE_BRACKET_SUBMITTED",
                reference_price=str(entry.as_double()),
                details={
                    "quantity": quantity.as_double(),
                    "entry": entry.as_double(),
                    "stop": stop.as_double(),
                    "target": target.as_double(),
                    "risk_fraction": str(self.config.risk_fraction),
                    "expiry_sequence": self.pending_expiry_sequence,
                    "stop_trigger_type": str(FLOW_STOP_TRIGGER_TYPE),
                    "cost_adjusted_net_rr": plan.details[
                        "cost_adjusted_net_rr"
                    ],
                },
            ),
        )
        self.submit_order_list(bracket)

    @staticmethod
    def _aggressor_sign(tick: TradeTick) -> int | None:
        if tick.aggressor_side == AggressorSide.BUYER:
            return 1
        if tick.aggressor_side == AggressorSide.SELLER:
            return -1
        return None

    def on_trade_tick(self, tick: TradeTick) -> None:
        aggressor = self._aggressor_sign(tick)
        if aggressor is None:
            return
        view = FlowTickView(
            ts_ns=tick.ts_event,
            price=tick.price.as_double(),
            quantity=tick.size.as_double(),
            aggressor=aggressor,
            trade_id=str(tick.trade_id),
        )

        self._check_pending_tick(view)
        must_flat = self._must_flatten(view.ts_ns)
        if must_flat:
            minute = self._minute_of_day(view.ts_ns)
            if minute != self.last_schedule_flat_minute:
                self._force_flat(view.ts_ns)
                self.last_schedule_flat_minute = minute
            if self.flow_machine is not None:
                self.flow_machine.active_probe = None

        if self.flow_machine is None:
            return
        transitions, plan, completed_bar = self.flow_machine.on_tick(view)
        for transition in transitions:
            self._record_flow_transition(transition)

        if completed_bar is not None:
            self._check_pending_tick(view)
        if plan is None:
            return

        inside_eval = self.config.eval_start_ns <= view.ts_ns < self.config.eval_end_ns
        if not inside_eval:
            self.signals_outside_evaluation += 1
            return
        self.signals_seen += 1
        can_enter = (
            not self._inside_funding_guard(view.ts_ns)
            and not must_flat
            and self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and self.active_trade is None
        )
        if can_enter:
            self._submit_flow_plan(plan, view)
        else:
            self.signals_blocked_by_open_risk += 1

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
            minute = self._minute_of_day(view.ts_ns)
            if minute != self.last_schedule_flat_minute:
                self._force_flat(view.ts_ns)
                self.last_schedule_flat_minute = minute
            if self.flow_machine is not None:
                self.flow_machine.active_probe = None

    def on_stop(self) -> None:
        super().on_stop()
        if self.flow_machine is not None:
            self.flow_machine.active_probe = None
