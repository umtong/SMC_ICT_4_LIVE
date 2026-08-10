"""Single-variable ablation for candidate-02 v89.

This diagnostic removes only the minimum common-impact-retention gate.  The
same two completed confirmation minutes, spot participation, spot flow,
basis-share, confirmation flow, trade direction, price geometry, risk and cost
model remain unchanged.  NautilusTrader continues to own execution and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass

from v89_cross_market_impact_core import (
    MODES,
    CrossMarketImpactConfig,
    build_rotation_signals,
    build_state,
)


@dataclass(frozen=True, slots=True)
class ImpactRetentionAblationConfig(CrossMarketImpactConfig):
    """v89 config which permits the locked impact-retention gate removal."""

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v89 ablation mode: {self.mode}")
        if self.prior_days < 2 or self.prior_minimum_events < 32:
            raise ValueError("insufficient prospective event history")
        for name in (
            "opening_flow_abs_quantile",
            "opening_turnover_quantile",
            "opening_abs_return_quantile",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if self.confirmation_minutes not in {1, 2, 3}:
            raise ValueError("confirmation_minutes must remain 1, 2 or 3")
        if self.accepted_range_minutes not in {15, 30, 60}:
            raise ValueError("accepted_range_minutes must be 15, 30 or 60")
        if not 0.0 < self.minimum_opening_flow_ratio < 1.0:
            raise ValueError("invalid opening flow floor")
        if not 0.0 <= self.maximum_transient_spot_participation < self.minimum_common_spot_participation:
            raise ValueError("spot-participation states must remain separated")
        if self.minimum_common_impact_retention > -1_000.0:
            raise ValueError("the locked ablation must remove the common impact-retention gate")
        if not 0.0 <= self.maximum_transient_impact_retention <= 1.0:
            raise ValueError("transient impact-retention definition changed")
        if self.atr_lookback_minutes < 30 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid horizon")
        if self.stop_buffer_atr < 0.0 or self.target_range_multiple <= 0.0:
            raise ValueError("invalid price geometry")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")


__all__ = [
    "ImpactRetentionAblationConfig",
    "build_state",
    "build_rotation_signals",
]
