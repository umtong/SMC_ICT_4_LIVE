"""First-retest horizontal S/R-flip objectives for EasyChart RE1.

The supplied live day-trading case uses a large channel for context, but exits
at the nearer low created during the preceding decline because that broken low
can act as resistance on the retracement.  This is not the same as targeting
every small pivot.  It is a role transition:

* a close-broken swing LOW becomes resistance for a later long retracement;
* a close-broken swing HIGH becomes support for a later short retracement;
* only the first later retest remains a live objective.

RE1 therefore preserves the v20 structural channel/pivot objective and inserts
only a nearer causal S/R flip when one exists.  No fixed-R target, timer, fitted
lookback, partial exit, trailing rule or post-entry discretion is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ObjectKind, ScenarioPath, ScenarioSetup, StructureFamily, StructureZone
from diagonal_core_v20 import DiagonalCoreScenarioEngine
from domain import Candle, Side
from easychart_re1_fresh import EasyChartRE1FreshBundle
from easychart_zones import ZoneSide
from scenario_target_ablation_v5 import NearestAnyPivotStructureBook


HORIZONTAL_FLIP_OBJECTIVE_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "CLOSE_BROKEN_SWING_LOW_BECOMES_FIRST_RETEST_RESISTANCE_AND_HIGH_BECOMES_SUPPORT"
)
if HORIZONTAL_FLIP_OBJECTIVE_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (HORIZONTAL_FLIP_OBJECTIVE_RULE,)


TargetChoice = tuple[StructureZone, float, str | None, float | None]


@dataclass(slots=True)
class HorizontalFlip:
    flip_id: str
    source_pivot_id: str
    source_pivot_side: str
    side: ZoneSide
    price: float
    formed_index: int
    formed_time_ns: int
    observed_time_ns: int
    pivot_span: int
    strength_ratio: float
    consumed_time_ns: int | None = None

    @property
    def active(self) -> bool:
        return self.consumed_time_ns is None


class HorizontalFlipObjectiveBook(NearestAnyPivotStructureBook):
    """Causal close-break levels which survive only through their first retest."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._flips: dict[str, HorizontalFlip] = {}
        self._flip_source_ids: set[str] = set()

    def _register_flips(self, bar: Candle) -> None:
        if len(self.bars) < 2:
            return
        previous_close = self.bars[-2].close
        for pivot in self.pivots:
            if pivot.pivot_id in self._flip_source_ids:
                continue
            if pivot.observed_time_ns > bar.ts_close_ns:
                continue
            if (
                pivot.side == "LOW"
                and previous_close >= pivot.price
                and bar.close < pivot.price
            ):
                side = ZoneSide.RESISTANCE
                suffix = "LOW_TO_RESISTANCE"
            elif (
                pivot.side == "HIGH"
                and previous_close <= pivot.price
                and bar.close > pivot.price
            ):
                side = ZoneSide.SUPPORT
                suffix = "HIGH_TO_SUPPORT"
            else:
                continue
            flip_id = f"{pivot.pivot_id}:SR_FLIP:{suffix}"
            self._flip_source_ids.add(pivot.pivot_id)
            self._flips[flip_id] = HorizontalFlip(
                flip_id=flip_id,
                source_pivot_id=pivot.pivot_id,
                source_pivot_side=pivot.side,
                side=side,
                price=pivot.price,
                formed_index=pivot.index,
                formed_time_ns=pivot.event_time_ns,
                observed_time_ns=bar.ts_close_ns,
                pivot_span=pivot.span,
                strength_ratio=pivot.strength_ratio,
            )
            self._inc(f"horizontal_flip_{suffix.lower()}_created")

    def on_bar(self, bar: Candle):  # type: ignore[no-untyped-def]
        created = super().on_bar(bar)
        self._register_flips(bar)
        return created

    def observe_price(self, bar: Candle) -> None:
        for flip in self._flips.values():
            if not flip.active or bar.ts_close_ns <= flip.observed_time_ns:
                continue
            touched = (
                bar.high >= flip.price
                if flip.side is ZoneSide.RESISTANCE
                else bar.low <= flip.price
            )
            if touched:
                flip.consumed_time_ns = bar.ts_close_ns
                self._inc("horizontal_flip_first_retest_consumed")
        # The parent still owns pivot/channel lifecycle diagnostics.  Direct
        # pivots are not emitted as new targets here; the inherited v20 target
        # policy already handles its own structural objectives.
        super().observe_price(bar)

    def _snapshot(self, flip: HorizontalFlip, time_ns: int) -> StructureZone:
        if flip.side is ZoneSide.RESISTANCE:
            kind = ObjectKind.HORIZONTAL_RESISTANCE
            lower, upper = flip.price, flip.price + self.tick_size
            invalidation = upper + self.tick_size
        else:
            kind = ObjectKind.HORIZONTAL_SUPPORT
            lower, upper = flip.price - self.tick_size, flip.price
            invalidation = lower - self.tick_size
        return StructureZone(
            zone_id=f"{flip.flip_id}:SNAP:{time_ns}",
            kind=kind,
            family=StructureFamily.HORIZONTAL,
            side=flip.side,
            timeframe_minutes=self.timeframe_minutes,
            lower=lower,
            upper=upper,
            invalidation=invalidation,
            impulse_extreme=flip.price,
            formed_index=flip.formed_index,
            formed_time_ns=flip.formed_time_ns,
            observed_time_ns=flip.observed_time_ns,
            formation_indices=(flip.formed_index,),
            strength_ratio=flip.strength_ratio,
            source_structure_id=flip.flip_id,
            source_pivot_span=flip.pivot_span,
        )

    def flip_target_for(
        self,
        side: Side,
        *,
        interaction_time_ns: int,
        current_high: float,
        current_low: float,
    ) -> tuple[StructureZone, float] | None:
        wanted = ZoneSide.RESISTANCE if side is Side.LONG else ZoneSide.SUPPORT
        candidates = [
            flip
            for flip in self._flips.values()
            if flip.active
            and flip.side is wanted
            and flip.observed_time_ns < interaction_time_ns
            and (
                (side is Side.LONG and flip.price > current_high)
                or (side is Side.SHORT and flip.price < current_low)
            )
        ]
        if not candidates:
            return None
        selected = (
            min(candidates, key=lambda item: (item.price, -item.pivot_span, item.flip_id))
            if side is Side.LONG
            else max(candidates, key=lambda item: (item.price, item.pivot_span, item.flip_id))
        )
        self._inc("horizontal_flip_target_selected")
        return self._snapshot(selected, interaction_time_ns), selected.price

    @property
    def flip_diagnostics(self) -> dict[str, Any]:
        return {
            "created": len(self._flips),
            "active": sum(item.active for item in self._flips.values()),
            "consumed": sum(not item.active for item in self._flips.values()),
            "rule_provenance": HORIZONTAL_FLIP_OBJECTIVE_RULE,
        }


class FlipObjectiveScenarioEngine(DiagonalCoreScenarioEngine):
    """v20 execution geometry with a nearer first-retest S/R flip objective."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.decision_flip_objectives = HorizontalFlipObjectiveBook(
            self.symbol,
            self.decision_minutes,
            self.tick_size,
        )
        self.higher_flip_objectives = HorizontalFlipObjectiveBook(
            self.symbol,
            self.higher_minutes,
            self.tick_size,
        )
        self._flip_objective_counts: dict[str, int] = {}

    def _flip_inc(self, key: str) -> None:
        self._flip_objective_counts[key] = self._flip_objective_counts.get(key, 0) + 1

    @staticmethod
    def _first_choice(
        side: Side,
        choices: list[tuple[str, TargetChoice]],
    ) -> tuple[str, TargetChoice]:
        # Existing structural objective is appended first, preserving it on an
        # exact price tie.  Price decides every non-tie.
        if side is Side.LONG:
            return min(enumerate(choices), key=lambda item: (item[1][1][1], item[0]))[1]
        return max(enumerate(choices), key=lambda item: (item[1][1][1], -item[0]))[1]

    @staticmethod
    def _from_flip(value: tuple[StructureZone, float] | None) -> TargetChoice | None:
        if value is None:
            return None
        zone, price = value
        return zone, price, None, None

    @staticmethod
    def _append_unique(
        choices: list[tuple[str, TargetChoice]],
        source: str,
        choice: TargetChoice | None,
        tick_size: float,
    ) -> None:
        if choice is None:
            return
        zone, price, _, _ = choice
        for _, existing in choices:
            existing_zone, existing_price, _, _ = existing
            if existing_zone.source_structure_id == zone.source_structure_id or (
                abs(existing_price - price) <= tick_size * 0.5
            ):
                return
        choices.append((source, choice))

    def _select_target(
        self,
        context: StructureZone,
        side: Side,
        path: ScenarioPath,
        bar: Candle,
    ) -> TargetChoice | None:
        inherited = super()._select_target(context, side, path, bar)
        decision_flip = self.decision_flip_objectives.flip_target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            current_high=bar.high,
            current_low=bar.low,
        )
        higher_flip = self.higher_flip_objectives.flip_target_for(
            side,
            interaction_time_ns=bar.ts_close_ns,
            current_high=bar.high,
            current_low=bar.low,
        )
        choices: list[tuple[str, TargetChoice]] = []
        self._append_unique(choices, "INHERITED_STRUCTURE", inherited, self.tick_size)
        self._append_unique(
            choices,
            "5M_SR_FLIP",
            self._from_flip(decision_flip),
            self.tick_size,
        )
        self._append_unique(
            choices,
            "15M_SR_FLIP",
            self._from_flip(higher_flip),
            self.tick_size,
        )
        if not choices:
            return None
        source, selected = self._first_choice(side, choices)
        zone, price, _, _ = selected
        self._flip_inc(f"target_selected_{source.lower()}")
        if source != "INHERITED_STRUCTURE":
            self._flip_inc("sr_flip_replaced_farther_objective")
        self._trace(
            "horizontal_sr_flip_target_selected",
            bar.ts_close_ns,
            side=side.name,
            path=path.value,
            context_zone_id=context.zone_id,
            selected_source=source,
            selected_zone_id=zone.zone_id,
            selected_timeframe_minutes=zone.timeframe_minutes,
            selected_price=price,
            candidates=[
                {
                    "source": candidate_source,
                    "zone_id": candidate[0].zone_id,
                    "timeframe_minutes": candidate[0].timeframe_minutes,
                    "price": candidate[1],
                }
                for candidate_source, candidate in choices
            ],
            rule_provenance=HORIZONTAL_FLIP_OBJECTIVE_RULE,
        )
        return selected

    def _target_is_spent(self, setup: ScenarioSetup, bar: Candle) -> bool:
        target = setup.target_zone
        if target is not None and ":SR_FLIP:" in target.source_structure_id:
            if setup.target_price is None:
                return True
            touched = (
                bar.high >= setup.target_price
                if setup.side is Side.LONG
                else bar.low <= setup.target_price
            )
            return touched and bar.ts_close_ns > setup.interaction_time_ns
        return super()._target_is_spent(setup, bar)

    def on_bar(self, timeframe_minutes: int, bar: Candle):  # type: ignore[no-untyped-def]
        if timeframe_minutes == self.higher_minutes:
            self.higher_flip_objectives.on_bar(bar)
            plans = super().on_bar(timeframe_minutes, bar)
            self.higher_flip_objectives.observe_price(bar)
            return plans
        if timeframe_minutes == self.decision_minutes:
            self.decision_flip_objectives.on_bar(bar)
            plans = super().on_bar(timeframe_minutes, bar)
            # A 5m close can consume both its own objective and an HTF level
            # intrabar, preventing stale 15m targets between HTF closes.
            self.decision_flip_objectives.observe_price(bar)
            self.higher_flip_objectives.observe_price(bar)
            return plans
        return super().on_bar(timeframe_minutes, bar)

    @property
    def flip_objective_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._flip_objective_counts.items())),
            "decision_structure": dict(self.decision_flip_objectives.diagnostics),
            "decision_flips": self.decision_flip_objectives.flip_diagnostics,
            "higher_structure": dict(self.higher_flip_objectives.diagnostics),
            "higher_flips": self.higher_flip_objectives.flip_diagnostics,
            "rule_provenance": HORIZONTAL_FLIP_OBJECTIVE_RULE,
        }


class EasyChartRE1FlipObjectiveBundle(EasyChartRE1FreshBundle):
    """Canonical RE1 candidate with causal HTF routing and day-trade S/R flips."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.micro = FlipObjectiveScenarioEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            minimum_gross_rr=minimum_gross_rr,
        )
        self._audit_offsets["micro"] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["daytrade_objective_policy"] = {
            "name": "FIRST_LATER_RETEST_OF_CLOSE_BROKEN_5M_OR_15M_SWING_BEFORE_FARTHER_STRUCTURE",
            **self.micro.flip_objective_diagnostics,
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlipObjectiveBundle
