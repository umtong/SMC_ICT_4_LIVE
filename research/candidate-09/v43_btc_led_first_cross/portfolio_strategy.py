"""Candidate 09 v43: BTC-led first completed-auction cross in lagging alts.

V40 proved that a strictly prior BTC repricing state removed every losing alt
trade, but waiting for the alt to complete true acceptance and then retest left
no executable follower opportunity.  V43 changes the local leg rather than
loosening that retest: BTC supplies strictly prior cross-asset context, while
the alt's first completed bar that crosses a completed-auction boundary with
local footprint initiative owns entry immediately.  The crossed boundary and
initiative bar own invalidation; the next unconsumed completed-auction pool owns
the target.

BTC retains frozen V35 unchanged.  The exact alt control removes only the BTC
leader requirement; local context, initiative, entry, stop, target, costs, risk
and the one-account global slot remain identical.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from strategy_base import PendingSetup
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy

from portfolio_strategy_v40 import CompletedLeaderState
from portfolio_strategy_v40 import SHARED_BTC_LEADER_CONTEXT
from portfolio_strategy_v40 import SharedSlotMixin
from portfolio_strategy_v40 import reset_shared_btc_leader_context
from portfolio_strategy_v40 import symbol_from_instrument


PROJECT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
_MINUTE_NS = 60_000_000_000


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate43_require_btc_leader: bool = True
    candidate43_leader_max_age_bars: int = 3


class CompletedStatePublisherMixin:
    """Publish every completed minute; consumers can use only prior timestamps."""

    def __init__(self, config: Candidate16Config) -> None:
        self._candidate43_symbol = symbol_from_instrument(config.instrument_id)
        super().__init__(config=config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "candidate43_states_published": 0,
                "candidate43_same_timestamp_leaders_used": 0,
            }
        )

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)  # type: ignore[misc]
        if len(self.bars) < 2:
            return
        row = self.bars[-1]
        ts_event = int(row["ts"])
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return
        observed = int(feature.get("observed_time_ns", 0))
        age_seconds = (ts_event - observed) / 1_000_000_000
        if age_seconds < -1e-9 or age_seconds > self.config.feature_max_age_seconds:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        previous_close = float(self.bars[-2]["close"])
        state = CompletedLeaderState(
            symbol=self._candidate43_symbol,
            ts_event=ts_event,
            return_atr=(float(row["close"]) - previous_close) / atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            footprint_delta_60s=self._feature("footprint_delta_60s"),
            stacked_buy_levels=int(
                max(0.0, self._feature("stacked_buy_imbalance_levels"))
            ),
            stacked_sell_levels=int(
                max(0.0, self._feature("stacked_sell_imbalance_levels"))
            ),
        )
        SHARED_BTC_LEADER_CONTEXT.publish(state)
        self.diagnostics["candidate43_states_published"] = int(
            self.diagnostics["candidate43_states_published"]
        ) + 1


class BtcLedFirstCrossMixin:
    """Replace alt true-acceptance/retest with one BTC-sponsored first cross."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)  # type: ignore[misc]
        self.diagnostics.update(
            {
                "candidate43_boundary_interactions": 0,
                "candidate43_local_impulses": 0,
                "candidate43_local_impulse_blocks": 0,
                "candidate43_btc_leader_passes": 0,
                "candidate43_btc_leader_blocks": 0,
                "candidate43_control_paths": 0,
                "candidate43_entry_attempts": 0,
                "candidate43_ambiguous_two_sided": 0,
            }
        )

    def _candidate43_leader_decision(
        self,
        *,
        direction: int,
        current_ts: int,
        local_progress_atr: float,
    ) -> tuple[bool, dict[str, Any]]:
        leader = SHARED_BTC_LEADER_CONTEXT.latest_before("BTCUSDT", current_ts)
        if leader is None:
            return False, {
                "candidate43_reason": "NO_STRICTLY_PRIOR_BTC_STATE",
                "candidate43_current_ts": current_ts,
            }
        age_ns = current_ts - leader.ts_event
        if age_ns <= 0:
            self.diagnostics["candidate43_same_timestamp_leaders_used"] = int(
                self.diagnostics["candidate43_same_timestamp_leaders_used"]
            ) + 1
            return False, {
                "candidate43_reason": "BTC_STATE_NOT_STRICTLY_PRIOR",
                "candidate43_leader": asdict(leader),
            }
        maximum_age_ns = self.config.candidate43_leader_max_age_bars * _MINUTE_NS
        directional_return = direction * leader.return_atr
        directional_flow = direction * leader.flow_60s
        directional_delta = direction * leader.footprint_delta_60s
        stack_levels = (
            leader.stacked_buy_levels if direction > 0 else leader.stacked_sell_levels
        )
        passed = (
            age_ns <= maximum_age_ns
            and directional_return >= self.config.router_acceptance_min_progress_atr
            and directional_return >= local_progress_atr
            and directional_flow >= self.config.acceptance_flow_min
            and leader.efficiency_60s >= self.config.router_acceptance_min_efficiency
            and directional_delta > 0.0
            and stack_levels >= self.config.candidate33_min_stacked_levels
        )
        return passed, {
            "candidate43_reason": (
                "STRICTLY_PRIOR_BTC_SPONSORED_FIRST_CROSS"
                if passed
                else "BTC_DID_NOT_OWN_A_STRONGER_PRIOR_DIRECTIONAL_LEG"
            ),
            "candidate43_leader_age_ns": age_ns,
            "candidate43_maximum_age_ns": maximum_age_ns,
            "candidate43_directional_leader_return_atr": directional_return,
            "candidate43_local_progress_atr": local_progress_atr,
            "candidate43_directional_leader_flow": directional_flow,
            "candidate43_directional_leader_delta": directional_delta,
            "candidate43_leader_stack_levels": stack_levels,
            "candidate43_leader": asdict(leader),
        }

    def _candidate43_local_impulse(
        self,
        *,
        direction: int,
        level: float,
        atr: float,
        row: dict[str, float | int],
    ) -> tuple[bool, dict[str, Any]]:
        if direction > 0:
            levels = int(max(0.0, self._feature("stacked_buy_imbalance_levels")))
            stack_low = self._feature("stacked_buy_low")
            stack_high = self._feature("stacked_buy_high")
        else:
            levels = int(max(0.0, self._feature("stacked_sell_imbalance_levels")))
            stack_low = self._feature("stacked_sell_low")
            stack_high = self._feature("stacked_sell_high")
        tolerance = self.config.candidate33_stack_boundary_tolerance_atr * atr
        stack_crossed = (
            math.isfinite(stack_low)
            and math.isfinite(stack_high)
            and (
                stack_high >= level and stack_low <= level + tolerance
                if direction > 0
                else stack_low <= level and stack_high >= level - tolerance
            )
        )
        progress_atr = direction * (float(row["close"]) - level) / atr
        flow = direction * self._feature("flow_60s")
        footprint_delta = direction * self._feature("footprint_delta_60s")
        efficiency = self._feature("efficiency_60s")
        notional_burst = self._feature("notional_burst")
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (float(row["close"]) - float(row["low"])) / span
            if direction > 0
            else (float(row["high"]) - float(row["close"])) / span
        )
        passed = (
            progress_atr >= self.config.acceptance_close_atr
            and flow >= self.config.acceptance_flow_min
            and efficiency >= self.config.acceptance_efficiency_min
            and footprint_delta > 0.0
            and notional_burst >= self.config.sweep_min_notional_burst
            and levels >= self.config.candidate33_min_stacked_levels
            and stack_crossed
            and close_location >= self.config.acceptance_close_location
        )
        return passed, {
            "candidate43_local_progress_atr": progress_atr,
            "candidate43_directional_flow": flow,
            "candidate43_directional_footprint_delta": footprint_delta,
            "candidate43_efficiency_60s": efficiency,
            "candidate43_notional_burst": notional_burst,
            "candidate43_stack_levels": levels,
            "candidate43_stack_low": stack_low,
            "candidate43_stack_high": stack_high,
            "candidate43_stack_crossed_boundary": stack_crossed,
            "candidate43_close_location": close_location,
        }

    def _candidate43_close_observation(
        self,
        *,
        scenario_id: str,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self._transition(
            scenario_id,
            "BTC_LED_FIRST_CROSS_NO_TRADE",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            details,
        )

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        if self.parent_auction is not None:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        min_age = self.config.pool_min_age_bars
        eligible = [
            pool
            for pool in self.active_pools.values()
            if pool.source.startswith("COMPLETED_")
            and self.bar_index - pool.created_index >= min_age
        ]
        high_crossed = [
            pool
            for pool in eligible
            if pool.kind == "HIGH"
            and previous_close <= pool.level
            and float(row["high"])
            >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool
            for pool in eligible
            if pool.kind == "LOW"
            and previous_close >= pool.level
            and float(row["low"])
            <= pool.level - self.config.sweep_min_penetration_atr * atr
        ]
        if not high_crossed and not low_crossed:
            return

        self.scenario_counter += 1
        scenario_id = f"x43-{self.scenario_counter:07d}"
        if high_crossed and low_crossed:
            self.diagnostics["candidate43_ambiguous_two_sided"] = int(
                self.diagnostics["candidate43_ambiguous_two_sided"]
            ) + 1
            for pool in high_crossed + low_crossed:
                self._consume_pool(pool, row, "AMBIGUOUS_TWO_SIDED_FIRST_CROSS")
            self._candidate43_close_observation(
                scenario_id=scenario_id,
                row=row,
                reason="AMBIGUOUS_TWO_SIDED_FIRST_CROSS",
                details={},
            )
            return

        if high_crossed:
            selected = max(high_crossed, key=lambda item: (item.level, item.strength))
            crossed = high_crossed
            direction = 1
        else:
            selected = min(low_crossed, key=lambda item: (item.level, -item.strength))
            crossed = low_crossed
            direction = -1
        for pool in crossed:
            self._consume_pool(pool, row, "FIRST_COMPLETED_AUCTION_CROSS")
        self.diagnostics["candidate43_boundary_interactions"] = int(
            self.diagnostics["candidate43_boundary_interactions"]
        ) + 1

        local_pass, local = self._candidate43_local_impulse(
            direction=direction,
            level=selected.level,
            atr=atr,
            row=row,
        )
        details = {
            "pool_id": selected.pool_id,
            "pool_kind": selected.kind,
            "pool_level": selected.level,
            "pool_source": selected.source,
            "pool_strength": selected.strength,
            "candidate43_direction": direction,
            "candidate43_require_btc_leader": (
                self.config.candidate43_require_btc_leader
            ),
            **local,
        }
        if not local_pass:
            self.diagnostics["candidate43_local_impulse_blocks"] = int(
                self.diagnostics["candidate43_local_impulse_blocks"]
            ) + 1
            self._candidate43_close_observation(
                scenario_id=scenario_id,
                row=row,
                reason="FIRST_CROSS_WITHOUT_LOCAL_FOOTPRINT_INITIATIVE",
                details=details,
            )
            return
        self.diagnostics["candidate43_local_impulses"] = int(
            self.diagnostics["candidate43_local_impulses"]
        ) + 1

        if self.config.candidate43_require_btc_leader:
            leader_pass, leader = self._candidate43_leader_decision(
                direction=direction,
                current_ts=int(row["ts"]),
                local_progress_atr=float(local["candidate43_local_progress_atr"]),
            )
            details.update(leader)
            if not leader_pass:
                self.diagnostics["candidate43_btc_leader_blocks"] = int(
                    self.diagnostics["candidate43_btc_leader_blocks"]
                ) + 1
                self._candidate43_close_observation(
                    scenario_id=scenario_id,
                    row=row,
                    reason="LOCAL_FIRST_CROSS_WITHOUT_PRIOR_BTC_SPONSORSHIP",
                    details=details,
                )
                return
            self.diagnostics["candidate43_btc_leader_passes"] = int(
                self.diagnostics["candidate43_btc_leader_passes"]
            ) + 1
        else:
            self.diagnostics["candidate43_control_paths"] = int(
                self.diagnostics["candidate43_control_paths"]
            ) + 1
            details["candidate43_reason"] = "BTC_LEADER_ABLATION_DISABLED"

        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind=selected.kind,
            pool_id=selected.pool_id,
            pool_level=selected.level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            structure=selected.level,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate43_entry_attempts"] = int(
            self.diagnostics["candidate43_entry_attempts"]
        ) + 1
        self._transition(
            scenario_id,
            "BTC_LED_FIRST_CROSS_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "STRICTLY_PRIOR_LEADER_AND_LOCAL_NEW_LEG_OWN_EXECUTION",
            float(row["close"]),
            details,
        )
        self._submit_entry(self.pending, row)


class SharedAccountV43BTCStrategy(
    SharedSlotMixin,
    CompletedStatePublisherMixin,
    _Candidate35Strategy,
):
    pass


class SharedAccountV43ETHStrategy(
    SharedSlotMixin,
    CompletedStatePublisherMixin,
    BtcLedFirstCrossMixin,
    _Candidate35Strategy,
):
    pass


class SharedAccountV43SOLStrategy(
    SharedSlotMixin,
    CompletedStatePublisherMixin,
    BtcLedFirstCrossMixin,
    _Candidate35Strategy,
):
    pass


class SharedAccountV43XRPStrategy(
    SharedSlotMixin,
    CompletedStatePublisherMixin,
    BtcLedFirstCrossMixin,
    _Candidate35Strategy,
):
    pass


STRATEGY_PATHS = {
    "BTCUSDT": "portfolio_strategy:SharedAccountV43BTCStrategy",
    "ETHUSDT": "portfolio_strategy:SharedAccountV43ETHStrategy",
    "SOLUSDT": "portfolio_strategy:SharedAccountV43SOLStrategy",
    "XRPUSDT": "portfolio_strategy:SharedAccountV43XRPStrategy",
}


__all__ = [
    "BtcLedFirstCrossMixin",
    "Candidate16Config",
    "CompletedStatePublisherMixin",
    "SharedAccountV43BTCStrategy",
    "SharedAccountV43ETHStrategy",
    "SharedAccountV43SOLStrategy",
    "SharedAccountV43XRPStrategy",
    "STRATEGY_PATHS",
    "reset_shared_btc_leader_context",
]
