"""Candidate 19: immediate shocks must prove later price transmission.

Candidate 18's sustained initiative and all-or-none FOK execution are reused.
Only the weak immediate-notional-shock path changes:

    notional shock
    -> arm a causal transmission state
    -> require a strictly later outside close with same-side flow, return,
       queue support, and displayed-liquidity withdrawal
    -> reject absorption or failed boundary hold
    -> execute through Candidate 18's capped FOK bracket

The remaining window is inherited from the already registered three-bar
initiative horizon; no PnL-derived threshold or new backtest/accounting layer
is introduced.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from displayed_liquidity_router import InitiativeDecision
from displayed_liquidity_router import InitiativeObservation
from displayed_liquidity_router import advance_failure_leg
from fok_capped_strategy import Candidate18Config
from fok_capped_strategy import Candidate18Strategy as Candidate18FokStrategy
from initiative_quality_router import InitiativeRoute
from initiative_quality_router import classify_initiative_quality
from shock_transmission_router import ShockDecision
from shock_transmission_router import ShockObservation
from shock_transmission_router import ShockTransmission
from shock_transmission_router import advance_shock_transmission
from strategy_base import PendingSetup


class Candidate19Config(Candidate18Config, frozen=True):
    """Candidate 18 parameters are retained without a new fitted threshold."""


class Candidate19Strategy(Candidate18FokStrategy):
    """Preserve sustained initiative; demand later transmission after shocks."""

    def __init__(self, config: Candidate19Config) -> None:
        super().__init__(config=config)
        self.shock_transmission: ShockTransmission | None = None
        self.diagnostics.update(
            {
                "candidate19_shock_transmissions_armed": 0,
                "candidate19_shock_transmission_observations": 0,
                "candidate19_shock_transmissions_confirmed": 0,
                "candidate19_shock_transmissions_invalidated": 0,
                "candidate19_shock_transmissions_expired": 0,
                "candidate19_shock_absorption_invalidations": 0,
            },
        )

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        shock_pending = (
            self.pending is not None and self.pending.branch == "SHOCK_TRANSMISSION"
        )
        super()._expire_pending(row, reason)
        if shock_pending:
            self.shock_transmission = None

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == "SHOCK_TRANSMISSION":
            return self._process_shock_transmission(row)
        return super()._process_pending(row)

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
            "FAILURE_FROZEN"
            if state.decision is InitiativeDecision.WAITING
            else state.decision.value,
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

        quality = classify_initiative_quality(
            observations=int(state.observations),
            max_wait_bars=int(state.max_wait_bars),
            notional_burst=self._feature("notional_burst"),
            shock_burst_min=self.config.initiative_shock_burst_min,
        )
        quality_details = {
            "route": quality.route.value,
            "reason": quality.reason,
            "notional_burst": self._feature("notional_burst"),
            "observations": int(state.observations),
            "max_wait_bars": int(state.max_wait_bars),
            "oi_change_5m": self._feature("oi_change_5m"),
            "metrics_age_seconds": self._feature("metrics_age_seconds"),
        }
        setup.details["candidate18_initiative_quality"] = quality_details
        if quality.route is InitiativeRoute.UNRESOLVED:
            self.diagnostics["candidate18_initiative_quality_rejected"] = int(
                self.diagnostics["candidate18_initiative_quality_rejected"],
            ) + 1
            self._transition(
                setup.scenario_id,
                "INITIATIVE_QUALITY_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                quality.reason,
                float(row["close"]),
                setup.details,
            )
            self.pending = None
            self.failure_leg = None
            return True

        self.diagnostics["candidate16_v2_failure_initiatives"] = int(
            self.diagnostics["candidate16_v2_failure_initiatives"],
        ) + 1
        if quality.route is InitiativeRoute.SHOCK:
            self.diagnostics["candidate18_initiative_shock"] = int(
                self.diagnostics["candidate18_initiative_shock"],
            ) + 1
            self._arm_shock_transmission(
                setup=setup,
                state=state,
                row=row,
                quality_details=quality_details,
            )
            return True

        self.diagnostics["candidate18_initiative_sustained"] = int(
            self.diagnostics["candidate18_initiative_sustained"],
        ) + 1
        completed = self._completed_rejection(
            setup=setup,
            state=state,
            details={
                **setup.details,
                "candidate19_branch": "FAILED_AUCTION_SUSTAINED_INITIATIVE",
            },
        )
        self.pending = completed
        self.failure_leg = None
        self._transition(
            completed.scenario_id,
            "FAILURE_INITIATIVE_QUALITY_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            quality.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True

    def _completed_rejection(
        self,
        *,
        setup: PendingSetup,
        state: Any,
        details: dict[str, Any],
    ) -> PendingSetup:
        return PendingSetup(
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
                **details,
                "confirmed_failure_leg": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
            },
        )

    def _arm_shock_transmission(
        self,
        *,
        setup: PendingSetup,
        state: Any,
        row: dict[str, float | int],
        quality_details: dict[str, Any],
    ) -> None:
        remaining_bars = max(1, int(state.max_wait_bars) - int(state.observations))
        transmission = ShockTransmission(
            scenario_id=setup.scenario_id,
            side=setup.side,
            shock_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=self.bar_index + remaining_bars,
            failure_high=float(state.failure_high),
            failure_low=float(state.failure_low),
            parent_extreme=float(state.parent_extreme),
            shock_close=float(row["close"]),
        )
        details = {
            **setup.details,
            "candidate19_branch": "FAILED_AUCTION_SHOCK_AWAITING_TRANSMISSION",
            "candidate19_initial_shock": quality_details,
            "confirmed_failure_leg": {
                **asdict(state),
                "decision": state.decision.value,
            },
            "transmission_window_bars": remaining_bars,
        }
        self.shock_transmission = transmission
        self.pending = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="SHOCK_TRANSMISSION",
            side=setup.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=transmission.expires_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.failure_leg = None
        self.diagnostics["candidate19_shock_transmissions_armed"] = int(
            self.diagnostics["candidate19_shock_transmissions_armed"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "SHOCK_TRANSMISSION_ARMED",
            int(row["ts"]),
            int(row["ts"]),
            "SHOCK_TRANSMISSION_PENDING",
            "NOTIONAL_SHOCK_IS_EVENT_NOT_ENTRY;_WAIT_FOR_LATER_TRANSMISSION",
            float(row["close"]),
            details,
        )

    def _process_shock_transmission(
        self,
        row: dict[str, float | int],
    ) -> bool:
        setup = self.pending
        state = self.shock_transmission
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_SHOCK_TRANSMISSION_STATE")
            return True
        if self.bar_index <= state.last_index:
            return True

        state = advance_shock_transmission(
            state,
            ShockObservation(
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
        self.shock_transmission = state
        setup.details["latest_shock_transmission"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self.diagnostics["candidate19_shock_transmission_observations"] = int(
            self.diagnostics["candidate19_shock_transmission_observations"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "SHOCK_TRANSMISSION_OBSERVED",
            int(row["ts"]),
            int(row["ts"]),
            "SHOCK_TRANSMISSION_PENDING"
            if state.decision is ShockDecision.WAITING
            else state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )
        if state.decision is ShockDecision.WAITING:
            return True
        if state.decision is ShockDecision.INVALIDATED:
            self.diagnostics["candidate19_shock_transmissions_invalidated"] = int(
                self.diagnostics["candidate19_shock_transmissions_invalidated"],
            ) + 1
            if "ABSORBED" in state.reason:
                self.diagnostics["candidate19_shock_absorption_invalidations"] = int(
                    self.diagnostics["candidate19_shock_absorption_invalidations"],
                ) + 1
            self.pending = None
            self.shock_transmission = None
            return True
        if state.decision is ShockDecision.EXPIRED:
            self.diagnostics["candidate19_shock_transmissions_expired"] = int(
                self.diagnostics["candidate19_shock_transmissions_expired"],
            ) + 1
            self.pending = None
            self.shock_transmission = None
            return True
        if state.decision is not ShockDecision.CONFIRMED:
            raise RuntimeError(f"unexpected shock decision {state.decision!r}")

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
                "candidate19_branch": "FAILED_AUCTION_SHOCK_TRANSMISSION_CONFIRMED",
                "confirmed_shock_transmission": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
            },
        )
        self.pending = completed
        self.shock_transmission = None
        self.diagnostics["candidate19_shock_transmissions_confirmed"] = int(
            self.diagnostics["candidate19_shock_transmissions_confirmed"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "SHOCK_TRANSMISSION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            state.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True


__all__ = ["Candidate19Config", "Candidate19Strategy"]
