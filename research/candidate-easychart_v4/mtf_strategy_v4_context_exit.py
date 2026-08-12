"""Causal active-position exit on a confirmed opposite 1h structure event.

EasyChart explicitly treats the appearance of an opposite order block or other
opposite market structure as a reason to close or take profit, and says a
failed premise must be cut rather than defended.  The frozen v4 entry policy
already maintains a live 1h structural event, but the submitted native bracket
ignored later context changes and could remain open until an old stop/target
for hours or days.

This overlay introduces no price threshold, timeout, score or look-ahead.  It
closes the one global position only when the same instrument receives a later,
fully confirmed live 1h event in the opposite direction.  Pending or failed
acceptance retests do not qualify.  Native NautilusTrader orders, fills, fees
and account state remain authoritative.
"""
from __future__ import annotations

from typing import Any

from mtf_strategy_v4 import EasyChartMTFConfig, EasyChartMTFStrategy
from mtf_strategy_v4_scale_execution import (
    DualScaleFirstAvailableEasyChartStrategy,
    MacroOnlyEasyChartStrategy,
)


class OppositeContextExitMixin:
    """Terminate a trade when its confirmed 1h directional premise reverses."""

    CONFIRMED_CONTEXT_TRANSITIONS = frozenset(
        {
            "context_structural_event_activated",
            "context_confirmed_fakeout_activated",
            "context_acceptance_first_retest_confirmed",
        },
    )

    def __init__(self, *args, **kwargs) -> None:
        self.context_exit_requested = False
        self.context_exit_event_id: str | None = None
        super().__init__(*args, **kwargs)

    def _record(self, kind: str, **values: Any) -> None:
        super()._record(kind, **values)
        if kind == "scenario_transition":
            self._maybe_exit_on_opposite_context(values)

    def _maybe_exit_on_opposite_context(self, transition: dict[str, Any]) -> None:
        if self.active_plan is None or self.active_instrument_id is None:
            return
        if self.context_exit_requested:
            return
        if transition.get("scale_name") != "MACRO":
            return
        if transition.get("scenario_kind") not in self.CONFIRMED_CONTEXT_TRANSITIONS:
            return
        if transition.get("instrument_id") != str(self.active_instrument_id):
            return
        context_side = transition.get("context_side")
        if context_side is None or context_side == self.active_plan.side.name:
            return

        self.context_exit_requested = True
        self.context_exit_event_id = transition.get("event_id")
        portfolio_flat = self.portfolio.is_flat(self.active_instrument_id)
        # Bypass this mixin's _record hook to avoid recursion.
        super()._record(
            "opposite_context_exit_requested",
            plan_id=self.active_plan.plan_id,
            instrument_id=str(self.active_instrument_id),
            plan_side=self.active_plan.side.name,
            replacement_context_side=context_side,
            replacement_context_path=transition.get("context_path"),
            replacement_context_structure_kind=transition.get(
                "context_structure_kind",
            ),
            replacement_context_event_id=self.context_exit_event_id,
            replacement_context_time_ns=transition.get("event_time_ns"),
            portfolio_flat=portfolio_flat,
        )
        self.cancel_all_orders(self.active_instrument_id)
        if not portfolio_flat:
            self.close_all_positions(self.active_instrument_id)

    @staticmethod
    def _order_type_name(order_type: Any) -> str:
        name = getattr(order_type, "name", None)
        return str(order_type) if name is None else str(name)

    def on_order_filled(self, event) -> None:
        """Persist the exact untagged market-close ID for audit reconciliation.

        ``Strategy.close_all_positions`` creates a native market closing order
        without the bracket's ``ROLE:*`` tag.  A protective stop/target racing
        the cancellation request must retain its own native role, so only a
        non-entry MARKET fill while the context-exit request is live receives
        this classification.
        """
        is_context_market_exit = (
            self.context_exit_requested
            and self.active_plan is not None
            and self.active_instrument_id is not None
            and event.client_order_id != self.active_entry_id
            and str(event.instrument_id) == str(self.active_instrument_id)
            and self._order_type_name(event.order_type) == "MARKET"
        )
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        replacement_event_id = self.context_exit_event_id
        super().on_order_filled(event)
        if is_context_market_exit:
            super()._record(
                "context_exit_order_filled",
                plan_id=plan_id,
                instrument_id=str(event.instrument_id),
                client_order_id=str(event.client_order_id),
                venue_order_id=(
                    None
                    if event.venue_order_id is None
                    else str(event.venue_order_id)
                ),
                position_id=(
                    None if event.position_id is None else str(event.position_id)
                ),
                last_qty=str(event.last_qty),
                last_px=str(event.last_px),
                commission=str(event.commission),
                replacement_context_event_id=replacement_event_id,
                event_ts_ns=event.ts_event,
            )

    def _submit_plan(self, instrument_id, plan) -> bool:
        submitted = super()._submit_plan(instrument_id, plan)
        if submitted:
            self.context_exit_requested = False
            self.context_exit_event_id = None
        return submitted

    def _reset_context_exit_if_inactive(self) -> None:
        if self.active_plan is None:
            self.context_exit_requested = False
            self.context_exit_event_id = None

    def on_order_canceled(self, event) -> None:
        super().on_order_canceled(event)
        self._reset_context_exit_if_inactive()

    def on_order_expired(self, event) -> None:
        super().on_order_expired(event)
        self._reset_context_exit_if_inactive()

    def on_order_rejected(self, event) -> None:
        super().on_order_rejected(event)
        self._reset_context_exit_if_inactive()

    def on_order_denied(self, event) -> None:
        super().on_order_denied(event)
        self._reset_context_exit_if_inactive()

    def on_position_closed(self, event) -> None:
        exit_event_id = self.context_exit_event_id
        plan_id = None if self.active_plan is None else self.active_plan.plan_id
        super().on_position_closed(event)
        if exit_event_id is not None:
            super()._record(
                "opposite_context_exit_completed",
                plan_id=plan_id,
                instrument_id=str(event.instrument_id),
                replacement_context_event_id=exit_event_id,
            )
        self.context_exit_requested = False
        self.context_exit_event_id = None


class OppositeContextExitMicroStrategy(
    OppositeContextExitMixin,
    EasyChartMTFStrategy,
):
    """Existing MICRO execution plus confirmed-opposite 1h exit."""


class OppositeContextExitMacroStrategy(
    OppositeContextExitMixin,
    MacroOnlyEasyChartStrategy,
):
    """Existing MACRO execution plus confirmed-opposite 1h exit."""


class OppositeContextExitDualStrategy(
    OppositeContextExitMixin,
    DualScaleFirstAvailableEasyChartStrategy,
):
    """Existing dual execution plus confirmed-opposite 1h exit."""


__all__ = [
    "EasyChartMTFConfig",
    "OppositeContextExitDualStrategy",
    "OppositeContextExitMacroStrategy",
    "OppositeContextExitMicroStrategy",
    "OppositeContextExitMixin",
]
