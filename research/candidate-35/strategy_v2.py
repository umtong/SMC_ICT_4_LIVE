"""Candidate 35b strategy adapter.

This module keeps Candidate 35's verified NautilusTrader account, global slot,
risk sizing and persistence.  It replaces only the trading policy with the
cross-context, sponsored, cost-aware continuation router.
"""
from __future__ import annotations

import math

import strategy as legacy_strategy
from strategy import Candidate35Config as LegacyCandidate35Config
from strategy import Candidate35Strategy as LegacyCandidate35Strategy

from router_v2 import RouteConfig, RouteDecision, economic_net_reward_r, route_universe


class Candidate35V2Config(LegacyCandidate35Config, frozen=True):
    context_bars: int = 60
    context_deadband_atr: float = 0.25
    min_context_median_atr: float = 0.50
    min_context_breadth: int = 3
    min_sponsored_flow: float = 0.02
    min_sponsored_return_bps: float = 0.50
    min_net_reward_r: float = 1.25
    allow_reversal: bool = False


class Candidate35V2Strategy(LegacyCandidate35Strategy):
    """One-account Candidate 35b policy with unchanged Nautilus execution."""

    def __init__(self, config: Candidate35V2Config) -> None:
        super().__init__(config=config)
        self.route_config = RouteConfig(
            atr_period=config.atr_period,
            min_impulse_atr_continuation=config.min_impulse_atr_continuation,
            min_impulse_atr_reversal=config.min_impulse_atr_reversal,
            min_response_atr=config.min_response_atr,
            min_participation_ratio=config.min_participation_ratio,
            min_route_score=config.min_route_score,
            ambiguity_score_gap=config.ambiguity_score_gap,
            continuation_target_r=config.continuation_target_r,
            reversal_target_r=config.reversal_target_r,
            context_bars=config.context_bars,
            context_deadband_atr=config.context_deadband_atr,
            min_context_median_atr=config.min_context_median_atr,
            min_context_breadth=config.min_context_breadth,
            min_sponsored_flow=config.min_sponsored_flow,
            min_sponsored_return_bps=config.min_sponsored_return_bps,
            min_net_reward_r=config.min_net_reward_r,
            all_in_cost_bps_each_side=config.all_in_cost_bps_each_side,
            adverse_slippage_bps_each_side=config.adverse_slippage_bps_each_side,
            funding_reserve_bps=config.funding_reserve_bps,
            allow_reversal=config.allow_reversal,
        )
        self.diagnostics.update(
            {
                "policy_version": "candidate-35b",
                "policy": "CROSS_CONTEXT_SPONSORED_COST_AWARE_CONTINUATION",
                "economic_rejections_at_submit": 0,
            },
        )

    def _submit_decision(self, decision: RouteDecision, ts_event: int) -> None:
        net_r, planned_loss, expected_profit = economic_net_reward_r(
            decision,
            self.route_config,
        )
        if not math.isfinite(net_r) or net_r < self.config.min_net_reward_r:
            self.diagnostics["economic_rejections_at_submit"] = int(
                self.diagnostics["economic_rejections_at_submit"],
            ) + 1
            self._event(
                "ECONOMIC_OBJECTIVE_REJECTED",
                ts_event,
                symbol=decision.symbol,
                state=decision.state,
                economic_net_reward_r=net_r,
                planned_loss_per_unit=planned_loss,
                expected_profit_per_unit=expected_profit,
                required_net_reward_r=self.config.min_net_reward_r,
            )
            return
        super()._submit_decision(decision, ts_event)


# The direct runner intentionally imports ``strategy:Candidate35Strategy``.
# Patch that already collision-checked module before BacktestNode resolves it.
legacy_strategy.route_universe = route_universe
legacy_strategy.Candidate35Config = Candidate35V2Config
legacy_strategy.Candidate35Strategy = Candidate35V2Strategy


__all__ = ["Candidate35V2Config", "Candidate35V2Strategy"]
