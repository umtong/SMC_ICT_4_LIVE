#!/usr/bin/env python3
"""Candidate 05 v35: sequential aggressor-flow regime then first retest."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar

from depth_logic import DIRECTIONAL_DEPTH_MIN
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from logic import choose_liquidity_target
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import pending_limit_invalidated
from sequential_flow_regime_logic import SequentialFlowState
from sequential_flow_regime_logic import first_sequential_boundary_retest
from sequential_flow_regime_logic import sequential_release_breakout
from sequential_flow_regime_logic import sequential_structural_stop
from sequential_flow_regime_logic import update_sequential_flow
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v26 import ScenarioValidEntryStrategy


@dataclass(slots=True)
class SequentialReleaseWatch:
    scenario_id: str
    side: int
    boundary: float
    evidence_high: float
    evidence_low: float
    entry: float
    stop: float
    target: float
    target_source: str
    target_pool_id: str
    target_r: float
    planned_loss: float
    breakout_index: int
    breakout_ts: int
    expires_index: int
    details: dict[str, Any]


class SequentialFlowRegimeStrategy(ScenarioValidEntryStrategy):
    """Trade realised flow persistence only after price and book confirmation.

    Informative completed minutes update mirrored likelihood ratios comparing a
    2:1 aggressor regime with a 1:1 null. A boundary crossing is not a trade. It
    must become an efficient break of the price range formed while evidence was
    accumulating, with the threatened book side withdrawing and activity above
    the existing v26 minimum. The breakout only arms a scenario.

    Entry is a passive limit at the first later defended return to the frozen
    range boundary. A failed first touch closes the scenario; a later favourable
    touch cannot be selected. The stop is beyond the opposite evidence-range
    extreme and the target is a still-live opposing liquidity pool frozen at
    breakout. Costs, slippage, 3% current-NAV sizing, order lifecycle, margin,
    liquidation, positions and NAV remain inherited from v26/NautilusTrader.
    """

    BRANCH = "SEQUENTIAL_FLOW_REGIME_RELEASE"

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.sequential_state = SequentialFlowState()
        self.sequential_watch: SequentialReleaseWatch | None = None
        self.sequential_scenario_counter = 0
        self.diagnostics.update(
            {
                "sequential_flow_informative_minutes": 0,
                "sequential_flow_likelihood_decisions": 0,
                "sequential_flow_state_resets": 0,
                "sequential_flow_release_rejections": 0,
                "sequential_flow_release_confirmations": 0,
                "sequential_flow_watches": 0,
                "sequential_flow_retest_observations": 0,
                "sequential_flow_first_touch_failures": 0,
                "sequential_flow_retest_confirmations": 0,
                "sequential_flow_retest_expiries": 0,
                "sequential_flow_structural_invalidations": 0,
                "sequential_flow_target_reached_before_entry": 0,
                "sequential_flow_target_source_expired": 0,
                "sequential_flow_slot_conflicts": 0,
                "sequential_flow_no_live_target": 0,
                "sequential_flow_geometry_rejections": 0,
                "sequential_flow_submissions": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        ts = int(row["ts"])
        if not self._in_evaluation(ts):
            self._close_sequential_watch(row, "EVALUATION_ENDED_DURING_SEQUENTIAL_REGIME")
            self._reset_sequential_state()
            return
        if self._funding_blackout(ts):
            self._close_sequential_watch(row, "FUNDING_BLACKOUT_DURING_SEQUENTIAL_REGIME")
            self._reset_sequential_state()
            return
        if not self._sequential_features_ready(ts):
            return

        if self.sequential_watch is not None:
            self._advance_sequential_watch(row)
            return
        if not self._sequential_entry_slot_idle():
            self._reset_sequential_state()
            return
        self._advance_sequential_detector(row)

    def _sequential_features_ready(self, ts: int) -> bool:
        feature = self.current_feature
        if feature is None or not bool(feature.get("feature_ready", False)):
            return False
        observed = int(feature.get("observed_time_ns", 0))
        age = (ts - observed) / 1_000_000_000
        if age < -1e-9:
            raise RuntimeError("future feature observation reached v35")
        return age <= self.config.feature_max_age_seconds

    def _sequential_entry_slot_idle(self) -> bool:
        return (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and not self.exit_pending
            and self.pending is None
            and self.armed_entry_path is None
        )

    def _reset_sequential_state(self) -> None:
        if self.sequential_state.informative_observations:
            self.diagnostics["sequential_flow_state_resets"] += 1
        self.sequential_state = SequentialFlowState()

    def _advance_sequential_detector(self, row: dict[str, float | int]) -> None:
        prior = self.sequential_state
        if (
            prior.informative_observations > 0
            and prior.first_index >= 0
            and self.bar_index - prior.first_index > self.config.max_hold_bars
        ):
            self._reset_sequential_state()
            prior = self.sequential_state

        update = update_sequential_flow(
            state=prior,
            flow_60s=self._feature("flow_60s"),
            high=float(row["high"]),
            low=float(row["low"]),
            bar_index=self.bar_index,
            minimum_absolute_flow=self.config.acceptance_flow_min,
        )
        self.sequential_state = update.state
        if update.informative:
            self.diagnostics["sequential_flow_informative_minutes"] += 1
        side = update.decision
        if side == 0:
            return
        self.diagnostics["sequential_flow_likelihood_decisions"] += 1
        if (
            prior.informative_observations == 0
            or not math.isfinite(prior.range_high)
            or not math.isfinite(prior.range_low)
            or prior.range_high <= prior.range_low
        ):
            self.diagnostics["sequential_flow_release_rejections"] += 1
            return
        atr = self._atr()
        if not sequential_release_breakout(
            side=side,
            prior_high=prior.range_high,
            prior_low=prior.range_low,
            open_price=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            notional_burst=self._feature("notional_burst"),
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
            minimum_break_distance_atr=self.config.acceptance_close_atr,
            minimum_flow=self.config.acceptance_flow_min,
            minimum_efficiency=self.config.acceptance_efficiency_min,
            minimum_notional_burst=self.config.sweep_min_notional_burst,
            minimum_depth_withdrawal=self.config.acceptance_depth_withdrawal_min,
            minimum_close_location=self.config.acceptance_close_location,
        ):
            self.diagnostics["sequential_flow_release_rejections"] += 1
            return
        self.diagnostics["sequential_flow_release_confirmations"] += 1
        self._arm_sequential_release(side=side, prior=prior, row=row, atr=atr)
        self._reset_sequential_state()

    def _arm_sequential_release(
        self,
        *,
        side: int,
        prior: SequentialFlowState,
        row: dict[str, float | int],
        atr: float,
    ) -> None:
        boundary = prior.range_high if side > 0 else prior.range_low
        evidence_high = max(prior.range_high, float(row["high"]))
        evidence_low = min(prior.range_low, float(row["low"]))
        entry_price = self.instrument.make_price(boundary)
        entry = _as_float(entry_price)
        raw_stop = sequential_structural_stop(
            side=side,
            evidence_high=evidence_high,
            evidence_low=evidence_low,
            atr=atr,
            stop_buffer_atr=self.config.stop_buffer_atr,
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, slippage_rate)
        self.sequential_scenario_counter += 1
        scenario_id = f"sfr-{self.sequential_scenario_counter:07d}"
        details = {
            "side": side,
            "evidence_first_index": prior.first_index,
            "evidence_last_index": prior.last_index,
            "informative_observations": prior.informative_observations,
            "upward_log_likelihood": prior.upward_log_likelihood,
            "downward_log_likelihood": prior.downward_log_likelihood,
            "evidence_high": evidence_high,
            "evidence_low": evidence_low,
            "breakout_index": self.bar_index,
            "breakout_ts": int(row["ts"]),
            "breakout_close": float(row["close"]),
            "breakout_flow_60s": self._feature("flow_60s"),
            "breakout_efficiency_60s": self._feature("efficiency_60s"),
            "breakout_notional_burst": self._feature("notional_burst"),
            "breakout_depth_imbalance": self._feature("depth_imbalance_1"),
            "entry_boundary": entry,
            "stop": stop,
            "sequential_null_probability": 0.5,
            "sequential_alternative_probability": 2.0 / 3.0,
            "sequential_type_i_error": 0.05,
            "sequential_type_ii_error": 0.05,
        }
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["sequential_flow_geometry_rejections"] += 1
            self._transition(
                scenario_id,
                "SEQUENTIAL_FLOW_RELEASE_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "INVALID_SEQUENTIAL_RANGE_STOP_GEOMETRY",
                entry,
                details,
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
            self.diagnostics["sequential_flow_no_live_target"] += 1
            self._transition(
                scenario_id,
                "SEQUENTIAL_FLOW_RELEASE_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "NO_STILL_LIVE_OPPOSING_LIQUIDITY_TARGET",
                entry,
                details,
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
            self.diagnostics["sequential_flow_geometry_rejections"] += 1
            self._transition(
                scenario_id,
                "SEQUENTIAL_FLOW_RELEASE_REJECTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "ROUNDED_SEQUENTIAL_RELEASE_BRACKET_INVALID",
                entry,
                {**details, "target": target, "target_source": target_source},
            )
            return

        details.update(
            {
                "target": target,
                "target_source": target_source,
                "target_net_r": rounded_r,
                "planned_loss_per_unit": planned_loss,
                "target_policy": "FROZEN_AT_SEQUENTIAL_RELEASE",
            },
        )
        self.sequential_watch = SequentialReleaseWatch(
            scenario_id=scenario_id,
            side=side,
            boundary=entry,
            evidence_high=evidence_high,
            evidence_low=evidence_low,
            entry=entry,
            stop=stop,
            target=target,
            target_source=target_source,
            target_pool_id=target_source.split(":", 1)[1],
            target_r=rounded_r,
            planned_loss=planned_loss,
            breakout_index=self.bar_index,
            breakout_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            details=details,
        )
        self.diagnostics["sequential_flow_watches"] += 1
        self._transition(
            scenario_id,
            "SEQUENTIAL_FLOW_RELEASE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "WAIT_FIRST_SEQUENTIAL_BOUNDARY_RETEST",
            "LIKELIHOOD_REGIME_BECAME_EFFICIENT_STRUCTURE_AND_BOOK_RELEASE",
            entry,
            details,
        )

    def _advance_sequential_watch(self, row: dict[str, float | int]) -> None:
        watch = self.sequential_watch
        if watch is None or self.bar_index <= watch.breakout_index:
            return
        self.diagnostics["sequential_flow_retest_observations"] += 1
        if pending_limit_invalidated(
            side=watch.side,
            stop=watch.stop,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self.diagnostics["sequential_flow_structural_invalidations"] += 1
            self._close_sequential_watch(row, "OPPOSITE_EVIDENCE_RANGE_INVALIDATED_BEFORE_ENTRY")
            return
        if (watch.side > 0 and float(row["high"]) >= watch.target) or (
            watch.side < 0 and float(row["low"]) <= watch.target
        ):
            self.diagnostics["sequential_flow_target_reached_before_entry"] += 1
            self._close_sequential_watch(row, "FROZEN_TARGET_REACHED_BEFORE_FIRST_RETEST_ENTRY")
            return
        if watch.target_pool_id not in self.active_pools:
            self.diagnostics["sequential_flow_target_source_expired"] += 1
            self._close_sequential_watch(row, "FROZEN_TARGET_SOURCE_EXPIRED_BEFORE_ENTRY")
            return
        if self.bar_index > watch.expires_index:
            self.diagnostics["sequential_flow_retest_expiries"] += 1
            self._close_sequential_watch(row, "EXISTING_FIRST_RETEST_WINDOW_EXPIRED")
            return

        touched = (
            float(row["low"]) <= watch.boundary
            if watch.side > 0
            else float(row["high"]) >= watch.boundary
        )
        if not touched:
            return
        if not first_sequential_boundary_retest(
            side=watch.side,
            boundary=watch.boundary,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            flow_15s=self._feature("flow_15s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
            maximum_counterflow=self.config.acceptance_max_counterflow,
            minimum_directional_depth=DIRECTIONAL_DEPTH_MIN,
        ):
            self.diagnostics["sequential_flow_first_touch_failures"] += 1
            self._close_sequential_watch(row, "FIRST_BOUNDARY_TOUCH_NOT_DEFENDED_BY_FLOW_AND_DEPTH")
            return
        self.diagnostics["sequential_flow_retest_confirmations"] += 1
        if not self._sequential_entry_slot_idle():
            self.diagnostics["sequential_flow_slot_conflicts"] += 1
            self._close_sequential_watch(row, "LOCAL_ENTRY_SLOT_OCCUPIED_AT_FIRST_RETEST")
            return
        self._submit_sequential_release(watch, row)

    def _submit_sequential_release(
        self,
        watch: SequentialReleaseWatch,
        row: dict[str, float | int],
    ) -> None:
        setup = PendingSetup(
            scenario_id=watch.scenario_id,
            branch=self.BRANCH,
            side=watch.side,
            swept_kind="SEQUENTIAL_FLOW_RANGE",
            pool_id=watch.target_pool_id,
            pool_level=watch.boundary,
            created_index=watch.breakout_index,
            expires_index=watch.expires_index,
            sweep_extreme=watch.evidence_high if watch.side > 0 else watch.evidence_low,
            structure=watch.boundary,
            atr=self._atr(),
            hold_count=0,
            retrace_armed=True,
            details=dict(watch.details),
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="SEQUENTIAL_FLOW_REGIME_RELEASE",
            choch_close=watch.entry,
            stop=watch.stop,
            atr=self._atr(),
            created_index=watch.breakout_index,
            created_ts=int(row["ts"]),
            details=dict(watch.details),
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=self.instrument.make_price(watch.entry),
            stop_price=self.instrument.make_price(watch.stop),
            target_price=self.instrument.make_price(watch.target),
            sizing_entry=watch.entry,
            planned_loss=watch.planned_loss,
            target_source=watch.target_source,
            target_r=watch.target_r,
            branch=self.BRANCH,
            event_type="SEQUENTIAL_FLOW_BOUNDARY_LIMIT_SUBMITTED",
            reason="FIRST_DEFENDED_RETEST_AFTER_SEQUENTIAL_FLOW_RELEASE",
            expires_index=watch.expires_index,
            entry_tag="SEQUENTIAL_FLOW_REGIME_ENTRY",
            extra={
                **watch.details,
                "retest_index": self.bar_index,
                "retest_ts": int(row["ts"]),
                "retest_close": float(row["close"]),
                "retest_flow_15s": self._feature("flow_15s"),
                "retest_depth_imbalance": self._feature("depth_imbalance_1"),
            },
        )
        if submitted:
            self.sequential_watch = None
            self.diagnostics["sequential_flow_submissions"] += 1
        else:
            self._close_sequential_watch(row, "SEQUENTIAL_FLOW_BRACKET_SUBMISSION_FAILED")

    def _close_sequential_watch(
        self,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        watch = self.sequential_watch
        if watch is None:
            return
        self.sequential_watch = None
        if self.scenario_states.get(watch.scenario_id) == "CLOSED":
            return
        self._transition(
            watch.scenario_id,
            "SEQUENTIAL_FLOW_RELEASE_CLOSED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            watch.boundary,
            dict(watch.details),
        )

    def on_stop(self) -> None:
        if self.sequential_watch is not None and self.bars:
            self._close_sequential_watch(
                self.bars[-1],
                "BACKTEST_ENDED_WITH_SEQUENTIAL_RELEASE_WATCH_OPEN",
            )
        super().on_stop()


__all__ = ["SequentialFlowRegimeStrategy"]
