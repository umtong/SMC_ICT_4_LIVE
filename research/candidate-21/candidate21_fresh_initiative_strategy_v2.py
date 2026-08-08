"""Risk-efficient Fresh Initiative execution.

V1 proved that the auction policy can produce post-cost alpha, but its entry
limit was the *furthest* price that still preserved minimum reward/risk. The
same distant limit was also used for sizing. A marketable limit normally filled
far better than that boundary, so actual planned risk was often only a small
fraction of the permitted 3% NAV budget.

V2 keeps every signal, target, stop, fee, slippage and shared-account rule
unchanged. It limits execution to the modeled adverse-slippage fill and sizes
from that identical price. Thus the quantity formula matches the project's
"expected entry fill plus expected costs" contract without permitting a fill
beyond modeled slippage or exceeding the 3% planned-loss budget.
"""
from __future__ import annotations

import math

from candidate21_fresh_initiative_strategy import FreshInitiativeAcceptanceMixin
from flow_inflection_logic import worst_entry_preserving_net_r
from logic import net_r_at_price, planned_loss_per_unit
from strategy_base import _as_float
from strategy_v26 import ScenarioValidEntryStrategy


def modeled_adverse_fill_raw(
    observed_price: float,
    side: int,
    adverse_slippage_rate: float,
) -> float:
    if not math.isfinite(observed_price) or observed_price <= 0.0:
        return math.nan
    if side not in (-1, 1):
        return math.nan
    if not math.isfinite(adverse_slippage_rate) or adverse_slippage_rate < 0.0:
        return math.nan
    return observed_price * (1.0 + side * adverse_slippage_rate)


class RiskEfficientFreshInitiativeMixin(FreshInitiativeAcceptanceMixin):
    """Override only entry protection/sizing geometry; alpha stays frozen."""

    def _geometry(self, *, side: int, observed: float, stop: float, target: float):
        cost = self.config.all_in_cost_bps_each_side / 10_000.0
        slip = self.config.adverse_slippage_bps_each_side / 10_000.0

        reward_bound = worst_entry_preserving_net_r(
            stop=stop,
            target=target,
            side=side,
            minimum_net_r=self.config.min_target_net_r,
            cost_rate=cost,
            adverse_slippage_rate=slip,
        )
        modeled_raw = modeled_adverse_fill_raw(observed, side, slip)
        if not math.isfinite(reward_bound) or not math.isfinite(modeled_raw):
            return None

        increment = _as_float(self.instrument.price_increment)
        price = self.instrument.make_price(modeled_raw)
        entry = _as_float(price)
        # Price quantization must remain adverse, never silently improve the
        # assumed fill used for risk sizing.
        if side > 0 and entry < modeled_raw:
            price = self.instrument.make_price(modeled_raw + increment)
            entry = _as_float(price)
        elif side < 0 and entry > modeled_raw:
            price = self.instrument.make_price(modeled_raw - increment)
            entry = _as_float(price)

        reward_preserved = entry <= reward_bound if side > 0 else entry >= reward_bound
        structural = (
            stop < observed <= entry < target
            if side > 0
            else target < entry <= observed < stop
        )
        if not reward_preserved or not structural:
            return None

        loss = planned_loss_per_unit(entry, stop, side, cost, slip)
        if not math.isfinite(loss) or loss <= 0.0:
            return None
        target_r = net_r_at_price(entry, target, side, loss, cost)
        if target_r + 1e-9 < self.config.min_target_net_r:
            return None
        return price, entry, loss, target_r


class RiskEfficientFreshInitiativeStrategy(
    RiskEfficientFreshInitiativeMixin,
    ScenarioValidEntryStrategy,
):
    pass


CandidateStrategy = RiskEfficientFreshInitiativeStrategy
StrategyClass = RiskEfficientFreshInitiativeStrategy
SystemicRepricingGateMixin = RiskEfficientFreshInitiativeMixin
SystemicRepricingGateStrategy = RiskEfficientFreshInitiativeStrategy

__all__ = [
    "RiskEfficientFreshInitiativeMixin",
    "RiskEfficientFreshInitiativeStrategy",
    "SystemicRepricingGateMixin",
    "SystemicRepricingGateStrategy",
    "modeled_adverse_fill_raw",
]
