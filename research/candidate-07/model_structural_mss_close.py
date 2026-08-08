"""True protected one-minute MSS with immediate close entry.

This is the single controlled ablation of ``StructuralMSSRouter``.  The source
five-minute sweep, independently confirmed protected one-minute swing, ranked
one-minute displacement, source-extreme invalidation, objective preconsumption,
stop, target and risk geometry are unchanged.  Only the same-boundary retest is
removed: once the true protected swing is broken by a completed ranked minute,
the plan becomes entry-ready at that completed MSS close.

The module owns scenario state only.  It creates no orders, fills, cash, PnL or
NAV.
"""
from __future__ import annotations

from typing import Any

from model import ScenarioState, SignalBar, TradePlan, Transition
from model_structural_mss import NS_PER_MINUTE, StructuralStage, _StructuralEpisode
from strategy_structural_mss import _TargetSafeStructuralMSSRouter


class TargetSafeStructuralMSSCloseRouter(_TargetSafeStructuralMSSRouter):
    """Require a real independent 1M MSS, but do not wait for its retest."""

    def _advance_structural_episode(
        self,
        *,
        bar: SignalBar,
        minute_atr: float | None,
        body_atr: float,
        displacement_rank: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._structural_episode
        if episode is None:
            return None, []
        transitions: list[Transition] = []

        target = self._nearest_source_objective(episode)
        if target is not None and self._target_touched(
            episode=episode,
            bar=bar,
            target=target,
        ):
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "SOURCE_OBJECTIVE_DELIVERED_BEFORE_ENTRY",
                    bar.ts_event_ns,
                    target,
                    {
                        "target": target,
                        "direction": episode.direction.value,
                        "stage": episode.stage.value,
                        "minute_high": bar.high,
                        "minute_low": bar.low,
                    },
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        if self._source_invalidated(episode, bar):
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "SOURCE_INVALIDATED_BEFORE_MSS",
                    bar.ts_event_ns,
                    episode.event_extreme,
                    {"minute_high": bar.high, "minute_low": bar.low},
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        if episode.stage is not StructuralStage.AWAIT_MSS:
            raise RuntimeError("MSS-close router entered an unexpected retest stage")
        deadline = episode.source_time_ns + (
            self.structure.maximum_mss_minutes * NS_PER_MINUTE
        )
        if bar.ts_event_ns > deadline:
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "INDEPENDENT_1M_MSS_NOT_CONFIRMED_WITHIN_WINDOW",
                    bar.ts_event_ns,
                    episode.boundary.level,
                    {"deadline_ns": deadline},
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        if not self._mss_confirmed(
            episode,
            bar,
            minute_atr=minute_atr,
            body_atr=body_atr,
            displacement_rank=displacement_rank,
        ):
            return None, transitions

        episode.mss_ns = bar.ts_event_ns
        episode.mss_body_atr = body_atr
        episode.mss_rank = displacement_rank
        transitions.append(
            self._state_transition(
                episode,
                ScenarioState.CONFIRMED,
                "INDEPENDENT_1M_DISPLACEMENT_MSS",
                bar.ts_event_ns,
                episode.boundary.level,
                {
                    "boundary_id": episode.boundary.swing_id,
                    "boundary_level": episode.boundary.level,
                    "body_atr": body_atr,
                    "displacement_rank": displacement_rank,
                    "close_location": bar.close_location,
                },
            )
        )

        base_plan = self._build_structural_plan(episode, bar)
        if base_plan is None:
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "STRUCTURAL_MSS_CLOSE_GEOMETRY_UNTRADEABLE",
                    bar.ts_event_ns,
                    bar.close,
                    {
                        "entry": bar.close,
                        "event_extreme": episode.event_extreme,
                        "opposing_internal": episode.opposing_internal,
                        "opposing_external": episode.opposing_external,
                    },
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        details: dict[str, Any] = dict(base_plan.details)
        details.update(
            {
                "structural_route": "5M_SWEEP_1M_TRUE_MSS_CLOSE",
                "same_boundary_retest": False,
                "retest_ns": None,
                "ablation": "REMOVE_SAME_BOUNDARY_RETEST_ONLY",
            }
        )
        plan = TradePlan(
            scenario_id=base_plan.scenario_id,
            kind=base_plan.kind,
            direction=base_plan.direction,
            observed_time_ns=base_plan.observed_time_ns,
            entry_reference=base_plan.entry_reference,
            stop_price=base_plan.stop_price,
            target_price=base_plan.target_price,
            liquidity_level=base_plan.liquidity_level,
            expected_rr=base_plan.expected_rr,
            details=details,
        )
        transitions.append(
            self._state_transition(
                episode,
                ScenarioState.ENTRY_READY,
                "INDEPENDENT_1M_MSS_CLOSE_ENTRY_READY",
                bar.ts_event_ns,
                plan.entry_reference,
                {
                    "boundary_id": episode.boundary.swing_id,
                    "boundary_level": episode.boundary.level,
                    "mss_ns": episode.mss_ns,
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "expected_rr": plan.expected_rr,
                    "same_boundary_retest": False,
                },
            )
        )
        self._finish_episode(bar.ts_event_ns)
        return plan, transitions


__all__ = ["TargetSafeStructuralMSSCloseRouter"]
