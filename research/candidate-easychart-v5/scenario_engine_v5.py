"""Structure-first EasyChart v5 engine composed from focused causal mixins."""
from __future__ import annotations

from typing import Any

from causal_lifecycle_v5 import LifecycleAwareStructureBook
from domain import Candle, Side
from event_footprints_v5 import EventLocalZoneDetector
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from scenario_context_v5 import ScenarioContextMixin
from scenario_execution_v5 import ScenarioExecutionMixin
from scenario_transitions_v5 import ScenarioTransitionMixin


class StructureScenarioEngine(
    ScenarioContextMixin,
    ScenarioTransitionMixin,
    ScenarioExecutionMixin,
):
    def __init__(
        self,
        symbol: str,
        tick_size: float,
        *,
        scale_name: str,
        higher_minutes: int,
        decision_minutes: int,
        trigger_minutes: int,
        minimum_gross_rr: float,
    ) -> None:
        if not higher_minutes > decision_minutes > trigger_minutes:
            raise ValueError("scenario timeframes must descend")
        self.symbol = symbol
        self.tick_size = tick_size
        self.scale_name = scale_name
        self.higher_minutes = higher_minutes
        self.decision_minutes = decision_minutes
        self.trigger_minutes = trigger_minutes
        self.minimum_gross_rr = minimum_gross_rr
        self.structure = LifecycleAwareStructureBook(symbol, higher_minutes, tick_size)
        self.trigger_detector = EventLocalZoneDetector(symbol, trigger_minutes, tick_size)
        self.decision_bars: list[Candle] = []
        self.setups: list[ScenarioSetup] = []
        self._active: dict[str, ScenarioSetup] = {}
        self.plans: list[V5TradePlan] = []
        self.audit_zones: list[Any] = []
        self._audit_zone_ids: set[str] = set()
        self.trace_events: list[dict[str, Any]] = []
        self.diagnostics: dict[str, int] = {}
        self._claimed_structures: set[str] = set()
        self._claimed_episodes: set[str] = set()
        self.sequence = 0
        self._current_trigger_bar: Candle | None = None

    def _inc(self, key: str) -> None:
        self.diagnostics[key] = self.diagnostics.get(key, 0) + 1

    def _audit(self, zone: Any) -> None:
        zone_id = getattr(zone, "zone_id", None)
        if zone_id and zone_id not in self._audit_zone_ids:
            self._audit_zone_ids.add(zone_id)
            self.audit_zones.append(zone)

    def _trace(self, kind: str, time_ns: int, setup: ScenarioSetup | None = None, **values: Any) -> None:
        event: dict[str, Any] = {
            "scenario_kind": kind,
            "event_time_ns": time_ns,
            "scale_name": self.scale_name,
            "higher_timeframe_minutes": self.higher_minutes,
            "decision_timeframe_minutes": self.decision_minutes,
            "trigger_timeframe_minutes": self.trigger_minutes,
            **values,
        }
        if setup is not None:
            event.update(
                {
                    "setup_id": setup.setup_id,
                    "setup_state": setup.state.value,
                    "scenario_path": setup.path.value,
                    "side": setup.side.name,
                    "higher_zone_id": setup.context.zone_id,
                    "decision_zone_id": setup.context_members[-1].zone_id,
                    "overlap_lower": setup.context.lower,
                    "overlap_upper": setup.context.upper,
                    "interaction_time_ns": setup.interaction_time_ns,
                    "structure_age_ns": max(0, setup.interaction_time_ns - setup.observed_time_ns),
                    "event_age_from_interaction_ns": max(0, time_ns - setup.interaction_time_ns),
                    "context_member_ids": [
                        member.source_structure_id for member in setup.context_members
                    ],
                    "context_member_kinds": [
                        getattr(member.kind, "value", str(member.kind))
                        for member in setup.context_members
                    ],
                },
            )
        self.trace_events.append(event)

    def drain_trace(self) -> list[dict[str, Any]]:
        output, self.trace_events = self.trace_events, []
        return output

    @staticmethod
    def _terminal(state: SetupState) -> bool:
        return state in {
            SetupState.PLANNED,
            SetupState.INVALIDATED,
            SetupState.TARGET_SPENT,
            SetupState.NO_TARGET,
            SetupState.NO_TRADE_GEOMETRY,
            SetupState.UNRESOLVED,
            SetupState.DUPLICATE_EPISODE,
        }

    def _finish(
        self,
        setup: ScenarioSetup,
        state: SetupState,
        time_ns: int,
        reason: str,
        **values: Any,
    ) -> None:
        setup.state = state
        setup.terminal_reason = reason
        self._active.pop(setup.setup_id, None)
        if setup.trigger_zone is not None:
            setup.trigger_zone.consumed = True
        self._inc(reason)
        self._trace(reason, time_ns, setup, **values)

    def _acceptance_stop(self, setup: ScenarioSetup, time_ns: int) -> float | None:
        """Translate close-confirmed retest invalidation into one causal stop.

        The source allows entry only after the retest bar has completed. A stop
        inside that already-observed bar would have been crossed before the
        entry decision existed, and in live trading it turns normal retest wick
        noise into an immediate stop. Keep the structural invalidation from the
        base policy, but place the executable stop beyond the completed retest
        bar as well.
        """
        structural_stop = super()._acceptance_stop(setup, time_ns)
        if structural_stop is None:
            return None
        bar = self._current_trigger_bar
        if bar is None or bar.ts_close_ns != time_ns:
            raise RuntimeError("acceptance stop requested without its completed trigger bar")
        if setup.side is Side.LONG:
            executable_stop = min(structural_stop, bar.low - self.tick_size)
        else:
            executable_stop = max(structural_stop, bar.high + self.tick_size)
        if executable_stop != structural_stop:
            self._inc("acceptance_stop_extended_beyond_entry_bar")
            self._trace(
                "acceptance_stop_extended_beyond_entry_bar",
                time_ns,
                setup,
                structural_stop=structural_stop,
                entry_bar_low=bar.low,
                entry_bar_high=bar.high,
                executable_stop=executable_stop,
            )
        return executable_stop

    def _advance_acceptance_retests(self, bar: Candle, index: int) -> list[V5TradePlan]:
        """Keep direct diagnostic calls causally bound to their supplied bar."""
        previous = self._current_trigger_bar
        if previous is None:
            self._current_trigger_bar = bar
        elif previous.ts_close_ns != bar.ts_close_ns:
            raise RuntimeError("acceptance retest bar differs from active trigger bar")
        try:
            return super()._advance_acceptance_retests(bar, index)
        finally:
            if previous is None:
                self._current_trigger_bar = None

    def _make_plan(
        self,
        setup: ScenarioSetup,
        bar: Candle,
        *,
        entry: float,
        stop: float,
        trigger_zone: Any,
        trigger_kind: Any,
        trigger_strength: float,
    ) -> V5TradePlan | None:
        """Reject any plan whose stop was already traded before bar-close entry."""
        stop_inside_completed_bar = (
            stop >= bar.low if setup.side is Side.LONG else stop <= bar.high
        )
        if stop_inside_completed_bar:
            self._finish(
                setup,
                SetupState.NO_TRADE_GEOMETRY,
                bar.ts_close_ns,
                "stop_inside_observed_entry_bar",
                entry=entry,
                stop=stop,
                entry_bar_low=bar.low,
                entry_bar_high=bar.high,
            )
            return None
        return super()._make_plan(
            setup,
            bar,
            entry=entry,
            stop=stop,
            trigger_zone=trigger_zone,
            trigger_kind=trigger_kind,
            trigger_strength=trigger_strength,
        )

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.higher_minutes:
            pivots, lines, channels = self.structure.on_bar(bar)
            self._inc("context_bar")
            self._trace(
                "context_structure_update",
                bar.ts_close_ns,
                pivots=len(pivots),
                trend_lines=len(lines),
                channels=len(channels),
            )
            return []
        if timeframe_minutes == self.decision_minutes:
            if self.decision_bars and bar.ts_close_ns <= self.decision_bars[-1].ts_close_ns:
                raise ValueError("decision bars must arrive in increasing close time")
            self.decision_bars.append(bar)
            index = len(self.decision_bars) - 1
            self._advance_decision_setups(bar, index)
            if index >= 1:
                self._discover_interactions(bar, self.decision_bars[index - 1], index)
            # The designated decision bar first owns the interaction. Only
            # after classification do diagonal boundaries leave the fresh
            # opportunity set, including crossed lookalikes which were not
            # selected for a setup.
            self.structure.observe_price(bar)
            return []
        if timeframe_minutes != self.trigger_minutes:
            raise ValueError(f"unsupported timeframe {timeframe_minutes} for {self.scale_name}")
        self._current_trigger_bar = bar
        try:
            created = self.trigger_detector.on_bar(bar)
            for zone in created:
                self._audit(zone)
            index = len(self.trigger_detector.bars) - 1
            self._arm_displacements(bar, index, created)
            plans = self._advance_acceptance_retests(bar, index)
            plan_count_before_retest = len(self.plans)
            footprint_plans = self._advance_footprint_retests(bar, index)
            if footprint_plans is None:
                # Compatibility guard for the prior method version which appended
                # plans to self.plans but accidentally omitted its return statement.
                footprint_plans = self.plans[plan_count_before_retest:]
            plans.extend(footprint_plans)
            return sorted(
                plans,
                key=lambda plan: (
                    plan.interaction_time_ns,
                    -plan.higher_timeframe_minutes,
                    plan.symbol,
                    plan.plan_id,
                ),
            )
        finally:
            self._current_trigger_bar = None

    def find_zone(self, zone_id: str) -> Any | None:
        return next((zone for zone in self.audit_zones if zone.zone_id == zone_id), None)
