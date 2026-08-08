#!/usr/bin/env python3
"""Candidate 05 v62: scheduled post-funding forced-position normalization.

At each fixed eight-hour settlement boundary, v62 freezes the last completed
pre-funding basis and a prior-only normal basis from the preceding history. From
minute 6 through 20 after settlement it may attempt one trade only when payer
crowding, perpetual-vs-spot forced movement, OI contraction, tail-flow reversal
and visible-depth sponsorship all agree. The target is current spot translated
by the frozen normal basis.

v46 processes every bar first and remains authoritative for liquidity-failure
reversals. Entry capping, fees, adverse slippage, current-NAV sizing, brackets,
portfolio and NAV remain inherited through NautilusTrader.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any

from basis_dislocation_logic import robust_basis_location_scale
from basis_dislocation_logic import spot_implied_perpetual_price
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from funding_reset_logic import funding_cycle_key
from funding_reset_logic import funding_forced_reset_confirmed
from funding_reset_logic import funding_reset_side
from funding_reset_logic import in_post_funding_window
from funding_reset_logic import minutes_after_funding
from logic import net_r_at_price
from logic import planned_loss_per_unit
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


BRANCH = "POST_FUNDING_FORCED_RESET"
_REQUIRED_FEATURES = {
    "spot_trade_vwap_60s",
    "perp_minus_spot_return_bps",
    "perp_spot_basis_bps",
    "oi_change_5m",
    "metrics_ready",
}


class PostFundingForcedResetStrategy(NoPostRetraceBreakawayStrategy):
    """Add at most one evidence-complete reset attempt per funding cycle."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.funding_basis_history: deque[float] = deque(maxlen=720)
        self.last_completed_basis = float("nan")
        self.current_funding_cycle: int | None = None
        self.cycle_pre_funding_basis = float("nan")
        self.cycle_normal_basis = float("nan")
        self.cycle_normal_scale = float("nan")
        self.cycle_attempted = False
        self.funding_reset_counter = 0
        self.diagnostics.update(
            {
                "funding_cycles_observed": 0,
                "funding_cycles_with_prior_state": 0,
                "funding_window_observations": 0,
                "funding_crowding_move_alignments": 0,
                "funding_forced_reset_confirmations": 0,
                "funding_slot_conflicts": 0,
                "funding_invalid_geometry": 0,
                "funding_insufficient_target_r": 0,
                "funding_reset_submissions": 0,
                "funding_long_resets": 0,
                "funding_short_resets": 0,
            },
        )

    def on_start(self) -> None:
        super().on_start()
        available = set(self.features[0]) if self.features else set()
        missing = sorted(_REQUIRED_FEATURES - available)
        if missing:
            raise RuntimeError(
                "post-funding reset observation contract was not installed: "
                f"{missing}",
            )

    def on_bar(self, bar: Any) -> None:
        # The base strategy has first priority. The scheduled family observes the
        # same completed bar only after the v46 state machine has declined it.
        super().on_bar(bar)
        if not self.bars or self.current_feature is None:
            return
        row = self.bars[-1]
        ts_event = int(row["ts"])
        basis = self._feature("perp_spot_basis_bps")
        cycle = funding_cycle_key(ts_event)
        if self.current_funding_cycle is None or cycle != self.current_funding_cycle:
            self._roll_funding_cycle(cycle)
        self._consider_post_funding_reset(row)
        if math.isfinite(basis):
            self.last_completed_basis = float(basis)
            self.funding_basis_history.append(float(basis))

    def _roll_funding_cycle(self, cycle: int) -> None:
        self.current_funding_cycle = int(cycle)
        self.cycle_pre_funding_basis = self.last_completed_basis
        history = list(self.funding_basis_history)
        # The final 30 completed minutes immediately before settlement describe
        # current crowding and are excluded from the normal-state estimator.
        normal_history = history[:-30] if len(history) > 30 else []
        self.cycle_normal_basis, self.cycle_normal_scale = (
            robust_basis_location_scale(normal_history)
            if len(normal_history) >= 60
            else (float("nan"), float("nan"))
        )
        self.cycle_attempted = False
        self.diagnostics["funding_cycles_observed"] += 1
        if math.isfinite(self.cycle_pre_funding_basis) and math.isfinite(
            self.cycle_normal_basis,
        ):
            self.diagnostics["funding_cycles_with_prior_state"] += 1

    def _observation_ready(self, ts_event: int) -> bool:
        feature = self.current_feature
        return (
            feature is not None
            and bool(feature.get("feature_ready", False))
            and bool(feature.get("metrics_ready", False))
            and self._in_evaluation(ts_event)
            and not self._funding_blackout(ts_event)
            and all(
                math.isfinite(self._feature(name))
                for name in (
                    "spot_trade_vwap_60s",
                    "perp_minus_spot_return_bps",
                    "perp_spot_basis_bps",
                    "oi_change_5m",
                    "flow_15s",
                    "flow_60s",
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

    def _consider_post_funding_reset(
        self,
        row: dict[str, float | int],
    ) -> None:
        ts_event = int(row["ts"])
        if self.cycle_attempted or not in_post_funding_window(ts_event):
            return
        self.diagnostics["funding_window_observations"] += 1
        if not self._observation_ready(ts_event):
            return
        side = funding_reset_side(
            pre_funding_basis_bps=self.cycle_pre_funding_basis,
            normal_basis_bps=self.cycle_normal_basis,
            perp_minus_spot_return_bps=self._feature(
                "perp_minus_spot_return_bps",
            ),
        )
        if side == 0:
            return
        self.diagnostics["funding_crowding_move_alignments"] += 1
        if not funding_forced_reset_confirmed(
            side=side,
            oi_change_5m=self._feature("oi_change_5m"),
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
        ):
            return
        self.diagnostics["funding_forced_reset_confirmations"] += 1
        if not self._entry_slot_idle():
            self.diagnostics["funding_slot_conflicts"] += 1
            return
        self.cycle_attempted = True
        self._submit_post_funding_reset(row=row, side=side)

    def _submit_post_funding_reset(
        self,
        *,
        row: dict[str, float | int],
        side: int,
    ) -> bool:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
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
            self.diagnostics["funding_invalid_geometry"] += 1
            return False
        raw_stop = (
            float(row["low"]) - self.config.stop_buffer_atr * atr
            if side > 0
            else float(row["high"]) + self.config.stop_buffer_atr * atr
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        target_raw = spot_implied_perpetual_price(
            spot_price=self._feature("spot_trade_vwap_60s"),
            normal_basis_bps=self.cycle_normal_basis,
        )
        target_price = self.instrument.make_price(target_raw)
        target = _as_float(target_price)
        if (
            (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["funding_invalid_geometry"] += 1
            return False
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["funding_invalid_geometry"] += 1
            return False
        target_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if target_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R:
            self.diagnostics["funding_insufficient_target_r"] += 1
            return False

        self.funding_reset_counter += 1
        scenario_id = f"funding-reset-{self.funding_reset_counter:07d}"
        details = {
            "branch": BRANCH,
            "side": side,
            "funding_cycle": self.current_funding_cycle,
            "minutes_after_funding": minutes_after_funding(int(row["ts"])),
            "pre_funding_basis_bps": self.cycle_pre_funding_basis,
            "normal_basis_bps": self.cycle_normal_basis,
            "normal_basis_scale_bps": self.cycle_normal_scale,
            "current_basis_bps": self._feature("perp_spot_basis_bps"),
            "perp_minus_spot_return_bps": self._feature(
                "perp_minus_spot_return_bps",
            ),
            "oi_change_5m": self._feature("oi_change_5m"),
            "flow_15s": self._feature("flow_15s"),
            "flow_60s": self._feature("flow_60s"),
            "depth_imbalance_1": self._feature("depth_imbalance_1"),
            "spot_trade_vwap_60s": self._feature("spot_trade_vwap_60s"),
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": "POST_FUNDING_SPOT_IMPLIED_NORMAL_BASIS",
            "target_net_r": target_r,
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=BRANCH,
            side=side,
            swept_kind="FUNDING_LONG_RESET" if side > 0 else "FUNDING_SHORT_RESET",
            pool_id=f"funding-normal-{scenario_id}",
            pool_level=target,
            created_index=self.bar_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=(
                float(row["low"]) if side > 0 else float(row["high"])
            ),
            structure=target,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="POST_FUNDING_FORCED_RESET",
            choch_close=entry,
            stop=stop,
            atr=atr,
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
            target_source="POST_FUNDING_SPOT_IMPLIED_NORMAL_BASIS",
            target_r=target_r,
            branch=BRANCH,
            event_type="POST_FUNDING_RESET_LIMIT_SUBMITTED",
            reason="PAYER_CROWDING_UNWOUND_WITH_OI_CONTRACTION_AND_TAIL_REVERSAL",
            expires_index=self.bar_index + 2,
            entry_tag="POST_FUNDING_FORCED_RESET_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["funding_reset_submissions"] += 1
            self.diagnostics[
                "funding_long_resets" if side > 0 else "funding_short_resets"
            ] += 1
        elif self.armed_entry_path is armed:
            self.armed_entry_path = None
        return submitted


LiquidityResponseStrategy = PostFundingForcedResetStrategy

__all__ = [
    "BRANCH",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "PostFundingForcedResetStrategy",
]
