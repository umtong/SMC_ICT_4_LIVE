"""Immediate post-interaction spot/perpetual liquidity-vacuum continuation."""
from __future__ import annotations

from strategy_base import PendingSetup
from spot_perp_strategy import SpotPerpSessionConfig
from spot_perp_strategy import SpotPerpSessionStrategy


class SpotPerpVacuumConfig(SpotPerpSessionConfig, frozen=True):
    pass


class SpotPerpVacuumStrategy(SpotPerpSessionStrategy):
    """Trade one new auction leg; never confirm the already consumed impulse."""

    def __init__(self, config: SpotPerpVacuumConfig) -> None:
        super().__init__(config=config)
        self.diagnostics.update(
            {
                "vacuum_interactions": 0,
                "vacuum_broad_parent_attacks": 0,
                "vacuum_immediate_persistence": 0,
                "vacuum_immediate_rejections": 0,
                "vacuum_entries": 0,
            },
        )

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        before = self.parent_auction
        super()._detect_sweep(row, previous_close)
        if before is None and self.parent_auction is not None and self.pending is not None:
            self.diagnostics["vacuum_interactions"] += 1
            self.diagnostics["vacuum_broad_parent_attacks"] += int(
                bool(self.pending.details.get("spot_perp_broad_attack", False)),
            )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        state = self.parent_auction
        if setup is None or setup.branch != "OBSERVATION" or state is None:
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True

        direction = int(state.direction)
        if direction > 0:
            setup.sweep_extreme = max(setup.sweep_extreme, float(row["high"]))
        else:
            setup.sweep_extreme = min(setup.sweep_extreme, float(row["low"]))

        # This is the first and only transition-confirmation minute.  It is
        # strictly later than the interaction minute and serves a different role.
        self._accumulate_displayed_state(setup, direction)
        close = float(row["close"])
        outside = direction * (close - setup.pool_level) > 0.0
        perp_persists = (
            direction * self._finite_feature("flow_60s") > 0.0
            and direction * self._finite_feature("ret_60s_bps") > 0.0
        )
        spot_persists = (
            direction * self._finite_feature("spot_flow_60s") > 0.0
            and direction * self._finite_feature("spot_ret_60s_bps") > 0.0
        )
        latest_l1 = setup.details.get("latest_l1_pressure") or {}
        l1_persists = bool(latest_l1.get("parent_pressure_persistence", False))
        broad_parent = bool(setup.details.get("spot_perp_broad_attack", False))

        details = {
            **setup.details,
            "vacuum_transition": {
                "strictly_later_bar": True,
                "outside_source_boundary": outside,
                "perpetual_flow_and_price_persist": perp_persists,
                "spot_flow_and_price_persist": spot_persists,
                "l1_pressure_persists": l1_persists,
                "broad_parent_attack": broad_parent,
            },
        }
        completed = broad_parent and outside and perp_persists and spot_persists and l1_persists
        if not completed:
            self.diagnostics["vacuum_immediate_rejections"] += 1
            self.diagnostics["candidate16_unresolved"] += 1
            self._transition(
                setup.scenario_id,
                "VACUUM_TRANSITION_EVALUATED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "NO_IMMEDIATE_BROAD_PRICE_DISCOVERY_PERSISTENCE",
                close,
                details,
            )
            self.pending = None
            self.parent_auction = None
            return True

        self.diagnostics["vacuum_immediate_persistence"] += 1
        self.diagnostics["candidate16_acceptance_continuations"] += 1
        accepted = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.pool_level,
            atr=setup.atr,
            hold_count=1,
            retrace_armed=False,
            details={**details, "candidate11_branch": "SPOT_PERP_L1_VACUUM_CONTINUATION"},
        )
        self.pending = accepted
        self.parent_auction = None
        self._transition(
            accepted.scenario_id,
            "VACUUM_TRANSITION_EVALUATED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "BROAD_PRICE_DISCOVERY_PERSISTED_ON_FIRST_LATER_MINUTE",
            close,
            accepted.details,
        )
        submitted = self._submit_entry(accepted, row)
        self.diagnostics["vacuum_entries"] += int(submitted)
        return True


__all__ = ["SpotPerpVacuumConfig", "SpotPerpVacuumStrategy"]
