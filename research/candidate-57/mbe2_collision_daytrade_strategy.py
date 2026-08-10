"""Candidate 57 cross-asset-confirmed MBE2 short day-trade adapter.

This module does not rewrite the public MBE2 RSI/TEMA entry or ROI ladder.
It adds two independently switchable project-level factors:

1. require a configurable number of simultaneous four-symbol source signals
   before the existing one-slot arbitration is allowed to choose a winner;
2. close an accepted episode at a configurable causal day-trade horizon.

The base source adapter, NautilusTrader execution, realistic costs, current-NAV
3% risk sizing and global one-position account are reused unchanged.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Mapping, Sequence

import strategy_mbe2_base as _base
from router import FeatureObservation, RouteConfig, RouteDecision

_ORIGINAL_ROUTE_UNIVERSE = _base.route_universe
_MIN_ACTIONABLE_CANDIDATES = 1


def _collision_confirmed_route_universe(
    bars_by_symbol: Mapping[str, Sequence[object]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    winner, decisions = _ORIGINAL_ROUTE_UNIVERSE(
        bars_by_symbol=bars_by_symbol,
        features_by_symbol=features_by_symbol,
        config=config,
    )
    actionable = [item for item in decisions.values() if item.actionable]
    if len(actionable) >= int(_MIN_ACTIONABLE_CANDIDATES):
        return winner, decisions

    rejected: dict[str, RouteDecision] = {}
    for symbol, decision in decisions.items():
        if not decision.actionable:
            rejected[symbol] = decision
            continue
        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "candidate57_collision_confirmation_pass": 0,
                "candidate57_actionable_candidates_at_boundary": len(actionable),
                "candidate57_min_actionable_candidates": int(
                    _MIN_ACTIONABLE_CANDIDATES
                ),
            }
        )
        rejected[symbol] = replace(
            decision,
            state=_base.UNRESOLVED,
            side=0,
            score=0.0,
            entry_reference=math.nan,
            stop_reference=math.nan,
            objective_reference=math.nan,
            reasons=("MBE_CROSS_ASSET_CONFIRMATION_ABSENT",),
            diagnostics=diagnostics,
        )
    return None, rejected


# The inherited method resolves this name in its defining module at runtime.
_base.route_universe = _collision_confirmed_route_universe


class Candidate35Config(_base.Candidate35Config, frozen=True):
    mbe_min_actionable_candidates: int = 1
    mbe_daytrade_max_hold_minutes: int = 10_080


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        global _MIN_ACTIONABLE_CANDIDATES
        minimum = int(config.mbe_min_actionable_candidates)
        horizon = int(config.mbe_daytrade_max_hold_minutes)
        if minimum < 1 or minimum > 4:
            raise ValueError("mbe_min_actionable_candidates must be in [1, 4]")
        if horizon < 1:
            raise ValueError("mbe_daytrade_max_hold_minutes must be positive")
        _MIN_ACTIONABLE_CANDIDATES = minimum
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_mbe_collision_confirmation": 1,
                "mbe_min_actionable_candidates": minimum,
                "mbe_daytrade_max_hold_minutes": horizon,
                "mbe_daytrade_horizon_exits": 0,
                "source_entry_logic_changed": 0,
                "source_roi_ladder_changed": 0,
                "collision_filter_before_arbitration": 1,
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is not None and self.current_scenario is not None:
            if self.current_scenario.get("state") == _base.MBE_STATE:
                age = max(0, self.minute_index - self.position_open_minute)
                horizon = int(self.config.mbe_daytrade_max_hold_minutes)
                if age >= horizon:
                    self._close_source_position(
                        "CANDIDATE57_MBE_DAYTRADE_HORIZON",
                        ts_event,
                        age_minutes=age,
                        horizon_minutes=horizon,
                    )
                    self.diagnostics["mbe_daytrade_horizon_exits"] += 1
                    return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
