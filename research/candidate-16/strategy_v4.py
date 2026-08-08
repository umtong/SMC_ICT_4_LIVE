"""Candidate 16 v4: whole-minute to closing L1 pressure transitions.

The v2 temporal and execution state machine is preserved. Its coarse ±1% depth
band inputs are replaced with the frozen one-minute bookTicker observations that
actually exist:

- failure: attack-direction TWAP pressure flips by the completed close;
- acceptance: TWAP and closing pressure persist in the same direction;
- later initiative: price, aggressor flow, and L1 pressure persist together.

This is a pressure-persistence policy. It does not claim to observe L3 orders or
same-price queue replenishment.
"""
from __future__ import annotations

from typing import Any

from l1_pressure_router import PressureObservation
from l1_pressure_router import failure_pressure_transition
from l1_pressure_router import pressure_persistence
from l1_pressure_router import pressure_state
from strategy_base import PendingSetup
from strategy_v2 import Candidate16V2Config
from strategy_v2 import Candidate16V2Strategy


class Candidate16V4Config(Candidate16V2Config, frozen=True):
    pass


class Candidate16V4Strategy(Candidate16V2Strategy):
    """Use categorical TWAP-to-close L1 pressure as independent state evidence."""

    def __init__(self, config: Candidate16V4Config) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "candidate16_v4_pressure_observations": 0,
                "candidate16_v4_failure_pressure_flips": 0,
                "candidate16_v4_parent_pressure_persistence": 0,
                "candidate16_v4_pressure_unresolved": 0,
                "candidate16_v4_later_pressure_persistence": 0,
            },
        )

    def _raw_feature(self, name: str) -> float:
        return super()._feature(name)

    def _pressure_observation(self) -> PressureObservation:
        return PressureObservation(
            imbalance_twap=self._raw_feature("bt_imbalance_twap"),
            imbalance_close=self._raw_feature("bt_imbalance_close"),
            microprice_premium_close=self._raw_feature(
                "bt_microprice_premium_close",
            ),
            spread_bps_twap=self._raw_feature("bt_spread_bps_twap"),
            spread_bps_close=self._raw_feature("bt_spread_bps_close"),
            update_rate=self._raw_feature("bt_update_rate"),
        )

    def _directional_persistence(self, direction: int) -> bool:
        value = pressure_persistence(direction, self._pressure_observation())
        if value:
            self.diagnostics["candidate16_v4_later_pressure_persistence"] = int(
                self.diagnostics["candidate16_v4_later_pressure_persistence"],
            ) + 1
        return value

    def _feature(self, name: str) -> float:
        if name == "depth_imbalance_1":
            return self._raw_feature("bt_imbalance_close")
        if name == "depth_snapshot_age_seconds":
            return 0.0
        if name == "ask_depth_change_1_1m":
            return -1.0 if self._directional_persistence(1) else 0.0
        if name == "bid_depth_change_1_1m":
            return -1.0 if self._directional_persistence(-1) else 0.0
        return self._raw_feature(name)

    def _accumulate_displayed_state(
        self,
        setup: PendingSetup,
        direction: int,
    ) -> None:
        observation = self._pressure_observation()
        state = pressure_state(direction, observation)
        flipped = failure_pressure_transition(direction, observation)
        persisted = pressure_persistence(direction, observation)
        reversal_support = -direction * observation.imbalance_close
        acceptance_support = direction * observation.imbalance_close
        details = setup.details

        details["max_reversal_book_support"] = self._max_finite(
            details.get("max_reversal_book_support"),
            reversal_support,
        )
        details["max_acceptance_book_support"] = self._max_finite(
            details.get("max_acceptance_book_support"),
            acceptance_support,
        )
        # v2's pure state contract consumes positive defense and negative
        # withdrawal markers. Here they explicitly mean a completed pressure
        # flip and completed directional pressure persistence respectively.
        details["max_defending_depth_change"] = self._max_finite(
            details.get("max_defending_depth_change"),
            1.0 if flipped else 0.0,
        )
        details["min_liquidity_ahead_change"] = self._min_finite(
            details.get("min_liquidity_ahead_change"),
            -1.0 if persisted else 0.0,
        )
        details["displayed_observation_count"] = int(
            details.get("displayed_observation_count", 0),
        ) + 1
        details["latest_depth_imbalance_1"] = observation.imbalance_close
        details["latest_liquidity_ahead_change"] = (
            -1.0 if persisted else 0.0
        )
        details["latest_depth_snapshot_age_seconds"] = 0.0
        details["latest_l1_pressure"] = {
            "parent_direction": direction,
            "state": state,
            "imbalance_twap": observation.imbalance_twap,
            "imbalance_close": observation.imbalance_close,
            "microprice_premium_close": (
                observation.microprice_premium_close
            ),
            "spread_bps_twap": observation.spread_bps_twap,
            "spread_bps_close": observation.spread_bps_close,
            "update_rate": observation.update_rate,
            "failure_pressure_transition": flipped,
            "parent_pressure_persistence": persisted,
        }

        self.diagnostics["candidate16_v4_pressure_observations"] = int(
            self.diagnostics["candidate16_v4_pressure_observations"],
        ) + 1
        if flipped:
            self.diagnostics["candidate16_v4_failure_pressure_flips"] = int(
                self.diagnostics["candidate16_v4_failure_pressure_flips"],
            ) + 1
        elif persisted:
            self.diagnostics["candidate16_v4_parent_pressure_persistence"] = int(
                self.diagnostics[
                    "candidate16_v4_parent_pressure_persistence"
                ],
            ) + 1
        else:
            self.diagnostics["candidate16_v4_pressure_unresolved"] = int(
                self.diagnostics["candidate16_v4_pressure_unresolved"],
            ) + 1


__all__ = ["Candidate16V4Config", "Candidate16V4Strategy"]
