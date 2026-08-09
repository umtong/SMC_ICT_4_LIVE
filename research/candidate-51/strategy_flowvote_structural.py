"""Structural-risk execution wrapper for the public flow-vote strategy."""
from __future__ import annotations

from dataclasses import replace

import strategy_flowvote as _source


class Candidate35Config(_source.Candidate35Config, frozen=True):
    flowvote_structural_lookback_minutes: int = 30
    flowvote_structural_atr_buffer: float = 0.50
    flowvote_remote_target_r: float = 10.0


class Candidate35Strategy(_source.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            flowvote_structural_lookback_minutes=int(
                config.flowvote_structural_lookback_minutes
            ),
            flowvote_structural_atr_buffer=float(
                config.flowvote_structural_atr_buffer
            ),
            flowvote_remote_target_r=float(config.flowvote_remote_target_r),
        )
        self.diagnostics.update(
            {
                "flowvote_risk_variant": "recent-balance-structural",
                "flowvote_structural_lookback_minutes": int(
                    config.flowvote_structural_lookback_minutes
                ),
                "flowvote_structural_atr_buffer": float(
                    config.flowvote_structural_atr_buffer
                ),
                "flowvote_remote_target_r": float(
                    config.flowvote_remote_target_r
                ),
                "flowvote_management_variant": (
                    "opposite-strong-vote-or-daytrade-timeout"
                ),
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
