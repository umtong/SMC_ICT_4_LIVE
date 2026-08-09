"""Candidate 16 v2: displayed-liquidity defense and later-initiative router.

Candidate 05 remains the execution/accounting owner. Candidate 16 v1 remains the
first-pass effort/result classifier. This module changes the economic state
transition only:

- a failed auction must also have independent displayed-liquidity defense;
- failure is frozen without an order;
- a strictly later price/flow/book initiative owns reversal entry;
- true acceptance needs directional book support and liquidity withdrawal;
- a market fill which has already crossed its protective stop is fail-closed.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import math
from typing import Any

from displayed_liquidity_router import FailureLeg
from displayed_liquidity_router import InitiativeDecision
from displayed_liquidity_router import InitiativeObservation
from displayed_liquidity_router import advance_failure_leg
from displayed_liquidity_router import displayed_acceptance_supported
from displayed_liquidity_router import displayed_failure_supported
from effort_result_router import AuctionDecision
from effort_result_router import ParentAuction
from effort_result_router import observe
from strategy import Candidate16Config
from strategy import Candidate16Strategy
from strategy_base import PendingSetup


class Candidate16V2Config(Candidate16Config, frozen=True):
    initiative_max_wait_bars: int = 3


class Candidate16V2Strategy(Candidate16Strategy):
    """One parent, one frozen state, and one later initiative at most."""

    def __init__(self, config: Candidate16V2Config) -> None:
        super().__init__(config=config)
        self.failure_leg: FailureLeg | None = None
        self.protective_fail_close_pending = False
        self.exit_order_submitted = False
        self.exit_request_index = -1
        self.diagnostics.update(
            {
                "candidate16_v2_failure_liquidity_rejected": 0,
                "candidate16_v2_failure_frozen": 0,
                "candidate16_v2_failure_initiatives": 0,
                "candidate16_v2_failure_initiative_invalidated": 0,
                "candidate16_v2_failure_initiative_expired": 0,
                "candidate16_v2_acceptance_liquidity_rejected": 0,
                "candidate16_v2_acceptance_liquidity_confirmed": 0,
                "candidate16_v2_protective_stop_fail_closes": 0,
                "candidate16_v2_late_exit_rejections": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.failure_leg = None
        self.protective_fail_close_pending = False
        self.exit_order_submitted = False
        self.exit_request_index = -1

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        super()._expire_pending(row, reason)
        self.failure_leg = None

    @staticmethod
    def _max_finite(existing: Any, value: float) -> float | None:
        old = float(existing) if existing is not None and math.isfinite(float(existing)) else None
        if not math.isfinite(value):
            return old
        return value if old is None else max(old, value)

    @staticmethod
    def _min_finite(existing: Any, value: float) -> float | None:
        old = float(existing) if existing is not None and math.isfinite(float(existing)) else None
        if not math.isfinite(value):
            return old
        return value if old is None else min(old, value)

    @staticmethod
    def _depth_field_for_direction(direction: int) -> str:
        return "ask_depth_change_1_1m" if direction > 0 else "bid_depth_change_1_1m"

    @staticmethod
    def _ahead_depth_field(side: int) -> str:
        return "ask_depth_change_1_1m" if side > 0 else "bid_depth_change_1_1m"

    def _accumulate_displayed_state(self, setup: PendingSetup, direction: int) -> None:
        raw_imbalance = self._feature("depth_imbalance_1")
        ahead_change = self._feature(self._depth_field_for_direction(direction))
        reversal_support = -direction * raw_imbalance
        acceptance_support = direction * raw_imbalance
        details = setup.details
        details["max_reversal_book_support"] = self._max_finite(
            details.get("max_reversal_book_support"),
            reversal_support,
        )
        details["max_acceptance_book_support"] = self._max_finite(
            details.get("max_acceptance_book_support"),
            acceptance_support,
        )
        details["max_defending_depth_change"] = self._max_finite(
            details.get("max_defending_depth_change"),
            ahead_change,
        )
        details["min_liquidity_ahead_change"] = self._min_finite(
            details.get("min_liquidity_ahead_change"),
            ahead_change,
        )
        details["displayed_observation_count"] = int(
            details.get("displayed_observation_count", 0),
        ) + 1
        details["latest_depth_imbalance_1"] = raw_imbalance
        details["latest_liquidity_ahead_change"] = ahead_change
        details["latest_depth_snapshot_age_seconds"] = self._feature(
            "depth_snapshot_age_seconds",
        )

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
            and float(row["high"])
            >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool
            for pool in self.active_pools.values()
            if pool.kind == "LOW"
            and self.bar_index - pool.created_index >= min_age
            and previous_close >= pool.level
            and float(row["low"])
            <= pool.level - self.config.sweep_min_penetration_atr * atr
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
        scenario_id = f"c16v2-{self.scenario_counter:07d}"
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
        self._accumulate_displayed_state(self.pending, direction)
        self.diagnostics["candidate16_parent_auctions"] = int(
            self.diagnostics["candidate16_parent_auctions"],
        ) + 1
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
        if setup.branch == "FAILURE_INITIATIVE":
            return self._process_failure_initiative(row)
        if setup.branch == "OBSERVATION" and self.parent_auction is not None:
            if self.bar_index > setup.created_index:
                self._accumulate_displayed_state(setup, self.parent_auction.direction)
        return super()._process_pending(row)

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        if state.decision is AuctionDecision.UNRESOLVED:
            super()._complete_parent(row)
            return

        details = {**setup.details, "terminal_router_state": self._state_details(state)}
        direction = state.direction
        if state.decision is AuctionDecision.FAILED_AUCTION:
            self.diagnostics["candidate16_failed_auctions"] = int(
                self.diagnostics["candidate16_failed_auctions"],
            ) + 1
            supported = displayed_failure_supported(
                parent_direction=direction,
                max_reversal_book_support=float(
                    details.get("max_reversal_book_support")
                    if details.get("max_reversal_book_support") is not None
                    else float("nan")
                ),
                max_defending_depth_change=float(
                    details.get("max_defending_depth_change")
                    if details.get("max_defending_depth_change") is not None
                    else float("nan")
                ),
            )
            if not supported:
                self.diagnostics["candidate16_unresolved"] = int(
                    self.diagnostics["candidate16_unresolved"],
                ) + 1
                self.diagnostics["candidate16_v2_failure_liquidity_rejected"] = int(
                    self.diagnostics["candidate16_v2_failure_liquidity_rejected"],
                ) + 1
                self._transition(
                    setup.scenario_id,
                    "PARENT_AUCTION_COMPLETED",
                    int(row["ts"]),
                    int(row["ts"]),
                    "CLOSED",
                    "FAILED_AUCTION_WITHOUT_DISPLAYED_LIQUIDITY_DEFENSE",
                    float(row["close"]),
                    details,
                )
                self.pending = None
                self.parent_auction = None
                return

            side = -direction
            rows = list(self.bars)
            pre = rows[-(self.config.structure_lookback_bars + 1) : -1]
            structure = (
                max(float(item["high"]) for item in pre)
                if side > 0
                else min(float(item["low"]) for item in pre)
            )
            self.failure_leg = FailureLeg(
                scenario_id=setup.scenario_id,
                side=side,
                failure_index=self.bar_index,
                last_index=self.bar_index,
                failure_high=float(row["high"]),
                failure_low=float(row["low"]),
                parent_extreme=setup.sweep_extreme,
                max_wait_bars=self.config.initiative_max_wait_bars,
            )
            self.pending = PendingSetup(
                scenario_id=setup.scenario_id,
                branch="FAILURE_INITIATIVE",
                side=side,
                swept_kind=setup.swept_kind,
                pool_id=setup.pool_id,
                pool_level=setup.pool_level,
                created_index=self.bar_index,
                expires_index=self.bar_index + self.config.initiative_max_wait_bars,
                sweep_extreme=setup.sweep_extreme,
                structure=structure,
                atr=setup.atr,
                hold_count=0,
                retrace_armed=False,
                details={
                    **details,
                    "displayed_failure_supported": True,
                    "failure_bar_high": float(row["high"]),
                    "failure_bar_low": float(row["low"]),
                    "failure_bar_close": float(row["close"]),
                    "failure_bar_index": self.bar_index,
                },
            )
            self.parent_auction = None
            self.diagnostics["candidate16_v2_failure_frozen"] = int(
                self.diagnostics["candidate16_v2_failure_frozen"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "PARENT_AUCTION_COMPLETED",
                int(row["ts"]),
                int(row["ts"]),
                "FAILURE_FROZEN",
                "DISPLAYED_LIQUIDITY_DEFENDED_AND_BOUNDARY_RECLAIMED",
                float(row["close"]),
                self.pending.details,
            )
            return

        supported = displayed_acceptance_supported(
            parent_direction=direction,
            max_acceptance_book_support=float(
                details.get("max_acceptance_book_support")
                if details.get("max_acceptance_book_support") is not None
                else float("nan")
            ),
            min_liquidity_ahead_change=float(
                details.get("min_liquidity_ahead_change")
                if details.get("min_liquidity_ahead_change") is not None
                else float("nan")
            ),
        )
        if not supported:
            self.diagnostics["candidate16_unresolved"] = int(
                self.diagnostics["candidate16_unresolved"],
            ) + 1
            self.diagnostics["candidate16_v2_acceptance_liquidity_rejected"] = int(
                self.diagnostics["candidate16_v2_acceptance_liquidity_rejected"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "PARENT_AUCTION_COMPLETED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "ACCEPTANCE_WITHOUT_DIRECTIONAL_BOOK_WITHDRAWAL",
                float(row["close"]),
                details,
            )
            self.pending = None
            self.parent_auction = None
            return
        self.diagnostics["candidate16_v2_acceptance_liquidity_confirmed"] = int(
            self.diagnostics["candidate16_v2_acceptance_liquidity_confirmed"],
        ) + 1
        super()._complete_parent(row)

    def _process_failure_initiative(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.failure_leg
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_FROZEN_FAILURE_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True

        state = advance_failure_leg(
            state,
            InitiativeObservation(
                bar_index=self.bar_index,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._feature("flow_60s"),
                ret_60s_bps=self._feature("ret_60s_bps"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
                liquidity_ahead_change_1m=self._feature(
                    self._ahead_depth_field(state.side),
                ),
            ),
        )
        self.failure_leg = state
        setup.details["latest_failure_leg"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self._transition(
            setup.scenario_id,
            "FAILURE_INITIATIVE_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "FAILURE_FROZEN" if state.decision is InitiativeDecision.WAITING else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is InitiativeDecision.WAITING:
            return True
        if state.decision is InitiativeDecision.INVALIDATED:
            self.diagnostics["candidate16_v2_failure_initiative_invalidated"] = int(
                self.diagnostics["candidate16_v2_failure_initiative_invalidated"],
            ) + 1
            self.pending = None
            self.failure_leg = None
            return True
        if state.decision is InitiativeDecision.EXPIRED:
            self.diagnostics["candidate16_v2_failure_initiative_expired"] = int(
                self.diagnostics["candidate16_v2_failure_initiative_expired"],
            ) + 1
            self.pending = None
            self.failure_leg = None
            return True

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="REJECTION",
            side=setup.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "candidate16_branch": "FAILED_AUCTION_LATER_INITIATIVE",
                "confirmed_failure_leg": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
            },
        )
        self.pending = completed
        self.failure_leg = None
        self.diagnostics["candidate16_v2_failure_initiatives"] = int(
            self.diagnostics["candidate16_v2_failure_initiatives"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "FAILURE_INITIATIVE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            state.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True

    def on_order_rejected(self, event: Any) -> None:
        text = str(event)
        protective = "STOP_MARKET" in text and "was in the market" in text
        late_flatten = (
            "REDUCE_ONLY MARKET" in text
            and "would have increased position" in text
        )
        if not protective:
            if late_flatten:
                self.diagnostics["candidate16_v2_late_exit_rejections"] = int(
                    self.diagnostics["candidate16_v2_late_exit_rejections"],
                ) + 1
            super().on_order_rejected(event)
            return

        self.diagnostics["order_rejections"] = int(
            self.diagnostics["order_rejections"],
        ) + 1
        self.diagnostics["candidate16_v2_protective_stop_fail_closes"] = int(
            self.diagnostics["candidate16_v2_protective_stop_fail_closes"],
        ) + 1
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        if (
            self.current_scenario_id is not None
            and self.scenario_states.get(self.current_scenario_id) != "CLOSED"
        ):
            self._transition(
                self.current_scenario_id,
                "PROTECTIVE_STOP_REJECTED",
                ts,
                ts,
                "PROTECTIVE_EXIT_PENDING",
                "ACTUAL_FILL_ALREADY_CROSSED_PLANNED_STOP",
                float(self.bars[-1]["close"]),
                {"event": text},
            )
        self.protective_fail_close_pending = True
        self.entry_pending = False
        self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)
            self.exit_order_submitted = True
            self.exit_request_index = self.bar_index

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        if self.protective_fail_close_pending:
            if not self.exit_order_submitted:
                self.cancel_all_orders(self.config.instrument_id)
                self.close_all_positions(self.config.instrument_id)
                self.exit_order_submitted = True
                self.exit_request_index = self.bar_index
            return
        if self.exit_order_submitted:
            return

        moment = datetime.fromtimestamp(
            int(row["ts"]) / 1_000_000_000,
            tz=timezone.utc,
        )
        before_funding = (
            moment.hour in (7, 15, 23)
            and moment.minute >= self.config.funding_flatten_minute
        )
        timed_out = (
            self.position_open_index >= 0
            and self.bar_index - self.position_open_index >= self.config.max_hold_bars
        )
        evaluation_ended = int(row["ts"]) >= self.config.evaluation_end_ns
        if before_funding or timed_out or evaluation_ended:
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            self.exit_order_submitted = True
            self.exit_request_index = self.bar_index
            if self.current_scenario_id is not None:
                self._transition(
                    self.current_scenario_id,
                    "FORCED_DAYTRADE_EXIT",
                    int(row["ts"]),
                    int(row["ts"]),
                    "EXIT_PENDING",
                    "FUNDING_OR_HOLD_OR_EVALUATION_BOUNDARY",
                    float(row["close"]),
                    {
                        "before_funding": before_funding,
                        "timed_out": timed_out,
                        "evaluation_ended": evaluation_ended,
                    },
                )


__all__ = ["Candidate16V2Config", "Candidate16V2Strategy"]
