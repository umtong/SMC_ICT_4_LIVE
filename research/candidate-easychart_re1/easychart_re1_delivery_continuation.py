"""Strict first-obstacle continuation for matching-scale delivery.

The first confirmed-continuation implementation evaluated every candidate
objective independently against the one-R minimum and then selected among the
survivors.  That could skip a real nearer obstacle offering less than one R and
trade toward a farther formation high.  A discretionary trader cannot erase the
first resistance merely because a more distant target produces a nicer planned
ratio.

This engine first identifies the nearest still-unspent positive-reward obstacle
between the displacement-wave extreme and the causal 5m/15m structure.  Only
then is its gross R evaluated; if the true first obstacle offers less than one R,
there is no trade.  The later response also requires above-baseline completed
one-minute activity, directed taker imbalance and a close beyond the first-touch
extreme.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import StructureZone
from domain import Candle, Side
from easychart_re1_persistent_confirmed import (
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
    PersistentContinuationSetup,
)
from easychart_re1_persistent_confirmed_fixed import (
    FixedConfirmedPersistentContinuationEngine,
)


TRUE_FIRST_OBSTACLE_RULE = (
    "SOURCE_EXPLICIT:"
    "CONTINUATION_SELECTS_THE_NEAREST_STILL_UNSPENT_FORMATION_OR_STRUCTURE_OBSTACLE_BEFORE_TESTING_THE_ONE_R_MINIMUM"
)
ACTIVE_CONTROL_TRANSFER_RESPONSE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CONTINUATION_RESPONSE_REQUIRES_ABOVE_BASELINE_ACTIVITY_DIRECTED_TAKER_FLOW_AND_A_CLOSE_BEYOND_THE_FIRST_TOUCH_EXTREME"
)
for _rule in (
    TRUE_FIRST_OBSTACLE_RULE,
    ACTIVE_CONTROL_TRANSFER_RESPONSE_RULE,
):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


class DeliveryContinuationEngine(FixedConfirmedPersistentContinuationEngine):
    """Confirmed five-minute rebalance whose real first obstacle owns geometry."""

    def _formation_objective_unspent(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        price: float,
    ) -> bool:
        for item in self.footprints.bars:
            if not (
                setup.source_zone.observed_time_ns
                < item.ts_close_ns
                <= bar.ts_close_ns
            ):
                continue
            if setup.side is Side.LONG and item.high >= price:
                return False
            if setup.side is Side.SHORT and item.low <= price:
                return False
        return True

    def _select_entry_objective(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        entry: float,
        stop: float,
    ) -> tuple[StructureZone, float, float] | None:
        risk = entry - stop if setup.side is Side.LONG else stop - entry
        if risk <= 0.0:
            return None

        candidates: list[tuple[str, StructureZone, float]] = []
        formation_zone, formation_price = self._formation_objective(
            setup,
            bar.ts_close_ns,
        )
        formation_reward = (
            formation_price - entry
            if setup.side is Side.LONG
            else entry - formation_price
        )
        if (
            formation_reward > 0.0
            and self._formation_objective_unspent(
                setup,
                bar,
                formation_price,
            )
        ):
            candidates.append(
                ("FORMATION_WAVE_EXTREME", formation_zone, formation_price)
            )
        elif formation_reward > 0.0:
            self._inc("formation_wave_objective_spent_before_entry")

        structural = self._nearest_target(
            setup.side,
            time_ns=bar.ts_close_ns,
            high=bar.high,
            low=bar.low,
        )
        if structural is not None:
            candidates.append(
                ("PREEXISTING_STRUCTURE", structural[0], structural[1])
            )
        if not candidates:
            self._inc("no_unspent_positive_first_obstacle")
            return None

        selected = (
            min(candidates, key=lambda item: (item[2], item[0]))
            if setup.side is Side.LONG
            else max(candidates, key=lambda item: (item[2], item[0]))
        )
        source, zone, price = selected
        reward = price - entry if setup.side is Side.LONG else entry - price
        gross_rr = reward / risk
        self._audit(zone)
        self._trace(
            "true_first_continuation_obstacle_selected",
            bar.ts_close_ns,
            setup_id=setup.setup_id,
            side=setup.side.name,
            entry=entry,
            stop=stop,
            selected_source=source,
            selected_zone_id=zone.zone_id,
            selected_price=price,
            selected_gross_rr=gross_rr,
            candidates=[
                {
                    "source": candidate[0],
                    "zone_id": candidate[1].zone_id,
                    "price": candidate[2],
                }
                for candidate in candidates
            ],
            rule_provenance=(
                PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
                TRUE_FIRST_OBSTACLE_RULE,
            ),
        )
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._inc("true_first_obstacle_below_minimum_gross_rr")
            return None
        self._inc(f"true_first_obstacle_{source.lower()}_selected")
        return zone, price, gross_rr

    def _response_mechanism(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
        observation: Any,
    ) -> str | None:
        if (
            observation is None
            or not observation.active
            or not observation.directed
        ):
            return None
        assert setup.touch_high is not None and setup.touch_low is not None
        price_confirms = (
            bar.close > setup.touch_high
            if setup.side is Side.LONG
            else bar.close < setup.touch_low
        )
        body_confirms = (
            bar.close > bar.open
            if setup.side is Side.LONG
            else bar.close < bar.open
        )
        if not price_confirms or not body_confirms:
            return None
        if (
            observation.material_progress
            and self._aligned(
                setup.side,
                observation.signed_taker_quote,
            )
        ):
            return "ACTIVE_CONFIRMED_REINITIATIVE"
        if self._opposite_delta(
            setup.side,
            observation.signed_taker_quote,
        ):
            return "ACTIVE_CONFIRMED_ADVERSE_FLOW_ABSORBED"
        return None

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["delivery_continuation_geometry"] = {
            "target": "TRUE_NEAREST_UNSPENT_OBSTACLE_THEN_ONE_R_TEST",
            "response": "ACTIVE_DIRECTED_FLOW_AND_CLOSE_BEYOND_FIRST_TOUCH_EXTREME",
            "rules": (
                PERSISTENT_CONFIRMED_RESPONSE_RULE,
                TRUE_FIRST_OBSTACLE_RULE,
                ACTIVE_CONTROL_TRANSFER_RESPONSE_RULE,
            ),
        }
        return output
