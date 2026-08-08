"""Candidate 16 v6: informed-initiative pullback continuation.

The v5 fade is discarded.  A completed cost-exceeding impulse with new open
interest and aligned aggressor/L1 pressure defines an informed initiative.  It
is frozen without an order.  A later counter bar must hold the impulse midpoint
and a still later bar must break the pullback boundary with renewed flow and L1
support.  Entry, pullback invalidation, and liquidity objective therefore belong
to the same new continuation leg.

NautilusTrader owns all execution and accounting.  Candidate 05's active pools
are reused only as causal objectives; no fallback price target is manufactured.
Candidate 16 v2's protective fail-close lifecycle remains inherited through v4.
"""
from __future__ import annotations

from dataclasses import asdict
import math

from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from informed_initiative_router import ContinuationDecision
from informed_initiative_router import InformedContinuationState
from informed_initiative_router import InformedInitiativeObservation
from informed_initiative_router import LaterContinuationObservation
from informed_initiative_router import advance_informed_continuation
from informed_initiative_router import qualify_informed_initiative
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from strategy_base import PendingSetup
from strategy_v4 import Candidate16V4Config
from strategy_v4 import Candidate16V4Strategy


class Candidate16V6Config(Candidate16V4Config, frozen=True):
    informed_min_notional_burst: float = 1.0
    informed_metrics_max_age_seconds: float = 300.0
    informed_max_wait_bars: int = 5
    informed_entry_rearm_atr: float = 0.01
    informed_entry_limit_risk_expansion: float = 0.25
    informed_target_min_net_r: float = 1.0
    informed_parent_cooldown_bars: int = 30


class Candidate16V6Strategy(Candidate16V4Strategy):
    """One informed initiative, one midpoint-held pullback, one entry max."""

    def __init__(self, config: Candidate16V6Config) -> None:
        super().__init__(config=config)
        if config.informed_min_notional_burst < 1.0:
            raise ValueError("informed_min_notional_burst must be at least one")
        if config.informed_metrics_max_age_seconds < 0.0:
            raise ValueError("informed_metrics_max_age_seconds must be non-negative")
        if config.informed_max_wait_bars < 2:
            raise ValueError("informed_max_wait_bars must permit pullback and resumption")
        if config.informed_entry_rearm_atr <= 0.0:
            raise ValueError("informed_entry_rearm_atr must be positive")
        if not 0.0 < config.informed_entry_limit_risk_expansion <= 1.0:
            raise ValueError(
                "informed_entry_limit_risk_expansion must be in (0, 1]",
            )
        if config.informed_target_min_net_r <= 0.0:
            raise ValueError("informed_target_min_net_r must be positive")
        if config.informed_parent_cooldown_bars <= config.informed_max_wait_bars:
            raise ValueError(
                "informed_parent_cooldown_bars must exceed observation window",
            )

        self.informed_state: InformedContinuationState | None = None
        self.last_informed_parent_index = -10**12
        self.diagnostics.update(
            {
                "candidate16_v6_cost_exceeding_observations": 0,
                "candidate16_v6_informed_initiatives": 0,
                "candidate16_v6_initiative_rejected": 0,
                "candidate16_v6_rejection_reasons": {},
                "candidate16_v6_later_observations": 0,
                "candidate16_v6_pullbacks_armed": 0,
                "candidate16_v6_continuations_confirmed": 0,
                "candidate16_v6_invalidated": 0,
                "candidate16_v6_expired": 0,
                "candidate16_v6_no_natural_target": 0,
                "candidate16_v6_geometry_rejected": 0,
                "candidate16_v6_fok_limit_entries": 0,
            },
        )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.informed_state = None

    def _expire_pending(self, row: dict[str, float | int], reason: str) -> None:
        super()._expire_pending(row, reason)
        self.informed_state = None

    def _economic_floor_bps(self) -> float:
        return 2.0 * (
            float(self.config.all_in_cost_bps_each_side)
            + float(self.config.adverse_slippage_bps_each_side)
        )

    def _record_rejection(self, reason: str) -> None:
        self.diagnostics["candidate16_v6_initiative_rejected"] = int(
            self.diagnostics["candidate16_v6_initiative_rejected"],
        ) + 1
        counts = self.diagnostics["candidate16_v6_rejection_reasons"]
        if not isinstance(counts, dict):
            counts = {}
            self.diagnostics["candidate16_v6_rejection_reasons"] = counts
        counts[reason] = int(counts.get(reason, 0)) + 1

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        """Detect informed initiative state without requiring a prior sweep."""
        del previous_close
        if self.informed_state is not None or self.pending is not None:
            return
        if (
            self.bar_index - self.last_informed_parent_index
            < self.config.informed_parent_cooldown_bars
        ):
            return

        atr = self._atr()
        ret_bps = self._raw_feature("ret_60s_bps")
        if not math.isfinite(atr) or atr <= 0.0 or not math.isfinite(ret_bps):
            return
        floor = self._economic_floor_bps()
        if abs(ret_bps) < floor:
            return
        self.diagnostics["candidate16_v6_cost_exceeding_observations"] = int(
            self.diagnostics["candidate16_v6_cost_exceeding_observations"],
        ) + 1

        observation = InformedInitiativeObservation(
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
        qualification = qualify_informed_initiative(
            observation,
            economic_floor_bps=floor,
            minimum_notional_burst=self.config.informed_min_notional_burst,
            maximum_metrics_age_seconds=(
                self.config.informed_metrics_max_age_seconds
            ),
        )
        if not qualification.qualified:
            self._record_rejection(qualification.reason)
            return

        self.scenario_counter += 1
        scenario_id = f"c16v6-{self.scenario_counter:07d}"
        direction = qualification.direction
        midpoint = (float(row["open"]) + float(row["close"])) / 2.0
        self.informed_state = InformedContinuationState(
            scenario_id=scenario_id,
            direction=direction,
            shock_index=self.bar_index,
            last_index=self.bar_index,
            expires_index=self.bar_index + self.config.informed_max_wait_bars,
            shock_open=float(row["open"]),
            shock_high=float(row["high"]),
            shock_low=float(row["low"]),
            shock_close=float(row["close"]),
            midpoint=midpoint,
            atr=atr,
        )
        self.last_informed_parent_index = self.bar_index
        details = {
            "candidate16_v6_state": "INFORMED_INITIATIVE",
            "qualification_reason": qualification.reason,
            "direction": direction,
            "shock_bar_index": self.bar_index,
            "shock_ts_event": int(row["ts"]),
            "shock_open": float(row["open"]),
            "shock_high": float(row["high"]),
            "shock_low": float(row["low"]),
            "shock_close": float(row["close"]),
            "shock_midpoint": midpoint,
            "shock_atr": atr,
            "economic_floor_bps": floor,
            "ret_60s_bps": observation.ret_60s_bps,
            "flow_60s": observation.flow_60s,
            "notional_burst": observation.notional_burst,
            "oi_change_5m": observation.oi_change_5m,
            "metrics_age_seconds": observation.metrics_age_seconds,
            "bt_imbalance_close": observation.l1_imbalance_close,
            "no_order_on_initiative_bar": True,
        }
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="INFORMED_INITIATIVE",
            side=direction,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id="INFORMED_INITIATIVE",
            pool_level=float(row["close"]),
            created_index=self.bar_index,
            expires_index=self.informed_state.expires_index,
            sweep_extreme=(
                float(row["high"]) if direction > 0 else float(row["low"])
            ),
            structure=midpoint,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["candidate16_v6_informed_initiatives"] = int(
            self.diagnostics["candidate16_v6_informed_initiatives"],
        ) + 1
        self._transition(
            scenario_id,
            "INFORMED_INITIATIVE_FROZEN",
            int(row["ts"]),
            int(row["ts"]),
            ContinuationDecision.WAITING_PULLBACK.value,
            qualification.reason,
            float(row["close"]),
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch != "INFORMED_INITIATIVE":
            return super()._process_pending(row)
        state = self.informed_state
        if state is None:
            self._expire_pending(row, "MISSING_INFORMED_INITIATIVE_STATE")
            return True
        if self.bar_index <= setup.created_index:
            return True

        previous_decision = state.decision
        state = advance_informed_continuation(
            state,
            LaterContinuationObservation(
                bar_index=self.bar_index,
                ts_event=int(row["ts"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                flow_60s=self._raw_feature("flow_60s"),
                l1_imbalance_close=self._raw_feature("bt_imbalance_close"),
            ),
        )
        self.informed_state = state
        self.diagnostics["candidate16_v6_later_observations"] = int(
            self.diagnostics["candidate16_v6_later_observations"],
        ) + 1
        if (
            previous_decision is ContinuationDecision.WAITING_PULLBACK
            and state.decision is ContinuationDecision.PULLBACK_ARMED
        ):
            self.diagnostics["candidate16_v6_pullbacks_armed"] = int(
                self.diagnostics["candidate16_v6_pullbacks_armed"],
            ) + 1
        setup.details["latest_informed_state"] = {
            **asdict(state),
            "decision": state.decision.value,
        }
        self._transition(
            setup.scenario_id,
            "INFORMED_INITIATIVE_LATER_OBSERVATION",
            int(row["ts"]),
            int(row["ts"]),
            state.decision.value,
            state.reason,
            float(row["close"]),
            setup.details,
        )

        if state.decision in {
            ContinuationDecision.WAITING_PULLBACK,
            ContinuationDecision.PULLBACK_ARMED,
        }:
            return True
        if state.decision is ContinuationDecision.INVALIDATED:
            self.diagnostics["candidate16_v6_invalidated"] = int(
                self.diagnostics["candidate16_v6_invalidated"],
            ) + 1
            self.pending = None
            self.informed_state = None
            return True
        if state.decision is ContinuationDecision.EXPIRED:
            self.diagnostics["candidate16_v6_expired"] = int(
                self.diagnostics["candidate16_v6_expired"],
            ) + 1
            self.pending = None
            self.informed_state = None
            return True

        completed = PendingSetup(
            scenario_id=setup.scenario_id,
            branch="INFORMED_CONTINUATION",
            side=state.direction,
            swept_kind=setup.swept_kind,
            pool_id=setup.pool_id,
            pool_level=setup.pool_level,
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=setup.sweep_extreme,
            structure=state.pullback_boundary,
            atr=setup.atr,
            hold_count=0,
            retrace_armed=True,
            details={
                **setup.details,
                "confirmed_informed_continuation": {
                    **asdict(state),
                    "decision": state.decision.value,
                },
                "pullback_extreme": state.pullback_extreme,
                "pullback_boundary": state.pullback_boundary,
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
        self.informed_state = None
        self.diagnostics["candidate16_v6_continuations_confirmed"] = int(
            self.diagnostics["candidate16_v6_continuations_confirmed"],
        ) + 1
        self._transition(
            completed.scenario_id,
            "INFORMED_CONTINUATION_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
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
    ) -> tuple[float, str, float] | None:
        candidates: list[tuple[float, str]] = []
        for pool in self.active_pools.values():
            if side > 0 and (pool.kind != "HIGH" or pool.level <= entry):
                continue
            if side < 0 and (pool.kind != "LOW" or pool.level >= entry):
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
            if target_r >= self.config.informed_target_min_net_r:
                return price, source, target_r
        return None

    def _submit_entry(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        atr = self._atr()
        side = setup.side
        signal_close = float(row["close"])
        pullback_extreme = float(setup.details["pullback_extreme"])
        if (
            side not in (-1, 1)
            or not math.isfinite(atr)
            or atr <= 0.0
            or not math.isfinite(pullback_extreme)
        ):
            self.diagnostics["candidate16_v6_geometry_rejected"] = int(
                self.diagnostics["candidate16_v6_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_INFORMED_CONTINUATION_GEOMETRY")
            return False

        stop = pullback_extreme - side * self.config.stop_buffer_atr * atr
        structural_risk = abs(signal_close - stop)
        if not math.isfinite(structural_risk) or structural_risk <= 0.0:
            self.diagnostics["candidate16_v6_geometry_rejected"] = int(
                self.diagnostics["candidate16_v6_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_PULLBACK_STOP_GEOMETRY")
            return False
        cap_distance = max(
            self.config.informed_entry_rearm_atr * atr,
            self.config.informed_entry_limit_risk_expansion * structural_risk,
        )
        entry_limit = signal_close + side * cap_distance

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
            self.diagnostics["candidate16_v6_geometry_rejected"] = int(
                self.diagnostics["candidate16_v6_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_INFORMED_FOK_LIMIT_RISK")
            return False

        target_values = self._natural_target(
            entry=entry_limit,
            side=side,
            planned_loss=planned_loss,
            cost_rate=cost_rate,
        )
        if target_values is None:
            self.diagnostics["candidate16_v6_no_natural_target"] = int(
                self.diagnostics["candidate16_v6_no_natural_target"],
            ) + 1
            self._expire_pending(
                row,
                "NO_COST_AWARE_LIQUIDITY_OBJECTIVE_FOR_INFORMED_CONTINUATION",
            )
            return False
        target, target_source, target_r = target_values

        if side > 0 and not (stop < signal_close < entry_limit < target):
            self.diagnostics["candidate16_v6_geometry_rejected"] = int(
                self.diagnostics["candidate16_v6_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_LONG_INFORMED_FOK_BRACKET")
            return False
        if side < 0 and not (target < entry_limit < signal_close < stop):
            self.diagnostics["candidate16_v6_geometry_rejected"] = int(
                self.diagnostics["candidate16_v6_geometry_rejected"],
            ) + 1
            self._expire_pending(row, "INVALID_SHORT_INFORMED_FOK_BRACKET")
            return False

        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
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
            entry_order_type=OrderType.LIMIT,
            entry_price=self.instrument.make_price(entry_limit),
            time_in_force=TimeInForce.FOK,
            entry_post_only=False,
            entry_tags=["ENTRY", "CANDIDATE16_V6_INFORMED_FOK"],
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
        self.diagnostics["candidate16_v6_fok_limit_entries"] = int(
            self.diagnostics["candidate16_v6_fok_limit_entries"],
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
            "NEW_PULLBACK_LEG_WITH_ALL_OR_NONE_PRICE_CAP",
            entry_limit,
            {
                **setup.details,
                "branch": setup.branch,
                "side": side,
                "signal_close": signal_close,
                "entry_limit_worst_fill": entry_limit,
                "entry_time_in_force": "FOK",
                "entry_all_or_none": True,
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


__all__ = ["Candidate16V6Config", "Candidate16V6Strategy"]
