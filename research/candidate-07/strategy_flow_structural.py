"""Delayed-entry validation for structural sweep stops.

The base flow strategy rejected a pending plan when its structural stop was
wider than an auxiliary ATR ceiling. This subclass retains causality and the
minimum stop geometry, but delegates the actual bracket, 3% NAV sizing, fees,
slippage and funding reserve to the existing Nautilus execution strategy.
"""
from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.data import Bar

from model import Direction, TradePlan
from strategy_flow import Candidate07FlowStrategy


class StructuralStopFlowStrategy(Candidate07FlowStrategy):
    def _submit_pending(self, bar: Bar) -> None:
        plan: TradePlan | None = self._pending_plan
        if plan is None:
            return
        raw_atr = plan.details.get("atr")
        if raw_atr is None:
            self._invalidate_pending("FLOW_PLAN_ATR_MISSING", int(bar.ts_event))
            return
        atr = Decimal(str(raw_atr))
        current = Decimal(str(bar.close.as_double()))
        stop = Decimal(str(plan.stop_price))
        risk = current - stop if plan.direction is Direction.LONG else stop - current
        if atr <= 0 or risk <= 0:
            self._invalidate_pending(
                "FLOW_DELAYED_ENTRY_GEOMETRY_INVALID",
                int(bar.ts_event),
            )
            return
        risk_atr = risk / atr
        if risk_atr < Decimal(str(self.logic.minimum_stop_atr)):
            self._invalidate_pending(
                "FLOW_DELAYED_ENTRY_STOP_TOO_TIGHT",
                int(bar.ts_event),
            )
            return
        # Skip Candidate07FlowStrategy's duplicate maximum-stop veto and call
        # the already validated Nautilus execution implementation directly.
        super(Candidate07FlowStrategy, self)._submit_pending(bar)


__all__ = ["StructuralStopFlowStrategy"]
