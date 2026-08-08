"""Forced-flow exhaustion reversal on the inherited Nautilus FOK path."""
from __future__ import annotations

from dataclasses import asdict
import math

from fok_capped_strategy import Candidate18Config
from fok_capped_strategy import Candidate18Strategy as Candidate18FokStrategy
from forced_exhaustion_router import ForcedDecision, ForcedEpisode, ForcedObservation
from forced_exhaustion_router import ForcedResponseThresholds, ForcedShockEvidence
from forced_exhaustion_router import ForcedShockThresholds, advance_forced_episode
from forced_exhaustion_router import classify_forced_shock
from logic import Pool, net_r_at_price, planned_loss_per_unit
from strategy_base import PendingSetup


class Candidate21ForcedConfig(Candidate18Config, frozen=True):
    forced_shock_lookback_bars: int = 5
    forced_shock_min_move_atr: float = 1.25
    forced_shock_min_notional_burst: float = 1.50
    forced_shock_min_flow: float = 1.0 / 3.0
    forced_shock_min_efficiency: float = 0.45
    forced_max_wait_bars: int = 6
    forced_min_retrace_fraction: float = 1.0 / 3.0
    forced_min_reverse_flow: float = 0.10
    forced_min_reverse_efficiency: float = 0.20


class Candidate21ForcedStrategy(Candidate18FokStrategy):
    """Detect deleveraging, then require separate exhaustion and reprice bars."""

    def __init__(self, config: Candidate21ForcedConfig) -> None:
        super().__init__(config=config)
        self.shock_thresholds = ForcedShockThresholds(
            config.forced_shock_min_move_atr,
            config.forced_shock_min_notional_burst,
            config.forced_shock_min_flow,
            config.forced_shock_min_efficiency,
        )
        self.response_thresholds = ForcedResponseThresholds(
            config.forced_max_wait_bars,
            config.forced_min_retrace_fraction,
            config.forced_min_reverse_flow,
            config.forced_min_reverse_efficiency,
        )
        self.forced_episode: ForcedEpisode | None = None
        self.diagnostics.update({
            "candidate21_forced_events_armed": 0,
            "candidate21_forced_events_not_ready": 0,
            "candidate21_forced_exhaustions": 0,
            "candidate21_forced_reprices": 0,
            "candidate21_forced_invalidated": 0,
            "candidate21_forced_expired": 0,
            "candidate21_forced_geometry_rejected": 0,
            "candidate21_forced_fok_entries": 0,
        })

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.forced_episode = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        forced = self.pending is not None and self.pending.branch == "FORCED_EXHAUSTION"
        super()._expire_pending(row, reason)
        if forced:
            self.forced_episode = None

    def _ready(self) -> bool:
        return (
            self._feature("metrics_ready") > 0.5
            and self._feature("basis_ready") > 0.5
            and all(math.isfinite(self._feature(name)) for name in (
                "oi_change_15m", "premium_change_5m", "premium_change_1m"
            ))
        )

    @staticmethod
    def _depth_field(side: int) -> str:
        return "bid_depth_change_1_1m" if side > 0 else "ask_depth_change_1_1m"

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        del previous_close
        if self.pending is not None or self.forced_episode is not None:
            return
        if not self._ready():
            self.diagnostics["candidate21_forced_events_not_ready"] += 1
            return
        atr = self._atr()
        lookback = int(self.config.forced_shock_lookback_bars)
        rows = list(self.bars)
        if not math.isfinite(atr) or atr <= 0.0 or len(rows) < lookback + 1:
            return
        origin = float(rows[-(lookback + 1)]["close"])
        close = float(row["close"])
        evidence = ForcedShockEvidence(
            move_atr=(close - origin) / atr,
            notional_burst=self._feature("notional_burst"),
            flow_3m=self._feature("flow_3m"),
            efficiency_60s=self._feature("efficiency_60s"),
            oi_change_15m=self._feature("oi_change_15m"),
            premium_change_5m=self._feature("premium_change_5m"),
        )
        direction = classify_forced_shock(evidence, self.shock_thresholds)
        if direction == 0:
            return
        leg = rows[-lookback:]
        high = max(float(item["high"]) for item in leg)
        low = min(float(item["low"]) for item in leg)
        if (direction > 0 and high <= origin) or (direction < 0 and low >= origin):
            return

        self.scenario_counter += 1
        scenario_id = f"c21-forced-{self.scenario_counter:07d}"
        state = ForcedEpisode(
            scenario_id=scenario_id,
            shock_direction=direction,
            shock_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=self.bar_index + self.config.forced_max_wait_bars,
            origin_price=origin,
            event_high=high,
            event_low=low,
            event_close=close,
            atr=atr,
            event_efficiency=evidence.efficiency_60s,
            event_oi_change_15m=evidence.oi_change_15m,
            event_premium_change_5m=evidence.premium_change_5m,
            event_notional_burst=evidence.notional_burst,
            event_flow_3m=evidence.flow_3m,
            latest_high=high,
            latest_low=low,
        )
        side = -direction
        details = {
            "candidate21_parent": "FORCED_POSITION_DELEVERAGING_CASCADE",
            "shock_direction": direction,
            "shock_origin": origin,
            "shock_move_atr": evidence.move_atr,
            "shock_notional_burst": evidence.notional_burst,
            "shock_flow_3m": evidence.flow_3m,
            "shock_efficiency_60s": evidence.efficiency_60s,
            "shock_oi_change_15m": evidence.oi_change_15m,
            "shock_premium_change_5m": evidence.premium_change_5m,
            "natural_target": origin,
        }
        self.forced_episode = state
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="FORCED_EXHAUSTION",
            side=side,
            swept_kind="UP_CASCADE" if direction > 0 else "DOWN_CASCADE",
            pool_id=f"forced-{scenario_id}",
            pool_level=origin,
            created_index=self.bar_index,
            expires_index=state.expires_index,
            sweep_extreme=high if direction > 0 else low,
            structure=origin,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate21_forced_events_armed"] += 1
        self._transition(
            scenario_id, "FORCED_FLOW_EVENT_OPENED", int(row["ts"]),
            int(row["ts"]), state.decision.value,
            "DELEVERAGING_IMPULSE_IS_AN_EVENT_NOT_AN_ENTRY", close, details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == "FORCED_EXHAUSTION":
            return self._process_forced(row)
        return super()._process_pending(row)

    def _process_forced(self, row: dict[str, float | int]) -> bool:
        setup, state = self.pending, self.forced_episode
        if setup is None or state is None:
            self._expire_pending(row, "MISSING_FORCED_FLOW_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True
        if not self._ready():
            if self.bar_index >= setup.expires_index:
                self._expire_pending(row, "FORCED_FLOW_OBSERVATIONS_BECAME_STALE")
            return True
        side = -state.shock_direction
        previous = state.decision
        state = advance_forced_episode(
            state,
            ForcedObservation(
                bar_index=self.bar_index,
                high=float(row["high"]), low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._feature("flow_60s"),
                flow_3m=self._feature("flow_3m"),
                ret_60s_bps=self._feature("ret_60s_bps"),
                efficiency_60s=self._feature("efficiency_60s"),
                depth_imbalance_1=self._feature("depth_imbalance_1"),
                defending_depth_change_1m=self._feature(self._depth_field(side)),
                oi_change_15m=self._feature("oi_change_15m"),
                premium_change_1m=self._feature("premium_change_1m"),
            ),
            self.response_thresholds,
        )
        self.forced_episode = state
        setup.sweep_extreme = state.latest_high if state.shock_direction > 0 else state.latest_low
        terminal = asdict(state)
        terminal["decision"] = state.decision.value
        setup.details["latest_forced_flow_state"] = terminal
        if previous is ForcedDecision.WAITING_EXHAUSTION and state.decision is ForcedDecision.WAITING_REVERSAL:
            self.diagnostics["candidate21_forced_exhaustions"] += 1
        self._transition(
            setup.scenario_id, "FORCED_FLOW_OBSERVED", int(row["ts"]),
            int(row["ts"]), state.decision.value, state.reason,
            float(row["close"]), setup.details,
        )
        if state.decision in (ForcedDecision.WAITING_EXHAUSTION, ForcedDecision.WAITING_REVERSAL):
            return True
        if state.decision is ForcedDecision.INVALIDATED:
            self.diagnostics["candidate21_forced_invalidated"] += 1
            self.pending = self.forced_episode = None
            return True
        if state.decision is ForcedDecision.EXPIRED:
            self.diagnostics["candidate21_forced_expired"] += 1
            self.pending = self.forced_episode = None
            return True

        self.diagnostics["candidate21_forced_reprices"] += 1
        setup.details["terminal_forced_flow_state"] = terminal
        self.forced_episode = None
        return self._submit_natural_target(setup, row)

    def _submit_natural_target(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        side = setup.side
        atr = self._atr()
        signal = float(row["close"])
        stop = setup.sweep_extreme - side * self.config.stop_buffer_atr * atr
        structural_risk = abs(signal - stop)
        cap = max(
            self.config.entry_rearm_atr * atr,
            self.config.entry_limit_risk_expansion * structural_risk,
        )
        entry_limit = signal + side * cap
        target = float(setup.details["natural_target"])
        cost = self.config.all_in_cost_bps_each_side / 10_000.0
        slip = self.config.adverse_slippage_bps_each_side / 10_000.0
        loss = planned_loss_per_unit(entry_limit, stop, side, cost, slip)
        target_r = net_r_at_price(entry_limit, target, side, loss, cost)
        geometry = (
            stop < signal < entry_limit < target if side > 0
            else target < entry_limit < signal < stop
        )
        if not geometry or not math.isfinite(loss) or target_r < self.config.min_target_net_r:
            self.diagnostics["candidate21_forced_geometry_rejected"] += 1
            self._expire_pending(row, "PRE_SHOCK_OBJECTIVE_NOT_EXECUTABLE_AFTER_COSTS")
            return False

        natural_pool = Pool(
            pool_id=f"target-{setup.scenario_id}",
            kind="HIGH" if side > 0 else "LOW",
            level=target,
            event_time_ns=int(row["ts"]),
            observed_time_ns=int(row["ts"]),
            source="FROZEN_PRE_SHOCK_ORIGIN",
            strength=1,
            created_index=self.bar_index,
        )
        saved_pools = self.active_pools
        saved_branch = setup.branch
        self.active_pools = {natural_pool.pool_id: natural_pool}
        setup.branch = "REJECTION"
        try:
            submitted = super()._submit_entry(setup, row)
        finally:
            setup.branch = saved_branch
            self.active_pools = saved_pools
        if submitted:
            self.current_branch = "FORCED_EXHAUSTION_REVERSAL"
            self.diagnostics["candidate21_forced_fok_entries"] += 1
        return submitted


__all__ = ["Candidate21ForcedConfig", "Candidate21ForcedStrategy"]
