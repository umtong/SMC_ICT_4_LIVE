"""v43 funded microstructure risk-transfer state."""
from __future__ import annotations

from c10_v40_state import SourceEquilibriumFailedAuctionEngine


class FundedMicroRiskTransferEngine(SourceEquilibriumFailedAuctionEngine):
    """v41 scenario with explicit partially funded residual-risk ownership."""

    def mark_funded_micro_reduction(
        self,
        *,
        observed_ts_ns: int,
        pivot_event_ts_ns: int,
        direction: str,
        pivot_level: float,
        entry_reference: float,
        partial_quantity: float,
        residual_quantity: float,
        locked_profit: float,
        residual_max_loss: float,
    ) -> None:
        if self.active_trade_id is None:
            return
        previous_state = self.active_trade_state or "POSITION"
        self._event(
            self.active_trade_id,
            "FUNDED_MICRO_RISK_TRANSFER_CONFIRMED",
            pivot_event_ts_ns,
            observed_ts_ns,
            previous_state,
            "FUNDED_RESIDUAL_RUNNER",
            "CONFIRMED_MICROSTRUCTURE_PROFIT_FUNDS_ORIGINAL_RESIDUAL_STOP",
            entry_reference,
            {
                "direction": direction,
                "pivot_timeframe": "ONE_MINUTE",
                "pivot_level": pivot_level,
                "entry_reference": entry_reference,
                "partial_quantity": partial_quantity,
                "residual_quantity": residual_quantity,
                "locked_profit": locked_profit,
                "residual_max_loss": residual_max_loss,
                "partial_fraction_contract": (
                    "minimum solved quantity; locked modeled all-cost profit is "
                    "not less than complete original-stop loss of residual"
                ),
                "residual_target": "SOURCE_DEALING_RANGE_EQUILIBRIUM",
                "residual_invalidation": "ORIGINAL_SOURCE_RAID_INVALIDATION",
            },
        )
        self.active_trade_state = "FUNDED_RESIDUAL_RUNNER"


__all__ = ["FundedMicroRiskTransferEngine"]
