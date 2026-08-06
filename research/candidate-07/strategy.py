"""NautilusTrader strategy integration for candidate-07."""
from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderRejected, PositionClosed, PositionOpened
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading.strategy import Strategy

from smc_ict_4.contracts import ResearchEvent

from model import CausalLiquidityRouter, Direction, LogicConfig, ScenarioState, SignalBar, TradePlan, Transition


NS_PER_MINUTE = 60_000_000_000


class Candidate07StrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_start_ns: int
    trade_end_ns: int
    initial_nav: Decimal
    risk_fraction: Decimal
    risk_funding_reserve_bps: Decimal
    max_hold_minutes: int
    logic_json: str


class Candidate07Strategy(Strategy):
    """Two-branch causal liquidity state machine executed by NautilusTrader."""

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self.logic = LogicConfig.from_mapping(json.loads(config.logic_json))
        self.router = CausalLiquidityRouter(self.logic)
        self._instrument = None
        self._bucket: list[SignalBar] = []
        self._signal_index = 0
        self._pending_plan: TradePlan | None = None
        self._pending_created_ns: int | None = None
        self._active_plan: TradePlan | None = None
        self._active_entry_nav: float | None = None
        self._position_open_ns: int | None = None
        self._exit_pending = False
        self._events: list[ResearchEvent] = []
        self._nav_series: list[dict[str, Any]] = []
        self._trades: list[dict[str, Any]] = []
        self._diagnostics: list[dict[str, Any]] = []
        self._last_nav = float(config.initial_nav)
        self._quote_currency = Currency.from_str("USDT")

    @property
    def research_events(self) -> tuple[ResearchEvent, ...]:
        return tuple(self._events)

    @property
    def nav_series(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._nav_series)

    @property
    def trade_diagnostics(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._trades)

    @property
    def scenario_diagnostics(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._diagnostics)

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            raise RuntimeError(f"instrument missing from cache: {self.config.instrument_id}")
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._record_nav(int(bar.ts_event))
        in_trade_window = self.config.trade_start_ns <= int(bar.ts_event) < self.config.trade_end_ns
        flat = self.portfolio.is_flat(self.config.instrument_id)

        if not flat and self._position_open_ns is not None:
            held_ns = int(bar.ts_event) - self._position_open_ns
            if held_ns >= self.config.max_hold_minutes * NS_PER_MINUTE and not self._exit_pending:
                self._exit_pending = True
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)

        if int(bar.ts_event) >= self.config.trade_end_ns - NS_PER_MINUTE and not flat and not self._exit_pending:
            self._exit_pending = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

        if self._pending_plan is not None and int(bar.ts_event) > self._pending_plan.observed_time_ns:
            if in_trade_window and flat and self._active_plan is None:
                self._submit_pending(bar)
            else:
                self._invalidate_pending("ENTRY_WINDOW_OR_SLOT_LOST", int(bar.ts_event))

        signal_bar = SignalBar(
            ts_event_ns=int(bar.ts_event),
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=bar.volume.as_double(),
        )
        self._bucket.append(signal_bar)
        if len(self._bucket) < self.logic.signal_minutes:
            return
        if len(self._bucket) > self.logic.signal_minutes:
            raise RuntimeError("signal aggregation bucket overflow")
        aggregated = SignalBar(
            ts_event_ns=self._bucket[-1].ts_event_ns,
            open=self._bucket[0].open,
            high=max(item.high for item in self._bucket),
            low=min(item.low for item in self._bucket),
            close=self._bucket[-1].close,
            volume=sum(item.volume for item in self._bucket),
        )
        self._bucket.clear()
        eligible = (
            self.config.trade_start_ns <= aggregated.ts_event_ns < self.config.trade_end_ns
            and self.portfolio.is_flat(self.config.instrument_id)
            and self._pending_plan is None
            and self._active_plan is None
        )
        observation = self.router.observe(aggregated, self._signal_index, eligible=eligible)
        self._signal_index += 1
        for transition in observation.transitions:
            self._append_transition(transition)
        if observation.diagnostics.get("reason") not in {"WARMUP", "INELIGIBLE"} or observation.transitions:
            self._diagnostics.append(dict(observation.diagnostics))
        if observation.plan is not None:
            self._pending_plan = observation.plan
            self._pending_created_ns = aggregated.ts_event_ns

    def on_position_opened(self, event: PositionOpened) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        self._position_open_ns = int(event.ts_event)
        self._exit_pending = False
        if self._active_plan is not None:
            self._append_manual_event(
                scenario_id=self._active_plan.scenario_id,
                previous_state=ScenarioState.ENTRY_READY.value,
                next_state="POSITION_OPEN",
                reason_code="NAUTILUS_POSITION_OPENED",
                event_time_ns=int(event.ts_event),
                reference_price=self._active_plan.entry_reference,
                details={"position_id": str(event.position_id)},
            )

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        nav_after = self._current_nav()
        plan = self._active_plan
        nav_before = self._active_entry_nav if self._active_entry_nav is not None else nav_after
        net_pnl = nav_after - nav_before
        if plan is not None:
            self._trades.append(
                {
                    "scenario_id": plan.scenario_id,
                    "kind": plan.kind.value,
                    "direction": plan.direction.value,
                    "entry_reference": plan.entry_reference,
                    "stop_price": plan.stop_price,
                    "target_price": plan.target_price,
                    "expected_rr": plan.expected_rr,
                    "opened_ns": self._position_open_ns,
                    "closed_ns": int(event.ts_event),
                    "nav_before": nav_before,
                    "nav_after": nav_after,
                    "net_pnl": net_pnl,
                    "net_return_on_nav": (nav_after / nav_before - 1.0) if nav_before > 0.0 else 0.0,
                    "position_id": str(event.position_id),
                    "plan_details": dict(plan.details),
                }
            )
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state="POSITION_OPEN",
                next_state=ScenarioState.TERMINAL.value,
                reason_code="NAUTILUS_POSITION_CLOSED",
                event_time_ns=int(event.ts_event),
                reference_price=plan.target_price if net_pnl > 0.0 else plan.stop_price,
                details={"net_pnl": net_pnl, "nav_after": nav_after},
            )
        self._active_plan = None
        self._active_entry_nav = None
        self._position_open_ns = None
        self._exit_pending = False
        self._record_nav(int(event.ts_event))

    def on_order_rejected(self, event: OrderRejected) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        if self._active_plan is not None and self.portfolio.is_flat(self.config.instrument_id):
            plan = self._active_plan
            self._append_manual_event(
                scenario_id=plan.scenario_id,
                previous_state=ScenarioState.ENTRY_READY.value,
                next_state=ScenarioState.INVALIDATED.value,
                reason_code="NAUTILUS_ORDER_REJECTED",
                event_time_ns=int(event.ts_event),
                reference_price=plan.entry_reference,
                details={"reason": str(event.reason)},
            )
            self._active_plan = None
            self._active_entry_nav = None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)

    def _submit_pending(self, bar: Bar) -> None:
        plan = self._pending_plan
        if plan is None or self._instrument is None:
            return
        current_price = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(plan.stop_price))
        target = Decimal(str(plan.target_price))
        if plan.direction is Direction.LONG:
            risk_distance = current_price - stop
            reward_distance = target - current_price
            side = OrderSide.BUY
        else:
            risk_distance = stop - current_price
            reward_distance = current_price - target
            side = OrderSide.SELL
        if risk_distance <= 0 or reward_distance <= 0:
            self._invalidate_pending("GAPPED_THROUGH_GEOMETRY", int(bar.ts_event))
            return
        actual_rr = reward_distance / risk_distance
        if actual_rr < Decimal(str(self.logic.minimum_rr)):
            self._invalidate_pending("DELAYED_ENTRY_RR_ERODED", int(bar.ts_event))
            return

        equity = Decimal(str(self._current_nav()))
        planned_budget = equity * self.config.risk_fraction
        tick = self._instrument.price_increment.as_decimal()
        entry_fill = current_price + tick if plan.direction is Direction.LONG else current_price - tick
        stop_fill = stop - tick if plan.direction is Direction.LONG else stop + tick
        fee_rate = self._instrument.taker_fee or Decimal(0)
        funding_reserve = entry_fill * self.config.risk_funding_reserve_bps / Decimal(10_000)
        per_unit_loss = abs(entry_fill - stop_fill) + entry_fill * fee_rate + stop_fill * fee_rate + funding_reserve
        if per_unit_loss <= 0:
            self._invalidate_pending("NONPOSITIVE_UNIT_LOSS", int(bar.ts_event))
            return
        raw_qty = planned_budget / per_unit_loss
        quantity = self._instrument.make_qty(raw_qty)
        if quantity.as_decimal() <= 0:
            self._invalidate_pending("QUANTITY_ROUNDED_TO_ZERO", int(bar.ts_event))
            return

        stop_price = self._instrument.make_price(stop)
        target_price = self._instrument.make_price(target)
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=quantity,
            sl_trigger_price=stop_price,
            tp_price=target_price,
        )
        self._active_plan = TradePlan(
            scenario_id=plan.scenario_id,
            kind=plan.kind,
            direction=plan.direction,
            observed_time_ns=plan.observed_time_ns,
            entry_reference=float(current_price),
            stop_price=stop_price.as_double(),
            target_price=target_price.as_double(),
            liquidity_level=plan.liquidity_level,
            expected_rr=float(actual_rr),
            details={
                **dict(plan.details),
                "planned_loss_budget": float(planned_budget),
                "per_unit_expected_loss": float(per_unit_loss),
                "quantity": str(quantity),
                "fee_rate": str(fee_rate),
                "funding_reserve_bps": str(self.config.risk_funding_reserve_bps),
                "slippage_ticks_each_adverse_fill": 1,
            },
        )
        self._active_entry_nav = float(equity)
        self._pending_plan = None
        self._pending_created_ns = None
        self.submit_order_list(order_list)

    def _invalidate_pending(self, reason: str, event_time_ns: int) -> None:
        plan = self._pending_plan
        if plan is None:
            return
        self._append_manual_event(
            scenario_id=plan.scenario_id,
            previous_state=ScenarioState.ENTRY_READY.value,
            next_state=ScenarioState.INVALIDATED.value,
            reason_code=reason,
            event_time_ns=event_time_ns,
            reference_price=plan.entry_reference,
            details={"created_ns": self._pending_created_ns},
        )
        self._pending_plan = None
        self._pending_created_ns = None

    def _append_transition(self, transition: Transition) -> None:
        self._events.append(
            ResearchEvent(
                scenario_id=transition.scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type=transition.event_type,
                event_time_ns=transition.event_time_ns,
                observed_time_ns=transition.event_time_ns,
                previous_state=transition.previous_state,
                next_state=transition.next_state,
                reason_code=transition.reason_code,
                reference_price=(str(transition.reference_price) if transition.reference_price is not None else None),
                details=dict(transition.details),
            )
        )

    def _append_manual_event(
        self,
        *,
        scenario_id: str,
        previous_state: str,
        next_state: str,
        reason_code: str,
        event_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> None:
        self._events.append(
            ResearchEvent(
                scenario_id=scenario_id,
                instrument_id=str(self.config.instrument_id),
                event_type="EXECUTION_TRANSITION",
                event_time_ns=event_time_ns,
                observed_time_ns=event_time_ns,
                previous_state=previous_state,
                next_state=next_state,
                reason_code=reason_code,
                reference_price=str(reference_price),
                details=details,
            )
        )

    def _current_nav(self) -> float:
        try:
            equities = self.portfolio.equity(self.config.instrument_id.venue)
            money = equities.get(self._quote_currency)
            if money is not None:
                value = money.as_double()
                if value > 0.0:
                    self._last_nav = value
                    return value
        except Exception:
            pass
        return self._last_nav

    def _record_nav(self, timestamp_ns: int) -> None:
        nav = self._current_nav()
        if self._nav_series and self._nav_series[-1]["timestamp_ns"] == timestamp_ns:
            self._nav_series[-1]["nav"] = nav
        else:
            self._nav_series.append({"timestamp_ns": timestamp_ns, "nav": nav})
