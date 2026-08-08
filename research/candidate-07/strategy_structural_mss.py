"""Nautilus strategy integration for protected one-minute MSS/retest plans."""
from __future__ import annotations

from typing import Any

from nautilus_trader.model.data import Bar

from model import Direction, Observation, ScenarioState, SignalBar, TradePlan
from model_structural_mss import StructuralMSSRouter, _StructuralEpisode
from strategy import Candidate07Strategy as _BaseCandidate07Strategy
from strategy import Candidate07StrategyConfig


class _TargetSafeStructuralMSSRouter(StructuralMSSRouter):
    """Refuse a setup after its source-time objective was already delivered."""

    def _advance_structural_episode(
        self,
        *,
        bar: SignalBar,
        minute_atr: float | None,
        body_atr: float,
        displacement_rank: float,
    ) -> tuple[TradePlan | None, list[Any]]:
        episode = self._structural_episode
        if episode is not None:
            target = self._nearest_source_objective(episode)
            if target is not None and self._target_touched(
                episode=episode,
                bar=bar,
                target=target,
            ):
                transition = self._terminal_transition(
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
                self._finish_episode(bar.ts_event_ns)
                return None, [transition]
        return super()._advance_structural_episode(
            bar=bar,
            minute_atr=minute_atr,
            body_atr=body_atr,
            displacement_rank=displacement_rank,
        )

    @staticmethod
    def _nearest_source_objective(
        episode: _StructuralEpisode,
    ) -> float | None:
        candidates = []
        for level in (episode.opposing_internal, episode.opposing_external):
            if episode.direction is Direction.LONG and level > episode.source_level:
                candidates.append(level)
            elif episode.direction is Direction.SHORT and level < episode.source_level:
                candidates.append(level)
        if not candidates:
            return None
        return (
            min(candidates)
            if episode.direction is Direction.LONG
            else max(candidates)
        )

    @staticmethod
    def _target_touched(
        *,
        episode: _StructuralEpisode,
        bar: SignalBar,
        target: float,
    ) -> bool:
        if episode.direction is Direction.LONG:
            return bar.high >= target
        return bar.low <= target


class Candidate07Strategy(_BaseCandidate07Strategy):
    """Drive source detection on 5M and state confirmation on completed 1M."""

    def __init__(self, config: Candidate07StrategyConfig):
        super().__init__(config)
        self.router = _TargetSafeStructuralMSSRouter(self.logic)

    def on_bar(self, bar: Bar) -> None:
        minute = SignalBar(
            ts_event_ns=int(bar.ts_event),
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=bar.volume.as_double(),
        )
        observation = self.router.observe_minute(minute)
        self._accept_minute_observation(observation)
        super().on_bar(bar)

    def _accept_minute_observation(self, observation: Observation) -> None:
        for transition in observation.transitions:
            self._append_transition(transition)
        if observation.transitions or observation.plan is not None:
            self._diagnostics.append(dict(observation.diagnostics))
        if observation.plan is None:
            return
        if self._pending_plan is not None or self._active_plan is not None:
            raise RuntimeError("structural plan collided with occupied strategy slot")
        self._pending_plan = observation.plan
        self._pending_created_ns = observation.plan.observed_time_ns

    @staticmethod
    def _structural_reset_price(plan: TradePlan) -> float:
        """Return the original opposing internal objective after protected exit."""
        raw_internal = plan.details.get("opposing_internal")
        candidate = (
            float(raw_internal)
            if raw_internal is not None
            else plan.target_price
        )
        if plan.direction is Direction.LONG and candidate <= plan.entry_reference:
            return plan.target_price
        if plan.direction is Direction.SHORT and candidate >= plan.entry_reference:
            return plan.target_price
        return candidate


__all__ = ["Candidate07Strategy", "Candidate07StrategyConfig"]
