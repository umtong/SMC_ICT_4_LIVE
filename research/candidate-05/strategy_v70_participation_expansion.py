#!/usr/bin/env python3
"""Candidate 05 v70: first pullback after OI-expanding joint repricing.

A completed five-minute perpetual move must be efficient, accompanied by new
open interest, aligned spot/perpetual three-minute aggressor flow and a spot move
which is not merely following a perpetual liquidation impulse. The strategy
then observes only the first later touch of the impulse midpoint. Current spot
flow, perpetual tail flow and visible depth must defend the repricing direction.

This continuation family is economically distinct from v46 failed-auction
reversals, v59 one-minute spot boundaries and v68 OI-contracting liquidation
reversions. v46 receives each bar first. Orders, fees, slippage, current-NAV
sizing, lifecycle, portfolio and NAV remain inherited through NautilusTrader.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from participation_expansion_logic import first_expansion_pullback_defended
from participation_expansion_logic import participation_expansion_direction
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


MINUTE_NS = 60_000_000_000
BRANCH = "PARTICIPATION_EXPANSION_PULLBACK"
_REQUIRED_FEATURES = {
    "spot_flow_60s",
    "spot_flow_3m",
    "spot_ret_60s_bps",
    "perp_minus_spot_return_bps",
    "oi_change_5m",
    "metrics_ready",
}


@dataclass(slots=True)
class ParticipationExpansionWatch:
    scenario_id: str
    side: int
    origin_close: float
    signal_close: float
    midpoint: float
    impulse_low: float
    impulse_high: float
    atr: float
    created_index: int
    created_ts: int
    expires_index: int
    details: dict[str, Any]


class ParticipationExpansionStrategy(NoPostRetraceBreakawayStrategy):
    """Add one first-midpoint-pullback attempt per five-minute information leg."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.participation_watch: ParticipationExpansionWatch | None = None
        self.participation_counter = 0
        self.diagnostics.update(
            {
                "participation_five_minute_observations": 0,
                "participation_expansion_signals": 0,
                "participation_same_side_repeats": 0,
                "participation_opposite_conflicts": 0,
                "participation_invalidations": 0,
                "participation_expiries": 0,
                "participation_first_midpoint_touches": 0,
                "participation_pullback_defense_rejections": 0,
                "participation_slot_conflicts": 0,
                "participation_no_live_target": 0,
                "participation_invalid_geometry": 0,
                "participation_submissions": 0,
                "participation_long_submissions": 0,
                "participation_short_submissions": 0,
            },
        )

    def on_start(self) -> None:
        super().on_start()
        available = set(self.features[0]) if self.features else set()
        missing = sorted(_REQUIRED_FEATURES - available)
        if missing:
            raise RuntimeError(
                "participation-expansion observation contract was not installed: "
                f"{missing}",
            )

    def on_bar(self, bar: Any) -> None:
        # v46 and all inherited position/order management run first.
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        self._advance_participation_watch(row)
        self._observe_five_minute_expansion(row)

    def _observation_ready(self, ts_event: int) -> bool:
        feature = self.current_feature
        return (
            feature is not None
            and bool(feature.get("feature_ready", False))
            and bool(feature.get("metrics_ready", False))
            and self._in_evaluation(ts_event)
            and all(
                math.isfinite(self._feature(name))
                for name in (
                    "spot_flow_60s",
                    "spot_flow_3m",
                    "spot_ret_60s_bps",
                    "perp_minus_spot_return_bps",
                    "oi_change_5m",
                    "flow_15s",
                    "flow_3m",
                    "depth_imbalance_1",
                )
            )
        )

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

    def _observe_five_minute_expansion(
        self,
        row: dict[str, float | int],
    ) -> None:
        ts_event = int(row["ts"])
        minute = ts_event // MINUTE_NS
        if minute % 5 != 4 or len(self.bars) < 6 or not self._observation_ready(ts_event):
            return
        self.diagnostics["participation_five_minute_observations"] += 1
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        rows = list(self.bars)
        selected = rows[-6:]
        origin_close = float(selected[0]["close"])
        signal_close = float(selected[-1]["close"])
        move = signal_close - origin_close
        path = sum(
            abs(float(right["close"]) - float(left["close"]))
            for left, right in zip(selected, selected[1:])
        )
        efficiency = abs(move) / path if path > 0.0 else 0.0
        side = participation_expansion_direction(
            move_atr=move / atr,
            path_efficiency=efficiency,
            oi_change_5m=self._feature("oi_change_5m"),
            spot_flow_3m=self._feature("spot_flow_3m"),
            perpetual_flow_3m=self._feature("flow_3m"),
            spot_return_bps=self._feature("spot_ret_60s_bps"),
            perp_minus_spot_return_bps=self._feature(
                "perp_minus_spot_return_bps",
            ),
        )
        if side == 0:
            return
        self.diagnostics["participation_expansion_signals"] += 1
        current = self.participation_watch
        if current is not None:
            if current.side == side:
                self.diagnostics["participation_same_side_repeats"] += 1
                current.details["same_side_signal_count"] = int(
                    current.details.get("same_side_signal_count", 1),
                ) + 1
                return
            self.diagnostics["participation_opposite_conflicts"] += 1
            self._close_participation_watch(
                row,
                "OPPOSITE_OI_EXPANSION_SIGNAL_MADE_CONTEXT_AMBIGUOUS",
            )
            return

        self.participation_counter += 1
        scenario_id = f"participation-{self.participation_counter:07d}"
        midpoint = origin_close + 0.50 * move
        impulse_low = min(float(item["low"]) for item in selected[1:])
        impulse_high = max(float(item["high"]) for item in selected[1:])
        details = {
            "branch": BRANCH,
            "side": side,
            "signal_index": self.bar_index,
            "signal_ts": ts_event,
            "origin_close": origin_close,
            "signal_close": signal_close,
            "midpoint": midpoint,
            "impulse_low": impulse_low,
            "impulse_high": impulse_high,
            "move_atr": move / atr,
            "path_efficiency_5m": efficiency,
            "oi_change_5m": self._feature("oi_change_5m"),
            "spot_flow_3m": self._feature("spot_flow_3m"),
            "perpetual_flow_3m": self._feature("flow_3m"),
            "spot_ret_60s_bps": self._feature("spot_ret_60s_bps"),
            "perp_minus_spot_return_bps": self._feature(
                "perp_minus_spot_return_bps",
            ),
            "same_side_signal_count": 1,
        }
        self.participation_watch = ParticipationExpansionWatch(
            scenario_id=scenario_id,
            side=side,
            origin_close=origin_close,
            signal_close=signal_close,
            midpoint=midpoint,
            impulse_low=impulse_low,
            impulse_high=impulse_high,
            atr=atr,
            created_index=self.bar_index,
            created_ts=ts_event,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            details=details,
        )
        self._transition(
            scenario_id,
            "PARTICIPATION_EXPANSION_WATCH_ARMED",
            ts_event,
            ts_event,
            "FIRST_MIDPOINT_PULLBACK",
            "EFFICIENT_JOINT_REPRICING_WITH_OPEN_INTEREST_EXPANSION",
            midpoint,
            details,
        )

    def _advance_participation_watch(
        self,
        row: dict[str, float | int],
    ) -> None:
        watch = self.participation_watch
        if watch is None or self.bar_index <= watch.created_index:
            return
        if self.bar_index > watch.expires_index:
            self.diagnostics["participation_expiries"] += 1
            self._close_participation_watch(
                row,
                "PARTICIPATION_PULLBACK_WINDOW_EXPIRED",
            )
            return
        invalid = (
            float(row["close"]) < watch.origin_close - 0.20 * watch.atr
            if watch.side > 0
            else float(row["close"]) > watch.origin_close + 0.20 * watch.atr
        )
        if invalid:
            self.diagnostics["participation_invalidations"] += 1
            self._close_participation_watch(
                row,
                "OI_EXPANSION_REPRICING_ORIGIN_INVALIDATED",
            )
            return
        touched = float(row["low"]) <= watch.midpoint <= float(row["high"])
        if not touched:
            return
        self.diagnostics["participation_first_midpoint_touches"] += 1
        defended = (
            self._observation_ready(int(row["ts"]))
            and first_expansion_pullback_defended(
                side=watch.side,
                midpoint=watch.midpoint,
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_15s=self._feature("flow_15s"),
                depth_imbalance=self._feature("depth_imbalance_1"),
                spot_flow_60s=self._feature("spot_flow_60s"),
            )
        )
        if not defended:
            self.diagnostics["participation_pullback_defense_rejections"] += 1
            self._close_participation_watch(
                row,
                "FIRST_MIDPOINT_TOUCH_LACKED_SPOT_PERPETUAL_DEFENSE",
            )
            return
        if not self._entry_slot_idle():
            self.diagnostics["participation_slot_conflicts"] += 1
            self._close_participation_watch(
                row,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_PARTICIPATION_PULLBACK",
            )
            return
        self._submit_participation_pullback(watch, row)

    def _submit_participation_pullback(
        self,
        watch: ParticipationExpansionWatch,
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
            self.diagnostics["participation_invalid_geometry"] += 1
            self._close_participation_watch(row, "PARTICIPATION_LIMIT_LACKED_SLIPPAGE_ROOM")
            return False
        raw_stop = (
            watch.impulse_low - self.config.stop_buffer_atr * watch.atr
            if side > 0
            else watch.impulse_high + self.config.stop_buffer_atr * watch.atr
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["participation_invalid_geometry"] += 1
            self._close_participation_watch(row, "INVALID_PARTICIPATION_PLANNED_LOSS")
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
            self.diagnostics["participation_no_live_target"] += 1
            self._close_participation_watch(
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
            self.diagnostics["participation_invalid_geometry"] += 1
            self._close_participation_watch(
                row,
                "ROUNDED_PARTICIPATION_BRACKET_INVALID",
            )
            return False

        details = {
            **watch.details,
            "pullback_index": self.bar_index,
            "pullback_ts": int(row["ts"]),
            "pullback_close": observed,
            "pullback_flow_15s": self._feature("flow_15s"),
            "pullback_depth_imbalance_1": self._feature("depth_imbalance_1"),
            "pullback_spot_flow_60s": self._feature("spot_flow_60s"),
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
            swept_kind="OI_EXPANSION_UP" if side > 0 else "OI_EXPANSION_DOWN",
            pool_id=f"participation-target-{watch.scenario_id}",
            pool_level=target,
            created_index=watch.created_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=watch.impulse_low if side > 0 else watch.impulse_high,
            structure=watch.midpoint,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="OI_EXPANSION_FIRST_PULLBACK",
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
            event_type="PARTICIPATION_EXPANSION_PULLBACK_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_MIDPOINT_PULLBACK_AFTER_OI_EXPANSION",
            expires_index=self.bar_index + 2,
            entry_tag="PARTICIPATION_EXPANSION_PULLBACK_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["participation_submissions"] += 1
            self.diagnostics[
                "participation_long_submissions" if side > 0 else "participation_short_submissions"
            ] += 1
            self.participation_watch = None
        elif self.armed_entry_path is armed:
            self.armed_entry_path = None
        return submitted

    def _close_participation_watch(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        watch = self.participation_watch
        if watch is None:
            return
        self._transition(
            watch.scenario_id,
            "PARTICIPATION_EXPANSION_WATCH_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            watch.details,
        )
        self.participation_watch = None


LiquidityResponseStrategy = ParticipationExpansionStrategy

__all__ = [
    "BRANCH",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "ParticipationExpansionStrategy",
    "ParticipationExpansionWatch",
]
