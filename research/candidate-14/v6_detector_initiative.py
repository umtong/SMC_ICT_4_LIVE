"""Detector-transition ownership for Candidate 14 V6.1.

A completed FAR/AAC transition is market-state evidence even when the original
passive entry is not executable after costs or has already moved away. Requiring
a trade-ready SCDAM plan to create initiative couples the pattern detector back
to one entry model and made V6 almost inert.

This router accepts only completed detector events which also win the frozen
cross-market ownership gate. It delegates lifecycle observation to the existing
GlobalInitiativeRouter and changes no orders, fills, fees, risk sizing, margin,
position accounting, or NAV calculation.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

from global_initiative_continuation import (
    GlobalInitiativeRouter,
    InitiativeState,
)
from logic import Direction, ResearchEvent

CONFIRMED_TRANSITIONS = frozenset({"FAR_CONFIRMED", "AAC_CONFIRMED"})


class DetectorInitiativeRouter(GlobalInitiativeRouter):
    """Activate global initiative from an owned completed detector transition."""

    @staticmethod
    def transition_direction(event: ResearchEvent) -> Direction | None:
        side = str(event.details.get("draw_side", "")).upper()
        if side == "HIGH":
            return Direction.LONG
        if side == "LOW":
            return Direction.SHORT
        return None

    @staticmethod
    def transition_scenario(event: ResearchEvent) -> str | None:
        event_type = str(event.event_type)
        if event_type not in CONFIRMED_TRANSITIONS:
            return None
        return event_type.removesuffix("_CONFIRMED")

    @staticmethod
    def _price(value: Any) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0.0 and isfinite(parsed) else None

    def observe_confirmed_event(
        self,
        *,
        event: ResearchEvent,
        symbol: str,
        leadership: Mapping[str, Any],
        observed_ts_ns: int,
    ) -> InitiativeState | None:
        scenario = self.transition_scenario(event)
        direction = self.transition_direction(event)
        boundary = self._price(event.reference_price)
        target = self._price(event.details.get("target"))
        if scenario is None or direction is None:
            self.skips["UNSUPPORTED_INITIATIVE_TRANSITION"] += 1
            return None
        if boundary is None or target is None:
            self.skips["INITIATIVE_TRANSITION_PRICE_MISSING"] += 1
            return None
        if direction is Direction.LONG:
            causal_order = target > boundary
        else:
            causal_order = target < boundary
        if not causal_order:
            self.skips["INITIATIVE_TRANSITION_TARGET_WRONG_SIDE"] += 1
            return None

        current = self._state
        source_id = str(event.scenario_id)
        if current is not None and current.source_plan_id == source_id:
            return current
        if current is not None:
            self._event(
                scenario_id=current.scenario_id,
                event_type="GLOBAL_INITIATIVE_TERMINATED",
                event_time_ns=int(observed_ts_ns),
                observed_time_ns=int(observed_ts_ns),
                previous_state="ACTIVE",
                next_state="TERMINAL",
                reason_code="FRESH_OWNED_DETECTOR_TRANSFER",
                reference_price=boundary,
                details={
                    "old_source_scenario_id": current.source_plan_id,
                    "old_source_symbol": current.source_symbol,
                    "old_direction": current.direction.value,
                    "new_source_scenario_id": source_id,
                    "new_source_symbol": symbol,
                    "new_direction": direction.value,
                },
            )

        self._sequence += 1
        state_id = (
            f"GI-{int(observed_ts_ns)}-{self._sequence:06d}-"
            f"{symbol}-{direction.value}"
        )
        state = InitiativeState(
            scenario_id=state_id,
            source_plan_id=source_id,
            source_symbol=symbol,
            direction=direction,
            source_level=boundary,
            target_level=target,
            activated_ts_ns=int(observed_ts_ns),
            source_scenario=scenario,
            leadership=dict(leadership),
        )
        self._state = state
        self._event(
            scenario_id=state.scenario_id,
            event_type="GLOBAL_INITIATIVE_ACTIVATED",
            event_time_ns=int(event.event_time_ns),
            observed_time_ns=int(observed_ts_ns),
            previous_state="IDLE",
            next_state="ACTIVE",
            reason_code="OWNED_COMPLETED_DETECTOR_TRANSITION",
            reference_price=boundary,
            details={
                "source_scenario_id": source_id,
                "source_symbol": symbol,
                "source_scenario": scenario,
                "direction": direction.value,
                "source_level": boundary,
                "target_level": target,
                "detector_reason_code": event.reason_code,
                "detector_observed_time_ns": int(event.observed_time_ns),
                "leadership": dict(leadership),
            },
        )
        return state
