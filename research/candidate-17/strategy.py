"""Candidate 17: remembered-defense absorption/exhaustion router.

Candidate 16 v2 owns the verified NautilusTrader execution path.  Candidate 17
changes only the economic decision policy:

* an immediate first-defense failed auction may keep the reversal path;
* a repeated/accepted interaction suppresses that reversal;
* a later reattack becomes continuation only when price, flow, impact,
  displayed liquidity and expanding open interest agree on defense depletion;
* the continuation still waits for the first defended retest;
* every incomplete combination is unresolved/no-trade.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from defense_memory_router import DefenseMemory
from defense_memory_router import DepletionDecision
from defense_memory_router import ReattackObservation
from defense_memory_router import advance_defense_memory
from defense_memory_router import clean_first_defense
from displayed_liquidity_router import InitiativeDecision
from displayed_liquidity_router import InitiativeObservation
from displayed_liquidity_router import advance_failure_leg
from displayed_liquidity_router import displayed_acceptance_supported
from displayed_liquidity_router import displayed_failure_supported
from effort_result_router import AuctionDecision
from strategy_base import PendingSetup
from strategy_v2 import Candidate16V2Config
from strategy_v2 import Candidate16V2Strategy


class Candidate17Config(Candidate16V2Config, frozen=True):
    depletion_max_wait_bars: int = 3
    positioning_max_age_seconds: float = 300.0


class Candidate17Strategy(Candidate16V2Strategy):
    """Route first-defense rejection and repeated-defense depletion separately."""

    def __init__(self, config: Candidate17Config) -> None:
        super().__init__(config=config)
        self.defense_memory: DefenseMemory | None = None
        self.diagnostics.update(
            {
                "candidate17_clean_first_defenses": 0,
                "candidate17_reversal_suppressions": 0,
                "candidate17_defense_memories": 0,
                "candidate17_reattack_observations": 0,
                "candidate17_depletion_confirmations": 0,
                "candidate17_depletion_invalidations": 0,
                "candidate17_depletion_expiries": 0,
                "candidate17_positioning_rejections": 0,
                "candidate17_depletion_retests_armed": 0,
                "candidate17_failure_reaccess_reroutes": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.defense_memory = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        memory_pending = self.pending is not None and self.pending.branch == "DEFENSE_MEMORY"
        super()._expire_pending(row, reason)
        if memory_pending:
            self.defense_memory = None

    @staticmethod
    def _terminal_details(state: Any, setup: PendingSetup) -> dict[str, Any]:
        values = {**setup.details, "terminal_router_state": Candidate17Strategy._state_details(state)}
        return values

    @staticmethod
    def _broad_ahead_depth_field(direction: int) -> str:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        return "ask_depth_change_2_1m" if direction > 0 else "bid_depth_change_2_1m"

    def _positioning_expanded(self) -> bool:
        ready = self._feature("metrics_ready") > 0.5
        age = self._feature("metrics_age_seconds")
        oi = self._feature("oi_change_5m")
        return (
            ready
            and math.isfinite(age)
            and 0.0 <= age <= self.config.positioning_max_age_seconds
            and math.isfinite(oi)
            and oi > 0.0
        )

    def _complete_parent(self, row: dict[str, float | int]) -> None:
        state = self.parent_auction
        setup = self.pending
        if state is None or setup is None:
            return
        if state.decision is AuctionDecision.UNRESOLVED:
            super()._complete_parent(row)
            return

        details = self._terminal_details(state, setup)
        direction = state.direction
        if state.decision is AuctionDecision.FAILED_AUCTION:
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
                super()._complete_parent(row)
                return

            clean = clean_first_defense(
                observations=int(state.observations),
                outside_closes=int(state.outside_closes),
            )
            if clean:
                self.diagnostics["candidate17_clean_first_defenses"] = int(
                    self.diagnostics["candidate17_clean_first_defenses"],
                ) + 1
                super()._complete_parent(row)
                return

            self.diagnostics["candidate16_failed_auctions"] = int(
                self.diagnostics["candidate16_failed_auctions"],
            ) + 1
            self.diagnostics["candidate17_reversal_suppressions"] = int(
                self.diagnostics["candidate17_reversal_suppressions"],
            ) + 1
            self._arm_defense_memory(
                setup=setup,
                row=row,
                details=details,
                cause="REPEATED_OR_OUTSIDE_RESIDENT_DEFENSE_SUPPRESSED_REVERSAL",
                last_index=self.bar_index,
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
            super()._complete_parent(row)
            return
        if self._positioning_expanded():
            super()._complete_parent(row)
            return

        self.diagnostics["candidate16_unresolved"] = int(
            self.diagnostics["candidate16_unresolved"],
        ) + 1
        self.diagnostics["candidate17_positioning_rejections"] = int(
            self.diagnostics["candidate17_positioning_rejections"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "PARENT_AUCTION_COMPLETED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            "TRUE_ACCEPTANCE_WITHOUT_FRESH_OPEN_INTEREST_EXPANSION",
            float(row["close"]),
            {
                **details,
                "oi_change_5m": self._feature("oi_change_5m"),
                "metrics_ready": self._feature("metrics_ready"),
                "metrics_age_seconds": self._feature("metrics_age_seconds"),
            },
        )
        self.pending = None
        self.parent_auction = None

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == "DEFENSE_MEMORY":
            return self._process_defense_memory(row)
        return super()._process_pending(row)

    def _process_failure_initiative(self, row: dict[str, float | int]) -> bool:
        """Reroute a re-accessed clean failure into depletion, not silent death."""
        setup = self.pending
        state = self.failure_leg
        if setup is None or state is None:
            return super()._process_failure_initiative(row)
        if self.bar_index <= setup.created_index:
            return True

        anticipated = advance_failure_leg(
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
        if anticipated.decision is not InitiativeDecision.INVALIDATED:
            return super()._process_failure_initiative(row)

        self.failure_leg = anticipated
        setup.details["latest_failure_leg"] = {
            **asdict(anticipated),
            "decision": anticipated.decision.value,
        }
        self._transition(
            setup.scenario_id,
            "FAILURE_INITIATIVE_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            anticipated.decision.value,
            anticipated.reason,
            float(row["close"]),
            setup.details,
        )
        self.diagnostics["candidate16_v2_failure_initiative_invalidated"] = int(
            self.diagnostics["candidate16_v2_failure_initiative_invalidated"],
        ) + 1
        self.diagnostics["candidate17_failure_reaccess_reroutes"] = int(
            self.diagnostics["candidate17_failure_reaccess_reroutes"],
        ) + 1
        details = dict(setup.details)
        self._arm_defense_memory(
            setup=setup,
            row=row,
            details=details,
            cause="CLEAN_FAILURE_REACCESSED_BEFORE_OPPOSITE_INITIATIVE",
            last_index=self.bar_index - 1,
        )
        if self.pending is not None and self.pending.branch == "DEFENSE_MEMORY":
            return self._process_defense_memory(row)
        return True

    def _arm_defense_memory(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        details: dict[str, Any],
        cause: str,
        last_index: int,
    ) -> None:
        terminal = details.get("terminal_router_state", {})
        direction = int(details.get("parent_direction", terminal.get("direction", 0)))
        baseline_efficiency = float(terminal.get("latest_efficiency", float("nan")))
        first_defense_change = float(details.get("max_defending_depth_change", float("nan")))
        if (
            direction not in (-1, 1)
            or not math.isfinite(baseline_efficiency)
            or not math.isfinite(first_defense_change)
            or first_defense_change <= 0.0
            or not math.isfinite(setup.atr)
            or setup.atr <= 0.0
        ):
            self.diagnostics["candidate16_unresolved"] = int(
                self.diagnostics["candidate16_unresolved"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "DEFENSE_MEMORY_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "REMEMBERED_DEFENSE_STATE_INCOMPLETE",
                float(row["close"]),
                details,
            )
            self.pending = None
            self.parent_auction = None
            self.failure_leg = None
            self.defense_memory = None
            return

        memory = DefenseMemory(
            scenario_id=setup.scenario_id,
            direction=direction,
            defended_level=float(setup.pool_level),
            parent_extreme=float(setup.sweep_extreme),
            atr=float(setup.atr),
            created_index=self.bar_index,
            last_index=last_index,
            expires_index=self.bar_index + self.config.depletion_max_wait_bars,
            baseline_efficiency=baseline_efficiency,
            first_defense_change=first_defense_change,
        )
        memory_details = {
            **details,
            "candidate17_branch": "REMEMBERED_DEFENSE_DEPLETION",
            "defense_memory_cause": cause,
            "defended_level": memory.defended_level,
            "parent_extreme": memory.parent_extreme,
            "baseline_efficiency": memory.baseline_efficiency,
            "first_defense_change": memory.first_defense_change,
        }
        self.defense_memory = memory
        self.pending = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="DEFENSE_MEMORY",
            side=direction,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=memory.expires_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.sweep_extreme,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details=memory_details,
        )
        self.parent_auction = None
        self.failure_leg = None
        self.diagnostics["candidate17_defense_memories"] = int(
            self.diagnostics["candidate17_defense_memories"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "DEFENSE_MEMORY_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "AWAIT_QUALITY_REATTACK",
            cause,
            float(row["close"]),
            memory_details,
        )

    def _process_defense_memory(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.defense_memory
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_DEFENSE_MEMORY_STATE")
            return True
        if self.bar_index <= state.last_index:
            return True

        direction = state.direction
        defending_field = self._depth_field_for_direction(direction)
        broad_ahead_field = self._broad_ahead_depth_field(direction)
        observation = ReattackObservation(
            bar_index=self.bar_index,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_60s=self._feature("flow_60s"),
            ret_60s_bps=self._feature("ret_60s_bps"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            defending_depth_change_1m=self._feature(defending_field),
            liquidity_ahead_change_1m=self._feature(broad_ahead_field),
            oi_change_5m=self._feature("oi_change_5m"),
            positioning_ready=self._feature("metrics_ready") > 0.5,
            positioning_age_seconds=self._feature("metrics_age_seconds"),
        )
        state = advance_defense_memory(
            state,
            observation,
            max_positioning_age_seconds=self.config.positioning_max_age_seconds,
        )
        self.defense_memory = state
        setup.details["latest_defense_memory"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self.diagnostics["candidate17_reattack_observations"] = int(
            self.diagnostics["candidate17_reattack_observations"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "DEFENSE_REATTACK_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "AWAIT_QUALITY_REATTACK"
            if state.decision is DepletionDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is DepletionDecision.WAITING:
            return True
        if state.decision is DepletionDecision.INVALIDATED:
            self.diagnostics["candidate17_depletion_invalidations"] = int(
                self.diagnostics["candidate17_depletion_invalidations"],
            ) + 1
            self.pending = None
            self.defense_memory = None
            return True
        if state.decision is DepletionDecision.EXPIRED:
            self.diagnostics["candidate17_depletion_expiries"] = int(
                self.diagnostics["candidate17_depletion_expiries"],
            ) + 1
            self.pending = None
            self.defense_memory = None
            return True

        accepted = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=state.parent_extreme,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            sweep_extreme=state.parent_extreme,
            structure=state.parent_extreme,
            atr=state.atr,
            hold_count=self.config.acceptance_min_hold_bars,
            retrace_armed=True,
            details={
                **setup.details,
                "candidate17_branch": "DEPLETION_ACCEPTANCE_FIRST_RETEST",
                "confirmed_defense_memory": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
            },
        )
        self.pending = accepted
        self.defense_memory = None
        self.diagnostics["candidate17_depletion_confirmations"] = int(
            self.diagnostics["candidate17_depletion_confirmations"],
        ) + 1
        self.diagnostics["candidate17_depletion_retests_armed"] = int(
            self.diagnostics["candidate17_depletion_retests_armed"],
        ) + 1
        self._transition(
            accepted.scenario_id,
            "DEFENSE_DEPLETION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "FIRST_RETEST_ARMED",
            state.reason,
            float(row["close"]),
            accepted.details,
        )
        return True


__all__ = ["Candidate17Config", "Candidate17Strategy"]
