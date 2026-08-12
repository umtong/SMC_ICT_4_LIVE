"""Runtime repair and semantic binding for the EasyChart v3 structure-first policy."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import contracts_v5
from domain import Side
from scenario_engine_v5 import StructureScenarioEngine
from scenario_execution_v5 import ScenarioExecutionMixin
from structure_v5 import CausalStructureBook


if not getattr(ScenarioExecutionMixin, "_ecv3_return_patch", False):
    _original_advance_footprint_retests = ScenarioExecutionMixin._advance_footprint_retests

    def _advance_footprint_retests_with_result(
        self: Any,
        bar: Any,
        index: int,
    ) -> list[Any]:
        """Return the plans created by the reused source mixin.

        The reused implementation mutates setup state and appends plans to
        ``self.plans`` but its source revision omits the final
        ``return output``. Recover the exact newly-created plans without
        changing any decision.
        """
        before = len(self.plans)
        result = _original_advance_footprint_retests(self, bar, index)
        if result is not None:
            return list(result)
        return list(self.plans[before:])

    ScenarioExecutionMixin._advance_footprint_retests = _advance_footprint_retests_with_result
    ScenarioExecutionMixin._ecv3_return_patch = True


if not getattr(ScenarioExecutionMixin, "_ecv3_owner_scale_patch", False):
    _original_make_plan = ScenarioExecutionMixin._make_plan

    def _make_plan_with_structure_owner_scale(
        self: Any,
        setup: Any,
        bar: Any,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ) -> Any | None:
        """Bind episode width and audit labels to the structure-owning timeframe.

        The integrated v3 policy lets the 60m/15m structure candle resolve its
        own rejection or acceptance state. The reused plan contract previously
        retained the 15m/5m intermediate timeframe, which under-sized causal
        episode overlap and could let a lower-scale duplicate survive routing.
        This replacement changes only metadata and duplicate-episode geometry;
        entry, stop, target and ordering remain exactly as constructed.
        """
        plan = _original_make_plan(
            self,
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )
        if plan is None or plan.decision_timeframe_minutes == self.interaction_minutes:
            return plan
        updated = replace(plan, decision_timeframe_minutes=self.interaction_minutes)
        if self.plans and self.plans[-1] is plan:
            self.plans[-1] = updated
        return updated

    ScenarioExecutionMixin._make_plan = _make_plan_with_structure_owner_scale
    ScenarioExecutionMixin._ecv3_owner_scale_patch = True


if not getattr(CausalStructureBook, "_ecv3_sweep_lifecycle_patch", False):
    def _observe_price_until_swept(self: Any, bar: Any) -> None:
        """Keep equal highs/lows live; spend liquidity only after price trades beyond it.

        A touch can establish or reinforce a visible level. Treating equality as
        consumption removes the nearest objective and forces plans toward remote
        targets. The source describes liquidity as absorbed when the prior
        high/low is *taken*, so one tradable increment beyond the level is the
        deterministic automation boundary.
        """
        for pivot_id, pivot in list(self._active_pivots.items()):
            if bar.ts_close_ns <= pivot.observed_time_ns:
                continue
            touched = bar.high >= pivot.price if pivot.side == "HIGH" else bar.low <= pivot.price
            if touched and pivot.first_touch_time_ns is None:
                pivot.first_touch_time_ns = bar.ts_close_ns
                pivot.first_touch_index = len(self.bars) - 1
                self._inc(f"pivot_{pivot.side.lower()}_first_touch")
            swept = (
                bar.high >= pivot.price + self.tick_size
                if pivot.side == "HIGH"
                else bar.low <= pivot.price - self.tick_size
            )
            if swept and pivot.consumed_time_ns is None:
                pivot.consumed = True
                pivot.consumed_time_ns = bar.ts_close_ns
                self._active_pivots.pop(pivot_id, None)
                self._inc(f"pivot_{pivot.side.lower()}_swept")

    CausalStructureBook.observe_price = _observe_price_until_swept
    CausalStructureBook._ecv3_sweep_lifecycle_patch = True


if not getattr(ScenarioExecutionMixin, "_ecv3_acceptance_stop_patch", False):
    def _acceptance_stop_at_causal_origin(
        self: Any,
        setup: Any,
        time_ns: int,
    ) -> float | None:
        """Place the immutable protective stop beyond the pre-break causal origin.

        The prior channel implementation used a one-tick stop immediately
        inside the projected edge, while the source's channel invalidation is based
        on a close back through the retested structure and its trend-line
        breakout example places the stop at the wave origin. A native intrabar
        bracket cannot represent a close-only stop without leaving the position
        unprotected. The pre-break swing origin is therefore the conservative,
        fully pre-observable automation translation. Geometry validation still
        rejects any origin which is not on the loss side of entry.
        """
        del time_ns
        origin = setup.acceptance_origin
        if origin is None:
            return None
        return (
            origin.price - self.tick_size
            if setup.side is Side.LONG
            else origin.price + self.tick_size
        )

    ScenarioExecutionMixin._acceptance_stop = _acceptance_stop_at_causal_origin
    ScenarioExecutionMixin._ecv3_acceptance_stop_patch = True


if not getattr(StructureScenarioEngine, "_ecv3_trigger_structure_patch", False):
    _original_engine_init = StructureScenarioEngine.__init__
    _original_engine_on_bar = StructureScenarioEngine.on_bar

    def _engine_init_with_trigger_structure(self: Any, *args: Any, **kwargs: Any) -> None:
        _original_engine_init(self, *args, **kwargs)
        self._ecv3_trigger_structure = CausalStructureBook(
            self.symbol,
            self.trigger_minutes,
            self.tick_size,
            pivot_spans=(2,),
        )
        self._ecv3_shift_pivot_cache: dict[str, Any | None] = {}

    def _engine_on_bar_with_trigger_structure(self: Any, timeframe_minutes: int, bar: Any) -> list[Any]:
        if timeframe_minutes != self.trigger_minutes:
            return _original_engine_on_bar(self, timeframe_minutes, bar)
        self._ecv3_trigger_structure.on_bar(bar)
        output = _original_engine_on_bar(self, timeframe_minutes, bar)
        self._ecv3_trigger_structure.observe_price(bar)
        return output

    StructureScenarioEngine.__init__ = _engine_init_with_trigger_structure
    StructureScenarioEngine.on_bar = _engine_on_bar_with_trigger_structure
    StructureScenarioEngine._ecv3_trigger_structure_patch = True


if not getattr(ScenarioExecutionMixin, "_ecv3_local_shift_patch", False):
    _original_select_footprint = ScenarioExecutionMixin._select_footprint

    def _preexisting_shift_pivot(self: Any, setup: Any) -> Any | None:
        cache = self._ecv3_shift_pivot_cache
        if setup.setup_id in cache:
            return cache[setup.setup_id]
        confirmation_time = setup.confirmation_time_ns
        wanted = "HIGH" if setup.side is Side.LONG else "LOW"
        candidates = [
            pivot
            for pivot in self._ecv3_trigger_structure.pivots
            if pivot.side == wanted
            and confirmation_time is not None
            and pivot.observed_time_ns < confirmation_time
            and not (
                pivot.consumed_time_ns is not None
                and pivot.consumed_time_ns < confirmation_time
            )
        ]
        pivot = max(
            candidates,
            key=lambda item: (item.event_time_ns, item.observed_time_ns, item.pivot_id),
            default=None,
        )
        cache[setup.setup_id] = pivot
        return pivot

    def _select_footprint_after_local_shift(
        self: Any,
        candidates: list[Any],
        setup: Any,
    ) -> Any | None:
        """Require the event-local OB/FVG to close through pre-reclaim local structure.

        An engulfing candle or three-candle gap is an observation, not by itself
        proof that the auction has transitioned. For the integrated policy, the
        lower-timeframe swing which was already observable before the owner-frame
        reclaim must be displaced by a candle close. This mirrors the source's
        frequent OB/FVG + trend-line/structure-break confirmations and prevents a
        tiny footprint from confirming itself.
        """
        if not candidates or self.interaction_minutes != self.higher_minutes:
            return _original_select_footprint(self, candidates, setup)
        pivot = _preexisting_shift_pivot(self, setup)
        if pivot is None:
            self._inc("event_local_shift_pivot_unavailable")
            return None
        shifted: list[Any] = []
        for zone in candidates:
            if not 0 <= zone.formed_index < len(self.trigger_detector.bars):
                continue
            close = self.trigger_detector.bars[zone.formed_index].close
            broke = close > pivot.price if setup.side is Side.LONG else close < pivot.price
            if broke:
                shifted.append(zone)
        if not shifted:
            self._inc("event_local_footprint_without_structure_shift")
            return None
        self._inc("event_local_structure_shift_confirmed")
        return _original_select_footprint(self, shifted, setup)

    ScenarioExecutionMixin._select_footprint = _select_footprint_after_local_shift
    ScenarioExecutionMixin._ecv3_local_shift_patch = True


_translation = (
    "SOURCE_AMBIGUITY_TRANSLATION:EQUAL_HIGH_LOW_REMAINS_LIQUIDITY_UNTIL_ONE_TICK_SWEEP",
    "SOURCE_AMBIGUITY_TRANSLATION:ACCEPTANCE_NATIVE_STOP_USES_PREBREAK_CAUSAL_ORIGIN",
    "SOURCE_AMBIGUITY_TRANSLATION:STRUCTURE_OWNER_TIMEFRAME_OWNS_CAUSAL_EPISODE_WIDTH",
    "RESEARCH_HYPOTHESIS:EVENT_LOCAL_FOOTPRINT_MUST_CLOSE_THROUGH_PREEXISTING_TRIGGER_SWING",
)
contracts_v5.TRANSLATION_RULES = tuple(dict.fromkeys(contracts_v5.TRANSLATION_RULES + _translation))
