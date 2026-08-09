"""Candidate 16: causal effort/result auction router on NautilusTrader.

Candidate 05 owns data preparation, fills, fees, latency, margin, liquidation,
portfolio accounting, and continuous NAV. This module replaces only the
market-state interpretation and entry policy.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from effort_result_router import AuctionDecision
from effort_result_router import AuctionObservation
from effort_result_router import ParentAuction
from effort_result_router import RouterThresholds
from effort_result_router import observe
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import LiquidityResponseConfig
from strategy_base import LiquidityResponseStrategy
from strategy_base import PendingSetup


class Candidate16Config(LiquidityResponseConfig, frozen=True):
    router_max_observation_bars: int = 3
    router_min_directional_effort: float = 0.14
    router_failed_max_progress_atr: float = 0.24
    router_failed_reclaim_atr: float = 0.01
    router_failed_max_efficiency: float = 0.42
    router_acceptance_min_outside_closes: int = 2
    router_acceptance_min_progress_atr: float = 0.24
    router_acceptance_min_efficiency: float = 0.34
    router_acceptance_min_close_location: float = 0.56
    router_acceptance_max_adverse_reentry_atr: float = 0.06


class Candidate16Strategy(LiquidityResponseStrategy):
    """One-decision-per-parent auction state machine."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self.parent_auction: ParentAuction | None = None
        self.router_thresholds = RouterThresholds(
            max_observation_bars=config.router_max_observation_bars,
            min_directional_effort=config.router_min_directional_effort,
            failed_max_progress_atr=config.router_failed_max_progress_atr,
            failed_reclaim_atr=config.router_failed_reclaim_atr,
            failed_max_efficiency=config.router_failed_max_efficiency,
            acceptance_min_outside_closes=config.router_acceptance_min_outside_closes,
            acceptance_min_progress_atr=config.router_acceptance_min_progress_atr,
            acceptance_min_efficiency=config.router_acceptance_min_efficiency,
            acceptance_min_close_location=config.router_acceptance_min_close_location,
            acceptance_max_adverse_reentry_atr=(
                config.router_acceptance_max_adverse_reentry_atr
            ),
        )
        self.diagnostics.update(
            {
                "candidate16_parent_auctions": 0,
                "candidate16_failed_auctions": 0,
                "candidate16_acceptance_continuations": 0,
                "candidate16_unresolved": 0,
                "candidate16_natural_objective_rejections": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.parent_auction = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        super()._expire_pending(row, reason)
        self.parent_auction = None

    def _router_observation(
        self,
        row: dict[str, float | int],
        direction: int,
    ) -> AuctionObservation:
        depth_name = "ask_depth_change_1_1m" if direction > 0 else "bid_depth_change_1_1m"
        return AuctionObservation(
            bar_index=self.bar_index,
            ts_event=int(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_60s=self._feature("flow_60s"),
            notional_burst=self._feature("notional_burst"),
            efficiency_60s=self._feature("efficiency_60s"),
            same_side_depth_change_1m=self._feature(depth_name),
        )

    @staticmethod
    def _state_details(state: ParentAuction) -> dict[str, Any]:
        values = asdict(state)
        values["decision"] = state.decision.value
        return values

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        if self.parent_auction is not None:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        min_age = self.config.pool_min_age_bars
        high_crossed = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == "HIGH"
            and self.bar_index - pool.created_index >= min_age
            and previous_close <= pool.level
            and float(row["high"]) >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == "LOW"
            and self.bar_index - pool.created_index >= min_age
            and previous_close >= pool.level
            and float(row["low"]) <= pool.level - self.config.sweep_min_penetration_atr * atr
        ]
        if high_crossed and low_crossed:
            for pool in high_crossed + low_crossed:
                self._consume_pool(pool, row, "AMBIGUOUS_TWO_SIDED_INTERACTION")
            return
        if not high_crossed and not low_crossed:
            return

        if high_crossed:
            pool = max(high_crossed, key=lambda item: (item.level, item.strength))
            kind = "HIGH"
            crossed = high_crossed
            direction = 1
        else:
            pool = min(low_crossed, key=lambda item: (item.level, -item.strength))
            kind = "LOW"
            crossed = low_crossed
            direction = -1
        for item in crossed:
            self._consume_pool(item, row, "PARENT_LIQUIDITY_INTERACTION")

        self.scenario_counter += 1
        scenario_id = f"c16-{self.scenario_counter:07d}"
        self.parent_auction = ParentAuction(
            scenario_id=scenario_id,
            direction=direction,
            pool_level=pool.level,
            atr=atr,
            started_index=self.bar_index,
            last_index=self.bar_index - 1,
        )
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="OBSERVATION",
            side=0,
            swept_kind=kind,
            pool_id=pool.pool_id,
            pool_level=pool.level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.router_max_observation_bars - 1,
            sweep_extreme=float(row["high"]) if direction > 0 else float(row["low"]),
            structure=pool.level,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details={
                "pool_id": pool.pool_id,
                "pool_kind": kind,
                "pool_level": pool.level,
                "pool_source": pool.source,
                "pool_strength": pool.strength,
                "parent_direction": direction,
                "interaction_bar_index": self.bar_index,
                "interaction_ts_event": int(row["ts"]),
            },
        )
        self.diagnostics["candidate16_parent_auctions"] = (
            int(self.diagnostics["candidate16_parent_auctions"]) + 1
        )
        self._transition(
            scenario_id,
            "PARENT_AUCTION_OPENED",
            int(row["ts"]),
            int(row["ts"]),
            "OBSERVING",
            "BOUNDARY_INTERACTION_REQUIRES_SEQUENTIAL_COMPLETION",
            pool.level,
            self.pending.details,
        )
        self.parent_auction = observe(
            self.parent_auction,
            self._router_observation(row, direction),
            self.router_thresholds,
        )
        if self.parent_auction.decision is not AuctionDecision.PENDING:
            self._complete_parent(row)

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != "OBSERVATION":
            return super()._process_pending(row)
        state = self.parent_auction
        if state is None:
            self._expire_pending(row, "MISSING_PARENT_AUCTION_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True
        if state.direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))
        state = observe(
            state,
            self._router_observation(row, state.direction),
            self.router_thresholds,
        )
        self.parent_auction = state
        setup.details["latest_router_state"] = self._state_details(state)
        self._transition(
            setup.scenario_id,
            "PARENT_AUCTION_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "OBSERVING" if state.decision is AuctionDecision.PENDING else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is AuctionDecision.PENDING:
            return True
        self._complete_parent(row)
        return True

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        details = {**setup.details, "terminal_router_state": self._state_details(state)}

        if state.decision is AuctionDecision.UNRESOLVED:
            self.diagnostics["candidate16_unresolved"] = int(
                self.diagnostics["candidate16_unresolved"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "PARENT_AUCTION_COMPLETED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                state.reason,
                float(row["close"]),
                details,
            )
            self.pending = None
            self.parent_auction = None
            return

        direction = state.direction
        rows = list(self.bars)
        pre = rows[-(self.config.structure_lookback_bars + 1) : -1]
        if state.decision is AuctionDecision.FAILED_AUCTION:
            side = -direction
            structure = (
                max(float(item["high"]) for item in pre)
                if side > 0
                else min(float(item["low"]) for item in pre)
            )
            completed = PendingSetup(
                scenario_id=setup.scenario_id,
                branch="REJECTION",
                side=side,
                swept_kind=setup.swept_kind,
                pool_id=setup.pool_id,
                pool_level=setup.pool_level,
                created_index=self.bar_index,
                expires_index=self.bar_index,
                sweep_extreme=setup.sweep_extreme,
                structure=structure,
                atr=setup.atr,
                hold_count=0,
                retrace_armed=False,
                details={**details, "candidate16_branch": state.decision.value},
            )
            self.pending = completed
            self.parent_auction = None
            self.diagnostics["candidate16_failed_auctions"] = int(
                self.diagnostics["candidate16_failed_auctions"],
            ) + 1
            self._transition(
                completed.scenario_id,
                "PARENT_AUCTION_COMPLETED",
                int(row["ts"]),
                int(row["ts"]),
                "ENTRY_EVALUATION",
                state.reason,
                float(row["close"]),
                completed.details,
            )
            self._submit_entry(completed, row)
            return

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.pool_level,
            atr=setup.atr,
            hold_count=self.config.acceptance_min_hold_bars,
            retrace_armed=True,
            details={**details, "candidate16_branch": state.decision.value},
        )
        self.pending = completed
        self.parent_auction = None
        self.diagnostics["candidate16_acceptance_continuations"] = int(
            self.diagnostics["candidate16_acceptance_continuations"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "PARENT_AUCTION_COMPLETED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_ARMED",
            state.reason,
            float(row["close"]),
            completed.details,
        )

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        """Reject fallback targets; only an unconsumed causal objective is valid."""
        atr = self._atr()
        side = setup.side
        entry = float(row["close"])
        if setup.branch == "REJECTION":
            stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        else:
            if side > 0:
                stop = min(
                    setup.pool_level - self.config.stop_buffer_atr * atr,
                    float(row["low"]) - 0.25 * self.config.stop_buffer_atr * atr,
                )
            else:
                stop = max(
                    setup.pool_level + self.config.stop_buffer_atr * atr,
                    float(row["high"]) + 0.25 * self.config.stop_buffer_atr * atr,
                )

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        objective_kind = "HIGH" if side > 0 else "LOW"
        objectives = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == objective_kind
            and side * (pool.level - entry) > 0.0
            and net_r_at_price(entry, pool.level, side, planned_loss, cost_rate)
            >= self.config.min_target_net_r
        ]
        if not objectives:
            self.diagnostics["candidate16_natural_objective_rejections"] = int(
                self.diagnostics["candidate16_natural_objective_rejections"],
            ) + 1
            self._expire_pending(row, "NO_UNCONSUMED_LIQUIDITY_OBJECTIVE_AFTER_COSTS")
            return False
        return super()._submit_entry(setup, row)


__all__ = ["Candidate16Config", "Candidate16Strategy"]
