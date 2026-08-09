#!/usr/bin/env python3
"""Candidate 05 v6: liquidation reversal and position-building continuation."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from auction_state_logic import DIRECTIONAL_DEPTH_MIN
from auction_state_logic import liquidation_breakaway_confirmed
from auction_state_logic import position_building_acceptance
from auction_state_logic import reversal_depth_confirmed
from logic import SweepEvidence
from logic import classify_sweep
from logic import confirmation_passes
from logic import cost_aware_target
from logic import floor_quantity
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import displacement_retrace_level
from retrace_logic import pending_limit_invalidated
from retrace_logic import structural_stop
from strategy_base import LiquidityResponseConfig
from strategy_base import PendingSetup
from strategy_base import _as_float
from strategy_v5 import LiquidityResponseBreakawayStrategy


@dataclass(slots=True)
class PositionBuildingSetup:
    scenario_id: str
    side: int
    pool_level: float
    created_index: int
    expires_index: int
    hold_count: int
    details: dict[str, Any]


class AuctionStateTransitionStrategy(LiquidityResponseBreakawayStrategy):
    """Two causal scenarios sharing one global Nautilus position constraint."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.position_building_setups: list[PositionBuildingSetup] = []
        self.diagnostics.update(
            {
                "reversal_depth_persisted": 0,
                "reversal_depth_decayed": 0,
                "retrace_confirmation_armed": 0,
                "retrace_ambiguous_invalidations": 0,
                "position_building_setups": 0,
                "position_building_entries": 0,
                "position_building_invalidations": 0,
                "metrics_not_ready": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        self.bar_index += 1
        row = {
            "ts": int(bar.ts_event),
            "open": _as_float(bar.open),
            "high": _as_float(bar.high),
            "low": _as_float(bar.low),
            "close": _as_float(bar.close),
            "volume": _as_float(bar.volume),
        }
        previous_close = float(self.bars[-1]["close"]) if self.bars else float(row["open"])
        self.bars.append(row)
        self._advance_features(int(row["ts"]))
        self._record_equity(int(row["ts"]))
        self._update_five_minute(row)
        self._prune_pools(row)

        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["max_open_positions_observed"] = 1
            self._manage_open_position(row)
            return
        if self.entry_pending:
            self._manage_pending_entry(row)
            return
        if not self._in_evaluation(int(row["ts"])):
            self.pending = None
            self.position_building_setups.clear()
            return
        if self._funding_blackout(int(row["ts"])):
            self._expire_pending(row, "FUNDING_BLACKOUT")
            self._expire_all_position_building(row, "FUNDING_BLACKOUT")
            return
        if not self._features_ready(int(row["ts"])) or len(self.bars) < self.config.atr_period + 2:
            return

        if self.pending is not None:
            self._process_pending(row)
            if self.entry_pending:
                return
        if self.position_building_setups:
            self._process_position_building(row)
            if self.entry_pending:
                return
        if self.pending is None and self.bar_index - self.last_entry_index >= self.config.cooldown_bars:
            self._detect_sweep(row, previous_close)

    def _metrics_ready(self) -> bool:
        if self.current_feature is None:
            return False
        value = self.current_feature.get("metrics_ready", False)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        high_crossed = [
            pool for pool in self.active_pools.values()
            if pool.kind == "HIGH"
            and self.bar_index - pool.created_index >= self.config.pool_min_age_bars
            and previous_close <= pool.level
            and float(row["high"]) >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool for pool in self.active_pools.values()
            if pool.kind == "LOW"
            and self.bar_index - pool.created_index >= self.config.pool_min_age_bars
            and previous_close >= pool.level
            and float(row["low"]) <= pool.level - self.config.sweep_min_penetration_atr * atr
        ]
        if high_crossed and low_crossed:
            for pool in high_crossed + low_crossed:
                self._consume_pool(pool, row, "AMBIGUOUS_TWO_SIDED_SWEEP")
            return
        if not high_crossed and not low_crossed:
            return

        if high_crossed:
            pool = max(high_crossed, key=lambda item: (item.level, item.strength))
            kind, crossed, direction = "HIGH", high_crossed, 1
        else:
            pool = min(low_crossed, key=lambda item: (item.level, -item.strength))
            kind, crossed, direction = "LOW", low_crossed, -1
        for item in crossed:
            self._consume_pool(item, row, "LIQUIDITY_ACCESSED")
        self.diagnostics["accessed_pools"] += len(crossed)

        evidence = SweepEvidence(
            kind=kind,
            pool_level=pool.level,
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            open=float(row["open"]),
            atr=atr,
            flow_15s=self._feature("flow_15s"),
            flow_60s=self._feature("flow_60s"),
            notional_burst=self._feature("notional_burst"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            bid_depth_change_1m=self._feature("bid_depth_change_1_1m"),
            ask_depth_change_1m=self._feature("ask_depth_change_1_1m"),
        )
        self.scenario_counter += 1
        scenario_id = f"lrr-{self.scenario_counter:07d}"
        pool_age = self.bar_index - pool.created_index
        accepted_distance = direction * (float(row["close"]) - pool.level) / atr
        same_side_depth_change = (
            evidence.ask_depth_change_1m if kind == "HIGH" else evidence.bid_depth_change_1m
        )
        details = {
            "pool_id": pool.pool_id,
            "pool_kind": kind,
            "pool_level": pool.level,
            "pool_source": pool.source,
            "pool_strength": pool.strength,
            "pool_age_minutes": pool_age,
            "penetration_atr": evidence.penetration_atr,
            "accepted_distance_atr": accepted_distance,
            "flow_15s": evidence.flow_15s,
            "flow_60s": evidence.flow_60s,
            "flow_3m": self._feature("flow_3m"),
            "notional_burst": evidence.notional_burst,
            "efficiency_60s": evidence.efficiency_60s,
            "absorption_60s": self._feature("absorption_60s"),
            "depth_imbalance_1": evidence.depth_imbalance_1,
            "bid_depth_change_1m": evidence.bid_depth_change_1m,
            "ask_depth_change_1m": evidence.ask_depth_change_1m,
            "sum_open_interest": self._feature("sum_open_interest"),
            "oi_change_15m": self._feature("oi_change_15m"),
        }

        if self._metrics_ready() and position_building_acceptance(
            accepted_distance_atr=accepted_distance,
            directional_flow_15s=direction * evidence.flow_15s,
            directional_flow_60s=direction * evidence.flow_60s,
            efficiency_60s=evidence.efficiency_60s,
            consumed_side_depth_change=same_side_depth_change,
            oi_change_15m=self._feature("oi_change_15m"),
        ):
            setup = PositionBuildingSetup(
                scenario_id=scenario_id,
                side=direction,
                pool_level=pool.level,
                created_index=self.bar_index,
                expires_index=self.bar_index + self.config.acceptance_retrace_bars,
                hold_count=0,
                details=details,
            )
            self.position_building_setups.append(setup)
            self.diagnostics["position_building_setups"] += 1
            self._transition(
                scenario_id,
                "POSITION_BUILDING_ACCEPTED",
                int(row["ts"]),
                int(row["ts"]),
                "OUTSIDE_HOLD_PENDING",
                "OPEN_INTEREST_EXPANDED_THROUGH_WITHDRAWN_LIQUIDITY",
                float(row["close"]),
                details,
            )
            return

        branch = classify_sweep(evidence, self.thresholds)
        if branch != "REJECTION":
            if not self._metrics_ready():
                self.diagnostics["metrics_not_ready"] += 1
            self.diagnostics["unresolved_sweeps"] += 1
            self._transition(
                scenario_id,
                "SWEEP_UNRESOLVED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "NO_COHERENT_EXHAUSTION_OR_POSITION_BUILDING_STATE",
                float(row["close"]),
                details,
            )
            return

        side = -direction
        if side * evidence.depth_imbalance_1 < DIRECTIONAL_DEPTH_MIN:
            self.diagnostics["directional_depth_fail"] += 1
            self._transition(
                scenario_id,
                "REJECTION_DEPTH_UNSUPPORTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                "RESTING_DEPTH_DID_NOT_SUPPORT_REVERSAL_DIRECTION",
                float(row["close"]),
                details,
            )
            return
        self.diagnostics["directional_depth_pass"] += 1

        rows = list(self.bars)
        pre = rows[-(self.config.structure_lookback_bars + 1) : -1]
        structure = max(float(item["high"]) for item in pre) if side > 0 else min(float(item["low"]) for item in pre)
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="REJECTION",
            side=side,
            swept_kind=kind,
            pool_id=pool.pool_id,
            pool_level=pool.level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            sweep_extreme=float(row["high"]) if kind == "HIGH" else float(row["low"]),
            structure=structure,
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["rejection_setups"] += 1
        self._transition(
            scenario_id,
            "EXHAUSTION_REJECTION_CLASSIFIED",
            int(row["ts"]),
            int(row["ts"]),
            "CHOCH_ARMED",
            "AGGRESSIVE_FLOW_ABSORBED_POOL_RECLAIMED_AND_DEPTH_SUPPORTED",
            float(row["close"]),
            details,
        )

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if setup.branch == "REJECTION_RETRACE_CONFIRM":
            return self._process_retrace_confirmation(setup, row)
        if self.bar_index > setup.expires_index:
            self._expire_pending(row, "CHOCH_WINDOW_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True

        stop = structural_stop(
            setup.sweep_extreme,
            setup.side,
            setup.atr,
            self.config.stop_buffer_atr,
        )
        if pending_limit_invalidated(
            side=setup.side,
            stop=stop,
            high=float(row["high"]),
            low=float(row["low"]),
        ):
            self._expire_pending(row, "SWEEP_EXTREME_INVALIDATED_BEFORE_CHOCH")
            return False

        passed = confirmation_passes(
            side=setup.side,
            open_price=float(row["open"]),
            close_price=float(row["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            structure=setup.structure,
            atr=self._atr(),
            flow_60s=self._feature("flow_60s"),
            efficiency_60s=self._feature("efficiency_60s"),
            min_body_atr=self.config.rejection_confirm_body_atr,
            min_flow=self.config.rejection_confirm_flow_min,
            min_efficiency=self.config.rejection_confirm_efficiency_min,
            min_close_location=self.config.rejection_confirm_close_location,
        )
        if not passed:
            return True

        self.diagnostics["displacement_confirmations"] += 1
        current_imbalance = self._feature("depth_imbalance_1")
        sweep_imbalance = float(setup.details["depth_imbalance_1"])
        sweep_oi = float(setup.details.get("sum_open_interest", float("nan")))
        current_oi = self._feature("sum_open_interest")
        oi_change = (
            current_oi / sweep_oi - 1.0
            if math.isfinite(sweep_oi) and sweep_oi > 0.0 and math.isfinite(current_oi)
            else float("nan")
        )
        setup.details.update(
            {
                "confirmation_close": float(row["close"]),
                "confirmation_delay_bars": self.bar_index - setup.created_index,
                "confirmation_depth_imbalance_1": current_imbalance,
                "oi_change_sweep_to_confirmation": oi_change,
            },
        )

        if liquidation_breakaway_confirmed(
            side=setup.side,
            sweep_imbalance=sweep_imbalance,
            current_imbalance=current_imbalance,
            oi_change_sweep_to_confirmation=oi_change,
        ):
            self.pending = None
            return self._submit_breakaway_entry(setup, row, sweep_imbalance)

        if not reversal_depth_confirmed(
            side=setup.side,
            sweep_imbalance=sweep_imbalance,
            current_imbalance=current_imbalance,
            pool_age_minutes=int(setup.details["pool_age_minutes"]),
        ):
            self.diagnostics["reversal_depth_decayed"] += 1
            self._expire_pending(row, "REVERSAL_DEPTH_DECAYED_BEFORE_CHOCH")
            return False

        self.diagnostics["reversal_depth_persisted"] += 1
        setup.branch = "REJECTION_RETRACE_CONFIRM"
        setup.created_index = self.bar_index
        setup.expires_index = self.bar_index + self.config.acceptance_retrace_bars
        setup.details["retrace_level"] = displacement_retrace_level(
            setup.sweep_extreme,
            float(row["close"]),
        )
        setup.details["retrace_stop"] = structural_stop(
            setup.sweep_extreme,
            setup.side,
            self._atr(),
            self.config.stop_buffer_atr,
        )
        self.diagnostics["retrace_confirmation_armed"] += 1
        self._transition(
            setup.scenario_id,
            "CHOCH_AND_DEPTH_CONFIRMED",
            int(row["ts"]),
            int(row["ts"]),
            "RETRACE_CONFIRMATION_PENDING",
            "DEPTH_SURVIVED_TO_CHOCH_WAIT_FOR_CAUSAL_RETRACE_RECLAIM",
            float(row["close"]),
            setup.details,
        )
        return True

    def _process_retrace_confirmation(
        self,
        setup: PendingSetup,
        row: dict[str, float | int],
    ) -> bool:
        if self.bar_index > setup.expires_index:
            self._expire_pending(row, "CONFIRMED_RETRACE_WINDOW_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True
        level = float(setup.details["retrace_level"])
        stop = float(setup.details["retrace_stop"])
        touched = float(row["low"]) <= level if setup.side > 0 else float(row["high"]) >= level
        stopped = float(row["low"]) <= stop if setup.side > 0 else float(row["high"]) >= stop
        if stopped:
            if touched:
                self.diagnostics["retrace_ambiguous_invalidations"] += 1
            self._expire_pending(row, "STOP_TOUCHED_BEFORE_OR_WITH_RETRACE_CONFIRMATION")
            return False
        if not touched:
            return True
        span = max(float(row["high"]) - float(row["low"]), 1e-12)
        close_location = (
            (float(row["close"]) - float(row["low"])) / span
            if setup.side > 0
            else (float(row["high"]) - float(row["close"])) / span
        )
        confirmed = (
            setup.side * (float(row["close"]) - level) > 0.0
            and setup.side * (float(row["close"]) - float(row["open"])) > 0.0
            and setup.side * self._feature("flow_15s") >= -self.config.acceptance_max_counterflow
            and setup.side * self._feature("depth_imbalance_1") > 0.0
            and close_location >= self.config.rejection_confirm_close_location
        )
        if not confirmed:
            return True
        self.pending = None
        return self._submit_market_bracket(
            setup=setup,
            row=row,
            stop=stop,
            branch="REJECTION_RETRACE_CONFIRMED",
            reason="COMPLETED_RETRACE_BAR_RECLAIMED_ENTRY_WITHOUT_TOUCHING_STOP",
            target_net_r=self.config.rejection_target_net_r,
        )

    def _process_position_building(self, row: dict[str, float | int]) -> None:
        remaining: list[PositionBuildingSetup] = []
        for setup in self.position_building_setups:
            atr = self._atr()
            if self.bar_index > setup.expires_index:
                self._expire_position_building(setup, row, "POSITION_BUILDING_RETEST_WINDOW_EXPIRED")
                continue
            outside = setup.side * (float(row["close"]) - setup.pool_level) / atr
            if outside < -self.config.acceptance_hold_tolerance_atr:
                self._expire_position_building(setup, row, "ACCEPTED_POOL_REENTERED")
                continue
            if outside > 0.0:
                setup.hold_count += 1
            else:
                setup.hold_count = 0
            if setup.hold_count < self.config.acceptance_min_hold_bars:
                remaining.append(setup)
                continue

            touched = (
                float(row["low"]) <= setup.pool_level + self.config.acceptance_retrace_tolerance_atr * atr
                if setup.side > 0
                else float(row["high"]) >= setup.pool_level - self.config.acceptance_retrace_tolerance_atr * atr
            )
            closed_outside = float(row["close"]) > setup.pool_level if setup.side > 0 else float(row["close"]) < setup.pool_level
            span = max(float(row["high"]) - float(row["low"]), 1e-12)
            close_location = (
                (float(row["close"]) - float(row["low"])) / span
                if setup.side > 0
                else (float(row["high"]) - float(row["close"])) / span
            )
            if not (
                touched
                and closed_outside
                and setup.side * self._feature("flow_15s") >= -self.config.acceptance_max_counterflow
                and close_location >= self.config.acceptance_retest_close_location
            ):
                remaining.append(setup)
                continue

            stop = (
                min(
                    setup.pool_level - self.config.stop_buffer_atr * atr,
                    float(row["low"]) - 0.25 * self.config.stop_buffer_atr * atr,
                )
                if setup.side > 0
                else max(
                    setup.pool_level + self.config.stop_buffer_atr * atr,
                    float(row["high"]) + 0.25 * self.config.stop_buffer_atr * atr,
                )
            )
            pseudo = PendingSetup(
                scenario_id=setup.scenario_id,
                branch="POSITION_BUILDING_CONTINUATION",
                side=setup.side,
                swept_kind="HIGH" if setup.side > 0 else "LOW",
                pool_id=str(setup.details["pool_id"]),
                pool_level=setup.pool_level,
                created_index=setup.created_index,
                expires_index=setup.expires_index,
                sweep_extreme=setup.pool_level,
                structure=setup.pool_level,
                atr=atr,
                hold_count=setup.hold_count,
                retrace_armed=True,
                details={**setup.details, "hold_count": setup.hold_count},
            )
            self.position_building_setups = []
            self.diagnostics["position_building_entries"] += 1
            self._submit_market_bracket(
                setup=pseudo,
                row=row,
                stop=stop,
                branch="POSITION_BUILDING_CONTINUATION",
                reason="OUTSIDE_HOLD_AND_FIRST_POOL_RETEST_CONFIRMED",
                target_net_r=self.config.acceptance_target_net_r,
            )
            return
        self.position_building_setups = remaining

    def _submit_market_bracket(
        self,
        *,
        setup: PendingSetup,
        row: dict[str, float | int],
        stop: float,
        branch: str,
        reason: str,
        target_net_r: float,
    ) -> bool:
        side = setup.side
        entry_price = self.instrument.make_price(float(row["close"]))
        stop_price = self.instrument.make_price(stop)
        entry, stop_value = _as_float(entry_price), _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(
            entry,
            stop_value,
            side,
            cost_rate,
            self.config.adverse_slippage_bps_each_side / 10_000.0,
        )
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            return False
        target_price = self.instrument.make_price(
            cost_aware_target(entry, side, planned_loss, target_net_r, cost_rate),
        )
        target = _as_float(target_price)
        if (side > 0 and not stop_value < entry < target) or (side < 0 and not target < entry < stop_value):
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(
            risk_budget / planned_loss,
            int(self.instrument.size_precision),
        )
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            return False
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = self.bar_index + 2
        self.entry_side, self.entry_stop, self.entry_limit = side, stop_value, entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = branch
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = 1
        self._transition(
            setup.scenario_id,
            "MARKET_BRACKET_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            reason,
            entry,
            {
                **setup.details,
                "branch": branch,
                "side": side,
                "expected_entry": entry,
                "stop": stop_value,
                "target": target,
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
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True

    def _expire_position_building(
        self,
        setup: PositionBuildingSetup,
        row: dict[str, float | int],
        reason: str,
    ) -> None:
        self.diagnostics["position_building_invalidations"] += 1
        self._transition(
            setup.scenario_id,
            "POSITION_BUILDING_INVALIDATED",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            setup.details,
        )

    def _expire_all_position_building(self, row: dict[str, float | int], reason: str) -> None:
        for setup in self.position_building_setups:
            self._expire_position_building(setup, row, reason)
        self.position_building_setups.clear()

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        self.exit_pending = False
        self.position_open_index = self.bar_index
        if self.current_scenario_id is not None:
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            self._transition(
                self.current_scenario_id,
                "POSITION_OPENED",
                ts,
                ts,
                "POSITION_OPEN",
                "NAUTILUS_MARKET_ENTRY_FILLED",
                float(self.bars[-1]["close"]),
                {"event": str(event), "expected_entry": self.entry_limit},
            )

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.position_building_setups.clear()


__all__ = ["AuctionStateTransitionStrategy"]
