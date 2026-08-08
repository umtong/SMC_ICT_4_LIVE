#!/usr/bin/env python3
"""Candidate 05 v68: perpetual-led liquidation exhaustion reversion.

A five-minute perpetual impulse is eligible only when OI contracts, perpetual
return leads spot, and completed spot three-minute aggressor flow does not
sponsor that impulse. Current tail flow and visible depth must then reverse. The
entry is a slippage-protected marketable limit; the stop is beyond the completed
five-minute impulse and the target is current spot translated by a prior-only
normal basis.

This family does not weaken v58's basis-dislocation definition; it detects a
separate forced-liquidation cause. v46 retains first priority, and all execution,
fees, slippage, sizing, portfolio and NAV contracts remain inherited.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any

from basis_dislocation_logic import robust_basis_location_scale
from basis_dislocation_logic import spot_implied_perpetual_price
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from liquidation_exhaustion_logic import liquidation_exhaustion_side
from liquidation_exhaustion_logic import liquidation_tail_reversal_confirmed
from logic import net_r_at_price
from logic import planned_loss_per_unit
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


BRANCH = "LIQUIDATION_EXHAUSTION_REVERSION"
_REQUIRED_FEATURES = {
    "spot_flow_3m",
    "spot_trade_vwap_60s",
    "perp_minus_spot_return_bps",
    "perp_spot_basis_bps",
    "oi_change_5m",
    "metrics_ready",
}


class LiquidationExhaustionStrategy(NoPostRetraceBreakawayStrategy):
    """Add one reversion attempt per completed five-minute liquidation impulse."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.liquidation_basis_history: deque[float] = deque(maxlen=720)
        self.last_liquidation_event_index = -10**12
        self.liquidation_counter = 0
        self.diagnostics.update(
            {
                "liquidation_exhaustion_observations": 0,
                "liquidation_perpetual_led_impulses": 0,
                "liquidation_tail_reversal_confirmations": 0,
                "liquidation_event_cooldown_rejections": 0,
                "liquidation_slot_conflicts": 0,
                "liquidation_invalid_geometry": 0,
                "liquidation_insufficient_target_r": 0,
                "liquidation_exhaustion_submissions": 0,
                "liquidation_exhaustion_longs": 0,
                "liquidation_exhaustion_shorts": 0,
            },
        )

    def on_start(self) -> None:
        super().on_start()
        available = set(self.features[0]) if self.features else set()
        missing = sorted(_REQUIRED_FEATURES - available)
        if missing:
            raise RuntimeError(
                "liquidation exhaustion observation contract was not installed: "
                f"{missing}",
            )

    def on_bar(self, bar: Any) -> None:
        super().on_bar(bar)
        if not self.bars or self.current_feature is None:
            return
        row = self.bars[-1]
        basis = self._feature("perp_spot_basis_bps")
        self._consider_liquidation_exhaustion(row)
        if math.isfinite(basis):
            self.liquidation_basis_history.append(float(basis))
            self.diagnostics["liquidation_exhaustion_observations"] += 1

    def _observation_ready(self, ts_event: int) -> bool:
        feature = self.current_feature
        return (
            feature is not None
            and bool(feature.get("feature_ready", False))
            and bool(feature.get("metrics_ready", False))
            and self._in_evaluation(ts_event)
            and not self._funding_blackout(ts_event)
            and len(self.bars) >= 6
            and all(
                math.isfinite(self._feature(name))
                for name in (
                    "spot_flow_3m",
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

    def _consider_liquidation_exhaustion(
        self,
        row: dict[str, float | int],
    ) -> None:
        ts_event = int(row["ts"])
        if not self._observation_ready(ts_event):
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        rows = list(self.bars)
        origin = float(rows[-6]["close"])
        perpetual_move_atr = (float(row["close"]) - origin) / atr
        side = liquidation_exhaustion_side(
            perpetual_move_atr=perpetual_move_atr,
            perp_minus_spot_return_bps=self._feature(
                "perp_minus_spot_return_bps",
            ),
            oi_change_5m=self._feature("oi_change_5m"),
            spot_flow_3m=self._feature("spot_flow_3m"),
        )
        if side == 0:
            return
        self.diagnostics["liquidation_perpetual_led_impulses"] += 1
        if self.bar_index - self.last_liquidation_event_index < 5:
            self.diagnostics["liquidation_event_cooldown_rejections"] += 1
            return
        if not liquidation_tail_reversal_confirmed(
            side=side,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
        ):
            return
        self.diagnostics["liquidation_tail_reversal_confirmations"] += 1
        self.last_liquidation_event_index = self.bar_index
        if not self._entry_slot_idle():
            self.diagnostics["liquidation_slot_conflicts"] += 1
            return
        self._submit_liquidation_exhaustion(
            row=row,
            side=side,
            perpetual_move_atr=perpetual_move_atr,
            impulse_rows=rows[-5:],
        )

    def _submit_liquidation_exhaustion(
        self,
        *,
        row: dict[str, float | int],
        side: int,
        perpetual_move_atr: float,
        impulse_rows: list[dict[str, float | int]],
    ) -> bool:
        history = list(self.liquidation_basis_history)
        if len(history) < 60:
            return False
        normal_basis, normal_scale = robust_basis_location_scale(history)
        if not math.isfinite(normal_basis):
            return False
        atr = self._atr()
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
            self.diagnostics["liquidation_invalid_geometry"] += 1
            return False
        impulse_low = min(float(item["low"]) for item in impulse_rows)
        impulse_high = max(float(item["high"]) for item in impulse_rows)
        raw_stop = (
            impulse_low - self.config.stop_buffer_atr * atr
            if side > 0
            else impulse_high + self.config.stop_buffer_atr * atr
        )
        stop_price = self.instrument.make_price(raw_stop)
        stop = _as_float(stop_price)
        target_raw = spot_implied_perpetual_price(
            spot_price=self._feature("spot_trade_vwap_60s"),
            normal_basis_bps=normal_basis,
        )
        target_price = self.instrument.make_price(target_raw)
        target = _as_float(target_price)
        if (
            (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["liquidation_invalid_geometry"] += 1
            return False
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["liquidation_invalid_geometry"] += 1
            return False
        target_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if target_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R:
            self.diagnostics["liquidation_insufficient_target_r"] += 1
            return False

        self.liquidation_counter += 1
        scenario_id = f"liquidation-exhaustion-{self.liquidation_counter:07d}"
        details = {
            "branch": BRANCH,
            "side": side,
            "perpetual_move_5m_atr": perpetual_move_atr,
            "perp_minus_spot_return_bps": self._feature(
                "perp_minus_spot_return_bps",
            ),
            "oi_change_5m": self._feature("oi_change_5m"),
            "spot_flow_3m": self._feature("spot_flow_3m"),
            "flow_15s": self._feature("flow_15s"),
            "flow_60s": self._feature("flow_60s"),
            "depth_imbalance_1": self._feature("depth_imbalance_1"),
            "normal_basis_bps": normal_basis,
            "normal_basis_scale_bps": normal_scale,
            "spot_trade_vwap_60s": self._feature("spot_trade_vwap_60s"),
            "impulse_low": impulse_low,
            "impulse_high": impulse_high,
            "entry_limit": entry,
            "stop": stop,
            "target": target,
            "target_source": "SPOT_IMPLIED_PRIOR_NORMAL_BASIS",
            "target_net_r": target_r,
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=BRANCH,
            side=side,
            swept_kind="LONG_LIQUIDATION" if side > 0 else "SHORT_LIQUIDATION",
            pool_id=f"liquidation-normal-{scenario_id}",
            pool_level=target,
            created_index=self.bar_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=impulse_low if side > 0 else impulse_high,
            structure=target,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="LIQUIDATION_EXHAUSTION_REVERSION",
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
            target_source="SPOT_IMPLIED_PRIOR_NORMAL_BASIS",
            target_r=target_r,
            branch=BRANCH,
            event_type="LIQUIDATION_EXHAUSTION_LIMIT_SUBMITTED",
            reason="PERPETUAL_LED_OI_CONTRACTING_IMPULSE_WITH_SPOT_AND_TAIL_REVERSAL",
            expires_index=self.bar_index + 2,
            entry_tag="LIQUIDATION_EXHAUSTION_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["liquidation_exhaustion_submissions"] += 1
            self.diagnostics[
                "liquidation_exhaustion_longs" if side > 0 else "liquidation_exhaustion_shorts"
            ] += 1
        elif self.armed_entry_path is armed:
            self.armed_entry_path = None
        return submitted


LiquidityResponseStrategy = LiquidationExhaustionStrategy

__all__ = [
    "BRANCH",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
    "LiquidationExhaustionStrategy",
]
