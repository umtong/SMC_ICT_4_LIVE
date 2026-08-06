#!/usr/bin/env python3
"""Candidate 05 v32: persistent queue pressure confirmed by actual release."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar

from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from queue_pressure_release_logic import CompressionRange
from queue_pressure_release_logic import boundary_retest_invalidated
from queue_pressure_release_logic import compressed_range
from queue_pressure_release_logic import compression_structural_stop
from queue_pressure_release_logic import first_boundary_retest_response
from queue_pressure_release_logic import persistent_queue_pressure
from queue_pressure_release_logic import pressure_release_breakout
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v26 import ScenarioValidEntryStrategy


@dataclass(slots=True)
class QueueSnapshot:
    index: int
    ts: int
    open: float
    high: float
    low: float
    close: float
    flow_15s: float
    flow_60s: float
    efficiency_60s: float
    notional_burst: float
    depth_imbalance: float
    bid_depth_change_1m: float
    ask_depth_change_1m: float


@dataclass(slots=True)
class QueueReleaseWatch:
    scenario_id: str
    side: int
    compression: CompressionRange
    boundary: float
    entry: float
    stop: float
    target: float
    target_source: str
    target_pool_id: str
    target_r: float
    planned_loss: float
    created_index: int
    created_ts: int
    expires_index: int
    details: dict[str, Any]


class QueuePressureReleaseStrategy(ScenarioValidEntryStrategy):
    """Trade only when displayed pressure becomes flow and price discovery.

    Three completed one-minute observations must hold a mirror-symmetric 2:1
    top-depth imbalance inside a range no wider than twice the existing v26
    displacement-body threshold.  No trade is taken from displayed depth alone.
    A completed breakout must also satisfy the existing acceptance flow,
    efficiency, notional-burst, close-location and opposing-depth-withdrawal
    contracts.  The strategy then waits for the first later completed retest of
    the frozen boundary and submits a passive limit only if current tail flow and
    depth still support the release.

    The opposite side of the frozen compression defines structural invalidation.
    The target is a still-live opposing liquidity pool selected at breakout time;
    it is never substituted later.  Costs, 3% current-NAV risk sizing, pending
    order validity, margin, liquidation and NAV remain v26/Nautilus-owned.
    """

    BRANCH = "QUEUE_PRESSURE_CONFIRMED_RELEASE"

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.queue_snapshots: deque[QueueSnapshot] = deque(maxlen=64)
        self.queue_release_watch: QueueReleaseWatch | None = None
        self.queue_scenario_counter = 0
        self.diagnostics.update(
            {
                "queue_pressure_compressions": 0,
                "queue_pressure_release_breakouts": 0,
                "queue_pressure_no_live_target": 0,
                "queue_pressure_invalid_geometry": 0,
                "queue_pressure_target_already_reached": 0,
                "queue_pressure_watches": 0,
                "queue_pressure_retest_observations": 0,
                "queue_pressure_retest_confirmations": 0,
                "queue_pressure_retest_expired": 0,
                "queue_pressure_balance_invalidated": 0,
                "queue_pressure_target_reached_before_entry": 0,
                "queue_pressure_target_source_expired": 0,
                "queue_pressure_slot_conflicts": 0,
                "queue_pressure_submissions": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        snapshot = QueueSnapshot(
            index=self.bar_index,
            ts=int(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
        )
        self.queue_snapshots.append(snapshot)

        if not self._in_evaluation(snapshot.ts):
            self._close_queue_watch(snapshot, "EVALUATION_ENDED_DURING_QUEUE_RELEASE")
            return
        if self._funding_blackout(snapshot.ts):
            self._close_queue_watch(snapshot, "FUNDING_BLACKOUT_DURING_QUEUE_RELEASE")
            return
        if not self._queue_features_ready(snapshot.ts):
            return

        if self.queue_release_watch is not None:
            self._advance_queue_watch(snapshot)
        if self.queue_release_watch is None:
            self._detect_queue_release(snapshot)

    def _queue_features_ready(self, ts: int) -> bool:
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return False
        observed = int(feature["observed_time_ns"])
        age = (ts - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached v32")
        return age <= self.config.feature_max_age_seconds

    def _detect_queue_release(self, breakout: QueueSnapshot) -> None:
        if len(self.queue_snapshots) < 4:
            return
        compression_bars = list(self.queue_snapshots)[-4:-1]
        side = persistent_queue_pressure(
            [item.depth_imbalance for item in compression_bars],
        )
        if side == 0:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        compression = compressed_range(
            highs=[item.high for item in compression_bars],
            lows=[item.low for item in compression_bars],
            atr=atr,
            maximum_width_atr=2.0 * self.config.rejection_confirm_body_atr,
        )
        if compression is None:
            return
        self.diagnostics["queue_pressure_compressions"] += 1
        if not pressure_release_breakout(
            side=side,
            compression=compression,
            open_price=breakout.open,
            high=breakout.high,
            low=breakout.low,
            close=breakout.close,
            atr=atr,
            flow_60s=breakout.flow_60s,
            efficiency_60s=breakout.efficiency_60s,
            notional_burst=breakout.notional_burst,
            bid_depth_change_1m=breakout.bid_depth_change_1m,
            ask_depth_change_1m=breakout.ask_depth_change_1m,
            minimum_break_distance_atr=self.config.acceptance_close_atr,
            minimum_flow=self.config.acceptance_flow_min,
            minimum_efficiency=self.config.acceptance_efficiency_min,
            minimum_notional_burst=self.config.sweep_min_notional_burst,
            minimum_depth_withdrawal=self.config.acceptance_depth_withdrawal_min,
            minimum_close_location=self.config.acceptance_close_location,
        ):
            return
        self.diagnostics["queue_pressure_release_breakouts"] += 1

        boundary = compression.upper if side > 0 else compression.lower
        entry_price = self.instrument.make_price(boundary)
        entry = _as_float(entry_price)
        raw_stop = compression_structural_stop(
            side=side,
            compression=compression,
            atr=atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        self.queue_scenario_counter += 1
        scenario_id = f"qpr-{self.queue_scenario_counter:07d}"
        base_details = {
            "side": side,
            "compression_start_index": compression_bars[0].index,
            "compression_end_index": compression_bars[-1].index,
            "compression_lower": compression.lower,
            "compression_upper": compression.upper,
            "compression_midpoint": compression.midpoint,
            "breakout_index": breakout.index,
            "breakout_ts": breakout.ts,
            "breakout_close": breakout.close,
            "breakout_flow_60s": breakout.flow_60s,
            "breakout_efficiency_60s": breakout.efficiency_60s,
            "breakout_notional_burst": breakout.notional_burst,
            "breakout_depth_imbalance": breakout.depth_imbalance,
            "entry_boundary": entry,
            "stop": stop,
        }
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["queue_pressure_invalid_geometry"] += 1
            self._transition(
                scenario_id,
                "QUEUE_RELEASE_REJECTED",
                breakout.ts,
                breakout.ts,
                "CLOSED",
                "INVALID_FROZEN_COMPRESSION_STOP_GEOMETRY",
                entry,
                base_details,
            )
            return

        target, target_source, _ = choose_liquidity_target(
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
            self.diagnostics["queue_pressure_no_live_target"] += 1
            self._transition(
                scenario_id,
                "QUEUE_RELEASE_REJECTED",
                breakout.ts,
                breakout.ts,
                "CLOSED",
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
                entry,
                base_details,
            )
            return
        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["queue_pressure_invalid_geometry"] += 1
            self._transition(
                scenario_id,
                "QUEUE_RELEASE_REJECTED",
                breakout.ts,
                breakout.ts,
                "CLOSED",
                "ROUNDED_QUEUE_RELEASE_BRACKET_INVALID",
                entry,
                {**base_details, "target": target, "target_source": target_source},
            )
            return
        if (side > 0 and breakout.high >= target) or (side < 0 and breakout.low <= target):
            self.diagnostics["queue_pressure_target_already_reached"] += 1
            self._transition(
                scenario_id,
                "QUEUE_RELEASE_REJECTED",
                breakout.ts,
                breakout.ts,
                "CLOSED",
                "FROZEN_TARGET_REACHED_ON_BREAKOUT_BEFORE_FUTURE_ENTRY",
                target,
                {**base_details, "target": target, "target_source": target_source},
            )
            return

        details = {
            **base_details,
            "target": target,
            "target_source": target_source,
            "target_net_r": rounded_r,
            "planned_loss_per_unit": planned_loss,
            "target_policy": "FROZEN_AT_CONFIRMED_QUEUE_RELEASE",
        }
        watch = QueueReleaseWatch(
            scenario_id=scenario_id,
            side=side,
            compression=compression,
            boundary=entry,
            entry=entry,
            stop=stop,
            target=target,
            target_source=target_source,
            target_pool_id=target_source.split(":", 1)[1],
            target_r=rounded_r,
            planned_loss=planned_loss,
            created_index=self.bar_index,
            created_ts=breakout.ts,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            details=details,
        )
        self.queue_release_watch = watch
        self.diagnostics["queue_pressure_watches"] += 1
        self._transition(
            scenario_id,
            "QUEUE_PRESSURE_RELEASE_CONFIRMED",
            breakout.ts,
            breakout.ts,
            "WAIT_FIRST_BOUNDARY_RETEST",
            "PERSISTENT_DEPTH_PRESSURE_BECAME_FLOW_EFFICIENCY_AND_OPPOSING_DEPTH_WITHDRAWAL",
            entry,
            details,
        )

    def _advance_queue_watch(self, bar: QueueSnapshot) -> None:
        watch = self.queue_release_watch
        if watch is None or self.bar_index <= watch.created_index:
            return
        self.diagnostics["queue_pressure_retest_observations"] += 1
        if boundary_retest_invalidated(
            side=watch.side,
            compression=watch.compression,
            close=bar.close,
        ):
            self.diagnostics["queue_pressure_balance_invalidated"] += 1
            self._close_queue_watch(bar, "PRICE_CLOSED_THROUGH_OPPOSITE_SIDE_OF_FROZEN_COMPRESSION")
            return
        if (watch.side > 0 and bar.high >= watch.target) or (watch.side < 0 and bar.low <= watch.target):
            self.diagnostics["queue_pressure_target_reached_before_entry"] += 1
            self._close_queue_watch(bar, "FROZEN_TARGET_REACHED_BEFORE_EXECUTABLE_RETEST_ENTRY")
            return
        if watch.target_pool_id not in self.active_pools:
            self.diagnostics["queue_pressure_target_source_expired"] += 1
            self._close_queue_watch(bar, "FROZEN_TARGET_SOURCE_EXPIRED_BEFORE_ENTRY")
            return
        if self.bar_index > watch.expires_index:
            self.diagnostics["queue_pressure_retest_expired"] += 1
            self._close_queue_watch(bar, "EXISTING_FIRST_BOUNDARY_RETEST_WINDOW_EXPIRED")
            return
        if not first_boundary_retest_response(
            side=watch.side,
            boundary=watch.boundary,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            flow_15s=bar.flow_15s,
            depth_imbalance=bar.depth_imbalance,
            maximum_counterflow=self.config.acceptance_max_counterflow,
        ):
            return
        self.diagnostics["queue_pressure_retest_confirmations"] += 1
        if not self._queue_entry_slot_idle():
            self.diagnostics["queue_pressure_slot_conflicts"] += 1
            self._close_queue_watch(bar, "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_FIRST_BOUNDARY_RETEST")
            return
        self._submit_queue_release(watch, bar)

    def _queue_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
        )

    def _submit_queue_release(
        self,
        watch: QueueReleaseWatch,
        bar: QueueSnapshot,
    ) -> None:
        entry_price = self.instrument.make_price(watch.entry)
        stop_price = self.instrument.make_price(watch.stop)
        target_price = self.instrument.make_price(watch.target)
        details = {
            **watch.details,
            "retest_index": self.bar_index,
            "retest_ts": bar.ts,
            "retest_close": bar.close,
            "retest_flow_15s": bar.flow_15s,
            "retest_depth_imbalance": bar.depth_imbalance,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=self.BRANCH,
            side=watch.side,
            swept_kind="QUEUE_PRESSURE",
            pool_id=watch.target_pool_id,
            pool_level=watch.boundary,
            created_index=watch.created_index,
            expires_index=watch.expires_index,
            sweep_extreme=watch.compression.upper if watch.side > 0 else watch.compression.lower,
            structure=watch.boundary,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="QUEUE_PRESSURE_CONFIRMED_RELEASE",
            choch_close=watch.entry,
            stop=watch.stop,
            atr=self._atr(),
            created_index=watch.created_index,
            expires_index=watch.expires_index,
            details=details,
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row={
                "ts": bar.ts,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            },
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=watch.entry,
            planned_loss=watch.planned_loss,
            target_source=watch.target_source,
            target_r=watch.target_r,
            branch=self.BRANCH,
            event_type="QUEUE_PRESSURE_BOUNDARY_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_AFTER_CONFIRMED_QUEUE_PRESSURE_RELEASE",
            expires_index=watch.expires_index,
            entry_tag="QUEUE_PRESSURE_RELEASE_ENTRY",
            extra=details,
        )
        if submitted:
            self.queue_release_watch = None
            self.diagnostics["queue_pressure_submissions"] += 1
        else:
            self._close_queue_watch(bar, "QUEUE_PRESSURE_BRACKET_SUBMISSION_FAILED")

    def _close_queue_watch(self, bar: QueueSnapshot, reason: str) -> None:
        watch = self.queue_release_watch
        if watch is None:
            return
        self.queue_release_watch = None
        if self.scenario_states.get(watch.scenario_id) == "CLOSED":
            return
        self._transition(
            watch.scenario_id,
            "QUEUE_PRESSURE_RELEASE_CLOSED",
            bar.ts,
            bar.ts,
            "CLOSED",
            reason,
            watch.boundary,
            dict(watch.details),
        )

    def on_stop(self) -> None:
        if self.queue_release_watch is not None and self.queue_snapshots:
            self._close_queue_watch(
                self.queue_snapshots[-1],
                "BACKTEST_ENDED_WITH_QUEUE_RELEASE_WATCH_OPEN",
            )
        super().on_stop()


__all__ = ["QueuePressureReleaseStrategy"]
