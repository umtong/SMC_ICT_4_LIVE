#!/usr/bin/env python3
"""Candidate 05 v59: first defended retest of a spot-led repricing boundary.

This is not a threshold relaxation of v55. It represents a different executable
scenario: when completed spot flow led an information move and perpetual price
then accepted it, the signal minute's perpetual close becomes the transferred
auction boundary. The strategy observes exactly the first later touch, requires
current perpetual tail-flow and displayed-depth defense plus non-opposing spot
flow, and enters with a slippage-protected marketable limit toward a still-live
opposing liquidity pool.

v46 remains authoritative for failed-auction reversals. Fees, slippage, current-
NAV risk sizing, order lifecycle, portfolio and NAV remain inherited through the
same NautilusTrader bracket path.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from external_acceptance_retest_logic import external_level_structural_stop
from external_acceptance_retest_logic import first_accepted_level_retest_response
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from sponsored_choch_logic import slippage_protected_marketable_limit
from spot_led_repricing_logic import SPOT_CONTEXT_MAX_AGE_BARS
from spot_led_repricing_logic import spot_context_accepted
from spot_led_repricing_logic import spot_context_invalidated
from spot_led_repricing_logic import spot_led_repricing_direction
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


BRANCH = "SPOT_LED_FIRST_BOUNDARY_RETEST"
_REQUIRED_SPOT_FEATURES = {
    "spot_flow_15s",
    "spot_flow_60s",
    "spot_flow_3m",
    "spot_ret_60s_bps",
    "spot_efficiency_60s",
    "spot_notional_burst",
    "spot_trade_vwap_60s",
    "perp_minus_spot_return_bps",
    "perp_spot_basis_bps",
}


@dataclass(slots=True)
class SpotBoundaryRetestWatch:
    scenario_id: str
    side: int
    level: float
    boundary_high: float
    boundary_low: float
    atr: float
    created_index: int
    created_ts: int
    expires_index: int
    phase: str
    favorable_extreme: float
    accepted_index: int
    details: dict[str, Any]


class SpotBoundaryRetestStrategy(NoPostRetraceBreakawayStrategy):
    """Add one first-retest attempt for each unambiguous spot-led boundary."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.spot_boundary_watch: SpotBoundaryRetestWatch | None = None
        self.spot_boundary_counter = 0
        self.diagnostics.update(
            {
                "spot_boundary_signal_bars": 0,
                "spot_boundary_watches": 0,
                "spot_boundary_same_side_repeats": 0,
                "spot_boundary_opposite_conflicts": 0,
                "spot_boundary_acceptances": 0,
                "spot_boundary_invalidations": 0,
                "spot_boundary_expiries": 0,
                "spot_boundary_first_touches": 0,
                "spot_boundary_defense_rejections": 0,
                "spot_boundary_spot_flow_rejections": 0,
                "spot_boundary_slot_conflicts": 0,
                "spot_boundary_no_live_target": 0,
                "spot_boundary_geometry_rejections": 0,
                "spot_boundary_submissions": 0,
            },
        )

    def on_start(self) -> None:
        super().on_start()
        available = set(self.features[0]) if self.features else set()
        missing = sorted(_REQUIRED_SPOT_FEATURES - available)
        if missing:
            raise RuntimeError(
                "spot boundary-retest feature contract was not installed: "
                f"{missing}",
            )

    def on_bar(self, bar: Any) -> None:
        # v46 gets first priority on the completed observation. The new state is
        # created or advanced only afterwards and cannot trade on its signal bar.
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._advance_spot_boundary_watch(row)
        self._observe_new_spot_boundary(row)

    def _spot_features_ready(self, ts_event: int) -> bool:
        feature = self.current_feature
        return (
            feature is not None
            and self._features_ready(ts_event)
            and all(
                math.isfinite(float(feature.get(name, math.nan)))
                for name in _REQUIRED_SPOT_FEATURES
            )
        )

    def _observe_new_spot_boundary(
        self,
        row: dict[str, float | int],
    ) -> None:
        ts_event = int(row["ts"])
        if not self._in_evaluation(ts_event) or not self._spot_features_ready(ts_event):
            return
        direction = spot_led_repricing_direction(
            spot_flow_15s=self._feature("spot_flow_15s"),
            spot_flow_60s=self._feature("spot_flow_60s"),
            spot_notional_burst=self._feature("spot_notional_burst"),
            spot_return_bps=self._feature("spot_ret_60s_bps"),
            spot_efficiency=self._feature("spot_efficiency_60s"),
            perpetual_return_bps=self._feature("ret_60s_bps"),
        )
        if direction == 0:
            return
        self.diagnostics["spot_boundary_signal_bars"] += 1
        watch = self.spot_boundary_watch
        if watch is not None:
            if watch.side == direction:
                self.diagnostics["spot_boundary_same_side_repeats"] += 1
                watch.details["same_side_signal_count"] = int(
                    watch.details.get("same_side_signal_count", 1),
                ) + 1
            else:
                self.diagnostics["spot_boundary_opposite_conflicts"] += 1
                self._close_spot_boundary_watch(
                    row,
                    "OPPOSITE_SPOT_LED_SIGNAL_MADE_BOUNDARY_AMBIGUOUS",
                )
            return

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        self.spot_boundary_counter += 1
        scenario_id = f"spot-boundary-{self.spot_boundary_counter:07d}"
        level = float(row["close"])
        details = {
            "branch": BRANCH,
            "signal_index": self.bar_index,
            "signal_ts": ts_event,
            "side": direction,
            "boundary_level": level,
            "boundary_high": float(row["high"]),
            "boundary_low": float(row["low"]),
            "spot_flow_15s": self._feature("spot_flow_15s"),
            "spot_flow_60s": self._feature("spot_flow_60s"),
            "spot_flow_3m": self._feature("spot_flow_3m"),
            "spot_ret_60s_bps": self._feature("spot_ret_60s_bps"),
            "perpetual_ret_60s_bps": self._feature("ret_60s_bps"),
            "perp_minus_spot_return_bps": self._feature(
                "perp_minus_spot_return_bps",
            ),
            "spot_efficiency_60s": self._feature("spot_efficiency_60s"),
            "spot_notional_burst": self._feature("spot_notional_burst"),
            "perp_spot_basis_bps": self._feature("perp_spot_basis_bps"),
            "same_side_signal_count": 1,
        }
        self.spot_boundary_watch = SpotBoundaryRetestWatch(
            scenario_id=scenario_id,
            side=direction,
            level=level,
            boundary_high=float(row["high"]),
            boundary_low=float(row["low"]),
            atr=atr,
            created_index=self.bar_index,
            created_ts=ts_event,
            expires_index=self.bar_index + SPOT_CONTEXT_MAX_AGE_BARS,
            phase="AWAIT_ACCEPTANCE",
            favorable_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            accepted_index=-1,
            details=details,
        )
        self.diagnostics["spot_boundary_watches"] += 1
        self._transition(
            scenario_id,
            "SPOT_LED_BOUNDARY_WATCH_ARMED",
            ts_event,
            ts_event,
            "AWAIT_PERPETUAL_ACCEPTANCE",
            "COMPLETED_SPOT_FLOW_LED_PERPETUAL_RETURN",
            level,
            details,
        )

    def _advance_spot_boundary_watch(
        self,
        row: dict[str, float | int],
    ) -> None:
        watch = self.spot_boundary_watch
        if watch is None or self.bar_index <= watch.created_index:
            return
        ts_event = int(row["ts"])
        if self.bar_index > watch.expires_index:
            self.diagnostics["spot_boundary_expiries"] += 1
            self._close_spot_boundary_watch(
                row,
                "SPOT_LED_BOUNDARY_WINDOW_EXPIRED",
            )
            return
        if not self._in_evaluation(ts_event):
            self._close_spot_boundary_watch(
                row,
                "EVALUATION_ENDED_WITH_BOUNDARY_UNRESOLVED",
            )
            return
        if spot_context_invalidated(
            direction=watch.side,
            boundary_low=watch.boundary_low,
            boundary_high=watch.boundary_high,
            current_close=float(row["close"]),
            atr=watch.atr,
        ):
            self.diagnostics["spot_boundary_invalidations"] += 1
            self._close_spot_boundary_watch(
                row,
                "SPOT_LED_BOUNDARY_INVALIDATED_BEFORE_ENTRY",
            )
            return

        if watch.phase == "AWAIT_ACCEPTANCE":
            watch.favorable_extreme = (
                max(watch.favorable_extreme, float(row["high"]))
                if watch.side > 0
                else min(watch.favorable_extreme, float(row["low"]))
            )
            if not self._spot_features_ready(ts_event):
                return
            if not spot_context_accepted(
                direction=watch.side,
                boundary_close=watch.level,
                favorable_extreme=watch.favorable_extreme,
                atr=watch.atr,
                perpetual_flow_3m=self._feature("flow_3m"),
            ):
                return
            watch.phase = "FIRST_RETEST"
            watch.accepted_index = self.bar_index
            watch.details.update(
                {
                    "acceptance_index": self.bar_index,
                    "acceptance_ts": ts_event,
                    "acceptance_close": float(row["close"]),
                    "acceptance_favorable_extreme": watch.favorable_extreme,
                    "acceptance_perpetual_flow_3m": self._feature("flow_3m"),
                },
            )
            self.diagnostics["spot_boundary_acceptances"] += 1
            self._transition(
                watch.scenario_id,
                "SPOT_LED_BOUNDARY_ACCEPTED",
                ts_event,
                ts_event,
                "FIRST_RETEST",
                "PERPETUAL_PRICE_AND_BROAD_FLOW_ACCEPTED_SPOT_LEAD",
                watch.level,
                watch.details,
            )
            return

        if self.bar_index <= watch.accepted_index:
            return
        touched = float(row["low"]) <= watch.level <= float(row["high"])
        if not touched:
            reentered = (
                float(row["close"]) < watch.level
                if watch.side > 0
                else float(row["close"]) > watch.level
            )
            if reentered:
                self._close_spot_boundary_watch(
                    row,
                    "ACCEPTED_SPOT_BOUNDARY_REENTERED_BEFORE_DEFENDED_RETEST",
                )
            return

        self.diagnostics["spot_boundary_first_touches"] += 1
        if not self._spot_features_ready(ts_event):
            self.diagnostics["spot_boundary_defense_rejections"] += 1
            self._close_spot_boundary_watch(
                row,
                "FIRST_BOUNDARY_TOUCH_LACKED_COMPLETE_FEATURE_STATE",
            )
            return
        if watch.side * self._feature("spot_flow_60s") < 0.0:
            self.diagnostics["spot_boundary_spot_flow_rejections"] += 1
            self._close_spot_boundary_watch(
                row,
                "SPOT_FLOW_REVERSED_AT_FIRST_BOUNDARY_RETEST",
            )
            return
        defended = first_accepted_level_retest_response(
            side=watch.side,
            level=watch.level,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_15s=self._feature("flow_15s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            maximum_counterflow=self.config.acceptance_max_counterflow,
        )
        if not defended:
            self.diagnostics["spot_boundary_defense_rejections"] += 1
            self._close_spot_boundary_watch(
                row,
                "FIRST_BOUNDARY_RETEST_LACKED_PERPETUAL_FLOW_DEPTH_DEFENSE",
            )
            return
        if not self._entry_slot_idle():
            self.diagnostics["spot_boundary_slot_conflicts"] += 1
            self._close_spot_boundary_watch(
                row,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_BOUNDARY_RETEST",
            )
            return
        self._submit_spot_boundary_retest(watch, row)

    def _entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not bool(getattr(self, "exit_pending", False))
            and self.pending is None
            and self.armed_entry_path is None
            and not bool(getattr(self, "counter_context_parent_lock_active", False))
            and self.bar_index - self.last_entry_index >= self.config.cooldown_bars
        )

    def _submit_spot_boundary_retest(
        self,
        watch: SpotBoundaryRetestWatch,
        row: dict[str, float | int],
    ) -> bool:
        side = watch.side
        observed = float(row["close"])
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        raw_entry = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        entry_price = self.instrument.make_price(raw_entry)
        entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["spot_boundary_geometry_rejections"] += 1
            self._close_spot_boundary_watch(
                row,
                "BOUNDARY_RETEST_LIMIT_LACKED_SLIPPAGE_ROOM",
            )
            return False
        raw_stop = external_level_structural_stop(
            side=side,
            level=watch.level,
            atr=watch.atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        if (side > 0 and not stop < entry) or (side < 0 and not entry < stop):
            self.diagnostics["spot_boundary_geometry_rejections"] += 1
            self._close_spot_boundary_watch(row, "INVALID_BOUNDARY_STOP_GEOMETRY")
            return False
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["spot_boundary_geometry_rejections"] += 1
            self._close_spot_boundary_watch(row, "INVALID_BOUNDARY_PLANNED_LOSS")
            return False
        target, target_source, target_r = choose_liquidity_target(
            entry=entry,
            side=side,
            pools=list(self.active_pools.values()),
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            min_net_r=MIN_LIQUIDITY_TARGET_NET_R,
            max_net_r=MAX_LIQUIDITY_TARGET_NET_R,
            fallback_net_r=FALLBACK_TARGET_NET_R,
        )
        if not target_source.startswith("POOL:"):
            self.diagnostics["spot_boundary_no_live_target"] += 1
            self._close_spot_boundary_watch(
                row,
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
            )
            return False
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["spot_boundary_geometry_rejections"] += 1
            self._close_spot_boundary_watch(
                row,
                "ROUNDED_BOUNDARY_RETEST_BRACKET_INVALID",
            )
            return False

        details = {
            **watch.details,
            "retest_index": self.bar_index,
            "retest_ts": int(row["ts"]),
            "retest_close": observed,
            "retest_spot_flow_60s": self._feature("spot_flow_60s"),
            "retest_perpetual_flow_15s": self._feature("flow_15s"),
            "retest_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r": rounded_r,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=BRANCH,
            side=side,
            swept_kind="SPOT_LED_HIGH" if side > 0 else "SPOT_LED_LOW",
            pool_id=f"spot-boundary-level-{watch.scenario_id}",
            pool_level=watch.level,
            created_index=watch.accepted_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=watch.level,
            structure=watch.level,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="SPOT_LED_FIRST_BOUNDARY_RETEST",
            choch_close=entry,
            stop=stop,
            atr=watch.atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.armed_entry_path = armed
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=rounded_r,
            branch=BRANCH,
            event_type="SPOT_LED_BOUNDARY_RETEST_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_AFTER_SPOT_LED_PERPETUAL_ACCEPTANCE",
            expires_index=self.bar_index + 2,
            entry_tag="SPOT_LED_BOUNDARY_RETEST_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["spot_boundary_submissions"] += 1
            self.spot_boundary_watch = None
        elif self.armed_entry_path is armed:
            self.armed_entry_path = None
        return submitted

    def _close_spot_boundary_watch(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        watch = self.spot_boundary_watch
        if watch is None:
            return
        self._transition(
            watch.scenario_id,
            "SPOT_LED_BOUNDARY_WATCH_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            watch.details,
        )
        self.spot_boundary_watch = None


LiquidityResponseStrategy = SpotBoundaryRetestStrategy

__all__ = [
    "BRANCH",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "SpotBoundaryRetestStrategy",
    "SpotBoundaryRetestWatch",
]
