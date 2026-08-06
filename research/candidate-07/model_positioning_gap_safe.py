"""Data-gap-safe positioning router for candidate-07.

Invalid exchange snapshots are never interpolated. A skipped five-minute
snapshot terminates any active positioning episode, and the first later OI
observation is classified neutral rather than comparing a ten-minute change to
five-minute history.
"""
from __future__ import annotations

from model import ScenarioState, Transition
from model_positioning import (
    InventoryState,
    PositioningAuctionRouter,
    PositioningSignalBar,
)


NS_PER_MINUTE = 60_000_000_000


class GapSafePositioningAuctionRouter(PositioningAuctionRouter):
    def _inventory_state(
        self,
        bar: PositioningSignalBar,
    ) -> tuple[float, float, InventoryState]:
        expected_ns = self.config.signal_minutes * NS_PER_MINUTE
        previous_bar = self._history[-1]
        if bar.ts_event_ns - previous_bar.ts_event_ns != expected_ns:
            return 0.0, 0.0, InventoryState.NEUTRAL

        previous = previous_bar.open_interest
        change = (bar.open_interest - previous) / previous
        bars = list(self._history)[-(self.config.oi_period + 1) :]
        prior_changes = [
            (right.open_interest - left.open_interest) / left.open_interest
            for left, right in zip(bars, bars[1:])
            if (
                left.open_interest > 0.0
                and right.ts_event_ns - left.ts_event_ns == expected_ns
            )
        ]
        magnitudes = [abs(value) for value in prior_changes]
        rank = (
            sum(value <= abs(change) for value in magnitudes) / len(magnitudes)
            if magnitudes
            else 0.0
        )
        impulse = rank >= self.config.oi_impulse_rank and abs(change) > 0.0
        if not impulse:
            return change, rank, InventoryState.NEUTRAL
        return (
            change,
            rank,
            InventoryState.BUILD if change > 0.0 else InventoryState.RELEASE,
        )

    def invalidate_data_gap(
        self,
        *,
        index: int,
        event_time_ns: int,
        reference_price: float,
        reason_code: str,
    ) -> tuple[Transition, ...]:
        """Terminate an active state without adding a synthetic market bar."""
        if index < 0 or event_time_ns < 0 or reference_price <= 0.0:
            raise ValueError("gap invalidation arguments are inconsistent")
        episode = self._episode
        self._cooldown_until = max(
            self._cooldown_until,
            index + self.config.episode_cooldown_bars,
        )
        if episode is None:
            return tuple()
        previous = episode.state
        episode.state = ScenarioState.INVALIDATED
        transition = Transition(
            scenario_id=episode.scenario_id,
            event_type="POSITIONING_SCENARIO_TRANSITION",
            previous_state=previous.value,
            next_state=ScenarioState.INVALIDATED.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details={
                "branch": episode.branch.value,
                "data_gap": True,
                "synthetic_positioning_used": False,
                "forward_fill_used": False,
                "interpolation_used": False,
            },
        )
        self._episode = None
        return (transition,)


__all__ = ["GapSafePositioningAuctionRouter"]
