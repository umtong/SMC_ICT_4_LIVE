#!/usr/bin/env python3
"""Candidate 05 v14: cost-aware participation in a fully sponsored CHoCH."""
from __future__ import annotations

import math
from typing import Any

from depth_logic import DIRECTIONAL_DEPTH_MIN
from flow_inflection_logic import MAX_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import MIN_LIQUIDITY_TARGET_NET_R
from flow_inflection_logic import FALLBACK_TARGET_NET_R
from flow_inflection_logic import choch_flow_state
from flow_inflection_logic import has_adverse_slippage_room
from logic import choose_liquidity_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import structural_stop
from sponsored_choch_logic import slippage_protected_marketable_limit
from sponsored_choch_logic import sponsored_choch_participation_ready
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v13 import TargetLiquidityHandoffStrategy


class SponsoredChochParticipationStrategy(TargetLiquidityHandoffStrategy):
    """Participate immediately only when confirmation is actively sponsored.

    v9's full one-minute observation protects passive or weak CHoCH events, but
    it also misses the first executable price when aligned aggressive flow and
    book sponsorship are already present at confirmation. This branch does not
    remove that observation path. It adds a separate, stricter state:

    * CHoCH must be ACTIVE_CONFIRMATION;
    * final-15-second aggressor flow must already point with the reversal;
    * ordinary setups must retain the existing 0.10 directional depth at CHoCH;
    * target handoffs may use the freshly confirmed reclaim depth from their own
      setup, because that branch is classified only after a delayed target raid;
    * the order is a bounded marketable limit covering configured slippage;
    * sizing and target selection use that worst executable limit price;
    * a real active opposing liquidity pool must still provide at least 0.40R.

    All other setups continue through v13/v12/v9 unchanged. Thus the earlier v8
    failure mode—entering every merely acceptable CHoCH before path observation—
    is not reintroduced.
    """

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "sponsored_choch_participation_eligible": 0,
                "sponsored_choch_participation_submissions": 0,
                "sponsored_choch_participation_observation_fallbacks": 0,
                "sponsored_choch_participation_no_live_target": 0,
                "sponsored_choch_participation_cost_geometry_rejected": 0,
            },
        )

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        side = setup.side
        flow_15s = self._feature("flow_15s")
        flow_3m = self._feature("flow_3m")
        current_depth = self._feature("depth_imbalance_1")
        setup_depth = float(setup.details.get("depth_imbalance_1", float("nan")))
        target_handoff = bool(setup.details.get("target_handoff", False))
        flow_state = choch_flow_state(
            side=side,
            flow_15s=flow_15s,
            flow_3m=flow_3m,
            depth_imbalance=current_depth,
        )
        if not sponsored_choch_participation_ready(
            flow_state=flow_state,
            side=side,
            flow_15s=flow_15s,
            current_depth_imbalance=current_depth,
            setup_depth_imbalance=setup_depth,
            target_handoff=target_handoff,
            minimum_depth=DIRECTIONAL_DEPTH_MIN,
        ):
            self.diagnostics["sponsored_choch_participation_observation_fallbacks"] += 1
            return super()._submit_entry(setup, row)

        atr = self._atr()
        stop_price = self.instrument.make_price(
            structural_stop(setup.sweep_extreme, side, atr, self.config.stop_buffer_atr),
        )
        observed_price = self.instrument.make_price(float(row["close"]))
        stop = _as_float(stop_price)
        observed = _as_float(observed_price)
        if (side > 0 and not stop < observed) or (side < 0 and not observed < stop):
            self.diagnostics["sponsored_choch_participation_cost_geometry_rejected"] += 1
            return super()._submit_entry(setup, row)

        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        raw_limit = slippage_protected_marketable_limit(
            observed_price=observed,
            side=side,
            adverse_slippage_rate=slippage_rate,
            price_increment=_as_float(self.instrument.price_increment),
        )
        if not math.isfinite(raw_limit):
            self.diagnostics["sponsored_choch_participation_cost_geometry_rejected"] += 1
            return super()._submit_entry(setup, row)
        entry_price = self.instrument.make_price(raw_limit)
        entry = _as_float(entry_price)
        if not has_adverse_slippage_room(
            observed_price=observed,
            limit_price=entry,
            side=side,
            adverse_slippage_rate=slippage_rate,
        ):
            self.diagnostics["sponsored_choch_participation_cost_geometry_rejected"] += 1
            return super()._submit_entry(setup, row)

        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["sponsored_choch_participation_cost_geometry_rejected"] += 1
            return super()._submit_entry(setup, row)

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
            self.diagnostics["sponsored_choch_participation_no_live_target"] += 1
            return super()._submit_entry(setup, row)

        target_price = self.instrument.make_price(target)
        target = _as_float(target_price)
        rounded_r = net_r_at_price(entry, target, side, planned_loss, cost_rate)
        if (
            rounded_r + 1e-9 < MIN_LIQUIDITY_TARGET_NET_R
            or (side > 0 and not stop < entry < target)
            or (side < 0 and not target < entry < stop)
        ):
            self.diagnostics["sponsored_choch_participation_cost_geometry_rejected"] += 1
            return super()._submit_entry(setup, row)

        # Preflight the exact quantity check used by _submit_price_capped_bracket
        # before changing scenario state, so an infeasible participation order
        # can still fall back to the unchanged one-minute observation path.
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self.diagnostics["sponsored_choch_participation_cost_geometry_rejected"] += 1
            return super()._submit_entry(setup, row)

        details: dict[str, Any] = {
            **setup.details,
            "flow_state": flow_state,
            "choch_flow_15s": flow_15s,
            "choch_flow_60s": self._feature("flow_60s"),
            "choch_flow_3m": flow_3m,
            "choch_depth_imbalance_1": current_depth,
            "participation_depth_source": (
                "TARGET_RECLAIM_SETUP" if target_handoff else "CHOCH_CURRENT_BOOK"
            ),
            "participation_directional_depth": side
            * (setup_depth if target_handoff else current_depth),
            "participation_directional_flow_15s": side * flow_15s,
            "side": side,
            "sweep_extreme": setup.sweep_extreme,
            "confirmation_close": observed,
            "stop": stop,
        }
        armed = ArmedEntryPath(
            setup=setup,
            flow_state=str(flow_state),
            choch_close=observed,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details=details,
        )
        self.armed_entry_path = armed
        self.pending = None
        self.diagnostics["choch_active_confirmation"] += 1
        self.diagnostics["entry_path_armed"] += 1
        self.diagnostics["sponsored_choch_participation_eligible"] += 1
        self._transition(
            setup.scenario_id,
            "SPONSORED_CHOCH_PARTICIPATION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "SPONSORED_CHOCH_PARTICIPATION",
            "ALIGNED_TAIL_FLOW_AND_RECENT_DIRECTIONAL_DEPTH_AT_CHOCH",
            observed,
            {
                **details,
                "raw_slippage_protected_limit": raw_limit,
                "rounded_slippage_protected_limit": entry,
                "preflight_quantity": quantity_value,
            },
        )
        submitted = self._submit_price_capped_bracket(
            armed=armed,
            row=row,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            sizing_entry=entry,
            planned_loss=planned_loss,
            target_source=target_source,
            target_r=target_r,
            branch="TAIL_FLOW_SPONSORED_CHOCH",
            event_type="SPONSORED_CHOCH_MARKETABLE_LIMIT_SUBMITTED",
            reason="ACTIVE_CHOCH_PARTICIPATION_WITH_COST_AWARE_PRICE_CAP",
            expires_index=self.bar_index + 2,
            entry_tag="SPONSORED_CHOCH_PARTICIPATION_ENTRY",
            extra={
                "observed_choch_price": observed,
                "raw_slippage_protected_limit": raw_limit,
                "rounded_slippage_protected_limit": entry,
                "rounded_target_net_r": rounded_r,
                "target_handoff": target_handoff,
            },
        )
        if submitted:
            self.diagnostics["sponsored_choch_participation_submissions"] += 1
        return submitted


__all__ = ["SponsoredChochParticipationStrategy"]
