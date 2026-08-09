"""NautilusTrader execution wrapper for the inverse flow-vote auction fade."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import strategy_flowvote as _source


class Candidate35Config(_source.Candidate35Config, frozen=True):
    flowvote_fade_lookback_minutes: int = 30
    flowvote_fade_atr_buffer: float = 0.50
    flowvote_fade_min_reward_r: float = 0.75
    flowvote_fade_min_target_fraction: float = 0.0040
    flowvote_fade_min_displacement_atr: float = 0.35


class Candidate35Strategy(_source.Candidate35Strategy):
    """One global slot with midpoint fade target and opposite-fade exit."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            flowvote_fade_lookback_minutes=int(
                config.flowvote_fade_lookback_minutes
            ),
            flowvote_fade_atr_buffer=float(
                config.flowvote_fade_atr_buffer
            ),
            flowvote_fade_min_reward_r=float(
                config.flowvote_fade_min_reward_r
            ),
            flowvote_fade_min_target_fraction=float(
                config.flowvote_fade_min_target_fraction
            ),
            flowvote_fade_min_displacement_atr=float(
                config.flowvote_fade_min_displacement_atr
            ),
        )
        self.diagnostics.update(
            {
                "flowvote_direction_policy": "inverse-auction-fade",
                "flowvote_fade_lookback_minutes": int(
                    config.flowvote_fade_lookback_minutes
                ),
                "flowvote_fade_atr_buffer": float(
                    config.flowvote_fade_atr_buffer
                ),
                "flowvote_fade_min_reward_r": float(
                    config.flowvote_fade_min_reward_r
                ),
                "flowvote_fade_min_target_fraction": float(
                    config.flowvote_fade_min_target_fraction
                ),
                "flowvote_fade_min_displacement_atr": float(
                    config.flowvote_fade_min_displacement_atr
                ),
            }
        )

    def _submit_decision(self, decision: Any, ts_event: int) -> None:
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(decision, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-public-flowvote-auction-fade",
                    "direction_policy": "inverse-of-public-continuation-vote",
                    "risk_geometry": (
                        "recent-balance-extreme-plus-atr-buffer"
                    ),
                    "objective_geometry": "recent-balance-midpoint",
                    "management": (
                        "midpoint-bracket-opposite-fade-vote-timeout"
                    ),
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
