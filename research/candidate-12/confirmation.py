"""Scenario confirmation, costed target selection, and plan transitions."""
from __future__ import annotations

from typing import Any

from model import BarObs, ConfirmationState, Direction, LiquidityPool, ScenarioKind, Side, TradePlan


class ScenarioConfirmationMixin:
        def _target_for(self, direction: Direction, entry: float, excluded_pool_id: str, atr: float) -> LiquidityPool | None:
            if direction is Direction.LONG:
                candidates = [
                    pool for pool in self._pools
                    if pool.active and pool.pool_id != excluded_pool_id and pool.side is Side.HIGH and pool.price > entry
                ]
                candidates.sort(key=lambda pool: (pool.price, -pool.observed_time_ns))
            else:
                candidates = [
                    pool for pool in self._pools
                    if pool.active and pool.pool_id != excluded_pool_id and pool.side is Side.LOW and pool.price < entry
                ]
                candidates.sort(key=lambda pool: (-pool.price, -pool.observed_time_ns))
            for pool in candidates:
                if abs(pool.price - entry) <= self.config.max_target_atr * atr:
                    return pool
            return None

        def _costed_plan(
            self,
            *,
            state: ConfirmationState,
            pool: LiquidityPool,
            bar: BarObs,
            atr: float,
            stop_anchor: float,
        ) -> TradePlan | None:
            entry = bar.close
            buffer = self.config.stop_buffer_atr * atr
            if state.direction is Direction.LONG:
                stop = stop_anchor - buffer
                raw_distance = entry - stop
                if raw_distance < self.config.min_stop_atr * atr:
                    stop = entry - self.config.min_stop_atr * atr
                target_pool = self._target_for(Direction.LONG, entry, pool.pool_id, atr)
            else:
                stop = stop_anchor + buffer
                raw_distance = stop - entry
                if raw_distance < self.config.min_stop_atr * atr:
                    stop = entry + self.config.min_stop_atr * atr
                target_pool = self._target_for(Direction.SHORT, entry, pool.pool_id, atr)
            stop_distance = abs(entry - stop)
            if stop_distance > self.config.max_stop_atr * atr:
                self.skips["STOP_TOO_WIDE"] += 1
                return None
            if stop <= 0 or target_pool is None:
                self.skips["NO_LIVE_STRUCTURAL_TARGET"] += 1
                return None
            target = target_pool.price
            if state.direction is Direction.LONG and target <= entry:
                self.skips["TARGET_WRONG_SIDE"] += 1
                return None
            if state.direction is Direction.SHORT and target >= entry:
                self.skips["TARGET_WRONG_SIDE"] += 1
                return None

            entry_cost = entry * self.config.effective_taker_rate
            stop_cost = stop * self.config.effective_taker_rate
            target_cost = target * self.config.effective_maker_rate
            slippage_allowance = self.config.tick_slippage_units * self.config.price_increment
            loss_per_unit = stop_distance + entry_cost + stop_cost + slippage_allowance
            expected_profit = abs(target - entry) - entry_cost - target_cost - slippage_allowance
            if expected_profit <= 0 or loss_per_unit <= 0:
                self.skips["NON_POSITIVE_COSTED_EXPECTANCY"] += 1
                return None
            net_r = expected_profit / loss_per_unit
            if net_r < self.config.min_net_r:
                self.skips["INSUFFICIENT_COSTED_STRUCTURAL_R"] += 1
                return None
            return TradePlan(
                scenario_id=state.scenario_id,
                scenario=state.kind,
                direction=state.direction,
                observed_ts_ns=bar.ts_ns,
                expected_entry=entry,
                stop_price=stop,
                target_price=target,
                loss_per_unit=loss_per_unit,
                expected_profit_per_unit=expected_profit,
                net_r=net_r,
                details={
                    "source_pool_id": pool.pool_id,
                    "source_pool_source": pool.source,
                    "target_pool_id": target_pool.pool_id,
                    "target_pool_source": target_pool.source,
                    "atr": atr,
                    "entry_flow": bar.signed_flow,
                    "stop_distance": stop_distance,
                    "entry_cost_per_unit": entry_cost,
                    "stop_cost_per_unit": stop_cost,
                    "target_cost_per_unit": target_cost,
                    "slippage_allowance_per_unit": slippage_allowance,
                },
            )

        def _advance_confirmation(self, bar: BarObs, atr: float, allow_entry: bool) -> TradePlan | None:
            state = self._confirmation
            if state is None:
                return None
            pool = self._pool_by_id(state.pool_id)
            if pool is None or not pool.active:
                self._terminate_confirmation(bar.ts_ns, "POOL_NOT_LIVE")
                return None
            age = self._bar_index - state.started_index
            if age > self.config.confirmation_expiry_bars:
                self._terminate_confirmation(bar.ts_ns, "CONFIRMATION_WINDOW_EXPIRED")
                return None

            plan: TradePlan | None = None
            if state.kind is ScenarioKind.REJECTION:
                structure = state.structure_level
                flow_floor = self._flow_threshold(self.config.mss_flow_min)
                if state.direction is Direction.SHORT:
                    confirmed = (
                        structure is not None
                        and bar.close < structure
                        and bar.close < bar.open
                        and bar.body >= self.config.mss_body_atr * atr
                        and bar.signed_flow <= -flow_floor
                        and bar.close_location <= 1.0 - self.config.mss_close_location
                    )
                    stop_anchor = state.trigger_extreme
                else:
                    confirmed = (
                        structure is not None
                        and bar.close > structure
                        and bar.close > bar.open
                        and bar.body >= self.config.mss_body_atr * atr
                        and bar.signed_flow >= flow_floor
                        and bar.close_location >= self.config.mss_close_location
                    )
                    stop_anchor = state.trigger_extreme
                if confirmed:
                    self._emit(
                        scenario_id=state.scenario_id,
                        event_type="MARKET_STRUCTURE_SHIFT_CONFIRMED",
                        event_time_ns=bar.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        next_state="ENTRY_READY",
                        reason_code="INTERNAL_PIVOT_BROKEN_WITH_DISPLACEMENT_AND_FLOW",
                        reference_price=structure,
                        details={"direction": state.direction.value, "flow": bar.signed_flow},
                    )
                    plan = self._costed_plan(state=state, pool=pool, bar=bar, atr=atr, stop_anchor=stop_anchor)

            else:
                flow_floor = self._flow_threshold(self.config.reacceleration_flow_min)
                tolerance = self.config.retest_tolerance_atr * atr
                hold = self.config.retest_hold_atr * atr
                if state.direction is Direction.LONG:
                    touched = bar.low <= pool.price + tolerance
                    held = bar.close >= pool.price + hold
                    reaccelerated = (
                        bar.close > bar.open
                        and bar.body >= self.config.reacceleration_body_atr * atr
                        and bar.signed_flow >= flow_floor
                        and bar.close_location >= self.config.acceptance_close_location
                    )
                    stop_anchor = min(bar.low, pool.price - self.config.stop_buffer_atr * atr)
                else:
                    touched = bar.high >= pool.price - tolerance
                    held = bar.close <= pool.price - hold
                    reaccelerated = (
                        bar.close < bar.open
                        and bar.body >= self.config.reacceleration_body_atr * atr
                        and bar.signed_flow <= -flow_floor
                        and bar.close_location <= 1.0 - self.config.acceptance_close_location
                    )
                    stop_anchor = max(bar.high, pool.price + self.config.stop_buffer_atr * atr)
                state.retest_seen = state.retest_seen or touched
                if state.retest_seen and held and reaccelerated:
                    self._emit(
                        scenario_id=state.scenario_id,
                        event_type="ACCEPTED_BOUNDARY_RETEST_CONFIRMED",
                        event_time_ns=bar.ts_ns,
                        observed_time_ns=bar.ts_ns,
                        next_state="ENTRY_READY",
                        reason_code="BOUNDARY_HELD_AND_AGGRESSOR_FLOW_REACCELERATED",
                        reference_price=pool.price,
                        details={"direction": state.direction.value, "flow": bar.signed_flow},
                    )
                    plan = self._costed_plan(state=state, pool=pool, bar=bar, atr=atr, stop_anchor=stop_anchor)

            if plan is None:
                # If a valid confirmation occurred but structural R failed, the
                # event chain is terminal rather than silently recycling it.
                if self._states.get(state.scenario_id) == "ENTRY_READY":
                    self._terminate_confirmation(bar.ts_ns, "COSTED_PLAN_REJECTED")
                return None
            if not allow_entry:
                self.skips["GLOBAL_SLOT_OCCUPIED"] += 1
                self._emit(
                    scenario_id=state.scenario_id,
                    event_type="TRADE_PLAN_REJECTED",
                    event_time_ns=bar.ts_ns,
                    observed_time_ns=bar.ts_ns,
                    next_state="TERMINAL",
                    reason_code="GLOBAL_SLOT_OCCUPIED",
                    reference_price=plan.expected_entry,
                    details={"net_r": plan.net_r},
                )
                self._confirmation = None
                return None

            self._emit(
                scenario_id=state.scenario_id,
                event_type="TRADE_PLAN_EMITTED",
                event_time_ns=bar.ts_ns,
                observed_time_ns=bar.ts_ns,
                next_state="PLAN_EMITTED",
                reason_code="CAUSAL_SCENARIO_AND_COSTED_TARGET_VALID",
                reference_price=plan.expected_entry,
                details={
                    "kind": plan.scenario.value,
                    "direction": plan.direction.value,
                    "entry": plan.expected_entry,
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "loss_per_unit": plan.loss_per_unit,
                    "expected_profit_per_unit": plan.expected_profit_per_unit,
                    "net_r": plan.net_r,
                },
            )
            self._deactivate_pool(pool, bar.ts_ns, "CONSUMED_BY_EXECUTABLE_SCENARIO")
            self._confirmation = None
            return plan

        def _terminate_confirmation(self, ts_ns: int, reason: str) -> None:
            state = self._confirmation
            if state is None:
                return
            self.skips[reason] += 1
            self._emit(
                scenario_id=state.scenario_id,
                event_type="SCENARIO_INVALIDATED",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                next_state="TERMINAL",
                reason_code=reason,
                details={"kind": state.kind.value, "pool_id": state.pool_id},
            )
            self._confirmation = None

        def mark_plan_rejected(self, plan: TradePlan, ts_ns: int, reason: str, details: dict[str, Any] | None = None) -> None:
            self.skips[reason] += 1
            self._emit(
                scenario_id=plan.scenario_id,
                event_type="TRADE_PLAN_REJECTED",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=plan.expected_entry,
                details=details or {},
            )

        def mark_plan_submitted(self, plan: TradePlan, ts_ns: int, details: dict[str, Any]) -> None:
            self._emit(
                scenario_id=plan.scenario_id,
                event_type="TRADE_PLAN_SUBMITTED",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                next_state="SUBMITTED",
                reason_code="NAUTILUS_ORDER_LIST_SUBMITTED",
                reference_price=plan.expected_entry,
                details=details,
            )

        def mark_trade_terminal(
            self,
            plan: TradePlan,
            ts_ns: int,
            reason: str,
            details: dict[str, Any] | None = None,
        ) -> None:
            self._emit(
                scenario_id=plan.scenario_id,
                event_type="TRADE_TERMINAL",
                event_time_ns=ts_ns,
                observed_time_ns=ts_ns,
                next_state="TERMINAL",
                reason_code=reason,
                reference_price=plan.expected_entry,
                details=details or {},
            )
