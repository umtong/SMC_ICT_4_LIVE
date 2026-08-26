"""Runtime-safe binding for confirmed persistent continuation.

The parent policy is unchanged.  This file maps its internal waiting state to
the existing audited SetupState.WAITING_ACCEPTANCE_RETEST enum and binds a
five-minute source span to formation-wave objectives because PriceZone is an
execution footprint rather than a StructureZone and intentionally carries no
machine-pivot span.
"""
from __future__ import annotations

from typing import Any

from contracts_v5 import ObjectKind, SetupState, StructureFamily, StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_persistent_confirmed import (
    ConfirmedPersistentContinuationEngine,
    EasyChartRE1ConfirmedPersistentBundle,
    PERSISTENT_CONFIRMED_RESPONSE_RULE,
    PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
    PersistentObjectiveKind,
)
from easychart_re1_persistent_continuation import (
    PersistentContinuationMarketStrategy,
    PersistentContinuationSetup,
)
from easychart_zones import ZoneSide


class FixedConfirmedPersistentContinuationEngine(ConfirmedPersistentContinuationEngine):
    def _formation_objective(
        self,
        setup: PersistentContinuationSetup,
        time_ns: int,
    ) -> tuple[StructureZone, float]:
        price = setup.source_zone.impulse_extreme
        if setup.side is Side.LONG:
            side = ZoneSide.RESISTANCE
            kind = ObjectKind.HORIZONTAL_RESISTANCE
            lower, upper = price, price + self.tick_size
            invalidation = upper + self.tick_size
        else:
            side = ZoneSide.SUPPORT
            kind = ObjectKind.HORIZONTAL_SUPPORT
            lower, upper = price - self.tick_size, price
            invalidation = lower - self.tick_size
        source_id = f"{setup.source_zone.zone_id}:FORMATION_WAVE_EXTREME"
        return (
            StructureZone(
                zone_id=f"{source_id}:SNAP:{time_ns}",
                kind=PersistentObjectiveKind.FORMATION_WAVE_EXTREME,
                family=StructureFamily.HORIZONTAL,
                side=side,
                timeframe_minutes=setup.source_zone.timeframe_minutes,
                lower=lower,
                upper=upper,
                invalidation=invalidation,
                impulse_extreme=price,
                formed_index=setup.source_zone.formed_index,
                formed_time_ns=setup.source_zone.formed_time_ns,
                observed_time_ns=setup.source_zone.observed_time_ns,
                formation_indices=setup.source_zone.formation_indices,
                strength_ratio=setup.source_zone.strength_ratio,
                source_structure_id=source_id,
                source_pivot_span=2,
            ),
            price,
        )

    def _advance_setups(self, bar: Candle) -> list[V5TradePlan]:
        output: list[V5TradePlan] = []
        observation = self._current_flow
        for setup in list(self._active.values()):
            if not self._setup_context_survives(setup):
                self._finish(
                    setup,
                    "persistent_continuation_common_regime_changed",
                    bar.ts_close_ns,
                    regime=self.common_snapshot.regime.value,
                    latest_side=None if self.common_snapshot.side is None else self.common_snapshot.side.name,
                )
                continue
            if bar.ts_close_ns <= setup.source_zone.observed_time_ns:
                continue
            if self._target_touched(setup, bar):
                self._finish(setup, "persistent_continuation_target_spent_before_entry", bar.ts_close_ns)
                continue
            if self._stop_touched(setup, bar):
                self._finish(setup, "persistent_continuation_source_invalidated_before_entry", bar.ts_close_ns)
                continue

            if setup.first_touch_time_ns is None:
                touched = bar.low <= setup.source_zone.upper and bar.high >= setup.source_zone.lower
                if not touched:
                    continue
                setup.first_touch_time_ns = bar.ts_close_ns
                setup.touch_high = bar.high
                setup.touch_low = bar.low
                setup.state = SetupState.WAITING_ACCEPTANCE_RETEST
                self._inc("persistent_continuation_first_touch_armed")
                self._trace(
                    "persistent_continuation_first_touch_armed",
                    bar.ts_close_ns,
                    setup_id=setup.setup_id,
                    side=setup.side.name,
                    touch_high=bar.high,
                    touch_low=bar.low,
                    touch_close=bar.close,
                    rule_provenance=PERSISTENT_CONFIRMED_RESPONSE_RULE,
                )
                continue

            if bar.ts_close_ns <= setup.first_touch_time_ns:
                continue
            mechanism = self._response_mechanism(setup, bar, observation)
            if mechanism is None:
                self._inc("persistent_continuation_waiting_control_transfer")
                continue
            plan = self._make_plan(setup, bar, mechanism)
            if plan is None:
                self._finish(setup, "persistent_continuation_no_trade_geometry", bar.ts_close_ns)
                continue
            self._finish(
                setup,
                "persistent_continuation_planned",
                bar.ts_close_ns,
                plan_id=plan.plan_id,
                response_mechanism=mechanism,
            )
            output.append(plan)
        return output


class EasyChartRE1FixedConfirmedPersistentBundle(EasyChartRE1ConfirmedPersistentBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.persistent_continuation = FixedConfirmedPersistentContinuationEngine(
            symbol,
            tick_size,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["persistent_continuation"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["runtime_state_binding"] = "WAITING_ACCEPTANCE_RETEST"
        output["formation_objective_source_span"] = 2
        output["confirmed_persistent_rules"] = (
            PERSISTENT_CONFIRMED_RESPONSE_RULE,
            PERSISTENT_FIRST_ELIGIBLE_OBJECTIVE_RULE,
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1FixedConfirmedPersistentBundle
StrategyClass = PersistentContinuationMarketStrategy
