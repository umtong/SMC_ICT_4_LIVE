#!/usr/bin/env python3
"""Resolved-impact state machine with acceptance-defined continuation stops.

The initiative detector, three-event response window, failure precedence,
confirmation boundary and targets are unchanged from candidate v17.  The only
candidate variable is continuation invalidation: once durable acceptance is
proved by at least two completed outside-value response events, the protected
swing is defined by the extreme of those accepted response events rather than
by the entire initiative-plus-response path.

This is a structural distinction, not a fitted stop distance.  A continuation
thesis is invalid when the response events which established accepted value are
lost.  The inherited 0.15 ATR structural buffer remains unchanged.  Reversal
plans retain their original full failed-auction path stop.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from core import Side
from impact_regime_probe import EventFeature, ScenarioPlan
from impact_resolution_candidate import (
    STOP_BUFFER_ATR,
    ImpactResolutionStateMachine,
)


@dataclass(frozen=True, slots=True)
class ProtectedSwingStopDiagnostic:
    scenario_id: str
    source_scenario_id: str
    side: str
    accepted_event_count: int
    accepted_low: float
    accepted_high: float
    atr: float
    original_stop: float
    protected_stop: float
    stop_distance_reduction: float


def protected_continuation_stop(
    *,
    side: Side,
    accepted_lows: list[float],
    accepted_highs: list[float],
    atr: float,
) -> float:
    """Return the buffered protected swing of completed accepted events."""

    if len(accepted_lows) != len(accepted_highs):
        raise ValueError("accepted low/high observations must align")
    if len(accepted_lows) < 2:
        raise ValueError("durable acceptance requires at least two events")
    if atr <= 0.0:
        raise ValueError("ATR must be positive")
    if side is Side.LONG:
        return min(accepted_lows) - STOP_BUFFER_ATR * atr
    return max(accepted_highs) + STOP_BUFFER_ATR * atr


class AcceptedSwingResolutionStateMachine(ImpactResolutionStateMachine):
    """Use accepted-response protected swings for continuation invalidation."""

    def __init__(self) -> None:
        super().__init__()
        self._accepted_lows: dict[str, list[float]] = {}
        self._accepted_highs: dict[str, list[float]] = {}
        self.stop_diagnostics: list[ProtectedSwingStopDiagnostic] = []

    def arm(self, plan: ScenarioPlan, *, atr: float, feature: EventFeature) -> None:
        super().arm(plan, atr=atr, feature=feature)
        if plan.response == "CONTINUATION":
            source = str(plan.scenario_id)
            self._accepted_lows[source] = []
            self._accepted_highs[source] = []

    @staticmethod
    def _source(plan: ScenarioPlan) -> str:
        marker = ":resolved-"
        if marker not in plan.scenario_id:
            raise ValueError(f"not a resolved-impact plan: {plan.scenario_id}")
        return plan.scenario_id.split(marker, 1)[0]

    def _replace_emitted_plan(self, old: ScenarioPlan, new: ScenarioPlan) -> None:
        for position in range(len(self.plans) - 1, -1, -1):
            if self.plans[position].scenario_id == old.scenario_id:
                self.plans[position] = new
                break
        else:
            raise RuntimeError(f"emitted plan not found: {old.scenario_id}")

        source = self._source(old)
        for position in range(len(self.transitions) - 1, -1, -1):
            row = self.transitions[position]
            if (
                str(row.scenario_id) == source
                and str(row.event_type) == "PLAN_EMITTED"
                and int(row.event_time_ns) == int(old.signal_time_ns)
            ):
                self.transitions[position] = replace(
                    row,
                    reason_code=new.reason_code,
                )
                break

    def on_feature(
        self,
        *,
        index: int,
        feature: EventFeature,
        new_initiative_plans: Iterable[ScenarioPlan] = (),
    ) -> list[ScenarioPlan]:
        # Record only completed response events which independently satisfy the
        # inherited outside-value acceptance test.  The current event is added
        # before the parent machine resolves the setup at its expiry index.
        for setup in list(self.active):
            if (
                index > setup.created_index
                and index <= setup.expiry_index
                and self._outside(setup, feature)
            ):
                source = str(setup.initiative_plan.scenario_id)
                self._accepted_lows.setdefault(source, []).append(
                    float(feature.bar.low),
                )
                self._accepted_highs.setdefault(source, []).append(
                    float(feature.bar.high),
                )

        emitted = super().on_feature(
            index=index,
            feature=feature,
            new_initiative_plans=new_initiative_plans,
        )
        result: list[ScenarioPlan] = []
        for plan in emitted:
            if plan.response != "CONTINUATION":
                result.append(plan)
                continue

            source = self._source(plan)
            lows = self._accepted_lows.get(source, [])
            highs = self._accepted_highs.get(source, [])
            # The inherited continuation plan stores the completed full-path
            # extreme in pulse_low/high and buffers that extreme by 0.15 ATR.
            # Recovering the same ATR is exact and avoids introducing a second
            # volatility estimate or parameter.
            if plan.side is Side.LONG:
                path_extreme = float(plan.pulse_low)
                inferred_atr = (
                    path_extreme - float(plan.stop_price)
                ) / STOP_BUFFER_ATR
            else:
                path_extreme = float(plan.pulse_high)
                inferred_atr = (
                    float(plan.stop_price) - path_extreme
                ) / STOP_BUFFER_ATR
            if inferred_atr <= 0.0:
                raise RuntimeError(
                    f"could not recover positive ATR for {plan.scenario_id}",
                )

            stop = protected_continuation_stop(
                side=plan.side,
                accepted_lows=lows,
                accepted_highs=highs,
                atr=inferred_atr,
            )
            # The protected swing can legitimately remain outside the original
            # confirmation boundary.  Its only required geometry is adverse to
            # the eventual entry and inside the unchanged target; Nautilus' own
            # viability check enforces that at submission time.
            replacement = replace(
                plan,
                stop_price=stop,
                reason_code="OUTSIDE_IMPACT_DURABLY_ACCEPTED_PROTECTED_SWING",
            )
            self._replace_emitted_plan(plan, replacement)
            original_distance = abs(
                float(plan.stop_price) - float(plan.confirmation_hold_price),
            )
            protected_distance = abs(
                float(stop) - float(plan.confirmation_hold_price),
            )
            self.stop_diagnostics.append(
                ProtectedSwingStopDiagnostic(
                    scenario_id=replacement.scenario_id,
                    source_scenario_id=source,
                    side=replacement.side.value,
                    accepted_event_count=len(lows),
                    accepted_low=min(lows),
                    accepted_high=max(highs),
                    atr=inferred_atr,
                    original_stop=float(plan.stop_price),
                    protected_stop=float(stop),
                    stop_distance_reduction=(
                        original_distance - protected_distance
                    ),
                ),
            )
            result.append(replacement)

        live_sources = {
            str(setup.initiative_plan.scenario_id) for setup in self.active
        }
        emitted_sources = {self._source(plan) for plan in result}
        for source in list(self._accepted_lows):
            if source not in live_sources and source not in emitted_sources:
                self._accepted_lows.pop(source, None)
                self._accepted_highs.pop(source, None)
        return result
