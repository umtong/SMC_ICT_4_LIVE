"""Freeze the first causal objective when the five-minute hold confirms.

The first efficient-pullback implementation selected its target at the later
one-minute entry.  If price had already consumed the original first obstacle
while the setup waited for a return, that could silently replace it with a more
distant objective.  A discretionary chart plan would instead be finished.

This correction freezes the nearest pre-existing significant 1m/5m/15m target
on the completed hold candle, cancels the setup if that target trades before
entry, and reuses exactly that target in the immutable plan.  No signal,
direction, stop, risk, account or execution rule is otherwise changed.
"""
from __future__ import annotations

from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import StructureZone, V5TradePlan
from domain import Candle, Side
from easychart_re1_efficient_pullback import (
    EFFICIENT_PULLBACK_IMPULSE_RULE,
    EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
    EFFICIENT_PULLBACK_OBJECTIVE_RULE,
    EFFICIENT_PULLBACK_RESPONSE_RULE,
    EfficientPullbackEngine,
    EasyChartRE1EfficientPullbackBundle,
)
from easychart_re1_local_auction_continuation import EasyChartRE1LocalAuctionStrategy


EFFICIENT_PULLBACK_FROZEN_TARGET_RULE = (
    "SOURCE_AMBIGUITY_TRANSLATION:"
    "FIRST_CAUSAL_OPPOSING_OBJECTIVE_IS_FROZEN_ON_THE_ACCEPTANCE_HOLD_AND_A_TOUCH_BEFORE_ENTRY_ENDS_THE_SETUP"
)
if EFFICIENT_PULLBACK_FROZEN_TARGET_RULE not in _contracts.TRANSLATION_RULES:
    _contracts.TRANSLATION_RULES += (EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,)


class FrozenTargetEfficientPullbackEngine(EfficientPullbackEngine):
    """Efficient pullback whose first target cannot move farther while waiting."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._frozen_targets: dict[str, tuple[StructureZone, float]] = {}

    def _finish(self, setup, reason: str, time_ns: int, **values: Any) -> None:  # type: ignore[no-untyped-def]
        self._frozen_targets.pop(setup.setup_id, None)
        super()._finish(setup, reason, time_ns, **values)

    def _advance_holds(self, bar: Candle) -> None:
        before = {
            setup.setup_id: setup.state
            for setup in self._active.values()
        }
        super()._advance_holds(bar)
        for setup in list(self._active.values()):
            if setup.state != "WAITING_RETEST":
                continue
            if before.get(setup.setup_id) != "WAITING_HOLD":
                continue
            target = EfficientPullbackEngine._target(self, setup, bar)
            if target is None:
                self._finish(
                    setup,
                    "efficient_pullback_no_target_on_acceptance_hold",
                    bar.ts_close_ns,
                )
                continue
            self._frozen_targets[setup.setup_id] = target
            self._inc("efficient_pullback_target_frozen_on_hold")
            self._trace(
                "efficient_pullback_target_frozen_on_hold",
                bar.ts_close_ns,
                setup_id=setup.setup_id,
                side=setup.side.name,
                target_zone_id=target[0].zone_id,
                target_price=target[1],
                rule_provenance=EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
            )

    def _target(self, setup, bar: Candle):  # type: ignore[no-untyped-def]
        return self._frozen_targets.get(setup.setup_id)

    @staticmethod
    def _frozen_target_touched(side: Side, target_price: float, bar: Candle) -> bool:
        return bar.high >= target_price if side is Side.LONG else bar.low <= target_price

    def _advance_micro_setups(self, bar: Candle, observation) -> list[V5TradePlan]:  # type: ignore[no-untyped-def]
        for setup in list(self._active.values()):
            if setup.state not in {"WAITING_RETEST", "WAITING_RESPONSE"}:
                continue
            frozen = self._frozen_targets.get(setup.setup_id)
            if frozen is None:
                self._finish(
                    setup,
                    "efficient_pullback_lost_frozen_target",
                    bar.ts_close_ns,
                )
                continue
            if setup.hold_time_ns is not None and bar.ts_close_ns > setup.hold_time_ns:
                if self._frozen_target_touched(setup.side, frozen[1], bar):
                    self._finish(
                        setup,
                        "efficient_pullback_target_spent_before_entry",
                        bar.ts_close_ns,
                        target_zone_id=frozen[0].zone_id,
                        target_price=frozen[1],
                        rule_provenance=EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
                    )
        return super()._advance_micro_setups(bar, observation)

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["frozen_target_policy"] = {
            "frozen_active": len(self._frozen_targets),
            "rule_provenance": EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
        }
        return output


class EasyChartRE1EfficientPullbackV2Bundle(EasyChartRE1EfficientPullbackBundle):
    """Integrated policy with causally frozen efficient-pullback objectives."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        self.efficient_pullback = FrozenTargetEfficientPullbackEngine(
            symbol,
            tick_size,
            minimum_gross_rr,
        )

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["efficient_pullback_v2"] = {
            "rules": (
                EFFICIENT_PULLBACK_LOCAL_LEG_RULE,
                EFFICIENT_PULLBACK_IMPULSE_RULE,
                EFFICIENT_PULLBACK_RESPONSE_RULE,
                EFFICIENT_PULLBACK_OBJECTIVE_RULE,
                EFFICIENT_PULLBACK_FROZEN_TARGET_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1EfficientPullbackV2Bundle
StrategyClass = EasyChartRE1LocalAuctionStrategy
