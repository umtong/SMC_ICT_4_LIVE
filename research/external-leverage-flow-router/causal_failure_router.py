"""Causal failed-auction router for the exhaustion family.

The first frozen holdout showed that a two-bar price break plus aggressor-flow
reversal can be only a liquidation reset inside an information-driven trend.
This router adds independent derivatives-state transitions rather than tuned
numeric thresholds:

* while price remains outside the parent external-liquidity boundary, the
  five-minute premium change must contract against the parent shock;
* outside the boundary, fifteen-minute open interest must also be stable or
  increasing, so forced position closure is not mistaken for durable opposing
  participation;
* once price has reclaimed the boundary, price itself has supplied the auction
  failure evidence and the derivatives guards are no longer required;
* the target is the nearest unconsumed auction objective in the reversal
  direction: external boundary, balance midpoint, then opposite edge.

This keeps context, state, transition, entry and objective in distinct roles and
prevents a reversal leg from being judged only by the price evidence which
created it.
"""
from __future__ import annotations

import math

from candidate21_strategy import EXHAUSTION_STATE
from market_entry_strategy import WindowedLimitConfig, WindowedLimitStrategy
from spot_perp_router import exhaustion_transition_confirmed


class FailureRouterConfig(WindowedLimitConfig, frozen=True):
    pass


class FailureRouterStrategy(WindowedLimitStrategy):
    """Require causal derivatives failure or actual boundary reclaim."""

    def __init__(self, config: FailureRouterConfig) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "failure_router_price_flow_candidates": 0,
                "failure_router_waiting_premium_contraction": 0,
                "failure_router_waiting_non_liquidation_participation": 0,
                "failure_router_boundary_reclaims": 0,
                "failure_router_premium_failures": 0,
                "failure_router_non_liquidation_participation": 0,
                "failure_router_no_objective": 0,
                "failure_router_objective_boundary": 0,
                "failure_router_objective_midpoint": 0,
                "failure_router_objective_opposite": 0,
            },
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != EXHAUSTION_STATE:
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["exhaustion_states_expired"] = int(
                self.diagnostics["exhaustion_states_expired"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "EXHAUSTION_STATE_EXPIRED",
                "NO_CAUSAL_FAILED_AUCTION_WITHIN_EPISODE",
            )
            return True

        self.diagnostics["exhaustion_later_observations"] = int(
            self.diagnostics["exhaustion_later_observations"],
        ) + 1
        direction = int(setup.details["event_direction"])
        if direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))

        structure_bars = self.config.exhaustion_transition_structure_bars
        rows = list(self.bars)
        if len(rows) < structure_bars + 1:
            return True
        prior = rows[-(structure_bars + 1) : -1]
        prior_high = max(float(item["high"]) for item in prior)
        prior_low = min(float(item["low"]) for item in prior)
        close = float(row["close"])
        perp_flow = self._feature("flow_60s")
        premium_change_5m = self._feature("premium_change_5m")
        oi_change_15m = self._feature("oi_change_15m")
        boundary = float(setup.details["boundary"])
        observation = {
            "bar_index": self.bar_index,
            "ts": int(row["ts"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": close,
            "prior_structure_high": prior_high,
            "prior_structure_low": prior_low,
            "perp_flow": perp_flow,
            "spot_flow": self._feature("spot_flow_60s"),
            "premium_change_5m": premium_change_5m,
            "oi_change_15m": oi_change_15m,
            "basis_bps": self._feature("perp_spot_basis_bps"),
            "basis_change_1m_bps": self._feature("perp_spot_basis_change_1m_bps"),
        }
        setup.details["latest_later_observation"] = observation

        price_flow_candidate = exhaustion_transition_confirmed(
            event_direction=direction,
            close=close,
            prior_high=prior_high,
            prior_low=prior_low,
            perp_flow=perp_flow,
        )
        if not price_flow_candidate:
            return True
        self.diagnostics["failure_router_price_flow_candidates"] = int(
            self.diagnostics["failure_router_price_flow_candidates"],
        ) + 1

        boundary_reclaimed = close <= boundary if direction > 0 else close >= boundary
        premium_failed = (
            math.isfinite(premium_change_5m)
            and direction * premium_change_5m < 0.0
        )
        non_liquidation_participation = (
            math.isfinite(oi_change_15m)
            and oi_change_15m >= 0.0
        )
        if boundary_reclaimed:
            self.diagnostics["failure_router_boundary_reclaims"] = int(
                self.diagnostics["failure_router_boundary_reclaims"],
            ) + 1
        elif not premium_failed:
            self.diagnostics["failure_router_waiting_premium_contraction"] = int(
                self.diagnostics["failure_router_waiting_premium_contraction"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "REVERSAL_PRICE_FLOW_WITHOUT_PREMIUM_FAILURE",
                int(row["ts"]),
                int(row["ts"]),
                "WAITING_FOR_CAUSAL_FAILED_AUCTION",
                "PREMIUM_STILL_SUPPORTS_PARENT_SHOCK_OUTSIDE_BOUNDARY",
                close,
                {**setup.details, "candidate_observation": observation},
            )
            return True
        elif not non_liquidation_participation:
            self.diagnostics["failure_router_waiting_non_liquidation_participation"] = int(
                self.diagnostics["failure_router_waiting_non_liquidation_participation"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "REVERSAL_PRICE_FLOW_DURING_OI_CONTRACTION",
                int(row["ts"]),
                int(row["ts"]),
                "WAITING_FOR_CAUSAL_FAILED_AUCTION",
                "COUNTERFLOW_CAN_BE_FORCED_LIQUIDATION_RESET_OUTSIDE_BOUNDARY",
                close,
                {**setup.details, "candidate_observation": observation},
            )
            return True
        else:
            self.diagnostics["failure_router_premium_failures"] = int(
                self.diagnostics["failure_router_premium_failures"],
            ) + 1
            self.diagnostics["failure_router_non_liquidation_participation"] = int(
                self.diagnostics["failure_router_non_liquidation_participation"],
            ) + 1

        side = -direction
        balance_high = float(setup.details["prior_balance_high"])
        balance_low = float(setup.details["prior_balance_low"])
        midpoint = 0.5 * (balance_high + balance_low)
        opposite = float(setup.details["opposite_edge"])
        ordered = [
            ("boundary", boundary),
            ("midpoint", midpoint),
            ("opposite", opposite),
        ]
        favorable = [
            (name, price)
            for name, price in ordered
            if side * (price - close) > 0.0
        ]
        if not favorable:
            self.diagnostics["failure_router_no_objective"] = int(
                self.diagnostics["failure_router_no_objective"],
            ) + 1
            self._close_exhaustion_state(
                setup,
                row,
                "FAILED_AUCTION_OBJECTIVES_ALREADY_CONSUMED",
                "NO_UNCONSUMED_BOUNDARY_MIDPOINT_OR_OPPOSITE_EDGE",
            )
            return True
        objective_name, target = min(favorable, key=lambda item: abs(item[1] - close))
        key = f"failure_router_objective_{objective_name}"
        self.diagnostics[key] = int(self.diagnostics[key]) + 1

        self.diagnostics["exhaustion_transitions"] = int(
            self.diagnostics["exhaustion_transitions"],
        ) + 1
        atr = self._atr()
        stop = setup.sweep_extreme - side * self.config.exhaustion_stop_buffer_atr * atr
        transition_details = {
            **setup.details,
            "transition_observation": observation,
            "boundary_reclaimed": boundary_reclaimed,
            "premium_failed_against_parent": premium_failed,
            "non_liquidation_participation": non_liquidation_participation,
            "selected_objective": objective_name,
            "selected_objective_price": target,
            "balance_midpoint": midpoint,
            "state_evidence_role": "PARENT_EXHAUSTION_ONLY",
            "price_flow_role": "REVERSAL_LEG_CANDIDATE_ONLY",
            "derivatives_or_reclaim_role": "FAILED_AUCTION_CONFIRMATION_ONLY",
            "objective_role": "NEAREST_UNCONSUMED_AUCTION_OBJECTIVE",
        }
        setup.details = transition_details
        self._transition(
            setup.scenario_id,
            "CAUSAL_FAILED_AUCTION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "REVERSAL_LEG_READY",
            (
                "BOUNDARY_RECLAIMED"
                if boundary_reclaimed
                else "PREMIUM_FAILED_WITH_NON_LIQUIDATION_PARTICIPATION"
            ),
            close,
            transition_details,
        )
        return self._submit_exhaustion_entry(
            setup=setup,
            row=row,
            side=side,
            stop_raw=stop,
            target_raw=target,
        )


__all__ = ["FailureRouterConfig", "FailureRouterStrategy"]
