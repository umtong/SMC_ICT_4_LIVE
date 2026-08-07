"""NautilusTrader strategy adapter for Candidate 12 causal plans."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from logic import BarObs, CausalLiquidityAuctionEngine, Direction, LogicConfig, RiskSizer, TradePlan
from metrics import decimal_value


def build_candidate_strategy(
    *,
    logic_config: LogicConfig,
    instrument: Any,
    settlement_currency: Any,
    bar_type: Any,
    source_volumes: list[float],
    taker_buy_volumes: list[float],
    evaluation_start_ns: int,
    evaluation_end_ns: int,
    starting_nav: Decimal,
) -> Any:
    from nautilus_trader.config import StrategyConfig
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce
    from nautilus_trader.model.events import OrderEvent
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.data import BarType
    from nautilus_trader.trading.strategy import Strategy

    class CandidateStrategyConfig(StrategyConfig, frozen=True):
        instrument_id: InstrumentId
        bar_type: BarType
        evaluation_start_ns: int
        evaluation_end_ns: int
        starting_nav: Decimal

    class CandidateStrategy(Strategy):
        def __init__(self, strategy_config: CandidateStrategyConfig) -> None:
            super().__init__(strategy_config)
            self.logic = CausalLiquidityAuctionEngine(logic_config, str(strategy_config.instrument_id))
            self.sizer = RiskSizer(logic_config.risk_fraction)
            self.flow_index = 0
            self.plans: list[dict[str, Any]] = []
            self.errors: list[dict[str, Any]] = []
            self.lifecycle: list[dict[str, Any]] = []
            self.last_ts_ns = 0
            self.active_plan: TradePlan | None = None
            self.slot_rejections = 0
            self.boundary_actions_started = False

        def on_start(self) -> None:
            self.subscribe_bars(self.config.bar_type)

        def _open_orders(self) -> int:
            return int(
                self.cache.orders_open_count(
                    instrument_id=self.config.instrument_id,
                    strategy_id=self.id,
                ),
            )

        def _slot_free(self) -> bool:
            return self.portfolio.is_flat(self.config.instrument_id) and self._open_orders() == 0

        def _account_values(self) -> tuple[Decimal, Decimal]:
            account = self.cache.account_for_venue(self.config.instrument_id.venue)
            if account is None:
                return self.config.starting_nav, self.config.starting_nav
            total = decimal_value(account.balance_total(settlement_currency))
            free = decimal_value(account.balance_free(settlement_currency), total) if hasattr(account, "balance_free") else total
            return total, free

        def _terminal_if_flat(self, ts_ns: int, reason: str) -> None:
            if self.active_plan is not None and self._slot_free():
                self.logic.mark_trade_terminal(
                    self.active_plan,
                    ts_ns,
                    reason,
                    {"lifecycle_events": len(self.lifecycle)},
                )
                self.active_plan = None

        def _submit_plan(self, plan: TradePlan) -> None:
            if not self._slot_free():
                self.slot_rejections += 1
                self.logic.mark_plan_rejected(plan, self.last_ts_ns, "GLOBAL_SLOT_OCCUPIED")
                return
            nav, free_balance = self._account_values()
            decision = self.sizer.size(
                nav=nav,
                loss_per_unit=Decimal(str(plan.loss_per_unit)),
                entry_price=Decimal(str(plan.expected_entry)),
                quantity_increment=Decimal(str(instrument.size_increment)),
                min_quantity=Decimal(str(instrument.min_quantity)),
                min_notional=decimal_value(instrument.min_notional),
                margin_init=instrument.margin_init,
                free_balance=free_balance,
            )
            if not decision.feasible:
                self.logic.mark_plan_rejected(
                    plan,
                    self.last_ts_ns,
                    decision.reason,
                    {
                        "planned_loss_budget": str(decision.planned_loss_budget),
                        "expected_total_loss": str(decision.expected_total_loss),
                        "required_margin": str(decision.required_margin),
                        "free_balance": str(free_balance),
                    },
                )
                return

            side = OrderSide.BUY if plan.direction is Direction.LONG else OrderSide.SELL
            try:
                order_list = self.order_factory.bracket(
                    instrument_id=self.config.instrument_id,
                    order_side=side,
                    quantity=instrument.make_qty(decision.quantity),
                    entry_order_type=OrderType.MARKET,
                    tp_order_type=OrderType.LIMIT,
                    tp_price=instrument.make_price(plan.target_price),
                    tp_time_in_force=TimeInForce.GTC,
                    tp_post_only=True,
                    sl_order_type=OrderType.STOP_MARKET,
                    sl_trigger_price=instrument.make_price(plan.stop_price),
                    sl_time_in_force=TimeInForce.GTC,
                )
                self.submit_order_list(order_list)
            except Exception as exc:
                record = {
                    "type": "ORDER_LIST_SUBMISSION_EXCEPTION",
                    "ts_ns": self.last_ts_ns,
                    "exception": type(exc).__name__,
                    "message": str(exc),
                }
                self.errors.append(record)
                self.logic.mark_plan_rejected(plan, self.last_ts_ns, record["type"], record)
                return

            record = {
                "scenario_id": plan.scenario_id,
                "scenario": plan.scenario.value,
                "direction": plan.direction.value,
                "observed_ts_ns": plan.observed_ts_ns,
                "entry_order_type": "MARKET",
                "entry": plan.expected_entry,
                "stop": plan.stop_price,
                "target": plan.target_price,
                "loss_per_unit": plan.loss_per_unit,
                "expected_profit_per_unit": plan.expected_profit_per_unit,
                "net_r": plan.net_r,
                "quantity": str(decision.quantity),
                "nav_before": str(nav),
                "planned_loss_budget": str(decision.planned_loss_budget),
                "expected_total_loss": str(decision.expected_total_loss),
                "required_margin": str(decision.required_margin),
                "details": plan.details,
            }
            self.plans.append(record)
            self.active_plan = plan
            self.logic.mark_plan_submitted(plan, self.last_ts_ns, record)

        def on_bar(self, bar: Bar) -> None:
            self.last_ts_ns = int(bar.ts_event)
            self._terminal_if_flat(self.last_ts_ns, "NAUTILUS_FLAT_NO_WORKING_ORDERS")
            if self.flow_index >= len(taker_buy_volumes):
                raise RuntimeError("aggressor-flow stream exhausted before Nautilus bars")
            source_volume = source_volumes[self.flow_index]
            taker_buy = taker_buy_volumes[self.flow_index]
            self.flow_index += 1

            if self.last_ts_ns >= self.config.evaluation_end_ns:
                if not self.boundary_actions_started:
                    self.boundary_actions_started = True
                    if self._open_orders() > 0:
                        self.cancel_all_orders(self.config.instrument_id)
                    if not self.portfolio.is_flat(self.config.instrument_id):
                        self.close_all_positions(self.config.instrument_id)
                return

            observation = BarObs(
                ts_ns=self.last_ts_ns,
                open=float(str(bar.open)),
                high=float(str(bar.high)),
                low=float(str(bar.low)),
                close=float(str(bar.close)),
                volume=source_volume,
                taker_buy_volume=taker_buy,
            )
            allow_entry = self.last_ts_ns >= self.config.evaluation_start_ns and self._slot_free()
            plan = self.logic.on_bar(observation, allow_entry=allow_entry)
            if plan is not None:
                self._submit_plan(plan)

        def _record_order_event(self, event: OrderEvent, kind: str) -> None:
            self.lifecycle.append(
                {
                    "type": kind,
                    "ts_event": int(event.ts_event),
                    "client_order_id": str(event.client_order_id),
                    "event": str(event),
                },
            )

        def on_order_filled(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_FILLED")

        def on_order_expired(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_EXPIRED")
            self._terminal_if_flat(int(event.ts_event), "ORDER_EXPIRED_FLAT")

        def on_order_canceled(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_CANCELED")
            self._terminal_if_flat(int(event.ts_event), "ORDERS_CANCELED_FLAT")

        def on_order_denied(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_DENIED")
            record = {"type": "ORDER_DENIED", "event": str(event)}
            self.errors.append(record)
            if self.active_plan is not None:
                self.logic.mark_plan_rejected(self.active_plan, int(event.ts_event), "ORDER_DENIED", record)
                self.active_plan = None

        def on_order_rejected(self, event: Any) -> None:
            self._record_order_event(event, "ORDER_REJECTED")
            record = {"type": "ORDER_REJECTED", "event": str(event)}
            self.errors.append(record)
            if self.active_plan is not None:
                self.logic.mark_plan_rejected(self.active_plan, int(event.ts_event), "ORDER_REJECTED", record)
                self.active_plan = None

        def on_stop(self) -> None:
            self.cancel_all_orders(self.config.instrument_id)
            if not self.portfolio.is_flat(self.config.instrument_id):
                self.close_all_positions(self.config.instrument_id)


    return CandidateStrategy(
        CandidateStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            evaluation_start_ns=evaluation_start_ns,
            evaluation_end_ns=evaluation_end_ns,
            starting_nav=starting_nav,
        ),
    )
