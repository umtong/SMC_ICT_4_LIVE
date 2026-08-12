"""Structure-first EasyChart engine composed from focused causal mixins.

The timeframe which owns a structure also owns rejection/acceptance decisions.
Lower timeframes may refine execution, but cannot declare a higher-timeframe
channel or trendline fakeout before that structure's candle has closed.
"""
from __future__ import annotations

from typing import Any

from domain import Candle
from event_footprints_v5 import EventLocalZoneDetector
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from scenario_context_v5 import ScenarioContextMixin
from scenario_execution_v5 import ScenarioExecutionMixin
from scenario_transitions_v5 import ScenarioTransitionMixin
from structure_v5 import CausalStructureBook


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
        interaction_minutes: int | None = None,
    ) -> None:
        if not higher_minutes > decision_minutes > trigger_minutes:
            raise ValueError("scenario timeframes must descend")
        owner = decision_minutes if interaction_minutes is None else interaction_minutes
        if owner not in {higher_minutes, decision_minutes}:
            raise ValueError("interaction_minutes must be the structure or intermediate timeframe")
        self.symbol = symbol
        self.tick_size = tick_size
        self.scale_name = scale_name
        self.higher_minutes = higher_minutes
        self.decision_minutes = decision_minutes
        self.trigger_minutes = trigger_minutes
        self.interaction_minutes = owner
        self.minimum_gross_rr = minimum_gross_rr
        self.structure = CausalStructureBook(symbol, higher_minutes, tick_size)
        self.trigger_detector = EventLocalZoneDetector(symbol, trigger_minutes, tick_size)
        # Retained name for compatibility with the focused transition mixin.
        # These bars are the bars which actually own the interaction state.
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
            "decision_timeframe_minutes": self.interaction_minutes,
            "intermediate_timeframe_minutes": self.decision_minutes,
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

    def _process_interaction_bar(self, bar: Candle) -> None:
        if self.decision_bars and bar.ts_close_ns <= self.decision_bars[-1].ts_close_ns:
            raise ValueError("interaction bars must arrive in increasing close time")
        self.decision_bars.append(bar)
        index = len(self.decision_bars) - 1
        # Existing episodes are advanced before a new episode can claim this
        # same close. This prevents one bar from both terminating and recreating
        # the same causal structure opportunity.
        self._advance_decision_setups(bar, index)
        if index >= 1:
            self._discover_interactions(bar, self.decision_bars[index - 1], index)
        self._inc("interaction_owner_bar")

    def on_bar(self, timeframe_minutes: int, bar: Candle) -> list[V5TradePlan]:
        if timeframe_minutes == self.higher_minutes:
            # Classify against structures known before this close. Only after
            # the decision is made may this bar confirm new pivots/lines/channels.
            if self.interaction_minutes == self.higher_minutes:
                self._process_interaction_bar(bar)
            pivots, lines, channels = self.structure.on_bar(bar)
            # Lifecycle updates occur after scenario classification. A pivot
            # first confirmed by this close cannot consume itself.
            self.structure.observe_price(bar)
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
            if self.interaction_minutes == self.decision_minutes:
                # Compatibility mode retained for focused historical tests.
                self._process_interaction_bar(bar)
                self.structure.observe_price(bar)
            else:
                # The intermediate frame is observable evidence only. It may
                # later host its own structure/footprint role, but it cannot
                # resolve the higher structure before that candle closes.
                self._inc("intermediate_bar_observed")
            return []

        if timeframe_minutes != self.trigger_minutes:
            raise ValueError(f"unsupported timeframe {timeframe_minutes} for {self.scale_name}")
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

    def find_zone(self, zone_id: str) -> Any | None:
        return next((zone for zone in self.audit_zones if zone.zone_id == zone_id), None)
