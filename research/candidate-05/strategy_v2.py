#!/usr/bin/env python3
"""Candidate 05 v2: confirmed 5m liquidity rejection and first retrace."""
from __future__ import annotations

import csv
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, TimeInForce

from logic import SweepEvidence
from logic import classify_sweep
from logic import confirmation_passes
from logic import cost_aware_target
from logic import floor_quantity
from logic import is_confirmed_pivot
from logic import net_r_at_price
from logic import planned_loss_per_unit
from retrace_logic import aggregate_completed_bar
from retrace_logic import displacement_retrace_level
from retrace_logic import pending_limit_invalidated
from retrace_logic import structural_stop
from smc_ict_4.event_log import write_events
from strategy_base import LiquidityResponseConfig
from strategy_base import LiquidityResponseStrategy
from strategy_base import PendingSetup
from strategy_base import _as_float


MINUTE_NS = 60_000_000_000


class LiquidityResponseRetraceStrategy(LiquidityResponseStrategy):
    """Reject a 5m liquidity raid, confirm CHoCH, rest at its 50% retrace."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        if config.pivot_span != 2:
            raise ValueError("v2 requires a completed 5m pivot span of two")
        if config.enable_acceptance:
            raise ValueError("v2 disables the failed acceptance branch")
        self.five_bars: deque[dict[str, float | int]] = deque(maxlen=1_500)
        self.five_rows: list[dict[str, float | int]] = []
        self.five_bucket: int | None = None
        self.exit_pending = False
        self.entry_expires_index = -1
        self.entry_side = 0
        self.entry_stop = float("nan")
        self.entry_limit = float("nan")
        self.order_rejection_events: list[dict[str, Any]] = []
        self.diagnostics.update(
            {
                "order_denials": 0,
                "five_minute_bars": 0,
                "incomplete_five_minute_buckets": 0,
                "five_minute_pools": 0,
                "accessed_pools": 0,
                "acceptance_observations_discarded": 0,
                "displacement_confirmations": 0,
                "entry_invalidated_unfilled": 0,
                "entry_expired_unfilled": 0,
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
            return
        if self._funding_blackout(int(row["ts"])):
            self._expire_pending(row, "FUNDING_BLACKOUT")
            return
        if not self._features_ready(int(row["ts"])) or len(self.bars) < self.config.atr_period + 2:
            return
        if self.pending is not None and self._process_pending(row):
            return
        if self.pending is None and self.bar_index - self.last_entry_index >= self.config.cooldown_bars:
            self._detect_sweep(row, previous_close)

    def _update_five_minute(self, row: dict[str, float | int]) -> None:
        minute = int(row["ts"]) // MINUTE_NS
        bucket = minute // 5
        if self.five_bucket is None:
            self.five_bucket = bucket
        elif bucket != self.five_bucket:
            if self.five_rows:
                self.diagnostics["incomplete_five_minute_buckets"] += 1
            self.five_rows = []
            self.five_bucket = bucket
        self.five_rows.append(row.copy())
        if minute % 5 != 4:
            return
        if len(self.five_rows) != 5:
            self.diagnostics["incomplete_five_minute_buckets"] += 1
        else:
            self.five_bars.append(aggregate_completed_bar(self.five_rows))
            self.diagnostics["five_minute_bars"] += 1
            self._confirm_five_pivot(int(row["ts"]))
        self.five_rows = []
        self.five_bucket = None

    def _confirm_five_pivot(self, observed_ns: int) -> None:
        span = self.config.pivot_span
        rows = list(self.five_bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_pool("HIGH", float(center["high"]), int(center["ts"]), observed_ns, "CONFIRMED_5M_SWING", strength=1)
            self.diagnostics["five_minute_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_pool("LOW", float(center["low"]), int(center["ts"]), observed_ns, "CONFIRMED_5M_SWING", strength=1)
            self.diagnostics["five_minute_pools"] += 1

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
            kind, crossed = "HIGH", high_crossed
        else:
            pool = min(low_crossed, key=lambda item: (item.level, -item.strength))
            kind, crossed = "LOW", low_crossed
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
        branch = classify_sweep(evidence, self.thresholds)
        self.scenario_counter += 1
        scenario_id = f"lrr-{self.scenario_counter:07d}"
        details = {
            "pool_id": pool.pool_id,
            "pool_kind": kind,
            "pool_level": pool.level,
            "pool_source": pool.source,
            "pool_strength": pool.strength,
            "pool_age_minutes": self.bar_index - pool.created_index,
            "penetration_atr": evidence.penetration_atr,
            "flow_15s": evidence.flow_15s,
            "flow_60s": evidence.flow_60s,
            "flow_3m": self._feature("flow_3m"),
            "notional_burst": evidence.notional_burst,
            "efficiency_60s": evidence.efficiency_60s,
            "absorption_60s": self._feature("absorption_60s"),
            "depth_imbalance_1": evidence.depth_imbalance_1,
            "bid_depth_change_1m": evidence.bid_depth_change_1m,
            "ask_depth_change_1m": evidence.ask_depth_change_1m,
        }
        if branch is None:
            self.diagnostics["unresolved_sweeps"] += 1
            self._transition(scenario_id, "SWEEP_UNRESOLVED", int(row["ts"]), int(row["ts"]), "CLOSED", "PRICE_AND_LIQUIDITY_RESPONSE_NOT_COHERENT", float(row["close"]), details)
            return
        if branch == "ACCEPTANCE":
            self.diagnostics["acceptance_observations_discarded"] += 1
            self._transition(scenario_id, "ACCEPTANCE_DISCARDED", int(row["ts"]), int(row["ts"]), "CLOSED", "ACCEPTANCE_FAILED_FIRST_WEEK_CAUSAL_DIRECTION", float(row["close"]), details)
            return

        side = -1 if kind == "HIGH" else 1
        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="REJECTION_RETRACE",
            side=side,
            swept_kind=kind,
            pool_id=pool.pool_id,
            pool_level=pool.level,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            sweep_extreme=float(row["high"]) if kind == "HIGH" else float(row["low"]),
            structure=float(row["low"]) if side < 0 else float(row["high"]),
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.diagnostics["rejection_setups"] += 1
        self._transition(scenario_id, "REJECTION_CLASSIFIED", int(row["ts"]), int(row["ts"]), "CHOCH_ARMED", "ABSORPTION_AT_CONFIRMED_5M_LIQUIDITY", float(row["close"]), details)

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        setup = self.pending
        if setup is None:
            return False
        if self.bar_index > setup.expires_index:
            self._expire_pending(row, "CHOCH_WINDOW_EXPIRED")
            return False
        if self.bar_index <= setup.created_index:
            return True
        stop = structural_stop(setup.sweep_extreme, setup.side, setup.atr, self.config.stop_buffer_atr)
        if pending_limit_invalidated(side=setup.side, stop=stop, high=float(row["high"]), low=float(row["low"])):
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
        self._transition(setup.scenario_id, "CHOCH_CONFIRMED", int(row["ts"]), int(row["ts"]), "RETRACE_ENTRY_PENDING", "OPPOSITE_DISPLACEMENT_BROKE_SWEEP_BAR", float(row["close"]), {**setup.details, "confirmation_close": float(row["close"]), "confirmation_delay_bars": self.bar_index - setup.created_index})
        return self._submit_entry(setup, row)

    def _submit_entry(self, setup: PendingSetup, row: dict[str, float | int]) -> bool:
        side = setup.side
        atr = self._atr()
        entry_price = self.instrument.make_price(displacement_retrace_level(setup.sweep_extreme, float(row["close"])))
        stop_price = self.instrument.make_price(structural_stop(setup.sweep_extreme, side, atr, self.config.stop_buffer_atr))
        entry, stop = _as_float(entry_price), _as_float(stop_price)
        cost_rate = self.config.all_in_cost_bps_each_side / 10_000.0
        planned_loss = planned_loss_per_unit(entry, stop, side, cost_rate, self.config.adverse_slippage_bps_each_side / 10_000.0)
        if not math.isfinite(planned_loss) or planned_loss <= 0.0:
            self._expire_pending(row, "INVALID_RETRACE_STOP_GEOMETRY")
            return False
        target_price = self.instrument.make_price(cost_aware_target(entry, side, planned_loss, self.config.rejection_target_net_r, cost_rate))
        target = _as_float(target_price)
        if (side > 0 and not stop < entry < target) or (side < 0 and not target < entry < stop):
            self._expire_pending(row, "INVALID_RETRACE_BRACKET")
            return False
        equity = self._equity_value()
        risk_budget = equity * self.config.risk_fraction
        quantity_value = floor_quantity(risk_budget / planned_loss, int(self.instrument.size_precision))
        if quantity_value <= 0.0 or quantity_value * entry < 10.0:
            self._expire_pending(row, "QUANTITY_BELOW_INSTRUMENT_MINIMUM")
            return False
        order_list = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity_value),
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            time_in_force=TimeInForce.GTC,
            entry_post_only=False,
            tp_price=target_price,
            tp_post_only=False,
            sl_trigger_price=stop_price,
        )
        self.submit_order_list(order_list)
        self.entry_pending = True
        self.entry_pending_index = self.bar_index
        self.entry_expires_index = self.bar_index + self.config.acceptance_retrace_bars
        self.entry_side, self.entry_stop, self.entry_limit = side, stop, entry
        self.last_entry_index = self.bar_index
        self.current_scenario_id = setup.scenario_id
        self.current_branch = "REJECTION_RETRACE"
        self.current_pool_level = setup.pool_level
        self.pending = None
        self.diagnostics["entry_submissions"] += 1
        self.diagnostics["max_simultaneous_entry_intents"] = 1
        self._transition(
            setup.scenario_id,
            "LIMIT_BRACKET_SUBMITTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_PENDING",
            "FIRST_FIFTY_PERCENT_DISPLACEMENT_RETRACE",
            entry,
            {
                **setup.details,
                "side": side,
                "sweep_extreme": setup.sweep_extreme,
                "confirmation_close": float(row["close"]),
                "entry_limit": entry,
                "stop": stop,
                "target": target,
                "target_net_r_after_rounding": net_r_at_price(entry, target, side, planned_loss, cost_rate),
                "quantity": quantity_value,
                "equity": equity,
                "risk_budget": risk_budget,
                "planned_loss_per_unit": planned_loss,
                "planned_account_loss": quantity_value * planned_loss,
            },
        )
        return True

    def _manage_pending_entry(self, row: dict[str, float | int]) -> None:
        reason: str | None = None
        if pending_limit_invalidated(side=self.entry_side, stop=self.entry_stop, high=float(row["high"]), low=float(row["low"])):
            reason = "STRUCTURAL_STOP_REACHED_BEFORE_LIMIT_FILL"
            self.diagnostics["entry_invalidated_unfilled"] += 1
        elif self.bar_index > self.entry_expires_index:
            reason = "FIRST_RETRACE_WINDOW_EXPIRED_UNFILLED"
            self.diagnostics["entry_expired_unfilled"] += 1
        elif self._funding_blackout(int(row["ts"])):
            reason = "FUNDING_BLACKOUT_BEFORE_LIMIT_FILL"
        elif not self._in_evaluation(int(row["ts"])):
            reason = "EVALUATION_ENDED_BEFORE_LIMIT_FILL"
        if reason is None:
            return
        self.cancel_all_orders(self.config.instrument_id)
        if self.current_scenario_id is not None:
            self._transition(self.current_scenario_id, "UNFILLED_ENTRY_CANCELED", int(row["ts"]), int(row["ts"]), "CLOSED", reason, float(row["close"]), {"entry_limit": self.entry_limit, "stop": self.entry_stop, "bars_resting": self.bar_index - self.entry_pending_index})
        self._clear_trade_state()

    def on_position_opened(self, event: Any) -> None:
        self.entry_pending = False
        self.exit_pending = False
        self.position_open_index = self.bar_index
        if self.current_scenario_id is not None:
            ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
            self._transition(self.current_scenario_id, "POSITION_OPENED", ts, ts, "POSITION_OPEN", "NAUTILUS_LIMIT_ENTRY_FILLED", float(self.bars[-1]["close"]), {"event": str(event), "entry_limit": self.entry_limit})

    def _manage_open_position(self, row: dict[str, float | int]) -> None:
        if self.exit_pending:
            return
        moment = datetime.fromtimestamp(int(row["ts"]) / 1_000_000_000, tz=timezone.utc)
        before_funding = moment.hour in (7, 15, 23) and moment.minute >= self.config.funding_flatten_minute
        timed_out = self.position_open_index >= 0 and self.bar_index - self.position_open_index >= self.config.max_hold_bars
        ended = int(row["ts"]) >= self.config.evaluation_end_ns
        if before_funding or timed_out or ended:
            self.exit_pending = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            if self.current_scenario_id is not None:
                self._transition(self.current_scenario_id, "FORCED_DAYTRADE_EXIT", int(row["ts"]), int(row["ts"]), "EXIT_PENDING", "FUNDING_OR_HOLD_OR_EVALUATION_BOUNDARY", float(row["close"]), {"before_funding": before_funding, "timed_out": timed_out, "evaluation_ended": ended})

    def on_order_rejected(self, event: Any) -> None:
        self._order_failure(event, "ORDER_REJECTED")

    def on_order_denied(self, event: Any) -> None:
        self.diagnostics["order_denials"] += 1
        self._order_failure(event, "ORDER_DENIED")

    def _order_failure(self, event: Any, event_type: str) -> None:
        self.diagnostics["order_rejections"] += 1
        ts = int(getattr(event, "ts_event", self.bars[-1]["ts"]))
        self.order_rejection_events.append({"scenario_id": self.current_scenario_id, "ts_event": ts, "event_type": event_type, "event": str(event)})
        if self.current_scenario_id is not None and self.scenario_states.get(self.current_scenario_id) != "CLOSED":
            self._transition(self.current_scenario_id, event_type, ts, ts, "CLOSED" if self.portfolio.is_flat(self.config.instrument_id) else "EXIT_PENDING", f"NAUTILUS_{event_type}", float(self.bars[-1]["close"]), {"event": str(event)})
        if self.portfolio.is_flat(self.config.instrument_id):
            self._clear_trade_state()
        elif not self.exit_pending:
            self.exit_pending = True
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self.exit_pending = False
        self.entry_expires_index = -1
        self.entry_side = 0
        self.entry_stop = float("nan")
        self.entry_limit = float("nan")

    def on_stop(self) -> None:
        if self.entry_pending:
            self.cancel_all_orders(self.config.instrument_id)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
        self._record_equity(int(self.bars[-1]["ts"]) if self.bars else 0)
        destination = Path(self.config.output_dir)
        write_events(destination / "scenario_events.jsonl", self.events)
        (destination / "closed_scenarios.json").write_text(json.dumps(self.closed_scenarios, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (destination / "order_rejections.json").write_text(json.dumps(self.order_rejection_events, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (destination / "strategy_diagnostics.json").write_text(json.dumps(self.diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if self.equity:
            with (destination / "equity.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["ts_event", "equity"])
                writer.writeheader()
                writer.writerows(self.equity)


__all__ = ["LiquidityResponseRetraceStrategy"]
