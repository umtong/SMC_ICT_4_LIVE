"""Auction-cycle order flow for the small EasyChart RE1 structure core.

Volume is neither a universal gate nor a late breakout trigger.  It has one of
two scenario-specific responsibilities at a pre-existing structure:

* acceptance: aligned aggressive flow must have moved price through and held
  beyond the boundary; on the first completed retest, absorbed counter-flow or
  renewed initiative may replace the next visual response candle;
* rejection/bounce/rotation: aggression against the intended trade must fail at
  the boundary and price must reclaim it.  Repeated absorption is restricted to
  one contiguous boundary-contact episode rather than any old flow since the
  original interaction.

Ordinary event-local OB/FVG and exact-retest entries stay executable and keep
priority.  No global volume filter, fitted percentile, score, session rule,
fixed-R target, partial exit or post-entry stop change is introduced.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup, SetupState, V5TradePlan
from domain import Candle, Side
from easychart_re1_flow import (
    CAUSAL_FLOW_BASELINE_RULE,
    BINANCE_AGGRESSOR_FLOW_RULE,
    FlowEntryMixin,
    FlowObservation,
    FlowSignal,
    FlowTriggerKind,
)
from easychart_re1_natural_geometry import (
    EasyChartRE1NaturalGeometryBundle,
    NaturalHorizontalEngine,
    NaturalMajorSwingEngine,
    NaturalMicroEngine,
)


AUCTION_CYCLE_FLOW_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ACCEPTANCE_USES_BREAK_HOLD_FLOW_PLUS_FIRST_RETEST_RESPONSE_WHILE_REVERSAL_USES_A_CONTIGUOUS_BOUNDARY_ABSORPTION_EPISODE"
)
FLOW_NOT_GLOBAL_GATE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "COMPLETE_VISUAL_OB_FVG_OR_EXACT_RETEST_PLANS_REMAIN_EXECUTABLE_WITHOUT_A_VOLUME_REQUIREMENT"
)
for _rule in (AUCTION_CYCLE_FLOW_RULE, FLOW_NOT_GLOBAL_GATE_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


_REVERSAL_PATHS = {
    ScenarioPath.REJECTION,
    ScenarioPath.BOUNCE,
    ScenarioPath.ROTATION,
}


class AuctionCycleFlowEntryMixin(FlowEntryMixin):
    """Mechanism-complete flow substitution inside one structure episode."""

    def _outside_and_aligned(
        self,
        setup: ScenarioSetup,
        bar: Any,
        lower: float,
        upper: float,
    ) -> bool:
        return (
            bar.close > upper and bar.close > bar.open
            if setup.side is Side.LONG
            else bar.close < lower and bar.close < bar.open
        )

    def _penetrated(
        self,
        setup: ScenarioSetup,
        item: FlowObservation,
        lower: float,
        upper: float,
    ) -> bool:
        return item.low < lower if setup.side is Side.LONG else item.high > upper

    def _contiguous_boundary_cluster(
        self,
        setup: ScenarioSetup,
        current_time_ns: int,
    ) -> list[FlowObservation]:
        event_start = setup.confirmation_time_ns or setup.interaction_time_ns
        output: list[FlowObservation] = []
        for item in reversed(self.flow_analyzer.history):
            if item.ts_close_ns > current_time_ns:
                continue
            if item.ts_close_ns <= event_start:
                break
            _, lower, upper = self._projected_bounds(setup, item.ts_close_ns)
            touches = item.low <= upper and item.high >= lower
            if not touches:
                if output:
                    break
                continue
            output.append(item)
        output.reverse()
        return output

    def _reversal_absorption_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        if observation is None or not observation.active or not observation.directed:
            return None
        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        if not self._outside_and_aligned(setup, bar, lower, upper):
            return None

        intended_half = (
            observation.close_location >= 0.5
            if setup.side is Side.LONG
            else observation.close_location <= 0.5
        )
        if not intended_half:
            return None

        current_opposite = self._opposite_delta(
            setup.side,
            observation.signed_taker_quote,
        )
        current_penetration = self._penetrated(
            setup,
            observation,
            lower,
            upper,
        )
        event_start = setup.confirmation_time_ns or setup.interaction_time_ns
        episode = self.flow_analyzer.since(event_start)
        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        net_progress = 0.0
        if episode:
            net_progress = self._intended_progress(
                setup.side,
                episode[0].open,
                episode[-1].close,
            )

        if current_opposite and current_penetration:
            kind = (
                FlowTriggerKind.SELL_ABSORPTION
                if setup.side is Side.LONG
                else FlowTriggerKind.BUY_ABSORPTION
            )
            return FlowSignal(
                kind=kind,
                mechanism="SWEEP_RECLAIM_CURRENT_ABSORPTION",
                strength=observation.activity_ratio * observation.delta_ratio,
                observation=observation,
                episode_bars=1,
                cumulative_signed_taker_quote=observation.signed_taker_quote,
                net_price_progress=self._intended_progress(
                    setup.side,
                    observation.open,
                    observation.close,
                ),
            )

        cluster = self._contiguous_boundary_cluster(setup, bar.ts_close_ns)
        opposite = [
            item
            for item in cluster
            if item.active
            and item.directed
            and self._opposite_delta(setup.side, item.signed_taker_quote)
        ]
        if len(opposite) < 2:
            return None
        cumulative_cluster_delta = sum(item.signed_taker_quote for item in cluster)
        if not self._opposite_delta(setup.side, cumulative_cluster_delta):
            return None
        penetrated = False
        for item in cluster:
            _, item_lower, item_upper = self._projected_bounds(
                setup,
                item.ts_close_ns,
            )
            if self._penetrated(setup, item, item_lower, item_upper):
                penetrated = True
                break
        if not penetrated:
            return None
        cluster_progress = self._intended_progress(
            setup.side,
            cluster[0].open,
            cluster[-1].close,
        )
        if cluster_progress < 0.0:
            return None
        kind = (
            FlowTriggerKind.REPEATED_SELL_ABSORPTION
            if setup.side is Side.LONG
            else FlowTriggerKind.REPEATED_BUY_ABSORPTION
        )
        return FlowSignal(
            kind=kind,
            mechanism="CONTIGUOUS_BOUNDARY_ABSORPTION",
            strength=observation.activity_ratio * observation.delta_ratio,
            observation=observation,
            episode_bars=len(cluster),
            cumulative_signed_taker_quote=cumulative_cluster_delta,
            net_price_progress=cluster_progress,
        )

    def _flow_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        if setup.path not in _REVERSAL_PATHS:
            return None
        signal = self._reversal_absorption_signal(setup, bar, observation)
        if signal is None:
            self._finc("reversal_without_boundary_absorption")
        else:
            self._finc("reversal_boundary_absorption_confirmed")
        return signal

    def _accepted_break_flow_evidence(
        self,
        setup: ScenarioSetup,
    ) -> tuple[list[FlowObservation], float, float] | None:
        if setup.confirmation_time_ns is None:
            return None
        episode = [
            item
            for item in self.flow_analyzer.history
            if setup.interaction_time_ns < item.ts_close_ns <= setup.confirmation_time_ns
        ]
        aligned = [
            item
            for item in episode
            if item.active
            and item.directed
            and item.material_progress
            and self._aligned_delta(setup.side, item.signed_taker_quote)
            and (
                item.body > 0.0
                if setup.side is Side.LONG
                else item.body < 0.0
            )
        ]
        if not aligned:
            return None
        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        if not self._aligned_delta(setup.side, cumulative_delta):
            return None
        net_progress = self._intended_progress(
            setup.side,
            episode[0].open,
            episode[-1].close,
        )
        if net_progress <= 0.0:
            return None
        return episode, cumulative_delta, net_progress

    def _accepted_retest_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        if observation is None or not observation.active or not observation.directed:
            return None
        break_evidence = self._accepted_break_flow_evidence(setup)
        if break_evidence is None:
            self._finc("acceptance_missing_break_hold_flow")
            return None
        episode, cumulative_delta, net_progress = break_evidence
        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        if not (bar.low <= upper and bar.high >= lower):
            return None
        if not self._outside_and_aligned(setup, bar, lower, upper):
            return None

        opposite = self._opposite_delta(setup.side, observation.signed_taker_quote)
        aligned = self._aligned_delta(setup.side, observation.signed_taker_quote)
        if opposite:
            kind = (
                FlowTriggerKind.SELL_ABSORPTION
                if setup.side is Side.LONG
                else FlowTriggerKind.BUY_ABSORPTION
            )
            mechanism = "ACCEPTED_RETEST_COUNTERFLOW_ABSORBED"
        elif aligned and observation.material_progress:
            intended_half = (
                observation.close_location >= 0.5
                if setup.side is Side.LONG
                else observation.close_location <= 0.5
            )
            if not intended_half:
                return None
            kind = (
                FlowTriggerKind.BUY_INITIATIVE
                if setup.side is Side.LONG
                else FlowTriggerKind.SELL_INITIATIVE
            )
            mechanism = "ACCEPTED_RETEST_REINITIATIVE"
        else:
            return None

        return FlowSignal(
            kind=kind,
            mechanism=mechanism,
            strength=observation.activity_ratio * observation.delta_ratio,
            observation=observation,
            episode_bars=len(episode) + 1,
            cumulative_signed_taker_quote=cumulative_delta,
            net_price_progress=net_progress,
        )

    def _create_accepted_retest_flow_plan(
        self,
        setup: ScenarioSetup,
        bar: Any,
        pending: Any,
        signal: FlowSignal,
    ) -> V5TradePlan | None:
        if self._target_is_spent(setup, bar):
            return None
        if self._pending_stop_touched(setup, pending, bar):
            return None
        proxy = self._flow_proxy(setup, bar.ts_close_ns)
        self._audit(proxy)
        plan = self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=pending.stop,
            trigger_zone=proxy,
            trigger_kind=signal.kind,
            trigger_strength=signal.strength,
        )
        if plan is None:
            self._finc("accepted_retest_flow_geometry_rejected")
            return None
        self._flow_plans.append(plan)
        self._finc("accepted_retest_flow_plan_created")
        self._finc(f"signal_{signal.kind.value.lower()}")
        self._trace(
            "accepted_retest_flow_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            retest_time_ns=pending.retest_time_ns,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            rule_provenance=(AUCTION_CYCLE_FLOW_RULE, FLOW_NOT_GLOBAL_GATE_RULE),
            **self._signal_trace(signal),
        )
        return plan

    def _advance_acceptance_retests(
        self,
        bar: Candle,
        index: int,
    ) -> list[V5TradePlan]:
        # Run the original exact-retest and pending visual-response state
        # machine, not FlowEntryMixin's unrestricted later-bar initiative path.
        output = super(FlowEntryMixin, self)._advance_acceptance_retests(bar, index)
        observation = self._flow_current
        pending_map = getattr(self, "_pending_acceptance_responses", None)
        if observation is None or pending_map is None:
            return output

        for setup in list(self._active.values()):
            if setup.state is not SetupState.WAITING_ACCEPTANCE_RETEST:
                continue
            if setup.path is not ScenarioPath.ACCEPTANCE:
                continue
            pending = pending_map.get(setup.setup_id)
            if pending is None or pending.retest_time_ns != bar.ts_close_ns:
                continue
            signal = self._accepted_retest_signal(setup, bar, observation)
            if signal is None:
                self._finc("accepted_retest_without_flow_substitution")
                continue
            plan = self._create_accepted_retest_flow_plan(
                setup,
                bar,
                pending,
                signal,
            )
            if plan is not None:
                pending_map.pop(setup.setup_id, None)
                output.append(plan)
        return output

    @property
    def auction_cycle_flow_diagnostics(self) -> dict[str, Any]:
        return {
            "reversal_paths": tuple(sorted(path.value for path in _REVERSAL_PATHS)),
            "acceptance": "BREAK_HOLD_FLOW_PLUS_FIRST_RETEST_RESPONSE",
            "reversal": "CURRENT_OR_CONTIGUOUS_BOUNDARY_ABSORPTION",
            "rules": (
                BINANCE_AGGRESSOR_FLOW_RULE,
                CAUSAL_FLOW_BASELINE_RULE,
                AUCTION_CYCLE_FLOW_RULE,
                FLOW_NOT_GLOBAL_GATE_RULE,
            ),
        }


class AuctionFlowMicroEngine(AuctionCycleFlowEntryMixin, NaturalMicroEngine):
    pass


class AuctionFlowHorizontalEngine(
    AuctionCycleFlowEntryMixin,
    NaturalHorizontalEngine,
):
    pass


class AuctionFlowMajorSwingEngine(
    AuctionCycleFlowEntryMixin,
    NaturalMajorSwingEngine,
):
    pass


class EasyChartRE1AuctionFlowBundle(EasyChartRE1NaturalGeometryBundle):
    """Three natural structure families with complete auction-cycle flow."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = AuctionFlowMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = AuctionFlowHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = AuctionFlowMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in ("micro", "horizontal", "major_swing"):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["auction_cycle_flow_core"] = {
            "families": ("MICRO", "HORIZONTAL", "LIQUIDITY"),
            "micro": self.micro.auction_cycle_flow_diagnostics,
            "horizontal": self.horizontal.auction_cycle_flow_diagnostics,
            "major_swing": self.major_swing.auction_cycle_flow_diagnostics,
            "rules": (AUCTION_CYCLE_FLOW_RULE, FLOW_NOT_GLOBAL_GATE_RULE),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1AuctionFlowBundle
