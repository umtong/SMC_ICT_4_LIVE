"""Implementation-only repair for the frozen source-faithful DRPT selector.

The v2 policy itself is unchanged.  Two jobs reached a funding blackout and
crashed only because the strategy-specific diagnostics dictionary did not
initialize ``funding_runway_rejections`` before incrementing it.  This wrapper
adds that missing counter and deliberately changes no market state, threshold,
entry, invalidation, objective, management, risk, or arbitration rule.
"""
from __future__ import annotations

from strategy_drpt_source_selector import Candidate35Config
from strategy_drpt_source_selector import Candidate35Strategy as _FrozenStrategy


class Candidate35Strategy(_FrozenStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.setdefault("funding_runway_rejections", 0)
        self.diagnostics["implementation_repair"] = (
            "initialize missing funding_runway_rejections diagnostic counter"
        )
        self.diagnostics["economic_policy_changed"] = 0


__all__ = ["Candidate35Config", "Candidate35Strategy"]
