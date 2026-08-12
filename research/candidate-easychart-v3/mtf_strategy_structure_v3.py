"""Structure-first EasyChart v3 binding with audited Nautilus order lifecycle."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import structure_runtime_v3  # noqa: F401 - applies the reused semantic repair
import mtf_strategy as _base
from contracts_v5 import V5TradePlan
from model import Side
from mtf_strategy_limit_v3 import (
    EasyChartMTFConfig,
    EasyChartMTFStrategy as _AuditedLifecycleStrategy,
)
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.orders.list import OrderList
from scenario_bundle_v5 import ResearchScenarioBundleV5


# The base strategy resolves this module global when it is instantiated.
_base.MultiScaleScenarioBundle = ResearchScenarioBundleV5


class EasyChartMTFStrategy(_AuditedLifecycleStrategy):
    """One structure-state policy, one four-symbol account, one global slot.

    The old v3 overlap/horizontal families are not combined as independent
    strategies here.  Confirmed horizontal pivots, wick trend lines and exact
    parallel channels establish context; interaction resolves rejection,
    acceptance, rotation, bounce or UNRESOLVED; OB/FVG are event-local
    confirmation objects where required.

    Entry occurs once, at the close of the first confirmed retest.  A market
    parent is therefore intentional rather than a chased substitute for a
    missed planned zone.  Stop and target are already immutable before the
    bracket is submitted.
    """

    def _submit_plan(self, instrument_id: InstrumentId, plan: V5TradePlan) -> bool:
        instrument = self.instruments[instrument_id]
        nav = self._current_nav()
        entry_slippage, stop_slippage = self._execution_reserves(instrument)
        quantity = self._quantity(instrument, plan, nav)
        if quantity is None:
            self._record(
                "plan_rejected_quantity",
                plan_id=plan.plan_id,
                instrument_id=str(instrument_id),
                nav_at_submission=float(nav),
                estimated_entry_slippage=float(entry_slippage),
                estimated_stop_slippage=float(stop_slippage),
            )
            return False

        plan_tag = f"PLAN:{plan.plan_id}"
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY if plan.side is Side.LONG else OrderSide.SELL,
            quantity=quantity,
            time_in_force=TimeInForce.GTC,
            entry_order_type=OrderType.MARKET,
            entry_post_only=False,
            sl_trigger_price=instrument.make_price(plan.stop),
            tp_price=instrument.make_price(plan.target),
            tp_post_only=False,
            emulation_trigger=TriggerType.NO_TRIGGER,
            entry_tags=[
                plan_tag,
                "ROLE:ENTRY",
                "POLICY:STRUCTURE_STATE_FIRST_RETEST_MARKET",
            ],
            sl_tags=[plan_tag, "ROLE:STOP_LOSS"],
            tp_tags=[plan_tag, "ROLE:TAKE_PROFIT"],
        )
        self.active_plan = plan
        self.active_instrument_id = instrument_id
        self.active_entry_id = order_list.first.client_order_id
        self.entry_cancel_requested = False
        self.emergency_exit_requested = False
        self._reset_trade_lifecycle()
        self.submit_order_list(order_list)
        self._record(
            "submitted",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            quantity=str(quantity),
            entry_client_order_id=str(self.active_entry_id),
            planned_entry=plan.entry,
            entry_policy="STRUCTURE_STATE_FIRST_RETEST_MARKET",
            nav_at_submission=float(nav),
            risk_budget=float(nav * Decimal(str(self.config.risk_fraction))),
            estimated_entry_slippage=float(entry_slippage),
            estimated_stop_slippage=float(stop_slippage),
        )
        return True

    def _flush_bar_bucket(self) -> None:
        if self.bar_bucket_ts is None:
            return
        plans: list[tuple[InstrumentId, V5TradePlan]] = []
        for instrument_id, timeframe, bar in sorted(
            self.bar_bucket,
            key=lambda item: (-item[1], str(item[0])),
        ):
            engine = self.scenario_engines[instrument_id]
            emitted = engine.on_bar(timeframe, self._candle(bar))
            for transition in engine.drain_trace():
                if transition.get("event_time_ns", 0) >= self.config.trading_start_ns:
                    self._record(
                        "scenario_transition",
                        instrument_id=str(instrument_id),
                        timeframe_minutes=timeframe,
                        **transition,
                    )
            if bar.ts_event < self.config.trading_start_ns:
                continue
            for plan in emitted:
                self.plan_log[plan.plan_id] = plan
                plans.append((instrument_id, plan))
                self._record("plan", **self._plan_event_values(plan))

        # Causal completion time and auction scale are the only semantic
        # tie-breakers.  Indicator count, footprint labels and outcome-derived
        # scores do not receive priority.
        ranked = sorted(
            plans,
            key=lambda item: (
                item[1].interaction_time_ns,
                -item[1].higher_timeframe_minutes,
                item[1].setup_observed_time_ns,
                item[1].symbol,
                item[1].plan_id,
            ),
        )
        if ranked:
            if self.active_plan is not None or not self._portfolio_flat():
                for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                    self._record(
                        "plan_skipped_global_slot",
                        plan_id=plan.plan_id,
                        instrument_id=str(instrument_id),
                        arbitration_rank=rank,
                        active_plan_id=None if self.active_plan is None else self.active_plan.plan_id,
                        portfolio_flat=self._portfolio_flat(),
                    )
            else:
                selected_index: int | None = None
                for index, (instrument_id, plan) in enumerate(ranked):
                    if self._submit_plan(instrument_id, plan):
                        selected_index = index
                        self._record(
                            "arbitration_selected",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=index + 1,
                            candidates=len(ranked),
                        )
                        break
                if selected_index is not None:
                    selected_plan_id = ranked[selected_index][1].plan_id
                    for rank, (instrument_id, plan) in enumerate(ranked, start=1):
                        if rank - 1 <= selected_index:
                            continue
                        self._record(
                            "plan_skipped_arbitration",
                            plan_id=plan.plan_id,
                            instrument_id=str(instrument_id),
                            arbitration_rank=rank,
                            selected_plan_id=selected_plan_id,
                        )

        self.bar_bucket.clear()
        self.bar_bucket_seen.clear()
        self.bar_bucket_ts = None
