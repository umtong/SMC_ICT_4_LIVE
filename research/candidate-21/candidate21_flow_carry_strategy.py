"""Candidate 21 synchronized flow-carry strategy.

The native 10-second event router is retained only as the parent-event detector.
A trade is allowed only for true acceptance at a full UTC hour when the response
close agrees with both one-hour and three-hour price discovery.  The old micro
stop and measured-balance target are discarded: this is a medium-horizon carry
leg protected by the strictly prior one-hour opposite extreme and exited after
four hours or before the next funding boundary.

NautilusTrader remains the sole order, fill, position, fee, margin, liquidation,
portfolio and continuous-NAV engine.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType

from candidate21_event_strategy import Candidate21EventConfig
from candidate21_event_strategy import Candidate21EventStrategy
from flow_carry_logic import FlowCarryPlan
from flow_carry_logic import build_flow_carry_plan
from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate21FlowCarryConfig(Candidate21EventConfig, frozen=True):
    flow_carry_hold_seconds: int = 4 * 60 * 60
    flow_carry_stop_buffer_atr: float = 0.08
    flow_carry_require_full_hour: bool = True


class Candidate21FlowCarryStrategy(Candidate21EventStrategy):
    """Enter only synchronized acceptance and carry the new price-discovery leg."""

    def __init__(self, config: Candidate21FlowCarryConfig) -> None:
        super().__init__(config=config)
        if config.flow_carry_hold_seconds < 1:
            raise ValueError("flow_carry_hold_seconds must be positive")
        if (
            not math.isfinite(config.flow_carry_stop_buffer_atr)
            or config.flow_carry_stop_buffer_atr < 0.0
        ):
            raise ValueError(
                "flow_carry_stop_buffer_atr must be finite and nonnegative",
            )
        self.flow_carry_plan: FlowCarryPlan | None = None
        self.flow_carry_quantity: float = 0.0
        self.flow_carry_stop_order_id: str | None = None
        self.flow_carry_exit_requested = False
        self.diagnostics.update(
            {
                "candidate21_flow_carry_acceptance_seen": 0,
                "candidate21_flow_carry_nonacceptance_closed": 0,
                "candidate21_flow_carry_not_full_hour": 0,
                "candidate21_flow_carry_trend_rejected": 0,
                "candidate21_flow_carry_context_rejected": 0,
                "candidate21_flow_carry_entries": 0,
                "candidate21_flow_carry_protective_stops": 0,
                "candidate21_flow_carry_protective_rejections": 0,
                "candidate21_flow_carry_fail_closes": 0,
                "candidate21_flow_carry_time_exits": 0,
                "candidate21_flow_carry_funding_exits": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.flow_carry_plan = None
        self.flow_carry_quantity = 0.0
        self.flow_carry_stop_order_id = None
        self.flow_carry_exit_requested = False

    def _close_without_trade(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any],
    ) -> bool:
        self._transition(
            setup.scenario_id,
            "FLOW_CARRY_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            {**setup.details, **details},
        )
        self.pending = None
        self.event_state = None
        self.flow_carry_plan = None
        return False

    def _submit_event_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        """Replace the micro bracket with synchronized medium-horizon carry."""
        if setup.branch != "ACCEPTANCE":
            self.diagnostics["candidate21_flow_carry_nonacceptance_closed"] = int(
                self.diagnostics[
                    "candidate21_flow_carry_nonacceptance_closed"
                ],
            ) + 1
            return self._close_without_trade(
                setup,
                row,
                "FAILED_AUCTION_HAS_NO_MEDIUM_HORIZON_CARRY_EDGE",
                {"candidate21_flow_carry_branch": setup.branch},
            )

        self.diagnostics["candidate21_flow_carry_acceptance_seen"] = int(
            self.diagnostics["candidate21_flow_carry_acceptance_seen"],
        ) + 1
        decision = build_flow_carry_plan(
            list(self.bars),
            side=setup.side,
            stop_buffer_atr=self.config.flow_carry_stop_buffer_atr,
            hold_seconds=self.config.flow_carry_hold_seconds,
            require_full_hour=self.config.flow_carry_require_full_hour,
        )
        if not decision.eligible or decision.plan is None:
            if decision.reason == "NOT_FULL_UTC_HOUR":
                key = "candidate21_flow_carry_not_full_hour"
            elif decision.reason == "ONE_AND_THREE_HOUR_TREND_NOT_ALIGNED":
                key = "candidate21_flow_carry_trend_rejected"
            else:
                key = "candidate21_flow_carry_context_rejected"
            self.diagnostics[key] = int(self.diagnostics[key]) + 1
            return self._close_without_trade(
                setup,
                row,
                decision.reason,
                {"candidate21_flow_carry_eligible": False},
            )

        plan = decision.plan
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = (
            self.config.adverse_slippage_bps_each_side / 10_000.0
        )
        planned_loss = planned_loss_per_unit(
            plan.entry_estimate,
            plan.stop_price,
            plan.side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["candidate21_flow_carry_context_rejected"] = int(
                self.diagnostics[
                    "candidate21_flow_carry_context_rejected"
                ],
            ) + 1
            return self._close_without_trade(
                setup,
                row,
                "INVALID_FLOW_CARRY_PLANNED_LOSS",
                {"candidate21_flow_carry_plan": asdict(plan)},
            )

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if (
            quantity_value <= 0.0
            or quantity_value * plan.entry_estimate < 10.0
        ):
            return self._close_without_trade(
                setup,
                row,
                "FLOW_CARRY_QUANTITY_BELOW_INSTRUMENT_MINIMUM",
                {
                    "candidate21_flow_carry_plan": asdict(plan),
                    "raw_risk_budget": risk_budget,
                },
            )

        order_side = OrderSide.BUY if plan.side > 0 else OrderSide.SELL
        entry_order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
            tags=["ENTRY", "CANDIDATE21_SYNC_FLOW_CARRY"],
        )
        self.flow_carry_plan = plan
        self.flow_carry_quantity = quantity_value
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "FLOW_CARRY_ACCEPTANCE"
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.event_state = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate21_flow_carry_entries"] = int(
            self.diagnostics["candidate21_flow_carry_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            decision.reason,
            plan.entry_estimate,
            {
                **setup.details,
                "candidate21_flow_carry_plan": asdict(plan),
                "entry_order_type": "MARKET",
                "entry_time_in_force": "IOC",
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
                "price_target": None,
                "exit_policy": "FOUR_HOURS_OR_BEFORE_FUNDING",
            },
        )
        self.submit_order(entry_order)
        return True

    def _fail_close_open_position(
        self,
        *,
        ts_event: int,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        if self.portfolio.is_flat(self.config.instrument_id):
            return
        self.diagnostics["candidate21_flow_carry_fail_closes"] = int(
            self.diagnostics["candidate21_flow_carry_fail_closes"],
        ) + 1
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.flow_carry_exit_requested = True
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "FLOW_CARRY_FAIL_CLOSE",
                ts_event,
                ts_event,
                "EXIT_PENDING",
                reason,
                float(self.bars[-1]["close"]),
                details,
            )

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        plan = self.flow_carry_plan
        if plan is None or self.flow_carry_quantity <= 0.0:
            self._fail_close_open_position(
                ts_event=int(getattr(event, "ts_event", self.bars[-1]["ts"])),
                reason="MISSING_FLOW_CARRY_PROTECTION_PLAN",
                details={},
            )
            return

        last_close = float(self.bars[-1]["close"])
        already_invalid = (
            last_close <= plan.stop_price
            if plan.side > 0
            else last_close >= plan.stop_price
        )
        if already_invalid:
            self._fail_close_open_position(
                ts_event=int(getattr(event, "ts_event", self.bars[-1]["ts"])),
                reason="STRUCTURAL_STOP_ALREADY_BREACHED_AT_ENTRY_FILL",
                details={"candidate21_flow_carry_plan": asdict(plan)},
            )
            return

        stop_side = OrderSide.SELL if plan.side > 0 else OrderSide.BUY
        stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=stop_side,
            quantity=self.instrument.make_qty(self.flow_carry_quantity),
            trigger_price=self.instrument.make_price(plan.stop_price),
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=["STOP_LOSS", "CANDIDATE21_FLOW_CARRY_STRUCTURE"],
        )
        self.flow_carry_stop_order_id = str(stop_order.client_order_id)
        self.submit_order(stop_order)
        self.diagnostics["candidate21_flow_carry_protective_stops"] = int(
            self.diagnostics[
                "candidate21_flow_carry_protective_stops"
            ],
        ) + 1
        if self.current_scenario_id is not None:
            ts_event = int(
                getattr(event, "ts_event", self.bars[-1]["ts"]),
            )
            self._transition(
                self.current_scenario_id,
                "PROTECTIVE_STOP_SUBMITTED",
                ts_event,
                ts_event,
                "POSITION_PROTECTED",
                "PRIOR_HOUR_OPPOSITE_EXTREME_WITH_ATR_BUFFER",
                plan.stop_price,
                {
                    "stop_order_id": self.flow_carry_stop_order_id,
                    "candidate21_flow_carry_plan": asdict(plan),
                },
            )

    def on_order_rejected(self, event: Any) -> None:
        rejected_id = str(getattr(event, "client_order_id", ""))
        stop_rejected = (
            self.flow_carry_stop_order_id is not None
            and rejected_id == self.flow_carry_stop_order_id
        )
        if not stop_rejected:
            super().on_order_rejected(event)
            return

        self.diagnostics["order_rejections"] = int(
            self.diagnostics["order_rejections"],
        ) + 1
        self.diagnostics["candidate21_flow_carry_protective_rejections"] = int(
            self.diagnostics[
                "candidate21_flow_carry_protective_rejections"
            ],
        ) + 1
        ts_event = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        self._fail_close_open_position(
            ts_event=ts_event,
            reason="PROTECTIVE_STOP_REJECTED_FAIL_CLOSED",
            details={"event": str(event)},
        )

    def on_position_closed(self, event: Any) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        super().on_position_closed(event)

    def _manage_open_position(
        self,
        row: dict[str, float | int],
    ) -> None:
        if self.flow_carry_exit_requested:
            return
        plan = self.flow_carry_plan
        if plan is None:
            self._fail_close_open_position(
                ts_event=int(row["ts"]),
                reason="OPEN_POSITION_LOST_FLOW_CARRY_PLAN",
                details={},
            )
            return

        moment = datetime.fromtimestamp(
            int(row["ts"]) / 1_000_000_000,
            tz=timezone.utc,
        )
        before_funding = (
            moment.hour in (7, 15, 23)
            and moment.minute >= self.config.funding_flatten_minute
        )
        horizon_reached = int(row["ts"]) >= plan.hold_until_ns
        evaluation_ended = int(row["ts"]) >= self.config.evaluation_end_ns
        if not (before_funding or horizon_reached or evaluation_ended):
            return

        if before_funding:
            self.diagnostics["candidate21_flow_carry_funding_exits"] = int(
                self.diagnostics[
                    "candidate21_flow_carry_funding_exits"
                ],
            ) + 1
            reason = "BEFORE_FUNDING_BOUNDARY"
        else:
            self.diagnostics["candidate21_flow_carry_time_exits"] = int(
                self.diagnostics["candidate21_flow_carry_time_exits"],
            ) + 1
            reason = (
                "FOUR_HOUR_PRICE_DISCOVERY_HORIZON"
                if horizon_reached
                else "EVALUATION_BOUNDARY"
            )

        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.flow_carry_exit_requested = True
        if self.current_scenario_id is not None:
            self._transition(
                self.current_scenario_id,
                "FLOW_CARRY_EXIT_REQUESTED",
                int(row["ts"]),
                int(row["ts"]),
                "EXIT_PENDING",
                reason,
                float(row["close"]),
                {
                    "candidate21_flow_carry_plan": asdict(plan),
                    "before_funding": before_funding,
                    "horizon_reached": horizon_reached,
                    "evaluation_ended": evaluation_ended,
                },
            )


__all__ = [
    "Candidate21FlowCarryConfig",
    "Candidate21FlowCarryStrategy",
]
