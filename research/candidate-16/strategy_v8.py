"""Candidate 16 v8: later-leg four-asset residual convergence.

The original Candidate 05 v52 state detector is preserved unchanged.  Its one
complete v7 state was immediately closed because the inherited pending handler
reused the same depth observation as a second confirmation.  v8 fixes the
economic role boundary rather than lowering that threshold:

- v52's robust residual, OI, tail-flow and depth observation freezes a state;
- no order can be created on that state bar;
- strictly later completed peer/own observations must show residual contraction;
- strictly later price, relative return, one-minute flow and displayed depth
  must all move in the convergence direction;
- that later bar starts a new tradeable leg with its own adverse extreme;
- a FOK LIMIT parent fills in full at or better than a worst-fill cap, or no
  position opens;
- the stop is beyond the state-to-confirmation adverse extreme;
- the target is a pre-existing live directional liquidity pool with at least
  one net R after configured costs.  No synthetic fallback target exists.

NautilusTrader and the existing shared-account lifecycle remain authoritative
for matching, fills, contingent children, fees, margin, liquidation, positions,
portfolio accounting and NAV.
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce

from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v52_cross_sectional_residual import CrossSectionalResidualStrategy


V8_STATE_BRANCH = "V8_RESIDUAL_OBSERVATION"
V8_TRADE_BRANCH = "V8_LATER_RESIDUAL_CONVERGENCE"
V8_MAX_WAIT_BARS = 15
V8_MIN_TARGET_NET_R = 1.0


class Candidate16V8Strategy(CrossSectionalResidualStrategy):
    """Freeze v52 state and trade only a strictly later convergence leg."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate16_v8_states_frozen": 0,
                "candidate16_v8_later_observations": 0,
                "candidate16_v8_residual_contractions": 0,
                "candidate16_v8_residual_neutralized": 0,
                "candidate16_v8_states_expired": 0,
                "candidate16_v8_later_confirmations": 0,
                "candidate16_v8_no_natural_target": 0,
                "candidate16_v8_geometry_rejected": 0,
                "candidate16_v8_fok_limit_entries": 0,
            },
        )

    def _maybe_arm_cross_sectional(
        self,
        row: dict[str, float | int],
    ) -> None:
        """Reuse v52 detection verbatim, then freeze its result without entry."""
        if self.pending is not None:
            return
        super()._maybe_arm_cross_sectional(row)
        setup = self.pending
        if setup is None:
            return
        if str(setup.details.get("branch")) != "CROSS_SECTIONAL_RESIDUAL_REJECTION":
            return

        initial_residual = float(setup.details["residual"])
        if not math.isfinite(initial_residual) or initial_residual == 0.0:
            self.pending = None
            return
        setup.branch = V8_STATE_BRANCH
        setup.expires_index = self.bar_index + V8_MAX_WAIT_BARS
        setup.sweep_extreme = (
            float(row["low"]) if setup.side > 0 else float(row["high"])
        )
        setup.details.update(
            {
                "candidate16_v8_state": "FOUR_ASSET_RESIDUAL_DISLOCATION",
                "v8_initial_residual": initial_residual,
                "v8_initial_abs_residual": abs(initial_residual),
                "v8_state_close": float(row["close"]),
                "v8_state_open": float(row["open"]),
                "v8_state_index": self.bar_index,
                "v8_state_ts": int(row["ts"]),
                "v8_state_expires_index": setup.expires_index,
                "v8_state_evidence_roles": {
                    "residual_oi_tail_flow_depth": "STATE_ONLY",
                    "strictly_later_residual_price_flow_depth": "CONFIRMATION_ONLY",
                },
                "v8_no_order_on_state_bar": True,
            },
        )
        self.diagnostics["candidate16_v8_states_frozen"] = int(
            self.diagnostics["candidate16_v8_states_frozen"],
        ) + 1
        self._transition(
            setup.scenario_id,
            "CROSS_SECTIONAL_RESIDUAL_STATE_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_LATER_CONVERGENCE_LEG",
            "V52_STATE_ACCEPTED_WITHOUT_REUSING_SAME_BAR_CONFIRMATION",
            float(row["close"]),
            setup.details,
        )

    def _close_v8_state(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
        *,
        event_type: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._transition(
            setup.scenario_id,
            event_type,
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            {**setup.details, **(details or {})},
        )
        self.pending = None

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != V8_STATE_BRANCH:
            return super()._process_pending(row)
        if self.bar_index <= setup.created_index:
            return True
        if self.bar_index > setup.expires_index:
            self.diagnostics["candidate16_v8_states_expired"] = int(
                self.diagnostics["candidate16_v8_states_expired"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="RESIDUAL_STATE_EXPIRED",
                reason="NO_STRICTLY_LATER_CONVERGENCE_LEG_WITHIN_OI_HORIZON",
            )
            return True

        side = setup.side
        if side > 0:
            setup.sweep_extreme = min(
                float(setup.sweep_extreme),
                float(row["low"]),
            )
        else:
            setup.sweep_extreme = max(
                float(setup.sweep_extreme),
                float(row["high"]),
            )

        peer = self._peer_state(int(row["ts"]))
        if peer is None:
            return True
        peer5, peer1 = peer
        own5 = self._own_normalized_return(5)
        own1 = self._own_normalized_return(1)
        if not all(math.isfinite(value) for value in (peer5, peer1, own5, own1)):
            return True
        residual = own5 - peer5
        initial = float(setup.details["v8_initial_residual"])
        self.diagnostics["candidate16_v8_later_observations"] = int(
            self.diagnostics["candidate16_v8_later_observations"],
        ) + 1

        observation = {
            "bar_index": self.bar_index,
            "ts_event": int(row["ts"]),
            "residual": residual,
            "initial_residual": initial,
            "own_normalized_5m": own5,
            "peer_normalized_5m": peer5,
            "own_normalized_1m": own1,
            "peer_normalized_1m": peer1,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "flow_60s": float(self._feature("flow_60s")),
            "depth_imbalance_1": float(self._feature("depth_imbalance_1")),
        }
        setup.details["v8_latest_later_observation"] = observation
        self._transition(
            setup.scenario_id,
            "RESIDUAL_STATE_LATER_OBSERVATION",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_LATER_CONVERGENCE_LEG",
            "STRICTLY_LATER_PEER_AND_LOCAL_OBSERVATION",
            float(row["close"]),
            {**setup.details, "v8_current_observation": observation},
        )

        if not math.isfinite(residual) or residual == 0.0 or residual * initial < 0.0:
            self.diagnostics["candidate16_v8_residual_neutralized"] = int(
                self.diagnostics["candidate16_v8_residual_neutralized"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="RESIDUAL_OBJECTIVE_CONSUMED_WITHOUT_ENTRY",
                reason="CROSS_SECTIONAL_RESIDUAL_CROSSED_COMMON_FACTOR_BEFORE_ENTRY",
                details={"v8_current_observation": observation},
            )
            return True

        contraction = abs(residual) < abs(initial)
        if contraction:
            self.diagnostics["candidate16_v8_residual_contractions"] = int(
                self.diagnostics["candidate16_v8_residual_contractions"],
            ) + 1
        flow60 = observation["flow_60s"]
        depth = observation["depth_imbalance_1"]
        state_close = float(setup.details["v8_state_close"])
        confirmation = (
            contraction
            and side * (float(row["close"]) - state_close) > 0.0
            and side * (own1 - peer1) > 0.0
            and side * (float(row["close"]) - float(row["open"])) > 0.0
            and math.isfinite(flow60)
            and side * flow60 > 0.0
            and math.isfinite(depth)
            and side * depth > 0.0
        )
        if not confirmation:
            return True

        self.diagnostics["candidate16_v8_later_confirmations"] = int(
            self.diagnostics["candidate16_v8_later_confirmations"],
        ) + 1
        setup.details.update(
            {
                "candidate16_v8_confirmation": observation,
                "v8_confirmation_residual_contraction": (
                    abs(initial) - abs(residual)
                ),
                "v8_confirmation_index": self.bar_index,
                "v8_confirmation_ts": int(row["ts"]),
            },
        )
        self._transition(
            setup.scenario_id,
            "STRICTLY_LATER_RESIDUAL_CONVERGENCE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            "RESIDUAL_PRICE_RELATIVE_RETURN_FLOW_AND_DEPTH_CONVERGED_LATER",
            float(row["close"]),
            setup.details,
        )
        return self._submit_v8_entry(setup, row)

    def _natural_target_v8(
        self,
        *,
        entry: float,
        side: int,
        planned_loss: float,
        cost_rate: float,
    ) -> tuple[Any, float, str, float] | None:
        candidates = []
        for pool in self.active_pools.values():
            if side > 0 and (pool.kind != "HIGH" or pool.level <= entry):
                continue
            if side < 0 and (pool.kind != "LOW" or pool.level >= entry):
                continue
            candidates.append(pool)
        candidates.sort(key=lambda pool: side * (float(pool.level) - entry))
        for pool in candidates:
            price_object = self.instrument.make_price(float(pool.level))
            price = _as_float(price_object)
            target_r = net_r_at_price(
                entry,
                price,
                side,
                planned_loss,
                cost_rate,
            )
            if target_r + 1e-9 >= V8_MIN_TARGET_NET_R:
                return price_object, price, f"POOL:{pool.pool_id}", target_r
        return None

    def _submit_v8_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        side = setup.side
        atr = float(self._atr())
        signal_close = float(row["close"])
        if side not in (-1, 1) or not math.isfinite(atr) or atr <= 0.0:
            self.diagnostics["candidate16_v8_geometry_rejected"] = int(
                self.diagnostics["candidate16_v8_geometry_rejected"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="V8_ENTRY_GEOMETRY_REJECTED",
                reason="INVALID_RESIDUAL_CONVERGENCE_ATR_OR_DIRECTION",
            )
            return True

        stop_raw = float(setup.sweep_extreme) - side * self.config.stop_buffer_atr * atr
        stop_price = self.instrument.make_price(stop_raw)
        stop = _as_float(stop_price)

        slippage_rate = self.config.adverse_slippage_bps_each_side / 10_000.0
        raw_entry_limit = signal_close * (1.0 + side * slippage_rate)
        entry_price = self.instrument.make_price(raw_entry_limit)
        entry = _as_float(entry_price)
        increment = _as_float(self.instrument.price_increment)
        if side > 0 and entry <= signal_close:
            entry_price = self.instrument.make_price(signal_close + increment)
            entry = _as_float(entry_price)
        elif side < 0 and entry >= signal_close:
            entry_price = self.instrument.make_price(signal_close - increment)
            entry = _as_float(entry_price)

        if (side > 0 and not stop < signal_close < entry) or (
            side < 0 and not entry < signal_close < stop
        ):
            self.diagnostics["candidate16_v8_geometry_rejected"] = int(
                self.diagnostics["candidate16_v8_geometry_rejected"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="V8_ENTRY_GEOMETRY_REJECTED",
                reason="INVALID_STATE_TO_CONFIRMATION_STOP_AND_CAP_GEOMETRY",
                details={"entry_limit": entry, "stop": stop},
            )
            return True

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop,
            side,
            cost_rate,
            slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["candidate16_v8_geometry_rejected"] = int(
                self.diagnostics["candidate16_v8_geometry_rejected"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="V8_ENTRY_GEOMETRY_REJECTED",
                reason="INVALID_WORST_FILL_PLANNED_LOSS",
            )
            return True

        target_values = self._natural_target_v8(
            entry=entry,
            side=side,
            planned_loss=planned_loss,
            cost_rate=cost_rate,
        )
        if target_values is None:
            self.diagnostics["candidate16_v8_no_natural_target"] = int(
                self.diagnostics["candidate16_v8_no_natural_target"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="V8_NO_TRADEABLE_LIQUIDITY_OBJECTIVE",
                reason="NO_PRE_EXISTING_LIQUIDITY_TARGET_WITH_ONE_NET_R",
            )
            return True
        target_price, target, target_source, target_r = target_values
        if (side > 0 and not entry < target) or (side < 0 and not target < entry):
            self.diagnostics["candidate16_v8_geometry_rejected"] = int(
                self.diagnostics["candidate16_v8_geometry_rejected"],
            ) + 1
            self._close_v8_state(
                setup,
                row,
                event_type="V8_ENTRY_GEOMETRY_REJECTED",
                reason="ROUNDED_LIQUIDITY_TARGET_NOT_DIRECTIONAL",
            )
            return True

        armed = ArmedEntryPath(
            setup=setup,
            flow_state="V8_STRICTLY_LATER_RESIDUAL_CONVERGENCE",
            choch_close=signal_close,
            stop=stop,
            atr=atr,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            details={
                **setup.details,
                "v8_entry_signal_close": signal_close,
                "v8_entry_limit_worst_fill": entry,
                "v8_stop": stop,
                "v8_target": target,
                "v8_target_source": target_source,
                "v8_target_net_r": target_r,
            },
        )
        self.pending = None
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
            target_r=target_r,
            branch=V8_TRADE_BRANCH,
            event_type="V8_FOK_LIQUIDITY_BRACKET_SUBMITTED",
            reason="STRICTLY_LATER_RESIDUAL_CONVERGENCE_WITH_NATURAL_OBJECTIVE",
            expires_index=self.bar_index + 1,
            entry_tag="CANDIDATE16_V8_FOK_PRICE_CAP",
            extra={
                "v8_entry_time_in_force": "FOK",
                "v8_entry_all_or_none": True,
                "v8_minimum_target_net_r": V8_MIN_TARGET_NET_R,
            },
        )
        if not submitted and self.pending is None and self.armed_entry_path is None:
            return True
        return True

    def _submit_price_capped_bracket(
        self,
        *,
        armed: ArmedEntryPath,
        row: dict[str, float | int],
        entry_price: Any,
        stop_price: Any,
        target_price: Any,
        sizing_entry: float,
        planned_loss: float,
        target_source: str,
        target_r: float,
        branch: str,
        event_type: str,
        reason: str,
        expires_index: int,
        entry_tag: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        """Reuse the mature bracket lifecycle with a full-or-none FOK parent."""
        side = armed.setup.side
        entry = _as_float(entry_price)
        stop = _as_float(stop_price)
        target = _as_float(target_price)
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * sizing_entry < 10.0:
            self._expire_armed_entry(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            time_in_force=TimeInForce.FOK,
            entry_post_only=False,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
            entry_tags=[entry_tag, "ENTRY"],
            tp_tags=["OPPOSING_LIQUIDITY_TARGET"],
            sl_tags=["STATE_TO_CONFIRMATION_EXTREME_INVALIDATION"],
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = expires_index
        self.entry_side = side
        self.entry_stop = stop
        self.entry_limit = entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = armed.setup.scenario_id
        self.current_branch = branch
        self.current_pool_level = armed.setup.pool_level
        self.armed_entry_path = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate16_v8_fok_limit_entries"] = int(
            self.diagnostics["candidate16_v8_fok_limit_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self.diagnostics["liquidity_pool_targets"] = int(
            self.diagnostics.get("liquidity_pool_targets", 0),
        ) + 1

        # Preserve the inherited scenario-valid pending lifecycle even though
        # the parent is FOK and normally resolves on the first executable event.
        self.pending_scenario_target = target
        self.pending_scenario_target_pool_id = target_source.split(":", 1)[1]
        self.pending_scenario_horizon_index = (
            armed.created_index + self.config.max_hold_bars
        )
        self.pending_original_expiry_index = expires_index
        self.pending_original_expiry_crossed = False
        self.diagnostics["scenario_valid_pending_entries"] = int(
            self.diagnostics.get("scenario_valid_pending_entries", 0),
        ) + 1

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        details = {
            **armed.details,
            "branch": branch,
            "entry_limit_worst_fill": entry,
            "entry_time_in_force": "FOK",
            "entry_all_or_none": True,
            "stop": stop,
            "target": target,
            "target_source": target_source,
            "target_net_r_before_rounding": target_r,
            "target_net_r_after_rounding": net_r_at_price(
                entry,
                target,
                side,
                planned_loss,
                cost_rate,
            ),
            "quantity": quantity_value,
            "equity": equity,
            "risk_budget": risk_budget,
            "planned_loss_per_unit_at_worst_fill": planned_loss,
            "planned_account_loss_at_worst_fill": quantity_value * planned_loss,
            "entry_expires_index": expires_index,
            "scenario_horizon_index": self.pending_scenario_horizon_index,
            **(extra or {}),
        }
        self._transition(
            armed.setup.scenario_id,
            event_type,
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            reason,
            entry,
            details,
        )
        return True


__all__ = [
    "Candidate16V8Strategy",
    "V8_MAX_WAIT_BARS",
    "V8_MIN_TARGET_NET_R",
    "V8_STATE_BRANCH",
    "V8_TRADE_BRANCH",
]
