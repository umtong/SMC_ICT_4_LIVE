"""Spot/perpetual participation router on completed-session auctions."""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

from effort_result_router import AuctionDecision
from strategy_base import PendingSetup
from strategy_v4 import Candidate16V4Config
from strategy_v4 import Candidate16V4Strategy


class SpotPerpSessionConfig(Candidate16V4Config, frozen=True):
    pass


class SpotPerpSessionStrategy(Candidate16V4Strategy):
    """Assign spot/perpetual and L1 observations to distinct causal roles."""

    def __init__(self, config: SpotPerpSessionConfig) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "spot_perp_interactions": 0,
                "spot_perp_broad_attacks": 0,
                "spot_perp_perp_only_attacks": 0,
                "spot_perp_failed_state_rejections": 0,
                "spot_perp_acceptance_state_rejections": 0,
                "spot_perp_post_interaction_observations": 0,
            },
        )

    def _finite_feature(self, name: str) -> float:
        value = float(self._raw_feature(name))
        return value if math.isfinite(value) else 0.0

    @staticmethod
    def _directional_pair(
        direction: int,
        flow: float,
        return_bps: float,
    ) -> bool:
        return direction * flow > 0.0 and direction * return_bps > 0.0

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        before = self.parent_auction
        super()._detect_sweep(row, previous_close)
        setup = self.pending
        if before is not None or self.parent_auction is None or setup is None:
            return
        if setup.created_index != self.bar_index or setup.branch != "OBSERVATION":
            return

        direction = int(setup.details["parent_direction"])
        spot_flow = self._finite_feature("spot_flow_60s")
        spot_return = self._finite_feature("spot_ret_60s_bps")
        perp_flow = self._finite_feature("flow_60s")
        perp_return = self._finite_feature("ret_60s_bps")
        basis_change = self._finite_feature("basis_change_bps")

        spot_attack = self._directional_pair(direction, spot_flow, spot_return)
        perp_attack = self._directional_pair(direction, perp_flow, perp_return)
        basis_expanded = direction * basis_change > 0.0
        broad_attack = spot_attack and perp_attack
        perp_only_attack = perp_attack and not spot_attack and basis_expanded

        setup.details["spot_perp_interaction"] = {
            "direction": direction,
            "spot_flow_60s": spot_flow,
            "spot_ret_60s_bps": spot_return,
            "perp_flow_60s": perp_flow,
            "perp_ret_60s_bps": perp_return,
            "basis_change_bps": basis_change,
            "spot_attack_confirmed": spot_attack,
            "perp_attack_confirmed": perp_attack,
            "basis_expanded_in_attack_direction": basis_expanded,
            "broad_attack": broad_attack,
            "perp_only_attack": perp_only_attack,
        }
        setup.details["spot_perp_broad_attack"] = broad_attack
        setup.details["spot_perp_perp_only_attack"] = perp_only_attack
        self.diagnostics["spot_perp_interactions"] += 1
        self.diagnostics["spot_perp_broad_attacks"] += int(broad_attack)
        self.diagnostics["spot_perp_perp_only_attacks"] += int(perp_only_attack)

    def _accumulate_displayed_state(
        self,
        setup: PendingSetup,
        direction: int,
    ) -> None:
        # L1 pressure remains execution-state evidence.
        super()._accumulate_displayed_state(setup, direction)
        spot_flow = self._finite_feature("spot_flow_60s")
        spot_return = self._finite_feature("spot_ret_60s_bps")
        perp_flow = self._finite_feature("flow_60s")
        perp_return = self._finite_feature("ret_60s_bps")
        spot_confirms_parent = self._directional_pair(
            direction,
            spot_flow,
            spot_return,
        )
        spot_rejects_parent = (
            direction * spot_flow < 0.0
            and direction * spot_return < 0.0
        )
        perp_confirms_parent = self._directional_pair(
            direction,
            perp_flow,
            perp_return,
        )
        details = setup.details
        details["spot_post_confirms_parent"] = bool(
            details.get("spot_post_confirms_parent", False)
            or spot_confirms_parent
        )
        details["spot_post_rejects_parent"] = bool(
            details.get("spot_post_rejects_parent", False)
            or spot_rejects_parent
        )
        details["perp_post_confirms_parent"] = bool(
            details.get("perp_post_confirms_parent", False)
            or perp_confirms_parent
        )
        details["latest_spot_perp_state"] = {
            "spot_flow_60s": spot_flow,
            "spot_ret_60s_bps": spot_return,
            "perp_flow_60s": perp_flow,
            "perp_ret_60s_bps": perp_return,
            "spot_confirms_parent": spot_confirms_parent,
            "spot_rejects_parent": spot_rejects_parent,
            "perp_confirms_parent": perp_confirms_parent,
        }
        self.diagnostics["spot_perp_post_interaction_observations"] += 1

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return

        if state.decision is AuctionDecision.FAILED_AUCTION:
            valid = bool(setup.details.get("spot_perp_perp_only_attack", False)) and bool(
                setup.details.get("spot_post_rejects_parent", False),
            )
            if not valid:
                self.diagnostics["spot_perp_failed_state_rejections"] += 1
                self.parent_auction = replace(
                    state,
                    decision=AuctionDecision.UNRESOLVED,
                    reason="FAILED_AUCTION_WITHOUT_PERP_ONLY_ATTACK_AND_SPOT_REJECTION",
                )
        elif state.decision is AuctionDecision.ACCEPTANCE_CONTINUATION:
            valid = bool(setup.details.get("spot_perp_broad_attack", False)) and bool(
                setup.details.get("spot_post_confirms_parent", False),
            )
            if not valid:
                self.diagnostics["spot_perp_acceptance_state_rejections"] += 1
                self.parent_auction = replace(
                    state,
                    decision=AuctionDecision.UNRESOLVED,
                    reason="ACCEPTANCE_WITHOUT_BROAD_SPOT_PERP_PARTICIPATION",
                )
        super()._complete_parent(row)


__all__ = ["SpotPerpSessionConfig", "SpotPerpSessionStrategy"]
