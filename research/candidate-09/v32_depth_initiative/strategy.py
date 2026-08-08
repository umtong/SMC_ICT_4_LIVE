"""Candidate 09 v32: displayed-liquidity defense followed by independent initiative.

Reuses Candidate 16 v1's causal parent-auction detector and Candidate 05's
NautilusTrader/data stack. The failed-auction label is never an entry. A
reversal is allowed only when (1) displayed liquidity on the attacked side
replenished during the failed interaction and (2) a later completed bar shows
opposite initiative with independent price, aggressor-flow and book support.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from effort_result_router import AuctionDecision
from strategy_base import PendingSetup
from strategy_v1 import Candidate16Config as _Candidate16V1Config
from strategy_v1 import Candidate16Strategy as _Candidate16V1Strategy


class Candidate16Config(_Candidate16V1Config, frozen=True):
    """Frozen v32 configuration exposed under the v1 runner contract."""

    candidate32_use_displayed_depth: bool = True
    candidate32_trade_acceptance: bool = False
    candidate32_min_defending_depth_refill: float = 0.01
    candidate32_initiative_timeout_bars: int = 3
    candidate32_min_initiative_body_atr: float = 0.20
    candidate32_min_initiative_flow: float = 0.08
    candidate32_min_initiative_efficiency: float = 0.25
    candidate32_min_initiative_close_location: float = 0.60
    candidate32_min_initiative_depth_support: float = 0.01


def initiative_depth_support(side: int, bid_change: float, ask_change: float) -> float:
    """Depth support for the proposed direction using completed public snapshots.

    Long initiative is supported by bid replenishment or ask withdrawal. Short
    initiative is supported by ask replenishment or bid withdrawal.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or +1")
    bid = bid_change if math.isfinite(bid_change) else 0.0
    ask = ask_change if math.isfinite(ask_change) else 0.0
    return max(bid, -ask) if side > 0 else max(ask, -bid)


def initiative_passes(
    *,
    side: int,
    open_price: float,
    high: float,
    low: float,
    close_price: float,
    structure: float,
    atr: float,
    flow_60s: float,
    efficiency_60s: float,
    bid_depth_change_1m: float,
    ask_depth_change_1m: float,
    use_displayed_depth: bool,
    min_body_atr: float,
    min_flow: float,
    min_efficiency: float,
    min_close_location: float,
    min_depth_support: float,
) -> tuple[bool, dict[str, float | bool]]:
    if side not in (-1, 1) or not math.isfinite(atr) or atr <= 0.0:
        return False, {"valid_geometry": False}
    span = max(high - low, 1e-12)
    body_atr = side * (close_price - open_price) / atr
    directional_flow = side * (flow_60s if math.isfinite(flow_60s) else 0.0)
    efficiency = efficiency_60s if math.isfinite(efficiency_60s) else 0.0
    close_location = (
        (close_price - low) / span
        if side > 0
        else (high - close_price) / span
    )
    structure_broken = close_price > structure if side > 0 else close_price < structure
    depth_support = initiative_depth_support(
        side,
        bid_depth_change_1m,
        ask_depth_change_1m,
    )
    depth_pass = (not use_displayed_depth) or depth_support >= min_depth_support
    passed = (
        structure_broken
        and body_atr >= min_body_atr
        and directional_flow >= min_flow
        and efficiency >= min_efficiency
        and close_location >= min_close_location
        and depth_pass
    )
    return passed, {
        "valid_geometry": True,
        "structure_broken": structure_broken,
        "body_atr": body_atr,
        "directional_flow": directional_flow,
        "efficiency_60s": efficiency,
        "close_location": close_location,
        "initiative_depth_support": depth_support,
        "depth_pass": depth_pass,
    }


class Candidate16Strategy(_Candidate16V1Strategy):
    """V32 state machine under Candidate 16's proven runner interface."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self._candidate32_peak_defending_refill = 0.0
        self.diagnostics.update(
            {
                "candidate32_failed_without_depth_defense": 0,
                "candidate32_failure_states_frozen": 0,
                "candidate32_initiative_confirmations": 0,
                "candidate32_initiative_timeouts": 0,
                "candidate32_external_reacceptances": 0,
                "candidate32_acceptance_no_trades": 0,
                "candidate32_protective_rejection_fail_closes": 0,
            },
        )

    def _router_observation(
        self,
        row: dict[str, float | int],
        direction: int,
    ):
        observation = super()._router_observation(row, direction)
        if self.parent_auction is not None and self.parent_auction.observations == 0:
            self._candidate32_peak_defending_refill = 0.0
        if math.isfinite(observation.same_side_depth_change_1m):
            self._candidate32_peak_defending_refill = max(
                self._candidate32_peak_defending_refill,
                observation.same_side_depth_change_1m,
            )
        return observation

    def _close_parent_without_trade(
        self,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any],
    ) -> None:
        setup = self.pending
        if setup is not None:
            self._transition(
                setup.scenario_id,
                "PARENT_AUCTION_COMPLETED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                reason,
                float(row["close"]),
                details,
            )
        self.pending = None
        self.parent_auction = None
        self._candidate32_peak_defending_refill = 0.0

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        details = {
            **setup.details,
            "terminal_router_state": asdict(state),
            "candidate32_peak_defending_refill": self._candidate32_peak_defending_refill,
            "candidate32_use_displayed_depth": self.config.candidate32_use_displayed_depth,
        }
        details["terminal_router_state"]["decision"] = state.decision.value

        if state.decision is AuctionDecision.UNRESOLVED:
            self.diagnostics["candidate16_unresolved"] = int(
                self.diagnostics["candidate16_unresolved"],
            ) + 1
            self._close_parent_without_trade(row, state.reason, details)
            return

        if state.decision is AuctionDecision.ACCEPTANCE_CONTINUATION:
            if self.config.candidate32_trade_acceptance:
                super()._complete_parent(row)
            else:
                self.diagnostics["candidate32_acceptance_no_trades"] = int(
                    self.diagnostics["candidate32_acceptance_no_trades"],
                ) + 1
                self._close_parent_without_trade(
                    row,
                    "ACCEPTANCE_NOT_PART_OF_FROZEN_V32_REVERSAL_FAMILY",
                    details,
                )
            return

        depth_defended = (
            self._candidate32_peak_defending_refill
            >= self.config.candidate32_min_defending_depth_refill
        )
        if self.config.candidate32_use_displayed_depth and not depth_defended:
            self.diagnostics["candidate32_failed_without_depth_defense"] = int(
                self.diagnostics["candidate32_failed_without_depth_defense"],
            ) + 1
            self._close_parent_without_trade(
                row,
                "FAILED_PRICE_AUCTION_WITHOUT_DISPLAYED_LIQUIDITY_DEFENSE",
                details,
            )
            return

        direction = state.direction
        side = -direction
        rows = list(self.bars)
        pre = rows[-(self.config.structure_lookback_bars + 1) : -1]
        structure = (
            max(float(item["high"]) for item in pre)
            if side > 0
            else min(float(item["low"]) for item in pre)
        )
        self.pending = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="REJECTION_INITIATIVE",
            side=side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.candidate32_initiative_timeout_bars,
            sweep_extreme=setup.sweep_extreme,
            structure=structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **details,
                "candidate32_failure_bar_index": self.bar_index,
                "candidate32_failure_ts_event": int(row["ts"]),
                "candidate32_defending_depth_pass": depth_defended,
                "candidate32_attack_direction": direction,
            },
        )
        self.parent_auction = None
        self._candidate32_peak_defending_refill = 0.0
        self.diagnostics["candidate16_failed_auctions"] = int(
            self.diagnostics["candidate16_failed_auctions"],
        ) + 1
        self.diagnostics["candidate32_failure_states_frozen"] = int(
            self.diagnostics["candidate32_failure_states_frozen"],
        ) + 1
        self._transition(
            self.pending.scenario_id,
            "FAILED_AUCTION_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "AWAITING_OPPOSITE_INITIATIVE",
            "DISPLAYED_DEFENSE_OBSERVED;_FAILURE_IS_NOT_AN_ENTRY",
            float(row["close"]),
            self.pending.details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or setup.branch != "REJECTION_INITIATIVE":
            return super()._process_pending(row)
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate32_initiative_timeouts"] = int(
                self.diagnostics["candidate32_initiative_timeouts"],
            ) + 1
            self._expire_pending(row, "OPPOSITE_INITIATIVE_WINDOW_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        external_reaccepted = (
            float(row["close"]) > setup.sweep_extreme
            if setup.side < 0
            else float(row["close"]) < setup.sweep_extreme
        )
        if external_reaccepted:
            self.diagnostics["candidate32_external_reacceptances"] = int(
                self.diagnostics["candidate32_external_reacceptances"],
            ) + 1
            self._expire_pending(row, "FAILED_BOUNDARY_EXTERNALLY_REACCEPTED")
            return False

        passed, evidence = initiative_passes(
            side=setup.side,
            open_price=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close_price=float(row["close"]),
            structure=setup.structure,
            atr=self._atr(),
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
            use_displayed_depth=self.config.candidate32_use_displayed_depth,
            min_body_atr=self.config.candidate32_min_initiative_body_atr,
            min_flow=self.config.candidate32_min_initiative_flow,
            min_efficiency=self.config.candidate32_min_initiative_efficiency,
            min_close_location=self.config.candidate32_min_initiative_close_location,
            min_depth_support=self.config.candidate32_min_initiative_depth_support,
        )
        setup.details["candidate32_latest_initiative_evidence"] = evidence
        if not passed:
            return True

        self.diagnostics["candidate32_initiative_confirmations"] = int(
            self.diagnostics["candidate32_initiative_confirmations"],
        ) + 1
        setup.branch = "REJECTION"
        self._transition(
            setup.scenario_id,
            "OPPOSITE_INITIATIVE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "SEPARATE_PRICE_FLOW_AND_BOOK_INITIATIVE",
            float(row["close"]),
            {**setup.details, "candidate32_initiative_evidence": evidence},
        )
        return self._submit_entry(setup, row)

    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["candidate32_protective_rejection_fail_closes"] = int(
                self.diagnostics["candidate32_protective_rejection_fail_closes"],
            ) + 1
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)


__all__ = [
    "Candidate16Config",
    "Candidate16Strategy",
    "initiative_depth_support",
    "initiative_passes",
]
