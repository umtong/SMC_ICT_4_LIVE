"""Market-state evaluation for Candidate 05 target-liquidity handoff."""
from __future__ import annotations

from typing import Any

from depth_logic import DIRECTIONAL_DEPTH_MIN
from depth_logic import directional_depth_support
from flow_inflection_logic import directional_tail_improvement
from flow_inflection_logic import sweep_tail_recovers
from strategy_base import PendingSetup
from target_handoff_logic import delayed_target_reclaim_ready
from target_handoff_logic import target_sweep_bar_sponsored
from target_handoff_models import TargetSweepWatch


class TargetHandoffReactionMixin:
    """Promote only a sponsored delayed reclaim into the existing CHoCH chain."""

    def _observe_target_watch(self, row: dict[str, float | int]) -> None:
        watch = self.target_sweep_watch
        if watch is None:
            return
        watch.high = max(watch.high, float(row["high"]))
        watch.low = min(watch.low, float(row["low"]))
        watch.rows_observed += 1

        flow_15s = self._feature("flow_15s")
        flow_60s = self._feature("flow_60s")
        notional_burst = self._feature("notional_burst")
        efficiency = self._feature("efficiency_60s")
        if not target_sweep_bar_sponsored(
            kind=watch.pool.kind,
            flow_15s=flow_15s,
            flow_60s=flow_60s,
            notional_burst=notional_burst,
            efficiency_60s=efficiency,
            minimum_directional_flow=self.config.rejection_flow_min,
            minimum_notional_burst=self.config.sweep_min_notional_burst,
            maximum_efficiency=self.config.rejection_efficiency_max,
        ):
            return
        direction = 1 if watch.pool.kind == "HIGH" else -1
        directional_flow = max(direction * flow_15s, direction * flow_60s)
        if not watch.sweep_sponsored or directional_flow > watch.sponsor_directional_flow:
            watch.sponsor_directional_flow = directional_flow
            watch.sponsor_notional_burst = notional_burst
            watch.sponsor_efficiency = efficiency
        if not watch.sweep_sponsored:
            self.diagnostics["target_handoff_sponsored_bars"] += 1
        watch.sweep_sponsored = True

    def _promote_target_watch(self, row: dict[str, float | int]) -> bool:
        watch = self.target_sweep_watch
        if watch is None or not delayed_target_reclaim_ready(
            kind=watch.pool.kind,
            pool_level=watch.pool.level,
            accumulated_high=watch.high,
            accumulated_low=watch.low,
            current_close=float(row["close"]),
            atr=watch.atr,
            sweep_sponsored=watch.sweep_sponsored,
            current_efficiency_60s=self._feature("efficiency_60s"),
            current_bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            current_ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
            minimum_penetration_atr=self.config.sweep_min_penetration_atr,
            maximum_efficiency=self.config.rejection_efficiency_max,
            minimum_same_side_refill=self.config.rejection_depth_refill_min,
        ):
            return False

        side = -1 if watch.pool.kind == "HIGH" else 1
        flow_15s = self._feature("flow_15s")
        flow_60s = self._feature("flow_60s")
        depth_imbalance = self._feature("depth_imbalance_1")
        if not sweep_tail_recovers(side=side, flow_15s=flow_15s, flow_60s=flow_60s):
            self.diagnostics["target_handoff_tail_waits"] += 1
            return False
        if not directional_depth_support(
            side=side,
            depth_imbalance=depth_imbalance,
            minimum=DIRECTIONAL_DEPTH_MIN,
        ):
            self.diagnostics["target_handoff_depth_waits"] += 1
            return False

        penetration = (
            (watch.high - watch.pool.level) / watch.atr
            if watch.pool.kind == "HIGH"
            else (watch.pool.level - watch.low) / watch.atr
        )
        details = {
            "pool_id": watch.pool.pool_id,
            "pool_kind": watch.pool.kind,
            "pool_level": watch.pool.level,
            "pool_source": watch.pool.source,
            "pool_strength": watch.pool.strength,
            "pool_age_minutes": self.bar_index - watch.pool.created_index,
            "penetration_atr": penetration,
            "flow_15s": flow_15s,
            "flow_60s": flow_60s,
            "flow_3m": self._feature("flow_3m"),
            "notional_burst": self._feature("notional_burst"),
            "efficiency_60s": self._feature("efficiency_60s"),
            "absorption_60s": self._feature("absorption_60s"),
            "depth_imbalance_1": depth_imbalance,
            "bid_depth_change_1m": self._feature("bid_depth_change_1_1m"),
            "ask_depth_change_1m": self._feature("ask_depth_change_1_1m"),
            "target_handoff": True,
            "source_scenario_id": watch.source_scenario_id,
            "target_watch_started_ns": watch.started_ts,
            "target_watch_rows": watch.rows_observed,
            "sponsor_directional_flow": watch.sponsor_directional_flow,
            "sponsor_notional_burst": watch.sponsor_notional_burst,
            "sponsor_efficiency_60s": watch.sponsor_efficiency,
            "directional_tail_improvement": directional_tail_improvement(
                side=side,
                flow_15s=flow_15s,
                flow_60s=flow_60s,
            ),
            "directional_depth_support": side * depth_imbalance,
        }
        structure = watch.low if side < 0 else watch.high
        sweep_extreme = watch.high if watch.pool.kind == "HIGH" else watch.low
        self.pending = PendingSetup(
            scenario_id=watch.scenario_id,
            branch="REJECTION_RETRACE",
            side=side,
            swept_kind=watch.pool.kind,
            pool_id=watch.pool.pool_id,
            pool_level=watch.pool.level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            sweep_extreme=sweep_extreme,
            structure=structure,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.target_sweep_watch = None
        self.diagnostics["rejection_setups"] += 1
        self.diagnostics["target_handoff_setups"] += 1
        self._transition(
            watch.scenario_id,
            "TARGET_LIQUIDITY_REJECTION_CLASSIFIED",
            int(row["ts"]),
            int(row["ts"]),
            "CHOCH_ARMED",
            "SPONSORED_TARGET_RAID_RECLAIMED_WITH_REVERSAL_DEPTH",
            float(row["close"]),
            details,
        )
        return True

    def _expire_target_watch(self, row: dict[str, float | int], reason: str) -> None:
        watch = self.target_sweep_watch
        if watch is None:
            return
        self.diagnostics["target_handoff_expired"] += 1
        self._transition(
            watch.scenario_id,
            "TARGET_SWEEP_WATCH_EXPIRED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            self._watch_details(watch),
        )
        self.target_sweep_watch = None

    def _watch_details(self, watch: TargetSweepWatch) -> dict[str, Any]:
        return {
            "source_scenario_id": watch.source_scenario_id,
            "pool_id": watch.pool.pool_id,
            "pool_kind": watch.pool.kind,
            "pool_level": watch.pool.level,
            "pool_source": watch.pool.source,
            "previous_entry_side": watch.previous_entry_side,
            "started_index": watch.started_index,
            "expires_index": watch.expires_index,
            "rows_observed": watch.rows_observed,
            "accumulated_high": watch.high,
            "accumulated_low": watch.low,
            "sweep_sponsored": watch.sweep_sponsored,
            "sponsor_directional_flow": watch.sponsor_directional_flow,
            "sponsor_notional_burst": watch.sponsor_notional_burst,
            "sponsor_efficiency_60s": watch.sponsor_efficiency,
        }


__all__ = ["TargetHandoffReactionMixin"]
