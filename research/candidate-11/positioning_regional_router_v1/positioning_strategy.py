"""Regional spot/perpetual/L1 router augmented by open-interest state."""
from __future__ import annotations

from dataclasses import replace

from effort_result_router import AuctionDecision
from regional_strategy import RegionalSpotPerpConfig
from regional_strategy import RegionalSpotPerpStrategy


class PositioningRegionalConfig(RegionalSpotPerpConfig, frozen=True):
    pass


class PositioningRegionalStrategy(RegionalSpotPerpStrategy):
    """Separate new-risk price discovery from forced position closure."""

    def __init__(self, config: PositioningRegionalConfig) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "positioning_interactions": 0,
                "positioning_new_risk_attacks": 0,
                "positioning_forced_closure_attacks": 0,
                "positioning_oi_conflict_rejections": 0,
                "positioning_failed_state_rejections": 0,
            },
        )

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        before = self.parent_auction
        super()._detect_sweep(row, previous_close)
        if before is not None or self.parent_auction is None or self.pending is None:
            return
        oi_change = self._finite_feature("oi_change_5m")
        oi_value_change = self._finite_feature("oi_value_change_5m")
        broad = bool(self.pending.details.get("spot_perp_broad_attack", False))
        perp_only = bool(self.pending.details.get("spot_perp_perp_only_attack", False))
        new_risk = broad and oi_change >= 0.0 and oi_value_change >= 0.0
        forced_closure = perp_only and oi_change < 0.0
        self.pending.details["positioning_interaction"] = {
            "oi_change_5m": oi_change,
            "oi_value_change_5m": oi_value_change,
            "new_risk_price_discovery": new_risk,
            "forced_position_closure": forced_closure,
        }
        self.pending.details["positioning_new_risk_attack"] = new_risk
        self.pending.details["positioning_forced_closure_attack"] = forced_closure
        # Broad price participation without stable/rising OI is not eligible for
        # the immediate continuation branch.  It remains unresolved unless the
        # independent failed-auction state is later completed.
        if broad and not new_risk:
            self.pending.details["spot_perp_broad_attack"] = False
            self.diagnostics["positioning_oi_conflict_rejections"] += 1
        self.diagnostics["positioning_interactions"] += 1
        self.diagnostics["positioning_new_risk_attacks"] += int(new_risk)
        self.diagnostics["positioning_forced_closure_attacks"] += int(forced_closure)

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if (
            state is not None
            and setup is not None
            and state.decision is AuctionDecision.FAILED_AUCTION
            and not bool(setup.details.get("positioning_forced_closure_attack", False))
        ):
            self.diagnostics["positioning_failed_state_rejections"] += 1
            self.parent_auction = replace(
                state,
                decision=AuctionDecision.UNRESOLVED,
                reason="FAILED_AUCTION_WITHOUT_FALLING_OPEN_INTEREST_FORCED_CLOSURE",
            )
        super()._complete_parent(row)


__all__ = ["PositioningRegionalConfig", "PositioningRegionalStrategy"]
