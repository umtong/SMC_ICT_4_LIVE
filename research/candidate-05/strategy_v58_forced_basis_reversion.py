#!/usr/bin/env python3
"""Candidate 05 v58: forced perpetual/spot basis mean reversion.

v46 continues to own liquidity-failure reversals. This independent family acts
only when the perpetual has moved away from spot, open interest contracted, and
completed tail flow plus visible depth turned toward normalization. Its target
is the current spot price translated by the trailing, prior-only median basis;
it is not a fitted fixed-R destination. Order geometry, costs, slippage,
current-NAV sizing and lifecycle remain inherited from Nautilus-backed v46.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any

from basis_dislocation_logic import basis_dislocation_side
from basis_dislocation_logic import forced_perpetual_dislocation_confirmed
from basis_dislocation_logic import spot_implied_perpetual_price
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import has_adverse_slippage_room
from logic import net_r_at_price
from logic import planned_loss_per_unit
from sponsored_choch_logic import slippage_protected_marketable_limit
from strategy import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy


BRANCH = "FORCED_SPOT_PERP_BASIS_REVERSION"
_REQUIRED_FEATURES = {
    "spot_trade_vwap_60s",
    "perp_minus_spot_return_bps",
    "perp_spot_basis_bps",
    "oi_change_5m",
    "metrics_ready",
}


class ForcedBasisReversionStrategy(NoPostRetraceBreakawayStrategy):
    """Add one prior-distribution, spot-implied basis normalization family."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.basis_history_bps: deque[float] = deque(maxlen=240)
        self.basis_scenario_counter = 0
        self.diagnostics.update(
            {
                "basis_observations": 0,
                "basis_statistical_dislocations": 0,
                "basis_forced_transfer_confirmations": 0,
                "basis_slot_conflicts": 0,
                "basis_invalid_geometry": 0,
                "basis_insufficient_target_r": 0,
                "basis_submissions": 0,
                "basis_long_signals": 0,
                "basis_short_signals": 0,
            },
        )

    def on_start(self) -> None:
        super().on_start()
        available = set(self.features[0]) if self.features else set()
        missing = sorted(_REQUIRED_FEATURES - available)
        if missing:
            raise RuntimeError(
                "forced basis-reversion observation contract was not installed: "
                f"{missing}",
            )

    def on_bar(self, bar: Any) -> None:
        # v46 receives the completed bar first. The new family may compete only
        # after the parent has declined to occupy the global slot.
        super().on_bar(bar)
        if not self.bars or self.current_feature is None:
            return
        basis = self._feature("perp_spot_basis_bps")
        if math.isfinite(basis):
            self._consider_basis_dislocation(self.bars[-1], basis)
            self.basis_history_bps.append(float(basis))
            self.diagnostics["basis_observations"] += 1

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

    def _consider_basis_dislocation(
        self,
        row: dict[str, float | int],
        current_basis_bps: float,
    ) -> None:
        ts_event = int(row["ts"])
        if not self._observation_ready(ts_event):
            return
        side, normal_basis_bps, robust_scale_bps = basis_dislocation_side(
            current_basis_bps=current_basis_bps,
            history_bps=tuple(self.basis_history_bps),
        )
        if side == 0:
            return
        self.diagnostics["basis_statistical_dislocations"] += 1
        if not forced_perpetual_dislocation_confirmed(
            side=side,
            perp_minus_spot_return_bps=self._feature(
                "perp_minus_spot_return_bps",
            ),
            oi_change_5m=self._feature("oi_change_5m"),
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            depth_imbalance=self._feature("depth_imbalance_1"),
        ):
            return
        self.diagnostics["basis_forced_transfer_confirmations"] += 1
        if not self._entry_slot_idle():
            self.diagnostics["basis_slot_conflicts"] += 1
            return
        self._submit_basis_reversion(
            row=row,
            side=side,
            current_basis_bps=current_basis_bps,
            normal_basis_bps=normal_basis_bps,
            robust_scale_bps=robust_scale_bps,
        )

    def _submit_basis_reversion(
        self,
        *,
        row: dict[str, float | int],
        side: int,
        current_basis_bps: float,
        normal_basis_bps: float,
        robust_scale_bps: float,
    ) -> bool:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        observed = float(row["close"])
        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
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
            self.diagnostics["basis_invalid_geometry"] += 1
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
            normal_basis_bps=normal_basis_bps,
        )
        target_price = self.instrument.make_price(target_raw)
        target = _as_float(target_price)
        if (
            (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["basis_invalid_geometry"] += 1
            return False
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["basis_invalid_geometry"] += 1
            return False
        target_r = net_r_at_price(
            entry,
            target,
            side,
            planned_loss,
            cost_rate,
        )
        if target_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R:
            self.diagnostics["basis_insufficient_target_r"] += 1
            return False

        self.basis_scenario_counter += 1
        scenario_id = f"basis-{self.basis_scenario_counter:07d}"
        details = {
            "branch": BRANCH,
            "side": side,
            "current_basis_bps": current_basis_bps,
            "normal_basis_bps": normal_basis_bps,
            "robust_basis_scale_bps": robust_scale_bps,
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
            "target_source": "SPOT_IMPLIED_TRAILING_BASIS_MEDIAN",
            "target_net_r": target_r,
        }
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=BRANCH,
            side=side,
            swept_kind="BASIS_PREMIUM" if side < 0 else "BASIS_DISCOUNT",
            pool_id=f"basis-normal-{scenario_id}",
            pool_level=target,
            created_index=self.bar_index,
            expires_index=self.bar_index + 2,
            sweep_extreme=(
                float(row["high"]) if side < 0 else float(row["low"])
            ),
            structure=target,
            atr=atr,
            hold_count=0,
            retrace_armed=True,
            details=details,
        )
        armed = ArmedEntryPath(
            setup=setup,
            flow_state="FORCED_BASIS_NORMALIZATION",
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
            target_source="SPOT_IMPLIED_TRAILING_BASIS_MEDIAN",
            target_r=target_r,
            branch=BRANCH,
            event_type="FORCED_BASIS_REVERSION_LIMIT_SUBMITTED",
            reason="PERPETUAL_LED_DISLOCATION_WITH_OI_CONTRACTION_AND_TAIL_REVERSAL",
            expires_index=self.bar_index + 2,
            entry_tag="FORCED_BASIS_REVERSION_ENTRY",
            extra=details,
        )
        if submitted:
            self.diagnostics["basis_submissions"] += 1
            self.diagnostics[
                "basis_long_signals" if side > 0 else "basis_short_signals"
            ] += 1
        elif self.armed_entry_path is armed:
            self.armed_entry_path = None
        return submitted


LiquidityResponseStrategy = ForcedBasisReversionStrategy

__all__ = [
    "BRANCH",
    "ForcedBasisReversionStrategy",
    "LiquidityResponseConfig",
    "LiquidityResponseStrategy",
]
