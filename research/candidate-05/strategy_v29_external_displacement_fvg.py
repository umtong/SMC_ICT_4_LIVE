#!/usr/bin/env python3
"""Candidate 05 v29: external-liquidity acceptance, displacement and FVG retest."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar

from external_displacement_fvg_logic import DisplacementGap
from external_displacement_fvg_logic import displacement_gap
from external_displacement_fvg_logic import first_retest_response
from external_displacement_fvg_logic import gap_invalidated
from external_displacement_fvg_logic import structural_gap_stop
from external_session_logic import utc_session_key
from external_session_logic import validate_uniform_session_hours
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from logic import SweepEvidence
from logic import choose_liquidity_target
from logic import classify_sweep
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v26 import ScenarioValidEntryStrategy


@dataclass(slots=True)
class ExternalLevel:
    level_id: str
    kind: str
    level: float
    observed_index: int
    observed_ts: int
    expires_index: int


@dataclass(slots=True)
class ObservedBar:
    index: int
    ts: int
    open: float
    high: float
    low: float
    close: float
    flow_15s: float
    flow_60s: float
    efficiency_60s: float
    depth_imbalance: float


@dataclass(slots=True)
class ExternalFvgWatch:
    scenario_id: str
    side: int
    level_id: str
    level_kind: str
    external_level: float
    breakout_index: int
    breakout_ts: int
    breakout_extreme: float
    atr: float
    phase: str
    formation_expires_index: int
    gap: DisplacementGap | None
    gap_formed_index: int
    retest_expires_index: int
    details: dict[str, Any]


class ExternalDisplacementFvgStrategy(ScenarioValidEntryStrategy):
    """Add one orthogonal continuation scenario to the frozen v26 baseline.

    The new branch begins only after a fully completed four-hour UTC activity
    session supplies an external high or low.  Crossing the level is not enough:
    the unchanged Candidate 05 acceptance classifier must confirm aligned
    aggressor flow, efficient price response and threatened-side depth
    withdrawal.  A completed three-bar directional imbalance must then form
    outside the accepted level.  Only the first later return into that imbalance
    can arm a passive midpoint order, and only when the completed retest defends
    the midpoint with current tail flow and resting depth.

    The structural stop is beyond both the external level and distal gap edge.
    The target must be a still-live opposing liquidity pool; no fallback target
    is allowed for this branch.  Sizing, costs, slippage, order lifecycle,
    accounting, margin and liquidation remain inherited from v26 and
    NautilusTrader.  Observation can be parallel, execution remains singular.
    """

    BRANCH = "EXTERNAL_DISPLACEMENT_FVG_RETEST"

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        span = validate_uniform_session_hours(tuple(config.session_hours))
        if span != 4:
            raise ValueError("v29 requires the frozen uniform four-hour session clock")
        self.external_session_key: int | None = None
        self.external_session_high = -math.inf
        self.external_session_low = math.inf
        self.external_level_counter = 0
        self.external_scenario_counter = 0
        self.external_levels: dict[str, ExternalLevel] = {}
        self.external_fvg_watches: dict[str, ExternalFvgWatch] = {}
        self.observed_bars: deque[ObservedBar] = deque(maxlen=16)
        self.diagnostics.update(
            {
                "external_fvg_completed_sessions": 0,
                "external_fvg_levels_armed": 0,
                "external_fvg_levels_expired": 0,
                "external_fvg_two_sided_shocks": 0,
                "external_fvg_accesses": 0,
                "external_fvg_acceptances": 0,
                "external_fvg_non_acceptances": 0,
                "external_fvg_watches": 0,
                "external_fvg_gaps_formed": 0,
                "external_fvg_formation_expiries": 0,
                "external_fvg_retest_expiries": 0,
                "external_fvg_invalidations": 0,
                "external_fvg_retest_confirmations": 0,
                "external_fvg_slot_conflicts": 0,
                "external_fvg_no_live_target": 0,
                "external_fvg_geometry_rejections": 0,
                "external_fvg_submissions": 0,
                "external_fvg_parallel_observers_max": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        previous_close = (
            self.observed_bars[-1].close
            if self.observed_bars
            else float(row["open"])
        )
        snapshot = ObservedBar(
            index=self.bar_index,
            ts=int(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
        )
        self.observed_bars.append(snapshot)
        self._roll_external_session(snapshot)
        self._expire_external_levels(snapshot)

        if not self._in_evaluation(snapshot.ts):
            self._close_all_external_watches(
                snapshot,
                "EVALUATION_ENDED_DURING_EXTERNAL_FVG_SCENARIO",
            )
            return
        if self._funding_blackout(snapshot.ts):
            self._close_all_external_watches(
                snapshot,
                "FUNDING_BLACKOUT_DURING_EXTERNAL_FVG_SCENARIO",
            )
            return
        if not self._external_features_ready(snapshot.ts):
            return

        self._advance_external_watches(snapshot)
        self._detect_external_acceptance(snapshot, previous_close)

    def _external_features_ready(self, ts: int) -> bool:
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return False
        observed = int(feature["observed_time_ns"])
        age = (ts - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached v29")
        return age <= self.config.feature_max_age_seconds

    def _roll_external_session(self, bar: ObservedBar) -> None:
        key = utc_session_key(bar.ts, tuple(self.config.session_hours))
        if self.external_session_key is None:
            self.external_session_key = key
        elif key != self.external_session_key:
            if math.isfinite(self.external_session_high) and math.isfinite(self.external_session_low):
                self._arm_external_level("HIGH", self.external_session_high, bar)
                self._arm_external_level("LOW", self.external_session_low, bar)
                self.diagnostics["external_fvg_completed_sessions"] += 1
            self.external_session_key = key
            self.external_session_high = -math.inf
            self.external_session_low = math.inf
        self.external_session_high = max(self.external_session_high, bar.high)
        self.external_session_low = min(self.external_session_low, bar.low)

    def _arm_external_level(
        self,
        kind: str,
        level: float,
        bar: ObservedBar,
    ) -> None:
        self.external_level_counter += 1
        level_id = f"ext4h-{self.external_level_counter:07d}"
        item = ExternalLevel(
            level_id=level_id,
            kind=kind,
            level=level,
            observed_index=self.bar_index,
            observed_ts=bar.ts,
            expires_index=self.bar_index + self.config.pool_max_age_bars,
        )
        self.external_levels[level_id] = item
        self.diagnostics["external_fvg_levels_armed"] += 1
        self._transition(
            level_id,
            "EXTERNAL_SESSION_LEVEL_CONFIRMED",
            bar.ts,
            bar.ts,
            "EXTERNAL_LEVEL_ARMED",
            "FULLY_COMPLETED_FOUR_HOUR_SESSION_EXTREME",
            level,
            {
                "kind": kind,
                "observed_index": item.observed_index,
                "expires_index": item.expires_index,
            },
        )

    def _expire_external_levels(self, bar: ObservedBar) -> None:
        for level_id, item in list(self.external_levels.items()):
            if self.bar_index <= item.expires_index:
                continue
            self.external_levels.pop(level_id, None)
            self.diagnostics["external_fvg_levels_expired"] += 1
            self._transition(
                level_id,
                "EXTERNAL_SESSION_LEVEL_EXPIRED",
                bar.ts,
                bar.ts,
                "CLOSED",
                "EXISTING_POOL_MAX_AGE_REACHED",
                item.level,
                {"age_bars": self.bar_index - item.observed_index},
            )

    def _detect_external_acceptance(
        self,
        bar: ObservedBar,
        previous_close: float,
    ) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        min_age = self.config.pool_min_age_bars
        high_crossed = [
            item
            for item in self.external_levels.values()
            if item.kind == "HIGH"
            and self.bar_index - item.observed_index >= min_age
            and previous_close <= item.level
            and bar.high >= item.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            item
            for item in self.external_levels.values()
            if item.kind == "LOW"
            and self.bar_index - item.observed_index >= min_age
            and previous_close >= item.level
            and bar.low <= item.level - self.config.sweep_min_penetration_atr * atr
        ]
        if not high_crossed and not low_crossed:
            return
        if high_crossed and low_crossed:
            self.diagnostics["external_fvg_two_sided_shocks"] += 1
            for item in high_crossed + low_crossed:
                self._consume_external_level(item, bar, "AMBIGUOUS_TWO_SIDED_EXTERNAL_SHOCK")
            return

        if high_crossed:
            chosen = max(high_crossed, key=lambda item: item.level)
            crossed = high_crossed
            kind = "HIGH"
            side = 1
        else:
            chosen = min(low_crossed, key=lambda item: item.level)
            crossed = low_crossed
            kind = "LOW"
            side = -1
        for item in crossed:
            self._consume_external_level(item, bar, "EXTERNAL_LIQUIDITY_ACCESSED")
        self.diagnostics["external_fvg_accesses"] += 1

        evidence = SweepEvidence(
            kind=kind,
            pool_level=chosen.level,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            open=bar.open,
            atr=atr,
            flow_15s=bar.flow_15s,
            flow_60s=bar.flow_60s,
            notional_burst=self._feature("notional_burst"),
            efficiency_60s=bar.efficiency_60s,
            depth_imbalance_1=bar.depth_imbalance,
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
        )
        branch = classify_sweep(evidence, self.thresholds)
        self.external_scenario_counter += 1
        scenario_id = f"edfvg-{self.external_scenario_counter:07d}"
        details = {
            "external_level_id": chosen.level_id,
            "external_level_kind": kind,
            "external_level": chosen.level,
            "side": side,
            "breakout_index": self.bar_index,
            "breakout_open": bar.open,
            "breakout_high": bar.high,
            "breakout_low": bar.low,
            "breakout_close": bar.close,
            "breakout_flow_15s": bar.flow_15s,
            "breakout_flow_60s": bar.flow_60s,
            "breakout_efficiency_60s": bar.efficiency_60s,
            "breakout_depth_imbalance": bar.depth_imbalance,
            "classification": branch,
        }
        if branch != "ACCEPTANCE":
            self.diagnostics["external_fvg_non_acceptances"] += 1
            self._transition(
                scenario_id,
                "EXTERNAL_ACCESS_NOT_ACCEPTED",
                bar.ts,
                bar.ts,
                "CLOSED",
                "EXTERNAL_BREAK_LACKED_ALIGNED_EFFICIENT_LIQUIDITY_WITHDRAWAL",
                bar.close,
                details,
            )
            return

        self.diagnostics["external_fvg_acceptances"] += 1
        watch = ExternalFvgWatch(
            scenario_id=scenario_id,
            side=side,
            level_id=chosen.level_id,
            level_kind=kind,
            external_level=chosen.level,
            breakout_index=self.bar_index,
            breakout_ts=bar.ts,
            breakout_extreme=bar.high if side > 0 else bar.low,
            atr=atr,
            phase="WAIT_DISPLACEMENT_GAP",
            formation_expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            gap=None,
            gap_formed_index=-1,
            retest_expires_index=-1,
            details=details,
        )
        self.external_fvg_watches[scenario_id] = watch
        self.diagnostics["external_fvg_watches"] += 1
        self.diagnostics["external_fvg_parallel_observers_max"] = max(
            int(self.diagnostics["external_fvg_parallel_observers_max"]),
            len(self.external_fvg_watches),
        )
        self._transition(
            scenario_id,
            "EXTERNAL_ACCEPTANCE_CONFIRMED",
            bar.ts,
            bar.ts,
            "WAIT_DISPLACEMENT_GAP",
            "EXTERNAL_LEVEL_ACCEPTED_WITH_FLOW_EFFICIENCY_AND_DEPTH_WITHDRAWAL",
            bar.close,
            details,
        )

    def _consume_external_level(
        self,
        item: ExternalLevel,
        bar: ObservedBar,
        reason: str,
    ) -> None:
        if self.external_levels.pop(item.level_id, None) is None:
            return
        self._transition(
            item.level_id,
            "EXTERNAL_SESSION_LEVEL_CONSUMED",
            bar.ts,
            bar.ts,
            "CLOSED",
            reason,
            item.level,
            {"bar_high": bar.high, "bar_low": bar.low},
        )

    def _advance_external_watches(self, bar: ObservedBar) -> None:
        for scenario_id in list(self.external_fvg_watches):
            watch = self.external_fvg_watches.get(scenario_id)
            if watch is None:
                continue
            if watch.phase == "WAIT_DISPLACEMENT_GAP":
                self._advance_gap_formation(watch, bar)
            else:
                self._advance_gap_retest(watch, bar)

    def _advance_gap_formation(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        if (
            (watch.side > 0 and bar.close <= watch.external_level)
            or (watch.side < 0 and bar.close >= watch.external_level)
        ):
            self.diagnostics["external_fvg_invalidations"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXTERNAL_ACCEPTANCE_FAILED_BEFORE_IMBALANCE_FORMED",
            )
            return
        if self.bar_index > watch.formation_expires_index:
            self.diagnostics["external_fvg_formation_expiries"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXISTING_FOUR_BAR_DISPLACEMENT_WINDOW_EXPIRED",
            )
            return
        if len(self.observed_bars) < 3:
            return
        first, impulse, third = list(self.observed_bars)[-3:]
        if first.index < watch.breakout_index:
            return
        if (
            watch.side * impulse.flow_60s < self.config.rejection_confirm_flow_min
            or impulse.efficiency_60s < self.config.rejection_confirm_efficiency_min
        ):
            return
        gap = displacement_gap(
            side=watch.side,
            first_high=first.high,
            first_low=first.low,
            impulse_open=impulse.open,
            impulse_high=impulse.high,
            impulse_low=impulse.low,
            impulse_close=impulse.close,
            third_high=third.high,
            third_low=third.low,
            atr=watch.atr,
            minimum_body_atr=self.config.rejection_confirm_body_atr,
            minimum_close_location=self.config.rejection_confirm_close_location,
        )
        if gap is None:
            return
        if (
            (watch.side > 0 and gap.lower <= watch.external_level)
            or (watch.side < 0 and gap.upper >= watch.external_level)
        ):
            return
        watch.gap = gap
        watch.phase = "WAIT_FIRST_FVG_RETEST"
        watch.gap_formed_index = self.bar_index
        watch.retest_expires_index = self.bar_index + self.config.acceptance_retrace_bars
        watch.details.update(
            {
                "gap_lower": gap.lower,
                "gap_upper": gap.upper,
                "gap_midpoint": gap.midpoint,
                "gap_formed_index": self.bar_index,
                "gap_impulse_flow_60s": impulse.flow_60s,
                "gap_impulse_efficiency_60s": impulse.efficiency_60s,
            },
        )
        self.diagnostics["external_fvg_gaps_formed"] += 1
        self._transition(
            watch.scenario_id,
            "EXTERNAL_DISPLACEMENT_GAP_CONFIRMED",
            bar.ts,
            bar.ts,
            "WAIT_FIRST_FVG_RETEST",
            "COMPLETED_THREE_BAR_IMBALANCE_OUTSIDE_ACCEPTED_EXTERNAL_LEVEL",
            gap.midpoint,
            dict(watch.details),
        )

    def _advance_gap_retest(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        gap = watch.gap
        if gap is None or self.bar_index <= watch.gap_formed_index:
            return
        if gap_invalidated(
            side=watch.side,
            external_level=watch.external_level,
            gap=gap,
            close=bar.close,
        ):
            self.diagnostics["external_fvg_invalidations"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "PRICE_ACCEPTED_BACK_THROUGH_EXTERNAL_LEVEL_AND_DISTAL_GAP",
            )
            return
        if self.bar_index > watch.retest_expires_index:
            self.diagnostics["external_fvg_retest_expiries"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXISTING_FIRST_RETEST_WINDOW_EXPIRED",
            )
            return
        if not first_retest_response(
            side=watch.side,
            external_level=watch.external_level,
            gap=gap,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            flow_15s=bar.flow_15s,
            depth_imbalance=bar.depth_imbalance,
            maximum_counterflow=self.config.acceptance_max_counterflow,
        ):
            return
        self.diagnostics["external_fvg_retest_confirmations"] += 1
        if not self._external_entry_slot_idle():
            self.diagnostics["external_fvg_slot_conflicts"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "GLOBAL_ENTRY_SLOT_OCCUPIED_AT_FIRST_FVG_RETEST",
            )
            return
        self._submit_external_fvg(watch, bar)

    def _external_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
        )

    def _submit_external_fvg(
        self,
        watch: ExternalFvgWatch,
        bar: ObservedBar,
    ) -> None:
        gap = watch.gap
        if gap is None:
            self._close_external_watch(watch.scenario_id, bar, "MISSING_GAP_STATE")
            return
        side = watch.side
        atr = self._atr()
        entry_price = self.instrument.make_price(gap.midpoint)
        entry = _as_float(entry_price)
        raw_stop = structural_gap_stop(
            side=side,
            external_level=watch.external_level,
            gap=gap,
            atr=atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["external_fvg_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "INVALID_EXTERNAL_FVG_STOP_GEOMETRY",
            )
            return
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
            self.diagnostics["external_fvg_no_live_target"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
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
            self.diagnostics["external_fvg_geometry_rejections"] += 1
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "ROUNDED_EXTERNAL_FVG_BRACKET_INVALID",
            )
            return

        details = {
            **watch.details,
            "retest_index": self.bar_index,
            "retest_close": bar.close,
            "retest_flow_15s": bar.flow_15s,
            "retest_depth_imbalance": bar.depth_imbalance,
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r": rounded_r,
        }
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=self.BRANCH,
            side=side,
            swept_kind=watch.level_kind,
            pool_id=watch.level_id,
            pool_level=watch.external_level,
            created_index=watch.breakout_index,
            expires_index=watch.retest_expires_index,
            sweep_extreme=watch.breakout_extreme,
            structure=watch.external_level,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="EXTERNAL_ACCEPTANCE_DISPLACEMENT_FVG",
            choch_close=entry,
            stop=stop,
            atr=atr,
            created_index=watch.breakout_index,
            created_ts=watch.breakout_ts,
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
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=rounded_r,
            branch=self.BRANCH,
            event_type="EXTERNAL_FVG_MIDPOINT_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETURN_TO_EXTERNAL_DISPLACEMENT_IMBALANCE",
            expires_index=watch.retest_expires_index,
            entry_tag="EXTERNAL_DISPLACEMENT_FVG_ENTRY",
            extra=details,
        )
        if submitted:
            self.external_fvg_watches.pop(watch.scenario_id, None)
            self.diagnostics["external_fvg_submissions"] += 1
        else:
            self._close_external_watch(
                watch.scenario_id,
                bar,
                "EXTERNAL_FVG_BRACKET_SUBMISSION_FAILED",
            )

    def _close_external_watch(
        self,
        scenario_id: str,
        bar: ObservedBar,
        reason: str,
    ) -> None:
        watch = self.external_fvg_watches.pop(scenario_id, None)
        if watch is None:
            return
        if self.scenario_states.get(scenario_id) == "CLOSED":
            return
        reference = watch.gap.midpoint if watch.gap is not None else watch.external_level
        self._transition(
            scenario_id,
            "EXTERNAL_FVG_SCENARIO_CLOSED",
            bar.ts,
            bar.ts,
            "CLOSED",
            reason,
            reference,
            dict(watch.details),
        )

    def _close_all_external_watches(
        self,
        bar: ObservedBar,
        reason: str,
    ) -> None:
        for scenario_id in list(self.external_fvg_watches):
            self._close_external_watch(scenario_id, bar, reason)

    def on_stop(self) -> None:
        if self.observed_bars:
            self._close_all_external_watches(
                self.observed_bars[-1],
                "BACKTEST_ENDED_WITH_EXTERNAL_FVG_WATCH_OPEN",
            )
        super().on_stop()


__all__ = ["ExternalDisplacementFvgStrategy"]
