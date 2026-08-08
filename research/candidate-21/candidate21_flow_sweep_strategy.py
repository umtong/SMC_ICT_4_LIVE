"""GTC market-sweep execution for synchronized flow carry.

The parent signal, context, structural stop, 3% risk budget and four-hour exit
are unchanged.  Only the market parent changes from IOC to GTC so the remaining
risk-sized quantity can consume later 10-second bars instead of being canceled
at the first bar-volume boundary.  The inherited full-size reduce-only stop is
armed on the first partial fill; a stop or timed close cancels the entry remainder.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce

from candidate21_flow_carry_strategy import Candidate21FlowCarryConfig
from candidate21_flow_carry_strategy import Candidate21FlowCarryStrategy
from flow_carry_logic import build_flow_carry_plan
from logic import floor_quantity
from logic import planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate21FlowSweepConfig(Candidate21FlowCarryConfig, frozen=True):
    """No new fitted parameters."""


class Candidate21FlowSweepStrategy(Candidate21FlowCarryStrategy):
    """Keep the market parent active until full, stopped, or time-canceled."""

    def __init__(self, config: Candidate21FlowSweepConfig) -> None:
        super().__init__(config=config)
        self.diagnostics["candidate21_flow_sweep_gtc_entries"] = 0

    def _submit_event_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
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
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            tags=["ENTRY", "CANDIDATE21_SYNC_FLOW_SWEEP_GTC"],
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
        self.diagnostics["candidate21_flow_sweep_gtc_entries"] = int(
            self.diagnostics["candidate21_flow_sweep_gtc_entries"],
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
                "entry_time_in_force": "GTC",
                "execution_policy": (
                    "CONSUME_SUCCESSIVE_EXTERNAL_10S_BAR_VOLUME_UNTIL_FULL"
                ),
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


__all__ = [
    "Candidate21FlowSweepConfig",
    "Candidate21FlowSweepStrategy",
]
