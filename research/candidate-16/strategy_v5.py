"""Candidate 16 v5: state-first crowded-initiative rejection.

This strategy abandons the sweep-first v1-v4 entry hierarchy.  A completed
cost-exceeding impulse with new open interest and opposing closing L1 pressure
is frozen without an order.  A strictly later bar must then show price,
aggressor flow, and L1 pressure moving in the fade direction.  Only that new
auction leg may re-arm a price-capped STOP_LIMIT bracket.

NautilusTrader continues to own orders, fills, contingent children, fees,
positions, margin, liquidation, portfolio accounting, and NAV.  Candidate 05's
causal liquidity pools remain available only as objectives, not as entry
patterns.  Candidate 16 v2's protective fail-close lifecycle remains inherited.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from crowded_initiative_router import CrowdedDecision
from crowded_initiative_router import CrowdedShockObservation
from crowded_initiative_router import CrowdedShockState
from crowded_initiative_router import LaterFailureObservation
from crowded_initiative_router import advance_crowded_shock
from crowded_initiative_router import qualify_crowded_shock
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_v4 import Candidate16V4Config
from strategy_v4 import Candidate16V4Strategy


class Candidate16V5Config(Candidate16V4Config, frozen=True):
    crowded_min_notional_burst: float = 1.0
    crowded_metrics_max_age_seconds: float = 300.0
    crowded_confirmation_bars: int = 3
    crowded_max_close_extension_atr: float = 0.05
    crowded_entry_rearm_atr: float = 0.01
    crowded_entry_limit_risk_expansion: float = 0.25
    crowded_target_min_net_r: float = 1.0
    crowded_parent_cooldown_bars: int = 30


class Candidate16V5Strategy(Candidate16V4Strategy):
    """One cost-exceeding crowded initiative, one later failure, one entry max."""

    def __init__(self, config: Candidate16V5Config) -> None:
        super().__init__(config=config)
        if config.crowded_min_notional_burst < 1.0:
            raise ValueError("crowded_min_notional_burst must be at least one")
        if config.crowded_metrics_max_age_seconds < 0.0:
            raise ValueError("crowded_metrics_max_age_seconds must be non-negative")
        if config.crowded_confirmation_bars <= 0:
            raise ValueError("crowded_confirmation_bars must be positive")
        if config.crowded_max_close_extension_atr < 0.0:
            raise ValueError("crowded_max_close_extension_atr must be non-negative")
        if config.crowded_entry_rearm_atr <= 0.0:
            raise ValueError("crowded_entry_rearm_atr must be positive")
        if not 0.0 < config.crowded_entry_limit_risk_expansion <= 1.0:
            raise ValueError(
                "crowded_entry_limit_risk_expansion must be in (0, 1]",
            )
        if config.crowded_target_min_net_r <= 0.0:
            raise ValueError("crowded_target_min_net_r must be positive")
        if config.crowded_parent_cooldown_bars <= config.crowded_confirmation_bars:
            raise ValueError(
                "crowded_parent_cooldown_bars must exceed confirmation window",
            )

        self.crowded_shock: CrowdedShockState | None = None
        self.last_crowded_parent_index = -10**12
        self.diagnostics.update(
            {
                "candidate16_v5_cost_exceeding_observations": 0,
                "candidate16_v5_crowded_shocks": 0,
                "candidate16_v5_shock_rejected": 0,
                "candidate16_v5_shock_rejection_reasons": {},
                "candidate16_v5_later_observations": 0,
                "candidate16_v5_failures_confirmed": 0,
                "candidate16_v5_failures_invalidated": 0,
                "candidate16_v5_failures_expired": 0,
                "candidate16_v5_geometry_rejected": 0,
                "candidate16_v5_no_natural_target": 0,
                "candidate16_v5_stop_limit_entries": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.crowded_shock = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        super()._expire_pending(row, reason)
        self.crowded_shock = None

    def _economic_floor_bps(self) -> float:
        return 2.0 * (
            float(self.config.all_in_cost_bps_each_side)
            + float(self.config.adverse_slippage_bps_each_side)
        )

    def _record_shock_rejection(self, reason: str) -> None:
        self.diagnostics["candidate16_v5_shock_rejected"] = int(
            self.diagnostics["candidate16_v5_shock_rejected"],
        ) + 1
        counts = self.diagnostics["candidate16_v5_shock_rejection_reasons"]
        if not isinstance(counts, dict):
            counts = {}
            self.diagnostics["candidate16_v5_shock_rejection_reasons"] = counts
        counts[reason] = int(counts.get(reason, 0)) + 1

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        """Detect a state-first crowded initiative; no liquidity sweep required."""
        del previous_close
        if self.crowded_shock is not None or self.pending is not None:
            return
        if (
            self.bar_index - self.last_crowded_parent_index
            < self.config.crowded_parent_cooldown_bars
        ):
            return

        atr = self._atr()
        ret_bps = self._raw_feature("ret_60s_bps")
        if not math.isfinite(atr) or atr <= 0.0 or not math.isfinite(ret_bps):
            return
        economic_floor = self._economic_floor_bps()
        if abs(ret_bps) < economic_floor:
            return
        self.diagnostics["candidate16_v5_cost_exceeding_observations"] = int(
            self.diagnostics["candidate16_v5_cost_exceeding_observations"],
        ) + 1

        observation = CrowdedShockObservation(
            bar_index=self.bar_index,
            ts_event=int(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            atr=atr,
            ret_60s_bps=ret_bps,
            flow_60s=self._raw_feature("flow_60s"),
            notional_burst=self._raw_feature("notional_burst"),
            oi_change_5m=self._raw_feature("oi_change_5m"),
            metrics_age_seconds=self._raw_feature("metrics_age_seconds"),
            l1_imbalance_close=self._raw_feature("bt_imbalance_close"),
        )
        qualification = qualify_crowded_shock(
            observation,
            economic_floor_bps=economic_floor,
            minimum_notional_burst=self.config.crowded_min_notional_burst,
            maximum_metrics_age_seconds=(
                self.config.crowded_metrics_max_age_seconds
            ),
        )
        if not qualification.qualified:
            self._record_shock_rejection(qualification.reason)
            return

        self.scenario_counter += 1
        scenario_id = f"c16v5-{self.scenario_counter:07d}"
        direction = qualification.shock_direction
        side = qualification.fade_side
        extreme = float(row["high"]) if direction > 0 else float(row["low"])
        self.crowded_shock = CrowdedShockState(
            scenario_id=scenario_id,
            shock_direction=direction,
            fade_side=side,
            shock_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=(
                self.bar_index + self.config.crowded_confirmation_bars
            ),
            shock_open=float(row["open"]),
            shock_high=float(row["high"]),
            shock_low=float(row["low"]),
            shock_close=float(row["close"]),
            atr=atr,
        )
        self.last_crowded_parent_index = self.bar_index
        details = {
            "candidate16_v5_state": "CROWDED_INITIATIVE",
            "qualification_reason": qualification.reason,
            "shock_direction": direction,
            "fade_side": side,
            "shock_bar_index": self.bar_index,
            "shock_ts_event": int(row["ts"]),
            "shock_open": float(row["open"]),
            "shock_high": float(row["high"]),
            "shock_low": float(row["low"]),
            "shock_close": float(row["close"]),
            "shock_atr": atr,
            "economic_floor_bps": economic_floor,
            "ret_60s_bps": observation.ret_60s_bps,
            "flow_60s": observation.flow_60s,
            "notional_burst": observation.notional_burst,
            "oi_change_5m": observation.oi_change_5m,
            "metrics_age_seconds": observation.metrics_age_seconds,
            "bt_imbalance_close": observation.l1_imbalance_close,
            "no_order_on_shock_bar": True,
        }
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="CROWDED_SHOCK",
            side=side,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id="CROWDED_INITIATIVE_ORIGIN",
            pool_level=float(row["open"]),
            created_index=self.bar_index,
            expires_index=self.crowded_shock.expires_index,
            sweep_extreme=extreme,
            structure=float(row["close"]),
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate16_v5_crowded_shocks"] = int(
            self.diagnostics["candidate16_v5_crowded_shocks"],
        ) + 1
        self._transition(
            scenario_id,
            "CROWDED_INITIATIVE_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            "WAITING_FOR_LATER_FAILURE",
            qualification.reason,
            float(row["close"]),
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != "CROWDED_SHOCK":
            return super()._process_pending(row)
        state = self.crowded_shock
        if state is None:
            self._expire_pending(row, "MISSING_CROWDED_SHOCK_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True

        state = advance_crowded_shock(
            state,
            LaterFailureObservation(
                bar_index=self.bar_index,
                ts_event=int(row["ts"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._raw_feature("flow_60s"),
                l1_imbalance_close=self._raw_feature(
                    "bt_imbalance_close",
                ),
            ),
            maximum_close_extension_atr=(
                self.config.crowded_max_close_extension_atr
            ),
        )
        self.crowded_shock = state
        self.diagnostics["candidate16_v5_later_observations"] = int(
            self.diagnostics["candidate16_v5_later_observations"],
        ) + 1
        setup.details["latest_crowded_state"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self._transition(
            setup.scenario_id,
            "CROWDED_INITIATIVE_LATER_OBSERVATION",
            int(row["ts"]),
            int(row["ts"]),
            state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )

        if state.decision is CrowdedDecision.WAITING:
            return True
        if state.decision is CrowdedDecision.INVALIDATED:
            self.diagnostics["candidate16_v5_failures_invalidated"] = int(
                self.diagnostics["candidate16_v5_failures_invalidated"],
            ) + 1
            self.pending = None
            self.crowded_shock = None
            return True
        if state.decision is CrowdedDecision.EXPIRED:
            self.diagnostics["candidate16_v5_failures_expired"] = int(
                self.diagnostics["candidate16_v5_failures_expired"],
            ) + 1
            self.pending = None
            self.crowded_shock = None
            return True

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="CROWDED_REJECTION",
            side=setup.side,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=setup.structure,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=False,
            details={
                **setup.details,
                "confirmed_crowded_failure": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
                "confirmation_bar": {
                    "ts_event": int(row["ts"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "flow_60s": self._raw_feature("flow_60s"),
                    "bt_imbalance_close": self._raw_feature(
                        "bt_imbalance_close",
                    ),
                },
            },
        )
        self.pending = completed
        self.crowded_shock = None
        self.diagnostics["candidate16_v5_failures_confirmed"] = int(
            self.diagnostics["candidate16_v5_failures_confirmed"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "CROWDED_INITIATIVE_FAILURE_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_REARM_EVALUATION",
            state.reason,
            float(row["close"]),
            completed.details,
        )
        self._submit_entry(completed, row)
        return True

    def _natural_target(
        self,
        *,
        entry: float,
        side: int,
        planned_loss: float,
        cost_rate: float,
        setup: PendingSetup,
    ) -> tuple[float, str, float] | None:
        candidates: list[tuple[float, str]] = []
        origin = float(setup.details["shock_open"])
        if side * (origin - entry) > 0.0:
            candidates.append((origin, "CROWDED_INITIATIVE_ORIGIN"))

        for pool in self.active_pools.values():
            directional = side * (pool.level - entry)
            if directional <= 0.0:
                continue
            if side > 0 and pool.kind != "HIGH":
                continue
            if side < 0 and pool.kind != "LOW":
                continue
            candidates.append(
                (
                    float(pool.level),
                    f"POOL:{pool.pool_id}:{pool.source}:S{pool.strength}",
                ),
            )
        candidates.sort(key=lambda item: side * (item[0] - entry))
        for price, source in candidates:
            target_r = net_r_at_price(
                entry,
                price,
                side,
                planned_loss,
                cost_rate,
            )
            if target_r >= self.config.crowded_target_min_net_r:
                return price, source, target_r
        return None

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        """Re-arm through a price-capped STOP_LIMIT after the later failure."""
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        if not math.isfinite(atr) or atr <= 0.0 or side not in (-1, 1):
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_CROWDED_FAILURE_GEOMETRY")
            return False

        shock_high = float(setup.details["shock_high"])
        shock_low = float(setup.details["shock_low"])
        shock_extreme = shock_low if side > 0 else shock_high
        stop = shock_extreme - side * self.config.stop_buffer_atr * atr
        entry_trigger = (
            signal_close + side * self.config.crowded_entry_rearm_atr * atr
        )
        structural_risk = abs(entry_trigger - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_CROWDED_FAILURE_STOP")
            return False
        entry_limit = entry_trigger + (
            side
            * self.config.crowded_entry_limit_risk_expansion
            * structural_risk
        )

        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        adverse_slippage_rate = (
            self.config.adverse_slippage_bps_each_side / 10_000.0
        )
        planned_loss = planned_loss_per_unit(
            entry_limit,
            stop,
            side,
            cost_rate,
            adverse_slippage_rate,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_CROWDED_STOP_LIMIT_RISK")
            return False

        target_values = self._natural_target(
            entry=entry_limit,
            side=side,
            planned_loss=planned_loss,
            cost_rate=cost_rate,
            setup=setup,
        )
        if target_values is None:
            self.diagnostics["candidate16_v5_no_natural_target"] = int(
                self.diagnostics["candidate16_v5_no_natural_target"],
            ) + 1
            self._expire_pending(
                row,
                "NO_COST_AWARE_NATURAL_OBJECTIVE_FOR_CROWDED_FAILURE",
            )
            return False
        target, target_source, target_r = target_values

        if side > 0 and not (stop < entry_trigger <= entry_limit < target):
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_LONG_CROWDED_STOP_LIMIT_BRACKET")
            return False
        if side < 0 and not (target < entry_limit <= entry_trigger < stop):
            self.diagnostics["candidate16_v5_geometry_rejected"] = int(
                self.diagnostics["candidate16_v5_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_SHORT_CROWDED_STOP_LIMIT_BRACKET")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        raw_quantity = risk_budget / planned_loss
        quantity_value = floor_quantity(
            raw_quantity,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry_limit < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False

        order_side = OrderSide.BUY if side > 0 else OrderSide.SELL
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=order_side,
            quantity=self.instrument.make_qty(quantity_value),
            time_in_force=TimeInForce.GTC,
            entry_order_type=OrderType.STOP_LIMIT,
            entry_trigger_price=self.instrument.make_price(entry_trigger),
            entry_price=self.instrument.make_price(entry_limit),
            entry_post_only=False,
            tp_price=self.instrument.make_price(target),
            sl_trigger_price=self.instrument.make_price(stop),
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = setup.branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] = int(
            self.diagnostics["entry_submissions"],
        ) + 1
        self.diagnostics["candidate16_v5_stop_limit_entries"] = int(
            self.diagnostics["candidate16_v5_stop_limit_entries"],
        ) + 1
        self.diagnostics["max_simultaneous_entry_intents"] = max(
            int(self.diagnostics["max_simultaneous_entry_intents"]),
            1,
        )
        self._transition(
            setup.scenario_id,
            "ENTRY_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "PRICE_CAPPED_REARM_AFTER_CROWDED_INITIATIVE_FAILURE",
            entry_trigger,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_trigger": entry_trigger,
                "entry_limit_worst_fill": entry_limit,
                "stop": stop,
                "target": target,
                "target_source": target_source,
                "target_net_r": target_r,
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit_at_worst_fill": planned_loss,
                "planned_account_loss_at_worst_fill": (
                    quantity_value * planned_loss
                ),
            },
        )
        return True


__all__ = ["Candidate16V5Config", "Candidate16V5Strategy"]
