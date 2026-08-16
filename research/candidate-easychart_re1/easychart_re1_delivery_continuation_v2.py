"""Complete first-obstacle geometry for matching-scale continuation.

The immutable objective is not limited to the footprint's displacement extreme
or coarse five/fifteen-minute pivots.  At entry a skilled chart trader also sees
already-confirmed local wave highs/lows and untouched opposing OB/FVG footprints.
Skipping either to preserve a larger planned R is a geometry error.

This engine builds only the two causal one-minute facts needed for that decision:

* a span-6 confirmed pivot book for significant local wave extremes;
* the existing >=2x OB/FVG detector for untouched opposing footprints.

The nearest positive-reward candidate among those facts, the formation-wave
extreme and the 5m/15m structure is selected first.  The inherited one-R rule is
then applied to that true first obstacle.  A current-bar touch spends an
objective before the plan can use it.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import StructureZone
from domain import Candle, Side
from easychart_re1_delivery_continuation import (
    ACTIVE_CONTROL_TRANSFER_RESPONSE_RULE,
    DeliveryContinuationEngine,
    TRUE_FIRST_OBSTACLE_RULE,
)
from easychart_re1_efficient_objective import PivotOnlyObjectiveBook
from easychart_re1_persistent_confirmed import PersistentContinuationSetup
from easychart_re1_rejection_micro_target_v2 import (
    StructureZoneMicroFootprintTargetMixin,
)
from easychart_zones import EasyChartZoneDetector, ZoneSide


COMPLETE_MICRO_FIRST_OBSTACLE_RULE = (
    "SOURCE_EXPLICIT:"
    "THE_FIRST_CONTINUATION_OBJECTIVE_INCLUDES_PREEXISTING_UNTOUCHED_HIGH_QUALITY_ONE_MINUTE_OB_FVG_AND_CAUSALLY_CONFIRMED_SIGNIFICANT_LOCAL_WAVE_EXTREMES"
)
if COMPLETE_MICRO_FIRST_OBSTACLE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (COMPLETE_MICRO_FIRST_OBSTACLE_RULE,)


class DeliveryContinuationEngineV2(DeliveryContinuationEngine):
    """Delivery continuation whose full objective is the actual first obstacle."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.micro_footprints = EasyChartZoneDetector(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
        )
        self.micro_waves = PivotOnlyObjectiveBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(6,),
        )
        self._complete_objective_counts: dict[str, int] = {}

    def _coinc(self, key: str) -> None:
        self._complete_objective_counts[key] = (
            self._complete_objective_counts.get(key, 0) + 1
        )

    def _micro_footprint_candidate(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
    ) -> tuple[StructureZone, float] | None:
        wanted = (
            ZoneSide.RESISTANCE
            if setup.side is Side.LONG
            else ZoneSide.SUPPORT
        )
        candidates = []
        for zone in self.micro_footprints.zones:
            if (
                zone.side is not wanted
                or not zone.high_quality_by_size
                or not zone.active
                or zone.first_touch_time_ns is not None
                or zone.observed_time_ns >= bar.ts_close_ns
            ):
                continue
            if setup.side is Side.LONG:
                if zone.lower <= bar.high:
                    continue
                price = zone.lower
            else:
                if zone.upper >= bar.low:
                    continue
                price = zone.upper
            candidates.append((zone, price))
        if not candidates:
            return None
        source, price = (
            min(
                candidates,
                key=lambda item: (
                    item[1],
                    item[0].observed_time_ns,
                    item[0].zone_id,
                ),
            )
            if setup.side is Side.LONG
            else max(
                candidates,
                key=lambda item: (
                    item[1],
                    -item[0].observed_time_ns,
                    item[0].zone_id,
                ),
            )
        )
        return (
            StructureZoneMicroFootprintTargetMixin._target_snapshot(
                source,
                bar.ts_close_ns,
            ),
            price,
        )

    def _micro_wave_candidate(
        self,
        setup: PersistentContinuationSetup,
        bar: Candle,
    ) -> tuple[StructureZone, float] | None:
        return self.micro_waves.target_for(
            setup.side,
            interaction_time_ns=bar.ts_close_ns,
            source_span=6,
            current_high=bar.high,
            current_low=bar.low,
        )

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

        raw: list[tuple[str, StructureZone, float]] = []
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
            raw.append(
                ("FORMATION_WAVE_EXTREME", formation_zone, formation_price)
            )
        elif formation_reward > 0.0:
            self._coinc("formation_wave_spent_before_entry")

        structural = self._nearest_target(
            setup.side,
            time_ns=bar.ts_close_ns,
            high=bar.high,
            low=bar.low,
        )
        if structural is not None:
            raw.append(
                ("FIVE_FIFTEEN_STRUCTURE", structural[0], structural[1])
            )
        footprint = self._micro_footprint_candidate(setup, bar)
        if footprint is not None:
            raw.append(("ONE_MINUTE_OB_FVG", footprint[0], footprint[1]))
        wave = self._micro_wave_candidate(setup, bar)
        if wave is not None:
            raw.append(("ONE_MINUTE_SPAN6_WAVE", wave[0], wave[1]))

        candidates: list[tuple[str, StructureZone, float]] = []
        seen_ids: set[str] = set()
        seen_prices: list[float] = []
        for source, zone, price in raw:
            reward = (
                price - entry
                if setup.side is Side.LONG
                else entry - price
            )
            if reward <= 0.0:
                continue
            source_id = zone.source_structure_id
            if source_id in seen_ids or any(
                abs(price - value) <= self.tick_size * 0.5
                for value in seen_prices
            ):
                continue
            seen_ids.add(source_id)
            seen_prices.append(price)
            candidates.append((source, zone, price))
        if not candidates:
            self._coinc("no_positive_unspent_obstacle")
            return None

        source, zone, price = (
            min(candidates, key=lambda item: (item[2], item[0]))
            if setup.side is Side.LONG
            else max(candidates, key=lambda item: (item[2], item[0]))
        )
        reward = price - entry if setup.side is Side.LONG else entry - price
        gross_rr = reward / risk
        self._audit(zone)
        self._trace(
            "complete_micro_first_obstacle_selected",
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
                    "source": item[0],
                    "zone_id": item[1].zone_id,
                    "price": item[2],
                }
                for item in candidates
            ],
            rule_provenance=(
                TRUE_FIRST_OBSTACLE_RULE,
                COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
            ),
        )
        if gross_rr + 1e-12 < self.minimum_gross_rr:
            self._coinc("true_first_micro_obstacle_below_one_r")
            return None
        self._coinc(f"objective_{source.lower()}_selected")
        return zone, price, gross_rr

    def on_bar(
        self,
        timeframe_minutes: int,
        bar: Candle,
    ) -> list[Any]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        # Register and spend micro objectives through the current completed bar
        # before entry geometry is evaluated.
        self.micro_footprints.on_bar(bar)
        self.micro_waves.on_bar(bar)
        plans = super().on_bar(timeframe_minutes, bar)
        self.micro_waves.observe_price(bar)
        return plans

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["complete_micro_first_obstacle"] = {
            "counts": dict(sorted(self._complete_objective_counts.items())),
            "footprints": dict(self.micro_footprints.diagnostics),
            "waves": dict(self.micro_waves.diagnostics),
            "rules": (
                TRUE_FIRST_OBSTACLE_RULE,
                COMPLETE_MICRO_FIRST_OBSTACLE_RULE,
                ACTIVE_CONTROL_TRANSFER_RESPONSE_RULE,
            ),
        }
        return output
