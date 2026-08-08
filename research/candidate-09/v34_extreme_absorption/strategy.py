"""Candidate 09 v34: extreme-price absorption, later initiative, pullback entry.

The strategy separates four causal roles:

1. Candidate 16's parent auction supplies the external-liquidity context.
2. Aggressor volume concentrated in the five extreme ticks while price fails
   supplies price-level absorption state evidence.
3. A later opposite stacked imbalance, price break and flow displacement owns
   the new reversal leg.
4. A later 35-65% pullback of that initiative bar owns execution.

True acceptance and unresolved interactions are explicit no-trades.  The exact
single ablation removes only extreme-price absorption; every later transition,
entry, invalidation, target, cost and risk rule remains unchanged.
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
    candidate34_require_extreme_absorption: bool = True
    candidate34_min_extreme_delta: float = 0.55
    candidate34_min_extreme_notional_share: float = 0.04
    candidate34_min_extreme_cell_multiple: float = 2.0
    candidate34_initiative_timeout_bars: int = 3
    candidate34_min_opposite_stack_levels: int = 3
    candidate34_initiative_body_atr: float = 0.20
    candidate34_initiative_flow: float = 0.08
    candidate34_initiative_efficiency: float = 0.25
    candidate34_initiative_close_location: float = 0.60
    candidate34_stack_structure_tolerance_atr: float = 0.50
    candidate34_pullback_timeout_bars: int = 3
    candidate34_pullback_min_fraction: float = 0.35
    candidate34_pullback_max_fraction: float = 0.65
    candidate34_pullback_hold_fraction: float = 0.50
    candidate34_pullback_max_counterflow: float = 0.08


class Candidate16Strategy(_Candidate16V1Strategy):
    """V34 state machine under the verified Candidate 05 Nautilus adapter."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self._candidate34_absorption_score = -math.inf
        self._candidate34_absorption: dict[str, float] = {}
        self.diagnostics.update(
            {
                "candidate34_failed_without_extreme_absorption": 0,
                "candidate34_absorptions_frozen": 0,
                "candidate34_opposite_initiatives": 0,
                "candidate34_initiative_timeouts": 0,
                "candidate34_pullback_entries": 0,
                "candidate34_pullback_timeouts": 0,
                "candidate34_external_reacceptances": 0,
                "candidate34_acceptance_no_trades": 0,
                "candidate34_late_flat_order_rejections": 0,
                "candidate34_protective_rejection_fail_closes": 0,
            }
        )

    def _reset_absorption(self) -> None:
        self._candidate34_absorption_score = -math.inf
        self._candidate34_absorption = {}

    def _router_observation(
        self,
        row: dict[str, float | int],
        direction: int,
    ):
        observation = super()._router_observation(row, direction)
        if self.parent_auction is not None and self.parent_auction.observations == 0:
            self._reset_absorption()
        if direction > 0:
            aggression = self._feature("top_extreme_aggressor_delta")
            share = self._feature("top_extreme_notional_share")
            cell_multiple = self._feature("top_extreme_cell_multiple")
        else:
            aggression = -self._feature("bottom_extreme_aggressor_delta")
            share = self._feature("bottom_extreme_notional_share")
            cell_multiple = self._feature("bottom_extreme_cell_multiple")
        values = (aggression, share, cell_multiple)
        if all(math.isfinite(value) for value in values):
            score = aggression * share * min(cell_multiple, 20.0)
            if score > self._candidate34_absorption_score:
                self._candidate34_absorption_score = score
                self._candidate34_absorption = {
                    "attack_direction": float(direction),
                    "extreme_aggressor_delta": aggression,
                    "extreme_notional_share": share,
                    "extreme_cell_multiple": cell_multiple,
                    "absorption_score": score,
                    "observed_ts_ns": float(row["ts"]),
                }
        return observation

    def _close_without_trade(
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
        self._reset_absorption()

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        details = {
            **setup.details,
            "candidate34_terminal_router_state": asdict(state),
            "candidate34_extreme_absorption": dict(self._candidate34_absorption),
            "candidate34_require_extreme_absorption": (
                self.config.candidate34_require_extreme_absorption
            ),
        }
        details["candidate34_terminal_router_state"]["decision"] = state.decision.value

        if state.decision is AuctionDecision.UNRESOLVED:
            self.diagnostics["candidate16_unresolved"] = int(
                self.diagnostics["candidate16_unresolved"]
            ) + 1
            self._close_without_trade(row, state.reason, details)
            return
        if state.decision is AuctionDecision.ACCEPTANCE_CONTINUATION:
            self.diagnostics["candidate34_acceptance_no_trades"] = int(
                self.diagnostics["candidate34_acceptance_no_trades"]
            ) + 1
            self._close_without_trade(
                row,
                "TRUE_ACCEPTANCE_NOT_PART_OF_V34_ABSORPTION_REVERSAL",
                details,
            )
            return

        evidence = self._candidate34_absorption
        absorption_pass = bool(evidence) and (
            evidence["extreme_aggressor_delta"]
            >= self.config.candidate34_min_extreme_delta
            and evidence["extreme_notional_share"]
            >= self.config.candidate34_min_extreme_notional_share
            and evidence["extreme_cell_multiple"]
            >= self.config.candidate34_min_extreme_cell_multiple
        )
        if self.config.candidate34_require_extreme_absorption and not absorption_pass:
            self.diagnostics["candidate34_failed_without_extreme_absorption"] = int(
                self.diagnostics["candidate34_failed_without_extreme_absorption"]
            ) + 1
            self._close_without_trade(
                row,
                "FAILED_PRICE_AUCTION_WITHOUT_EXTREME_PRICE_ABSORPTION",
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
            branch="REJECTION_FOOTPRINT_INITIATIVE",
            side=side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.candidate34_initiative_timeout_bars,
            sweep_extreme=setup.sweep_extreme,
            structure=structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **details,
                "candidate34_absorption_pass": absorption_pass,
                "candidate34_failure_ts_ns": int(row["ts"]),
            },
        )
        self.parent_auction = None
        self._reset_absorption()
        self.diagnostics["candidate16_failed_auctions"] = int(
            self.diagnostics["candidate16_failed_auctions"]
        ) + 1
        self.diagnostics["candidate34_absorptions_frozen"] = int(
            self.diagnostics["candidate34_absorptions_frozen"]
        ) + 1
        self._transition(
            self.pending.scenario_id,
            "EXTREME_ABSORPTION_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "AWAITING_OPPOSITE_FOOTPRINT_INITIATIVE",
            "FAILED_AUCTION_IS_STATE_EVIDENCE_NOT_AN_ENTRY",
            float(row["close"]),
            self.pending.details,
        )

    def _external_reaccepted(self, setup: PendingSetup, close: float) -> bool:
        return close > setup.sweep_extreme if setup.side < 0 else close < setup.sweep_extreme

    def _initiative_evidence(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> tuple[bool, dict[str, float | bool]]:
        side = setup.side
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False, {"valid_geometry": False}
        if side > 0:
            levels = int(max(0.0, self._feature("stacked_buy_imbalance_levels")))
            stack_low = self._feature("stacked_buy_low")
            stack_high = self._feature("stacked_buy_high")
        else:
            levels = int(max(0.0, self._feature("stacked_sell_imbalance_levels")))
            stack_low = self._feature("stacked_sell_low")
            stack_high = self._feature("stacked_sell_high")
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        body_atr = side * (float(row["close"]) - float(row["open"])) / atr
        flow = side * self._feature("flow_60s")
        efficiency = self._feature("efficiency_60s")
        footprint_delta = side * self._feature("footprint_delta_60s")
        close_location = (
            (float(row["close"]) - float(row["low"])) / span
            if side > 0
            else (float(row["high"]) - float(row["close"])) / span
        )
        structure_broken = (
            float(row["close"]) > setup.structure
            if side > 0
            else float(row["close"]) < setup.structure
        )
        tolerance = self.config.candidate34_stack_structure_tolerance_atr * atr
        stack_at_structure = (
            math.isfinite(stack_low)
            and math.isfinite(stack_high)
            and (
                stack_high >= setup.structure
                and stack_low <= setup.structure + tolerance
                if side > 0
                else stack_low <= setup.structure
                and stack_high >= setup.structure - tolerance
            )
        )
        passed = (
            levels >= self.config.candidate34_min_opposite_stack_levels
            and stack_at_structure
            and structure_broken
            and body_atr >= self.config.candidate34_initiative_body_atr
            and flow >= self.config.candidate34_initiative_flow
            and efficiency >= self.config.candidate34_initiative_efficiency
            and footprint_delta > 0.0
            and close_location >= self.config.candidate34_initiative_close_location
        )
        return passed, {
            "valid_geometry": True,
            "stack_levels": float(levels),
            "stack_low": stack_low,
            "stack_high": stack_high,
            "stack_at_structure": stack_at_structure,
            "structure_broken": structure_broken,
            "body_atr": body_atr,
            "directional_flow": flow,
            "efficiency_60s": efficiency,
            "directional_footprint_delta": footprint_delta,
            "close_location": close_location,
        }

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None or not setup.branch.startswith("REJECTION_FOOTPRINT_"):
            return super()._process_pending(row)

        close = float(row["close"])
        if self._external_reaccepted(setup, close):
            self.diagnostics["candidate34_external_reacceptances"] = int(
                self.diagnostics["candidate34_external_reacceptances"]
            ) + 1
            self._expire_pending(row, "ABSORPTION_EXTREME_EXTERNALLY_REACCEPTED")
            return False

        if setup.branch == "REJECTION_FOOTPRINT_INITIATIVE":
            if self.bar_index > setup.expires_index:
                self.diagnostics["candidate34_initiative_timeouts"] = int(
                    self.diagnostics["candidate34_initiative_timeouts"]
                ) + 1
                self._expire_pending(row, "OPPOSITE_FOOTPRINT_INITIATIVE_EXPIRED")
                return False
            if self.bar_index <= setup.created_index:
                return True
            passed, evidence = self._initiative_evidence(setup, row)
            setup.details["candidate34_latest_initiative_evidence"] = evidence
            if not passed:
                return True
            setup.branch = "REJECTION_FOOTPRINT_PULLBACK"
            setup.created_index = self.bar_index
            setup.expires_index = self.bar_index + self.config.candidate34_pullback_timeout_bars
            setup.details.update(
                {
                    "candidate34_initiative_evidence": evidence,
                    "candidate34_initiative_low": float(row["low"]),
                    "candidate34_initiative_high": float(row["high"]),
                    "candidate34_initiative_ts_ns": int(row["ts"]),
                }
            )
            self.diagnostics["candidate34_opposite_initiatives"] = int(
                self.diagnostics["candidate34_opposite_initiatives"]
            ) + 1
            self._transition(
                setup.scenario_id,
                "OPPOSITE_FOOTPRINT_INITIATIVE_CONFIRMED",
                int(row["ts"]),
                int(row["ts"]),
                "INITIATIVE_PULLBACK_ARMED",
                "NEW_LEG_OWNS_EXECUTION",
                close,
                setup.details,
            )
            return True

        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate34_pullback_timeouts"] = int(
                self.diagnostics["candidate34_pullback_timeouts"]
            ) + 1
            self._expire_pending(row, "INITIATIVE_PULLBACK_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        low = float(setup.details["candidate34_initiative_low"])
        high = float(setup.details["candidate34_initiative_high"])
        span = high - low
        if not math.isfinite(span) or span <= 0.0:
            self._expire_pending(row, "INVALID_INITIATIVE_RANGE")
            return False
        zone_low = low + self.config.candidate34_pullback_min_fraction * span
        zone_high = low + self.config.candidate34_pullback_max_fraction * span
        hold = low + self.config.candidate34_pullback_hold_fraction * span
        side = setup.side
        touched = (
            float(row["low"]) <= zone_high
            if side > 0
            else float(row["high"]) >= zone_low
        )
        held = close >= hold if side > 0 else close <= hold
        not_overdeep = close >= zone_low if side > 0 else close <= zone_high
        tail_flow = side * self._feature("flow_15s")
        row_span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (close - float(row["low"])) / row_span
            if side > 0
            else (float(row["high"]) - close) / row_span
        )
        if not (
            touched
            and held
            and not_overdeep
            and tail_flow >= -self.config.candidate34_pullback_max_counterflow
            and close_location >= 0.50
        ):
            return True

        setup.branch = "REJECTION"
        setup.details.update(
            {
                "candidate34_pullback_zone_low": zone_low,
                "candidate34_pullback_zone_high": zone_high,
                "candidate34_pullback_hold": hold,
                "candidate34_pullback_tail_flow": tail_flow,
                "candidate34_pullback_close_location": close_location,
            }
        )
        self.diagnostics["candidate34_pullback_entries"] = int(
            self.diagnostics["candidate34_pullback_entries"]
        ) + 1
        return self._submit_entry(setup, row)

    def on_order_rejected(self, event: Any) -> None:
        late_flat_child = (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and self.current_scenario_id is None
        )
        if late_flat_child:
            self.diagnostics["candidate34_late_flat_order_rejections"] = int(
                self.diagnostics["candidate34_late_flat_order_rejections"]
            ) + 1
            return
        super().on_order_rejected(event)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["candidate34_protective_rejection_fail_closes"] = int(
                self.diagnostics["candidate34_protective_rejection_fail_closes"]
            ) + 1
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)


__all__ = ["Candidate16Config", "Candidate16Strategy"]
